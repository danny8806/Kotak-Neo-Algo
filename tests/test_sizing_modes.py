"""Configured sizes reach reservation without extra local balance/notional gates."""
from dataclasses import replace
from datetime import datetime,time
from decimal import Decimal
import unittest
from live_desk.strategy import LivePolicy,candidate_entry
from live_desk.dhan_data import IST

class SizingModeTests(unittest.TestCase):
    def setUp(self):
        self.policy=LivePolicy(time(9,30),time(15),5,5,10,Decimal('500'),Decimal('1'),'end',1,2)
        self.args=dict(side='LONG',previous_price=Decimal('100000'),price=Decimal('100001'),
            orb_high=Decimal('100000'),orb_low=Decimal('90000'),tick_at=datetime(2026,11,11,9,20,tzinfo=IST))
    def test_two_shares_ignore_legacy_one_cap_and_tiny_gross_balance(self):
        self.assertEqual(candidate_entry(**self.args,policy=self.policy,reserved_gross=Decimal('99999999')),2)
    def test_capital_mode_uses_configured_capital_only_and_rounds_down(self):
        args={**self.args,'price':Decimal('103'),'previous_price':Decimal('100'),'orb_high':Decimal('100'),'orb_low':Decimal('95')}
        self.assertEqual(candidate_entry(**args,policy=replace(self.policy,fixed_entry_quantity=None)),4)
        self.assertIsNone(candidate_entry(**args,policy=replace(self.policy,fixed_entry_quantity=None,per_position=Decimal('1'))))
    def test_invalid_quantity_and_position_window_gates_still_apply(self):
        for qty in (0,-1,1.5,True):
            with self.assertRaises(ValueError):replace(self.policy,fixed_entry_quantity=qty)
        self.assertIsNone(candidate_entry(**self.args,policy=self.policy,open_positions=10))
        self.assertIsNone(candidate_entry(**{**self.args,'tick_at':self.args['tick_at'].replace(minute=30)},policy=self.policy))

if __name__=='__main__':unittest.main()
