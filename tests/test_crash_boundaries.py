"""Crash checkpoints use isolated PostgreSQL and broker stubs, never live orders."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch,Mock
import test_exit_races as fixtures
from live_desk.supervisor import LiveSupervisor

AT=fixtures.AT

class CrashBoundaryTests(unittest.TestCase):
    setUp=fixtures.ExitRaceTests.setUp
    report=fixtures.ExitRaceTests.report
    runtime=fixtures.ExitRaceTests.runtime

    def test_restart_recovers_exit_reserved_before_process_crashed(self):
        intent=self.ledgers[0].reserve_exit(session_date=AT.date(),symbol='RACE',at=AT)
        self.assertIsNotNone(intent)
        runtime=self.runtime(self.ledgers[1]);calls=[]
        def send(*args,**kwargs):
            calls.append(args[4]);return {'broker_order_id':'ISOLATED-EXIT'}
        with patch('live_desk.runtime.place_kotak_order',side_effect=send),patch('live_desk.runtime.reconcile_kotak_tag',side_effect=lambda broker,tag:self.report(tag)):
            runtime.exit_due(AT,read_positions=lambda:[{'symbol':'RACE','product':'MIS','net_quantity':1}])
            runtime.wait_for_submissions()
        self.assertEqual(calls,[intent['tag']], 'A durable exit that never reached dispatch must not remain stranded')
        row=next(r for r in self.ledgers[0].snapshot(AT.date()) if r['phase']=='EXIT')
        self.assertEqual(row['state'],'FILLED')

    def test_two_restarted_workers_recover_same_reserved_exit_once(self):
        self.ledgers[0].reserve_exit(session_date=AT.date(),symbol='RACE',at=AT)
        runtimes=[self.runtime(x) for x in self.ledgers];calls=[]
        def send(*args,**kwargs):calls.append(args[4]);return {'broker_order_id':'ISOLATED-EXIT'}
        with patch('live_desk.runtime.place_kotak_order',side_effect=send),patch('live_desk.runtime.reconcile_kotak_tag',side_effect=lambda broker,tag:self.report(tag)):
            with ThreadPoolExecutor(max_workers=20) as pool:
                list(pool.map(lambda i:runtimes[i%2].exit_due(AT,read_positions=lambda:[{'symbol':'RACE','product':'MIS','net_quantity':1}]),range(20)))
            for runtime in runtimes:runtime.wait_for_submissions()
        self.assertEqual(len(calls),1)
        self.assertTrue(all(not r.errors for r in runtimes))

    def test_unknown_exit_is_not_replayed_after_restart(self):
        intent=self.ledgers[0].reserve_exit(session_date=AT.date(),symbol='RACE',at=AT)
        self.ledgers[0].mark_submission_unknown(intent['tag'],AT)
        runtime=self.runtime(self.ledgers[1])
        with patch('live_desk.runtime.place_kotak_order') as send,patch('live_desk.runtime.reconcile_kotak_tag',side_effect=RuntimeError('Visibility delayed')):
            runtime.exit_due(AT,read_positions=lambda:[{'symbol':'RACE','product':'MIS','net_quantity':1}])
        send.assert_not_called()

    def test_unsent_reserved_entry_aborted_at_cutoff_without_broker(self):
        intent=self.ledgers[0].reserve_entry(session_date=AT.date(),symbol='UNSENT',side='BUY',quantity=2,reference_price=Decimal('100'),at=AT.replace(hour=9,minute=20),max_per_side=5,gross_cap=None,max_total=10)
        desk=object.__new__(LiveSupervisor);desk.allowed=True;desk.armed=False
        desk.policy=SimpleNamespace(entry_cutoff=AT.replace(hour=9,minute=30).time(),exit_time=AT.time())
        desk.ledger=self.ledgers[0];desk.runtime=None;desk.broker=None
        desk._manage_authorized_exits(AT)
        row=next(r for r in self.ledgers[0].snapshot(AT.date()) if r['tag']==intent['tag'])
        self.assertEqual(row['state'],'ABORTED','An entry provably never dispatched must not permanently block tomorrow')

    def test_reserved_exit_requires_fresh_exact_broker_positions(self):
        self.ledgers[0].reserve_exit(session_date=AT.date(),symbol='RACE',at=AT)
        runtime=self.runtime(self.ledgers[1])
        with patch('live_desk.runtime.place_kotak_order') as send:
            runtime.exit_due(AT)
            runtime.exit_due(AT,read_positions=lambda:[{'symbol':'RACE','product':'MIS','net_quantity':2}])
        send.assert_not_called()
        self.assertEqual(next(r for r in self.ledgers[0].snapshot(AT.date()) if r['phase']=='EXIT')['state'],'RESERVED')

    def test_unsubmitted_entry_cleanup_never_aborts_uncertain_submission(self):
        intent=self.ledgers[0].reserve_entry(session_date=AT.date(),symbol='UNKNOWN',side='BUY',quantity=2,reference_price=Decimal('100'),at=AT,max_per_side=5,gross_cap=None,max_total=10)
        self.ledgers[0].mark_submission_unknown(intent['tag'],AT)
        self.ledgers[0].abort_unsent_entries(AT.date(),AT)
        self.assertEqual(next(r for r in self.ledgers[0].snapshot(AT.date()) if r['tag']==intent['tag'])['state'],'SUBMISSION_UNKNOWN')

    def test_previous_day_unsent_cleanup_preserves_current_day_reservation(self):
        intent=self.ledgers[0].reserve_entry(session_date=AT.date(),symbol='UNSENT',side='BUY',quantity=2,reference_price=Decimal('100'),at=AT,max_per_side=5,gross_cap=None,max_total=10)
        self.assertEqual(self.ledgers[0].abort_unsent_entries(AT.date(),AT,prior_only=True),0)
        tomorrow=AT+timedelta(days=1)
        self.ledgers[0].abort_unsent_entries(tomorrow.date(),tomorrow,prior_only=True)
        self.assertEqual(next(r for r in self.ledgers[0].snapshot(AT.date()) if r['tag']==intent['tag'])['state'],'ABORTED')

    def test_new_cancelled_entry_cleanup_and_queued_abort_are_idempotent(self):
        intent=self.ledgers[0].reserve_entry(session_date=AT.date(),symbol='UNSENT',side='BUY',quantity=2,reference_price=Decimal('100'),at=AT,max_per_side=5,gross_cap=None,max_total=10)
        self.assertEqual(self.ledgers[0].abort_unsent_entries(AT.date(),AT),1)
        self.assertEqual(self.ledgers[0].abort_unsent_entries(AT.date(),AT),0)
        self.ledgers[0].mark_not_submitted(intent['tag'],AT)
