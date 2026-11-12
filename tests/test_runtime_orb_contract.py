"""Entry construction cannot bypass the completed-start-bar contract."""
import unittest
from dataclasses import replace
from datetime import datetime, time
from decimal import Decimal
from unittest.mock import Mock

from live_desk.dhan_data import IST
from live_desk.kotak_instruments import KotakInstrument
from live_desk.runtime import LiveRuntime
from live_desk.strategy import LivePolicy


class RuntimeOrbContractTests(unittest.TestCase):
    def kwargs(self):
        now = datetime(2026, 11, 12, 9, 17, tzinfo=IST)
        return dict(session_date=now.date(), signals=[dict(symbol='TCS', side='LONG')],
            dhan_ids={'TCS': '11536'},
            kotak_instruments={'TCS': KotakInstrument('TCS', 'TCS-EQ', '11536')},
            opening_ranges={'TCS': (Decimal('100'), Decimal('95'))},
            policy=LivePolicy(time(9,30), time(15), 5,5,10,Decimal('100000'),Decimal('1000000'),'start'),
            ledger=Mock(), broker=Mock(), enabled=True, clock=lambda: now)

    def test_entry_rejects_obsolete_or_unknown_candle_convention(self):
        for mode in ('end', None, 'unknown'):
            with self.subTest(mode=mode):
                kw=self.kwargs();kw['policy']=replace(kw['policy'],timestamp_mode=mode)
                with self.assertRaisesRegex(ValueError,'start-labelled'):
                    LiveRuntime(**kw)
                kw['broker'].place_order.assert_not_called()

    def test_entry_rejects_invalid_ranges_before_starting_workers(self):
        for high,low in (('94','95'),('0','0'),('NaN','95'),('100','Infinity')):
            with self.subTest(high=high,low=low):
                kw=self.kwargs();kw['opening_ranges']={'TCS':(Decimal(high),Decimal(low))}
                with self.assertRaisesRegex(ValueError,'opening range'):
                    LiveRuntime(**kw)
                kw['ledger'].reserve_entry.assert_not_called()

    def test_bad_extension_does_not_change_existing_candidates(self):
        kw=self.kwargs();runtime=LiveRuntime(**kw);self.addCleanup(runtime.close)
        with self.assertRaisesRegex(ValueError,'opening range'):
            runtime.extend_signals(signals=[dict(symbol='INFY',side='SHORT')],
                dhan_ids={'INFY':'1594'},
                kotak_instruments={'INFY':KotakInstrument('INFY','INFY-EQ','1594')},
                opening_ranges={'INFY':(Decimal('90'),Decimal('100'))})
        self.assertEqual(set(runtime.signals),{'TCS'})
        self.assertEqual(set(runtime.opening_ranges),{'TCS'})

    def test_recovery_exits_remain_available_under_legacy_convention(self):
        kw=self.kwargs();kw['policy']=replace(kw['policy'],timestamp_mode='end')
        runtime=LiveRuntime(**kw,close_only=True);self.addCleanup(runtime.close)
        self.assertTrue(runtime.close_only)
        self.assertIsNone(runtime.on_tick(dict(symbol='TCS',ltp='101',timestamp=kw['clock']())))
        kw['ledger'].reserve_entry.assert_not_called()


if __name__=='__main__':unittest.main()
