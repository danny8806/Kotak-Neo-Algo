"""Explicit automatic authorization; all broker behavior stays isolated in tests."""
from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from live_desk.dhan_data import IST
from live_desk.strategy import LivePolicy
from live_desk.supervisor import LiveSupervisor

NOW = datetime(2026, 11, 11, 9, 20, tzinfo=IST)


class AutomaticTradingTests(unittest.TestCase):
    def desk(self):
        d = object.__new__(LiveSupervisor)
        d.clock = lambda: NOW
        d.lock = threading.RLock()
        d.allowed = d.auto_trading = d.entry_watchlist = True
        d.armed = d.session_authorized = False
        d.arm_secret = ''
        d.auto_paused_on = d.auto_arm_error = None
        d.broker = object()
        d.broker_positions = []
        d.kotak_auth = d.dhan_auth = None
        d.dhan = SimpleNamespace(token='isolated-test-token')
        d.report = {'status': 'READY', 'signals': [{'symbol': 'MRF'}]}
        d.dhan_ids = d.kotak_ids = {'MRF': '2277'}
        d.opening_ranges = {'MRF': (Decimal('124000'), Decimal('123000'))}
        d.ticker = SimpleNamespace(snapshot=lambda: {'state': 'CONNECTED', 'connection_generation': 1})
        d.last_quote_at = d.last_book_at = NOW
        d._entry_generation = 1
        d.available_cash = '1000'
        d.runtime = SimpleNamespace(errors=[], enabled=False)
        d.error = None
        d.prior_session_recovery = 0
        d.policy = LivePolicy(time(9, 30), time(15), 5, 5, 10,
                             Decimal('100000'), Decimal('1000000'), 'end', 1, 1)
        d.ledger = Mock()
        d.ledger.snapshot.return_value = []
        return d

    def test_authenticated_ready_one_share_mode_arms_without_operator_otp(self):
        d = self.desk()
        d._maybe_auto_arm(NOW)
        self.assertTrue(d.armed)
        self.assertTrue(d.session_authorized)
        self.assertTrue(d.runtime.enabled)
        d._maybe_auto_arm(NOW)
        self.assertEqual(d.ledger.snapshot.call_count, 1)

    def test_disabled_or_non_fixed_mode_never_auto_arms(self):
        for field, value in [('auto_trading', False), ('allowed', False)]:
            d = self.desk()
            setattr(d, field, value)
            d._maybe_auto_arm(NOW)
            self.assertFalse(d.armed)
        d = self.desk()
        d.policy = replace(d.policy, fixed_entry_quantity=None)
        d._maybe_auto_arm(NOW)
        self.assertFalse(d.armed)

    def test_window_and_weekend_gates_cannot_be_bypassed(self):
        for at in [NOW.replace(hour=9, minute=15), NOW.replace(minute=30),
                   NOW.replace(hour=18), NOW + timedelta(days=3)]:
            with self.subTest(at=at):
                d = self.desk()
                d.clock = lambda: at
                d.last_quote_at = d.last_book_at = at
                d._maybe_auto_arm(at)
                self.assertFalse(d.armed)
                self.assertFalse(d.runtime.enabled)

    def test_stale_feed_broker_and_recovery_block_automatic_authorization(self):
        for field, value in [('last_quote_at', NOW-timedelta(seconds=30)),
                             ('last_book_at', NOW-timedelta(seconds=30)),
                             ('broker', None), ('prior_session_recovery', 1),
                             ('entry_watchlist', False)]:
            with self.subTest(field=field):
                d = self.desk()
                setattr(d, field, value)
                d._maybe_auto_arm(NOW)
                self.assertFalse(d.armed)

    def test_uncertain_order_or_overlapping_position_never_auto_arms(self):
        for position in [False, True]:
            d = self.desk()
            if position:
                d.broker_positions = [{'symbol': 'MRF', 'net_quantity': 1}]
            else:
                d.ledger.snapshot.return_value = [{'state': 'SUBMISSION_UNKNOWN'}]
            d._maybe_auto_arm(NOW)
            self.assertFalse(d.armed)
            self.assertIsNotNone(d.auto_arm_error)

    def test_operator_stop_and_health_halt_persist_daily_pause(self):
        for was_armed in [False, True]:
            d = self.desk()
            if was_armed:
                d._maybe_auto_arm(NOW)
            d.disarm('Isolated stop test', pause_automatic=not was_armed)
            self.assertFalse(d.runtime.enabled)
            d.ledger.write_settings.assert_called_once_with(
                {'auto_trading_paused_on': NOW.date().isoformat()}, NOW)
            d._maybe_auto_arm(NOW)
            self.assertFalse(d.armed)
            tomorrow = NOW + timedelta(days=1)
            d.clock = lambda: tomorrow
            d.last_quote_at = d.last_book_at = tomorrow
            d._maybe_auto_arm(tomorrow)
            self.assertTrue(d.armed)

    def test_pause_only_settings_restore_after_restart_without_budget_settings(self):
        ledger = Mock()
        ledger.read_settings.return_value = {'auto_trading_paused_on': NOW.date().isoformat()}
        with patch('live_desk.supervisor.LiveLedger', return_value=ledger), patch.dict(
                'os.environ', {'LIVE_AUTO_TRADING': 'YES', 'LIVE_FIXED_ENTRY_QUANTITY': '1'}):
            d = LiveSupervisor(None, ledger_path='postgresql://unused/isolated',
                               clock=lambda: NOW, kotak_factory=lambda: None)
        self.addCleanup(lambda: d.preparation.shutdown(wait=False))
        self.assertTrue(d.auto_trading)
        self.assertEqual(d.auto_paused_on, NOW.date().isoformat())
        d._maybe_auto_arm(NOW)
        self.assertFalse(d.armed)
