"""Real-book P&L contracts: stale/missing prices and costs never become profit."""
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from datetime import datetime,timedelta
from live_desk.dhan_data import IST
from live_desk.broker_view import positions,pnl_summary
from live_desk.supervisor import LiveSupervisor
from bridge.app import create_app
from postgres_test_db import create_test_database

NOW=datetime(2026,11,5,9,18,tzinfo=IST)


class PositionClient:
    def __init__(self,row):self.row=row;self.order_calls=0
    def positions(self):return {'stat':'Ok','data':[{'exSeg':'nse_cm','trdSym':'TESTA-EQ','prod':'MIS',**self.row}]}
    def order_report(self):return {'stat':'Not_Ok','stCode':5203,'errMsg':'No Data'}
    def trade_report(self):return {'stat':'Not_Ok','stCode':5203,'errMsg':'No Data'}
    def limits(self):return {'stat':'Ok','Net':'1000000'}
    def place_order(self,**kw):self.order_calls+=1;raise AssertionError('No real orders in P&L tests')


class PnlContractTests(unittest.TestCase):
    def test_long_partial_close_has_separate_realized_and_live_unrealized(self):
        row=positions(PositionClient({'flBuyQty':10,'buyAmt':'1000','flSellQty':5,'sellAmt':'550'}),{'TESTA':{'ltp':105}})[0]
        self.assertEqual((row['realized_gross'],row['unrealized_gross'],row['gross_mark_to_market']),(50,25,75))

    def test_short_partial_close_has_correct_profit_sign(self):
        row=positions(PositionClient({'flBuyQty':5,'buyAmt':'450','flSellQty':10,'sellAmt':'1000'}),{'TESTA':{'ltp':95}})[0]
        self.assertEqual((row['realized_gross'],row['unrealized_gross'],row['gross_mark_to_market']),(50,25,75))

    def test_closed_position_realized_profit_needs_no_live_quote(self):
        row=positions(PositionClient({'flBuyQty':10,'buyAmt':'1000','flSellQty':10,'sellAmt':'1050'}))[0]
        self.assertEqual((row['gross_mark_to_market'],row['realized_gross'],row['unrealized_gross']),(50,50,0))
        self.assertEqual(row['pnl_status'],'CLOSED')

    def test_open_position_without_quote_does_not_claim_zero_total_profit(self):
        rows=positions(PositionClient({'flBuyQty':10,'buyAmt':'1000'}))
        summary=pnl_summary(rows,books_fresh=True)
        self.assertIsNone(summary['gross_total']);self.assertIsNone(summary['gross_unrealized'])
        self.assertEqual(summary['gross_realized'],0)
        self.assertIsNone(summary['net_after_fees'])

    def test_missing_carryforward_acquisition_cost_is_not_invented(self):
        row=positions(PositionClient({'cfBuyQty':10,'cfBuyAmt':'0','prod':'CNC'}),{'TESTA':{'ltp':100}})[0]
        self.assertIsNone(row['gross_mark_to_market']);self.assertIsNone(row['realized_gross'])
        self.assertEqual(row['pnl_status'],'COST_BASIS_UNVERIFIED')

    def test_only_confirmed_empty_fresh_books_can_show_zero_profit(self):
        self.assertEqual(pnl_summary([],books_fresh=True)['gross_total'],0)
        self.assertIsNone(pnl_summary([],books_fresh=False)['gross_total'])


class PnlSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.url,cleanup=create_test_database();self.addCleanup(cleanup)
        self.app=create_app(database_url=self.url,bearer_token='synthetic-bridge-token-more-than-thirty-two-characters')
        self.addCleanup(self.app.state.engine.dispose)
        self.now=NOW;self.broker=PositionClient({'flBuyQty':3,'buyAmt':'300'})
        self.desk=LiveSupervisor(self.app.state.engine,ledger_path=self.url,clock=lambda:self.now,kotak_factory=lambda:self.broker)
        self.addCleanup(self.desk.stop)
        self.desk.broker=self.broker;self.desk.refresh_broker()

    def test_position_ticks_update_pnl_without_broker_poll_or_order_submission(self):
        self.desk.position_dhan_ids={'TESTA':'123'}
        self.desk.on_position_tick({'symbol':'TESTA','ltp':105,'timestamp':self.now})
        self.assertEqual(self.desk.snapshot()['pnl']['gross_total'],15)
        self.desk.on_position_tick({'symbol':'TESTA','ltp':110,'timestamp':self.now})
        self.assertEqual(self.desk.snapshot()['pnl']['gross_total'],30)
        self.assertIsNone(self.desk.last_quote_at,'Valuation ticks must not unlock signal readiness')
        self.assertEqual(self.broker.order_calls,0)

    def test_late_packet_cannot_extend_execution_readiness(self):
        self.desk.dhan_ids={'TESTA':'123','TESTB':'456'}
        newest=self.now-timedelta(seconds=4)
        self.desk.on_tick({'symbol':'TESTA','ltp':105,'timestamp':newest})
        self.desk.on_tick({'symbol':'TESTB','ltp':106,'timestamp':newest-timedelta(seconds=2)})
        state=self.desk.snapshot()
        self.assertEqual(datetime.fromisoformat(state['last_entry_quote_at']),newest)
        self.assertNotIn('FRESH_QUOTES',state['readiness_blockers'])
        self.now+=timedelta(seconds=2)
        self.assertIn('FRESH_QUOTES',self.desk.snapshot()['readiness_blockers'])
        self.assertEqual(self.broker.order_calls,0)

    def test_slow_dashboard_ledger_snapshot_does_not_hold_tick_lock(self):
        self.desk.dhan_ids={"TESTA":"123"}
        started,release=threading.Event(),threading.Event()
        read=self.desk.ledger.snapshot

        def slow_read(day):
            started.set()
            if not release.wait(3):
                raise RuntimeError("Test did not release the dashboard query")
            return read(day)

        with patch.object(self.desk.ledger,"snapshot",side_effect=slow_read), ThreadPoolExecutor(2) as pool:
            dashboard=pool.submit(self.desk.snapshot)
            try:
                self.assertTrue(started.wait(2))
                tick=pool.submit(self.desk.on_tick,{"symbol":"TESTA","ltp":105,"timestamp":self.now})
                tick.result(timeout=1)
                self.assertEqual(self.desk.last_quote_at,self.now)
            finally:
                release.set()
            dashboard.result(timeout=2)

    def test_slow_or_unavailable_candle_does_not_hide_other_verified_ranges(self):
        from unittest.mock import Mock

        desk=self.desk
        desk.session_date=NOW.date().isoformat()
        desk.entry_watchlist=True
        desk.dhan_ids={"TESTA":"123","TESTB":"456"}
        desk.ticker=Mock()
        released, published=threading.Event(),threading.Event()

        class PublishedRanges(dict):
            def __setitem__(self,symbol,bounds):
                super().__setitem__(symbol,bounds)
                if symbol=="TESTA":
                    published.set()

        desk.opening_ranges=PublishedRanges()

        def candle(_dhan,security_id,_day,**_kwargs):
            if security_id=="456":
                if not released.wait(3):
                    raise RuntimeError("Test did not release the slow candle")
                raise ValueError("Dhan has not published this candle")
            return 100,95

        with patch("live_desk.supervisor.opening_range",side_effect=candle), ThreadPoolExecutor(1) as pool:
            cycle=pool.submit(desk._build_runtime)
            try:
                self.assertTrue(published.wait(2))
                self.assertEqual(desk.opening_ranges,{"TESTA":(100,95)})
                self.assertIsNone(desk.runtime)
            finally:
                released.set()
            cycle.result(timeout=2)
        self.assertEqual(desk.status,"WAITING_FOR_OPENING_CANDLE")

    def test_broker_books_overlap_independent_reads_and_keep_order_before_fills(self):
        wave=threading.Barrier(3)
        orders_observed=threading.Event()
        original_positions=self.broker.positions
        original_limits=self.broker.limits
        original_orders=self.broker.order_report
        original_trades=self.broker.trade_report

        def positions():
            wave.wait(timeout=2)
            return original_positions()

        def limits():
            wave.wait(timeout=2)
            return original_limits()

        def orders():
            wave.wait(timeout=2)
            orders_observed.set()
            return original_orders()

        def trades():
            self.assertTrue(orders_observed.is_set())
            return original_trades()

        with patch.multiple(self.broker,positions=positions,limits=limits,order_report=orders,trade_report=trades):
            self.desk.refresh_broker()
        self.assertTrue(self.desk.snapshot()["broker_connected"])
        self.assertEqual(self.broker.order_calls,0)

    def test_reconnected_feed_requires_fresh_baselines_and_rearming(self):
        from unittest.mock import Mock

        desk=self.desk
        desk.dhan_ids={"TESTA":"123"}
        desk.runtime=Mock()
        desk.runtime.errors=[]
        desk.ticker=Mock()
        desk.ticker.snapshot.return_value={"state":"CONNECTED","connection_generation":1}
        desk.on_tick({"symbol":"TESTA","ltp":100,"timestamp":self.now,"feed_generation":1})
        desk.armed=True
        desk._orb_previous["TESTA"]=100
        desk.ticker.snapshot.return_value={"state":"CONNECTED","connection_generation":2}
        self.assertIn("FRESH_QUOTES",desk.snapshot()["readiness_blockers"])
        desk.on_tick({"symbol":"TESTA","ltp":101,"timestamp":self.now,"feed_generation":2})
        self.assertFalse(desk.armed)
        desk.runtime.reset_crossing_baselines.assert_called_once()
        self.assertNotIn("TESTA",desk._orb_previous)
        self.assertNotIn("FRESH_QUOTES",desk.snapshot()["readiness_blockers"])

    def test_snapshot_expires_quote_even_between_broker_refreshes(self):
        self.desk.quote_data={'TESTA':{'ltp':105,'tick_at':self.now.isoformat()}}
        self.assertEqual(self.desk.snapshot()['pnl']['gross_total'],15)
        self.now+=timedelta(seconds=9)
        state=self.desk.snapshot()
        self.assertIsNone(state['pnl']['gross_total']);self.assertIsNone(state['positions'][0]['ltp'])
        self.assertEqual(state['positions'][0]['pnl_status'],'QUOTE_UNAVAILABLE')

    def test_snapshot_expires_books_and_funds_when_broker_stops_refreshing(self):
        self.now+=timedelta(seconds=21)
        state=self.desk.snapshot()
        self.assertFalse(state['broker_connected']);self.assertIsNone(state['available_cash'])
        self.assertIsNone(state['pnl']['gross_total'])
        self.assertEqual(state['positions'][0]['pnl_status'],'BOOKS_STALE')

    def test_all_broker_orders_and_fills_are_returned_without_silent_truncation(self):
        self.desk.broker_orders=[{'broker_order_id':'O'+str(i)} for i in range(151)]
        self.desk.broker_fills=[{'fill_id':'F'+str(i)} for i in range(151)]
        state=self.desk.snapshot()
        self.assertEqual(len(state['orders']),151);self.assertEqual(len(state['fills']),151)

    def test_negative_funds_blocks_entries_without_hiding_account_books(self):
        self.broker.limits=lambda:{'stat':'Ok','Net':'-1.00'}
        self.desk.refresh_broker();state=self.desk.snapshot()
        self.assertTrue(state['broker_connected'])
        self.assertEqual(state['available_cash'],'-1.00')
        self.assertIn('FUNDS',state['readiness_blockers'])
        self.assertEqual(state['positions'][0]['net_quantity'],3)
        self.assertFalse(state['readiness']);self.assertEqual(self.broker.order_calls,0)
