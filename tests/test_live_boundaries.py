"""ORB and Kotak request boundaries for the sole live execution path."""
import unittest
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from live_desk.kotak_gateway import place_kotak_order
from live_desk.kotak_instruments import KotakInstrument
from live_desk.orb import opening_range
from live_desk.strategy import LivePolicy, candidate_entry

IST = ZoneInfo("Asia/Kolkata")


class LiveBoundaryTests(unittest.TestCase):
    def test_dhan_opening_range_requires_audited_timestamp_mode(self):
        epoch = int(datetime(2026, 11, 5, 9, 16, tzinfo=IST).timestamp())
        data = {"timestamp": [epoch], "open": [100], "high": [102], "low": [99],
                "close": [101], "volume": [1000]}
        class Dhan:
            def get_intraday(self, *_args, **_kwargs):
                return data
        with self.assertRaises(ValueError):
            opening_range(Dhan(), "123", date(2026, 11, 5), timestamp_mode="unknown")
        high, low = opening_range(Dhan(), "123", date(2026, 11, 5), timestamp_mode="end")
        self.assertEqual((high, low), (Decimal("102"), Decimal("99")))
        policy = LivePolicy(time(10), time(15, 15), 5, 5, 10,
                            Decimal("100000"), Decimal("1000000"), "end")
        decision = dict(side="LONG", orb_high=high, orb_low=low,
                        tick_at=datetime(2026, 11, 5, 9, 17, tzinfo=IST),
                        policy=policy, open_long=0, open_short=0,
                        reserved_gross=Decimal(0))
        self.assertGreater(candidate_entry(previous_price=Decimal("102"),
                           price=Decimal("102.05"), **decision), 0)
        self.assertIsNone(candidate_entry(previous_price=Decimal("102.01"),
                                          price=Decimal("102.05"), **decision))

    def test_legacy_cutoff_cannot_allow_a_cross_at_or_after_0930(self):
        cutoff = datetime(2026, 11, 5, 9, 30, tzinfo=IST)
        policy = LivePolicy(time(15), time(15, 15), 5, 5, 10,
                            Decimal("100000"), Decimal("1000000"), "end")
        decision = dict(side="LONG", orb_high=Decimal("100"), orb_low=Decimal("95"),
                        previous_price=Decimal("100"), price=Decimal("101"), policy=policy)
        self.assertEqual(policy.entry_cutoff, time(9, 30))
        self.assertGreater(candidate_entry(tick_at=cutoff-timedelta(microseconds=1), **decision), 0)
        self.assertIsNone(candidate_entry(tick_at=cutoff, **decision))
        self.assertIsNone(candidate_entry(tick_at=cutoff+timedelta(seconds=1), **decision))

    def test_kotak_order_boundary_requires_exact_mapped_instrument(self):
        class Client:
            def __init__(self):
                self.request = None
            def place_order(self, **kwargs):
                self.request = kwargs
                return {"stat": "Ok", "nOrdNo": "ORDER-1"}
        client = Client()
        instrument = KotakInstrument("ABC", "ABC-EQ", "1234")
        result = place_kotak_order(client, instrument, "BUY", 10, "unique-test-tag")
        self.assertEqual(result["broker_order_id"], "ORDER-1")
        self.assertEqual(client.request["trading_symbol"], "ABC-EQ")
        self.assertEqual(client.request["transaction_type"], "B")
        with self.assertRaises(ValueError):
            place_kotak_order(client, KotakInstrument("ABC", "ABC-EQ", "bad"),
                              "BUY", 10, "another-tag")


if __name__ == "__main__":
    unittest.main()
