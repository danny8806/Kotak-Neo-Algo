"""Production-response contracts, auth races, and isolated PostgreSQL replays.

These tests never contact a broker or the production database. SDK wire tests
use httpx.MockTransport; exchange behavior is simulated, not an exchange fill.
"""
import json
import os
import struct
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from live_desk.dhan_data import IST, DhanTokenSupervisor
from live_desk.kotak_auth import KotakSession, KotakSessionUnavailable
from live_desk.kotak_gateway import place_kotak_order, reconcile_kotak_tag
from live_desk.kotak_instruments import KotakInstrument
from live_desk.kotak_response import book_rows
from live_desk.ledger import LiveLedger
from live_desk.runtime import LiveRuntime
from live_desk.strategy import LivePolicy
from live_desk.ticker import LiveTicker, parse_ticker
from live_desk.rate_limit import SlidingWindowLimiter
from postgres_test_db import create_test_database

NOW=datetime(2026,11,5,9,18,tzinfo=IST)
NO_DATA={"stat":"Not_Ok","stCode":5203,"errMsg":"No Data","desc":"data not found"}


class AuthenticationRecoveryTests(unittest.TestCase):
    def test_parallel_broker_pacing_obeys_sliding_window(self):
        import time as wall_time
        limiter=SlidingWindowLimiter(3,.05);lock=threading.Lock();stamps=[]
        def request(_):
            limiter.acquire()
            with lock:stamps.append(wall_time.monotonic())
        with ThreadPoolExecutor(max_workers=20) as pool:list(pool.map(request,range(20)))
        stamps.sort()
        for i in range(3,len(stamps)):
            self.assertGreaterEqual(stamps[i]-stamps[i-3],.045)

    def test_kotak_startup_before_eight_refreshes_at_eight_and_next_day(self):
        now=[NOW.replace(hour=7)]
        factory_calls=[]
        manager=KotakSession(lambda:factory_calls.append(1) or object(),clock=lambda:now[0])
        self.assertTrue(manager.ensure_authenticated())
        self.assertTrue(manager.ensure_authenticated())
        now[0]=now[0].replace(hour=8,minute=0)
        self.assertTrue(manager.ensure_authenticated())
        now[0]+=timedelta(days=1)
        self.assertTrue(manager.ensure_authenticated())
        self.assertEqual(len(factory_calls),3)

    def test_twenty_parallel_refresh_requests_perform_one_login(self):
        started=threading.Event();release=threading.Event();calls=[]
        def login():
            calls.append(1);started.set();self.assertTrue(release.wait(2));return object()
        manager=KotakSession(login,clock=lambda:NOW)
        with ThreadPoolExecutor(max_workers=20) as pool:
            first=pool.submit(manager.ensure_authenticated)
            self.assertTrue(started.wait(1))
            others=[pool.submit(manager.ensure_authenticated) for _ in range(19)]
            self.assertTrue(all(f.result(timeout=1) is False for f in others))
            release.set();self.assertTrue(first.result(timeout=2))
        self.assertEqual(len(calls),1)

    def test_auth_failure_backoff_hides_exception_details(self):
        now=[NOW];calls=[]
        def fail():calls.append(1);raise RuntimeError('synthetic-secret-not-for-status')
        manager=KotakSession(fail,clock=lambda:now[0])
        for _ in range(10):self.assertFalse(manager.ensure_authenticated())
        self.assertEqual(len(calls),1)
        self.assertNotIn('synthetic-secret',str(manager.snapshot()))
        now[0]+=timedelta(seconds=60)
        self.assertFalse(manager.ensure_authenticated());self.assertEqual(len(calls),2)

    def test_auth_rejected_order_is_never_automatically_replayed(self):
        class Client:
            calls=0
            def place_order(self,**kwargs):
                self.calls+=1;return {"stat":"Not_Ok","stCode":403}
        client=Client();now=[NOW]
        manager=KotakSession(lambda:client,clock=lambda:now[0])
        self.assertTrue(manager.ensure_authenticated())
        with self.assertRaises(RuntimeError):manager.place_order()
        with self.assertRaises(KotakSessionUnavailable):manager.place_order()
        self.assertEqual(client.calls,1)
        self.assertEqual(manager.snapshot()['state'],'AUTH_EXPIRED')
        self.assertFalse(manager.ensure_authenticated())
        now[0]+=timedelta(seconds=30)
        self.assertTrue(manager.ensure_authenticated())

    def test_late_failure_from_old_session_cannot_invalidate_new_session(self):
        started=threading.Event();release=threading.Event()
        class Old:
            def positions(self):
                started.set();release.wait(2);return {'stCode':403}
        clients=iter([Old(),object()]);now=[NOW]
        manager=KotakSession(lambda:next(clients),clock=lambda:now[0]);manager.ensure_authenticated()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(manager.positions);self.assertTrue(started.wait(1))
            now[0]+=timedelta(days=1);self.assertTrue(manager.ensure_authenticated())
            release.set()
            with self.assertRaises(RuntimeError):pending.result(timeout=2)
        self.assertEqual(manager.snapshot()['state'],'AUTHENTICATED')

    def test_slow_book_call_does_not_serialize_other_http_calls_on_auth_lock(self):
        started=threading.Event();release=threading.Event()
        class Client:
            def positions(self):started.set();release.wait(2);return NO_DATA
            def limits(self):return {'stat':'Ok','Net':'100'}
        manager=KotakSession(Client,clock=lambda:NOW);manager.ensure_authenticated()
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending=pool.submit(manager.positions);self.assertTrue(started.wait(1))
            self.assertEqual(pool.submit(manager.limits).result(timeout=.5)['Net'],'100')
            release.set();pending.result(timeout=2)

    def test_dhan_login_before_eight_is_refreshed_at_eight(self):
        created=NOW.replace(hour=7)
        expiry=created+timedelta(hours=24)
        self.assertFalse(DhanTokenSupervisor.refresh_due(created,'token',expiry,created,created.date()))
        self.assertTrue(DhanTokenSupervisor.refresh_due(created.replace(hour=8),'token',expiry,created,created.date()))
        self.assertTrue(DhanTokenSupervisor.refresh_due(created,'',expiry,created,created.date()))

    def test_dhan_expiry_margin_refreshes_before_expiration(self):
        self.assertTrue(DhanTokenSupervisor.refresh_due(NOW,'token',NOW+timedelta(minutes=14),NOW,NOW.date()))

    def test_feed_reconnects_when_dhan_token_changes(self):
        class Client:token_created_at=NOW
        client=Client();ticker=LiveTicker(client,{'TESTA':'123'},lambda tick:None)
        ticker.token_created_at=NOW;closed=[]
        class Socket:
            def close(self):closed.append(1)
        ticker.socket=Socket();client.token_created_at+=timedelta(hours=23)
        self.assertTrue(ticker.refresh_authentication())
        self.assertFalse(ticker.refresh_authentication())
        self.assertEqual(closed,[1]);self.assertEqual(ticker.snapshot()['state'],'RECONNECTING')


