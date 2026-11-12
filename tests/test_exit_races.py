"""Real PostgreSQL exit contention with isolated broker stubs; no live orders."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time
from decimal import Decimal
import threading
import time as wall_time
import unittest
from unittest.mock import patch

from sqlalchemy import event
from live_desk.dhan_data import IST
from live_desk.kotak_instruments import KotakInstrument
from live_desk.ledger import LiveLedger
from live_desk.runtime import LiveRuntime
from live_desk.strategy import LivePolicy
from postgres_test_db import create_test_database

AT=datetime(2026,11,11,15,0,tzinfo=IST)


class ExitRaceTests(unittest.TestCase):
    def setUp(self):
        self.url,cleanup=create_test_database();self.addCleanup(cleanup)
        self.ledgers=[LiveLedger(self.url),LiveLedger(self.url)]
        for ledger in self.ledgers:self.addCleanup(ledger.close)
        e=self.ledgers[0].reserve_entry(session_date=AT.date(),symbol='RACE',side='BUY',
            quantity=1,reference_price=Decimal('100'),at=AT.replace(hour=9,minute=20),
            max_per_side=5,gross_cap=Decimal('10000'),max_total=10)
        self.entry_tag=e['tag']
        self.ledgers[0].mark_submission_unknown(e['tag'],AT)
        self.ledgers[0].apply_broker_report(self.report(e['tag']),AT)

    def report(self,tag):
        rows=self.ledgers[0].snapshot(AT.date())
        row=next(r for r in rows if r['tag']==tag)
        return dict(tag=tag,broker_order_id='ISOLATED-'+row['phase'],status='filled',
                    ordered_quantity=1,filled_quantity=1,average_fill_price='100',trading_symbol='RACE-EQ')

    def widen_prior_exit_read(self):
        def delay(_conn,_cursor,statement,parameters,_context,_executemany):
            values=parameters.values() if isinstance(parameters,dict) else parameters or []
            if statement.lstrip().startswith('SELECT') and 'live_order_intents.phase' in statement and 'EXIT' in values:
                wall_time.sleep(.02)
        for ledger in self.ledgers:
            event.listen(ledger.engine,'after_cursor_execute',delay)
            self.addCleanup(event.remove,ledger.engine,'after_cursor_execute',delay)

    def runtime(self,ledger):
        r=LiveRuntime(session_date=AT.date(),signals=[{'symbol':'RACE','side':'LONG'}],
            dhan_ids={'RACE':'1'},kotak_instruments={'RACE':KotakInstrument('RACE','RACE-EQ','1')},
            opening_ranges={'RACE':(Decimal('100'),Decimal('99'))},
            policy=LivePolicy(time(9,30),time(15),5,5,10,Decimal('100000'),Decimal('1000000'),'start',1,1),
            ledger=ledger,broker=object(),enabled=False,clock=lambda:AT)
        self.addCleanup(r.close);return r

    def test_twenty_independent_exit_claims_return_one_without_errors_or_deadlock(self):
        self.widen_prior_exit_read()
        gate=threading.Barrier(20)
        def claim(i):
            gate.wait(timeout=5)
            try:return self.ledgers[i%2].reserve_exit(session_date=AT.date(),symbol='RACE',at=AT)
            except Exception as exc:return type(exc).__name__
        started=wall_time.monotonic()
        with ThreadPoolExecutor(max_workers=20) as pool:results=list(pool.map(claim,range(20)))
        self.assertLess(wall_time.monotonic()-started,8,'Exit reservation deadlock/lock stall')
        self.assertEqual([x for x in results if isinstance(x,str)],[], 'Concurrent exit claims must lose cleanly')
        self.assertEqual(sum(isinstance(x,dict) for x in results),1)

    def test_twenty_simultaneous_squareoffs_send_exactly_one_exit(self):
        self.widen_prior_exit_read()
        runtimes=[self.runtime(x) for x in self.ledgers]
        gate=threading.Barrier(20);calls=[];lock=threading.Lock()
        def send(_broker,instrument,side,quantity,tag,**_kwargs):
            with lock:calls.append((side,quantity,tag))
            return {'broker_order_id':'ISOLATED-EXIT','tag':tag}
        def squareoff(i):
            gate.wait(timeout=5)
            return runtimes[i%2].exit_due(AT,read_positions=lambda:[{'symbol':'RACE','product':'MIS','net_quantity':1}])
        with patch('live_desk.runtime.place_kotak_order',side_effect=send),patch(
                'live_desk.runtime.reconcile_kotak_tag',side_effect=lambda _broker,tag:self.report(tag)):
            with ThreadPoolExecutor(max_workers=20) as pool:
                results=list(pool.map(squareoff,range(20)))
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][:2],('SELL',1))
        self.assertEqual(len([r for r in self.ledgers[0].snapshot(AT.date()) if r['phase']=='EXIT']),1)
        self.assertEqual(next(r for r in self.ledgers[0].snapshot(AT.date()) if r['phase']=='EXIT')['state'],'FILLED')

    def test_slow_exit_network_holds_neither_runtime_nor_database_claim_lock(self):
        runtime=self.runtime(self.ledgers[0]);started=threading.Event();release=threading.Event()
        def send(*args,**kwargs):
            started.set()
            if not release.wait(5):raise RuntimeError('Isolated network wait exceeded')
            return {'broker_order_id':'ISOLATED-EXIT'}
        with patch('live_desk.runtime.place_kotak_order',side_effect=send),patch(
                'live_desk.runtime.reconcile_kotak_tag',side_effect=lambda _broker,tag:self.report(tag)):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(runtime.exit_due,AT)
                try:
                    self.assertTrue(started.wait(3))
                    acquired=runtime.lock.acquire(timeout=.5)
                    self.assertTrue(acquired,'Broker HTTP held the runtime lock')
                    if acquired:runtime.lock.release()
                    begin=wall_time.monotonic()
                    self.assertIsNone(self.ledgers[1].reserve_exit(session_date=AT.date(),symbol='RACE',at=AT))
                    self.assertLess(wall_time.monotonic()-begin,1,'Broker HTTP held a PostgreSQL exit lock')
                finally:release.set()
                future.result(timeout=5)
