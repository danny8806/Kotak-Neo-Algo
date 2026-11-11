"""One-share mode delegates margin to the broker without bypassing other gates."""
from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace
from pathlib import Path
import unittest

from live_desk.dhan_data import IST
from live_desk.strategy import LivePolicy, candidate_entry, load_policy
from live_desk.supervisor import LiveSupervisor


NOW = datetime(2026, 11, 11, 9, 20, tzinfo=IST)


class FixedQuantityTests(unittest.TestCase):
    def setUp(self):
        self.policy = LivePolicy(time(9, 30), time(15), 5, 5, 10,
                                 Decimal('100000'), Decimal('1000000'), 'end', 1, 1)

    def test_expensive_share_uses_one_and_preserves_total_exposure_limit(self):
        args = dict(side='LONG', previous_price=Decimal('124000'), price=Decimal('124001'),
                    orb_high=Decimal('124000'), orb_low=Decimal('123000'), tick_at=NOW)
        self.assertEqual(candidate_entry(**args, policy=self.policy), 1)
        self.assertIsNone(candidate_entry(**args, policy=replace(self.policy, fixed_entry_quantity=None)))
        self.assertIsNone(candidate_entry(**args, policy=self.policy, reserved_gross=Decimal('900000')))
        self.assertIsNone(candidate_entry(**args, policy=self.policy, open_positions=10))
        self.assertIsNone(candidate_entry(**args, policy=self.policy, open_long=5))
        args.update(side='SHORT', previous_price=Decimal('123000'), price=Decimal('122999'))
        self.assertEqual(candidate_entry(**args, policy=self.policy), 1)

    def test_margin_delegation_keeps_freshness_order_permission_and_window_checks(self):
        desk = object.__new__(LiveSupervisor)
        desk.allowed = True
        desk.entry_watchlist = True
        desk.broker = object()
        desk.kotak_auth = desk.dhan_auth = None
        desk.dhan = SimpleNamespace(token='test-token')
        desk.report = {'status': 'READY', 'signals': [{'symbol': 'MRF'}]}
        desk.dhan_ids = desk.kotak_ids = {'MRF': '2277'}
        desk.opening_ranges = {'MRF': (Decimal('124000'), Decimal('123000'))}
        desk.ticker = SimpleNamespace(snapshot=lambda: {'state': 'CONNECTED', 'connection_generation': 1})
        desk.last_quote_at = desk.last_book_at = NOW
        desk._entry_generation = 1
        desk.available_cash = '-1'
        desk.runtime = SimpleNamespace(errors=[])
        desk.error = None
        desk.prior_session_recovery = 0
        desk.policy = self.policy
        self.assertEqual(desk._readiness_blockers_locked(NOW), [])
        desk.policy = replace(self.policy, fixed_entry_quantity=None)
        self.assertIn('FUNDS', desk._readiness_blockers_locked(NOW))
        desk.policy = self.policy
        desk.allowed = False
        self.assertIn('SERVER_ORDERS_DISABLED', desk._readiness_blockers_locked(NOW))
        desk.allowed = True
        later = NOW + timedelta(minutes=11)
        blockers = desk._readiness_blockers_locked(later)
        for gate in ['FRESH_QUOTES', 'BROKER_BOOKS', 'MARKET_ENTRY_WINDOW']:
            self.assertIn(gate, blockers)

    def test_fixed_mode_requires_explicit_one_share_setting(self):
        path = Path(__file__).parents[1] / 'live_desk/policy.json'
        self.assertIsNone(load_policy(path).fixed_entry_quantity)
        self.assertEqual(load_policy(path, fixed_entry_quantity='1').fixed_entry_quantity, 1)
        for invalid in ['0', '2', '-1', '1.5', 'YES']:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                load_policy(path, fixed_entry_quantity=invalid)