class BrokerContractTests(unittest.TestCase):
    def test_exact_production_empty_book_is_valid_but_other_errors_are_not(self):
        for label in ('positions','orders','trades'):self.assertEqual(book_rows(NO_DATA,label),[])
        for bad in ({'stCode':403,'errMsg':'No Data'},
                    {**NO_DATA,'error':[{'code':403}]},
                    {**NO_DATA,'errMsg':'Invalid session'},
                    {'stat':'Ok','data':None}, {'stat':'Ok','data':[None]}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):book_rows(bad,'positions')

    def test_accepted_market_order_requires_broker_order_id_and_exact_symbol(self):
        class Client:
            def place_order(self,**kwargs):return {'stat':'Ok','nOrdNo':''}
        with self.assertRaises(RuntimeError):place_kotak_order(Client(),KotakInstrument('TESTA','TESTA-EQ','123'),'BUY',1,'tag')
        with self.assertRaises(ValueError):place_kotak_order(Client(),KotakInstrument('TESTA','WRONG-EQ','123'),'BUY',1,'tag')

    def test_unfilled_order_with_empty_trade_book_reconciles_without_fake_fill(self):
        class Client:
            def order_report(self):return {'stat':'Ok','data':[{'GuiOrdId':'tag','nOrdNo':'OID','qty':'5','ordSt':'open','trdSym':'TESTA-EQ'}]}
            def trade_report(self):return NO_DATA
        report=reconcile_kotak_tag(Client(),'tag')
        self.assertEqual(report['filled_quantity'],0);self.assertIsNone(report['average_fill_price'])

    def test_missing_tag_in_empty_order_book_remains_unknown(self):
        class Client:
            def order_report(self):return NO_DATA
        with self.assertRaisesRegex(RuntimeError,'missing or ambiguous'):reconcile_kotak_tag(Client(),'tag')

    def test_real_installed_sdk_market_order_wire_contract(self):
        import httpx
        from neo_api_client import NeoAPI
        requests=[]
        def handler(request):
            requests.append(request)
            return httpx.Response(200,json={'stat':'Ok','stCode':200,'nOrdNo':'SYNTHETIC-OID'})
        # Set only synthetic tokens, no real auth endpoint or credential is used.
        client=NeoAPI(consumer_key='synthetic-key',environment='prod',transport=httpx.MockTransport(handler))
        client.configuration.edit_token='synthetic-token'
        client.configuration.edit_sid='synthetic-session'
        client.configuration.edit_rid='synthetic-rid'
        client.configuration.base_url='https://broker.test'
        result=place_kotak_order(client,KotakInstrument('TCS','TCS-EQ','11536'),'BUY',1,'synthetic-tag')
        self.assertEqual(result['broker_order_id'],'SYNTHETIC-OID')
        self.assertEqual(len(requests),1)
        body=requests[0].content.decode()
        from urllib.parse import unquote_plus
        body=unquote_plus(body)
        for value in ('MKT','MIS','TCS-EQ','synthetic-tag'):self.assertIn(value,body)


class ReplayExchange:
    """A deliberately fake exchange with acceptance/response-loss separation."""
    def __init__(self,timeout_after_accept=False,visible=True):
        self.lock=threading.Lock();self.orders=[];self.calls=[]
        self.timeout_after_accept=timeout_after_accept;self.visible=visible;self.filled=False
    def place_order(self,**kwargs):
        with self.lock:
            self.calls.append(kwargs)
            oid='SIM-'+str(len(self.calls))
            self.orders.append({'GuiOrdId':kwargs['tag'],'nOrdNo':oid,'qty':kwargs['quantity'],
                                'ordSt':'complete' if self.filled else 'open','trdSym':kwargs['trading_symbol']})
        if self.timeout_after_accept:raise TimeoutError('simulated lost acknowledgement')
        return {'stat':'Ok','nOrdNo':oid}
    def order_report(self):return {'stat':'Ok','data':list(self.orders)} if self.visible else NO_DATA
    def trade_report(self):
        if not self.filled:return NO_DATA
        return {'stat':'Ok','data':[{'nOrdNo':r['nOrdNo'],'trdSym':r['trdSym'],'flId':'F-'+r['nOrdNo'],'fldQty':r['qty'],'avgPrc':'101'} for r in self.orders]}


class ProductionReplayTests(unittest.TestCase):
    def setUp(self):
        self.url,cleanup=create_test_database();self.addCleanup(cleanup)
        self.ledger=LiveLedger(self.url);self.addCleanup(self.ledger.close)
        self.policy=LivePolicy(time(10),time(15,15),5,5,10,Decimal('100000'),Decimal('1000000'),'end')

    def runtime(self,broker=None,enabled=True):
        runtime=LiveRuntime(session_date=NOW.date(),signals=[{'symbol':'TESTA','side':'LONG'}],
            dhan_ids={'TESTA':'123'},kotak_instruments={'TESTA':KotakInstrument('TESTA','TESTA-EQ','456')},
            opening_ranges={'TESTA':(Decimal('100'),Decimal('95'))},policy=self.policy,
            ledger=self.ledger,broker=broker or ReplayExchange(),enabled=enabled,clock=lambda:NOW)
        self.addCleanup(runtime.close);return runtime

    def reserve(self):
        return self.ledger.reserve_entry(session_date=NOW.date(),symbol='TESTA',side='BUY',quantity=5,
            reference_price=Decimal('100'),at=NOW,max_per_side=5,gross_cap=Decimal('1000000'))

    def report(self,tag,**kw):
        result={'tag':tag,'broker_order_id':'OID','status':'open','ordered_quantity':5,'filled_quantity':0,
                'average_fill_price':None,'trading_symbol':'TESTA-EQ'};result.update(kw);return result

    def cross(self,runtime):
        runtime.on_tick({'symbol':'TESTA','ltp':'100','timestamp':NOW})
        return runtime.on_tick({'symbol':'TESTA','ltp':'101','timestamp':NOW})

    def test_unfilled_acknowledgement_persists_null_fill_price(self):
        intent=self.reserve();self.ledger.mark_submission_unknown(intent['tag'],NOW)
        self.ledger.apply_broker_report(self.report(intent['tag']),NOW)
        row=self.ledger.snapshot(NOW.date())[0]
        self.assertEqual(row['state'],'ACKNOWLEDGED');self.assertIsNone(row['average_fill_price'])

    def test_order_id_and_quantity_conflicts_cannot_overwrite_intent(self):
        intent=self.reserve();tag=intent['tag'];self.ledger.mark_submission_unknown(tag,NOW)
        self.ledger.apply_broker_report(self.report(tag),NOW)
        for changes in ({'broker_order_id':'WRONG'},{'ordered_quantity':6},{'filled_quantity':1,'average_fill_price':'NaN'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.ledger.apply_broker_report(self.report(tag,**changes),NOW)
        self.assertEqual(self.ledger.snapshot(NOW.date())[0]['filled_quantity'],0)

    def test_terminal_rejection_cannot_regress_to_open(self):
        intent=self.reserve();tag=intent['tag'];self.ledger.mark_submission_unknown(tag,NOW)
        self.ledger.apply_broker_report(self.report(tag,status='rejected'),NOW)
        with self.assertRaises(ValueError):self.ledger.apply_broker_report(self.report(tag),NOW)

    def test_prior_day_uncertain_order_blocks_new_session_until_reconciled(self):
        intent=self.reserve();tag=intent['tag'];self.ledger.mark_submission_unknown(tag,NOW)
        tomorrow=NOW.date()+timedelta(days=1)
        self.assertEqual(self.ledger.prior_session_recovery_count(tomorrow),1)
        self.ledger.apply_broker_report(self.report(tag,status='rejected'),NOW)
        self.assertEqual(self.ledger.prior_session_recovery_count(tomorrow),0)

    def test_lost_acknowledgement_reconciles_without_duplicate_market_order(self):
        broker=ReplayExchange(timeout_after_accept=True);broker.filled=True
        runtime=self.runtime(broker);self.cross(runtime);self.assertTrue(runtime.wait_for_submissions())
        self.assertEqual(len(broker.calls),1)
        self.assertEqual(self.ledger.snapshot(NOW.date())[0]['state'],'FILLED')
        self.assertEqual(runtime.errors,[])

    def test_eventual_order_visibility_recovers_without_resubmission(self):
        broker=ReplayExchange(timeout_after_accept=True,visible=False)
        runtime=self.runtime(broker);self.cross(runtime);self.assertTrue(runtime.wait_for_submissions())
        self.assertTrue(runtime.errors)
        self.assertEqual(self.ledger.snapshot(NOW.date())[0]['state'],'SUBMISSION_UNKNOWN')
        broker.visible=True;broker.filled=True;runtime.reconcile_pending(NOW)
        self.assertEqual(self.ledger.snapshot(NOW.date())[0]['state'],'FILLED')
        self.assertEqual(runtime.errors,[]);self.assertEqual(len(broker.calls),1)

    def test_restart_never_replays_unknown_order(self):
        broker=ReplayExchange(timeout_after_accept=True,visible=False)
        first=self.runtime(broker);self.cross(first);self.assertTrue(first.wait_for_submissions());first.close()
        restarted=self.runtime(broker,enabled=False)
        broker.visible=True;broker.filled=True;restarted.reconcile_pending(NOW)
        self.assertEqual(len(broker.calls),1);self.assertEqual(self.ledger.snapshot(NOW.date())[0]['state'],'FILLED')

    def test_disarmed_or_delayed_queued_entry_never_reaches_broker(self):
        runtime=self.runtime(enabled=False);intent=self.reserve()
        self.assertEqual(runtime._submit_and_reconcile(intent,NOW)['state'],'ABORTED')
        self.assertEqual(runtime.broker.calls,[])

    def test_stale_queued_crossing_is_aborted_before_http(self):
        runtime=self.runtime();intent=self.reserve()
        runtime.clock=lambda:NOW+timedelta(seconds=6)
        self.assertEqual(runtime._submit_and_reconcile(intent,NOW)['state'],'ABORTED')
        self.assertEqual(runtime.broker.calls,[])

    def test_twenty_duplicate_binary_ticks_submit_exactly_one_market_order(self):
        broker=ReplayExchange();broker.filled=True;runtime=self.runtime(broker)
        runtime.on_tick({'symbol':'TESTA','ltp':'100','timestamp':NOW})
        packet=struct.pack('<BhBifi',2,16,1,123,101.,int(NOW.timestamp()))
        gate=threading.Barrier(20)
        def deliver(_):
            tick=parse_ticker(packet);tick.update(symbol='TESTA',timestamp=datetime.fromisoformat(tick['exchange_timestamp']))
            gate.wait(timeout=3);return runtime.on_tick(tick)
        with ThreadPoolExecutor(max_workers=20) as pool:results=list(pool.map(deliver,range(20)))
        self.assertTrue(runtime.wait_for_submissions())
        self.assertEqual(sum(x is not None for x in results),1)
        self.assertEqual(len(broker.calls),1)
        request=broker.calls[0]
        self.assertEqual((request['order_type'],request['product'],request['amo']),('MKT','MIS','NO'))
        self.assertEqual(self.ledger.snapshot(NOW.date())[0]['state'],'FILLED')

    def test_reconciliation_http_does_not_hold_runtime_tick_lock(self):
        runtime=self.runtime();intent=self.reserve();self.ledger.mark_submission_unknown(intent['tag'],NOW)
        started=threading.Event();release=threading.Event()
        def slow(*args):started.set();release.wait(2);raise RuntimeError('simulated book timeout')
        with patch('live_desk.runtime.reconcile_kotak_tag',side_effect=slow),ThreadPoolExecutor(max_workers=2) as pool:
            pending=pool.submit(runtime.reconcile_pending,NOW);self.assertTrue(started.wait(1))
            tick=pool.submit(runtime.on_tick,{'symbol':'TESTA','ltp':'99','timestamp':NOW})
            self.assertIsNone(tick.result(timeout=.5));release.set();pending.result(timeout=2)

    def test_malformed_ticks_are_ignored_without_killing_feed_callback(self):
        runtime=self.runtime()
        for price in (None,'bad','NaN','Infinity',0,-1):
            self.assertIsNone(runtime.on_tick({'symbol':'TESTA','ltp':price,'timestamp':NOW}))
        self.assertEqual(runtime.broker.calls,[])

    def test_http_signal_mapping_orb_market_order_fill_exit_and_restart(self):
        from fastapi.testclient import TestClient
        from bridge.app import create_app
        from live_desk.app import create_live_app
        from live_desk.supervisor import LiveSupervisor
        from live_desk.dhan_instruments import DhanMasterSnapshot
        from test_failure_cases import ready_fixture
        now=[NOW.replace(minute=3)]
        token='synthetic-bridge-token-at-least-thirty-two-characters'
        password='synthetic-dashboard-password'
        secret='synthetic-session-secret-at-least-thirty-two-characters'
        class Exchange(ReplayExchange):
            def positions(self):
                qty=sum(int(c['quantity'])*(1 if c['transaction_type']=='B' else -1) for c in self.calls)
                if not qty:return NO_DATA
                return {'stat':'Ok','data':[{'exSeg':'nse_cm','trdSym':'TESTA-EQ','flBuyQty':qty,
                    'buyAmt':str(qty*101),'prod':'MIS'}]}
            def limits(self):return {'stat':'Ok','Net':'1500000'}
            def search_scrip(self,**kwargs):return [{'pExchSeg':'nse_cm','pGroup':'EQ',
                'pScripRefKey':'TESTA','pTrdSymbol':'TESTA-EQ','pSymbol':'456'}]
        broker=Exchange();broker.filled=True
        class Dhan:
            token='synthetic-dhan-token'
            def get_intraday(self,*args,**kwargs):return {'timestamp':[int(NOW.replace(minute=16).timestamp())],
                'open':[98],'high':[100],'low':[95],'close':[99]}
            def get_ohlc(self,*args,**kwargs):return {}
        class Auth:
            def snapshot(self):return {'state':'AUTHENTICATED'}
            def stop(self):pass
        class Ticker:
            def __init__(self,*args):pass
            def start(self):pass
            def stop(self):pass
            def snapshot(self):return {'state':'CONNECTED','error':None}
        master=lambda symbols,now:DhanMasterSnapshot(now.date().isoformat(),now,'synthetic-master',{'TESTA':'123'})
        env={'LIVE_ORDERS_ALLOWED':'YES','DASHBOARD_COOKIE_SECURE':'false','LIVE_ARM_TOTP_SECRET':'synthetic-operator-secret'}
        with patch.dict(os.environ,env):
            bridge=create_app(database_url=self.url,bearer_token=token,now_provider=lambda:now[0],allowed_symbols={'TESTA'})
            self.addCleanup(bridge.state.engine.dispose)
            desk=LiveSupervisor(bridge.state.engine,ledger_path=self.url,clock=lambda:now[0],kotak_factory=lambda:broker,
                master_fetch=master,ticker_factory=Ticker,allowed_symbols={'TESTA'})
            self.addCleanup(desk.stop);desk.dhan=Dhan();desk.dhan_auth=Auth()
            app=create_live_app(database_url=self.url,bearer_token=token,dashboard_password=password,
                session_secret=secret,supervisor=desk,clock=lambda:now[0],start_supervisor=False)
            self.addCleanup(app.state.engine.dispose)
            client=TestClient(app);self.addCleanup(client.close)
            report=ready_fixture(NOW.date().isoformat())
            report['signals'][0]['first_available_at']='2026-11-04T16:00:00+05:30'
            report['signals'][0]['sources'][0]['published_at']='2026-11-04T16:00:00+05:30'
            response=client.post('/api/v1/signals/incoming',headers={'Authorization':'Bearer '+token,
                'Idempotency-Key':'synthetic-e2e'},json=report)
            self.assertEqual(response.status_code,200,response.text)
            now[0]=NOW;desk.cycle()
            self.assertEqual(desk.dhan_ids,{'TESTA':'123'});self.assertEqual(desk.kotak_ids['TESTA'].token,'456')
            self.assertEqual(client.post('/api/auth/login',json={'password':password}).status_code,200)
            def emit(price):
                packet=struct.pack('<BhBifi',2,16,1,123,float(price),int(now[0].timestamp()))
                tick=parse_ticker(packet);tick.update(symbol='TESTA',timestamp=datetime.fromisoformat(tick['exchange_timestamp']))
                desk.on_tick(tick)
            emit(100)
            with patch('live_desk.app.totp_now',return_value='123456'):
                response=client.post('/api/live/arm',headers={'Origin':'http://testserver'},json={
                    'acknowledgement':'I AUTHORIZE LIVE KOTAK ORDERS','totp':'123456'})
            self.assertEqual(response.status_code,200,response.text)
            emit(101);self.assertTrue(desk.runtime.wait_for_submissions())
            desk.refresh_broker()
            state=client.get('/api/state').json()
            self.assertEqual(state['intents'][0]['state'],'FILLED')
            self.assertEqual(len(broker.calls),1);self.assertEqual(broker.calls[0]['order_type'],'MKT')
            now[0]=NOW.replace(hour=15,minute=15);desk.cycle()
            self.assertTrue(all(row['state']=='FILLED' for row in desk.ledger.snapshot(NOW.date())))
            self.assertEqual(len(broker.calls),2)
            now[0]+=timedelta(seconds=16);desk.cycle()
            self.assertEqual(len(broker.calls),2,'Completed exits must not cause another exit or position mismatch')
            desk.stop()
            restarted=LiveSupervisor(bridge.state.engine,ledger_path=self.url,clock=lambda:now[0],kotak_factory=lambda:broker,
                master_fetch=master,ticker_factory=Ticker,allowed_symbols={'TESTA'})
            self.addCleanup(restarted.stop);restarted.dhan=Dhan();restarted.dhan_auth=Auth()
            restarted.cycle()
            self.assertFalse(restarted.armed);self.assertEqual(len(broker.calls),2)
