"""ORB and Kotak request boundaries for the sole live execution path."""
import unittest
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from random import Random
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

    def test_sizing_rounds_to_whole_shares_and_respects_total_cap(self):
        policy = LivePolicy(time(9, 30), time(15, 15), 2, 1, 3,
                            Decimal("125500.25"), Decimal("300000.25"), "end")
        tick_at = datetime(2026, 11, 5, 9, 17, tzinfo=IST)
        cases = [
            ("LONG", "320", "320.50", "320", "300", 391),
            ("SHORT", "321", "320.50", "350", "321", 391),
            ("LONG", "125500.25", "125500.26", "125500.25", "300", None),
        ]
        for side, previous, price, high, low, expected in cases:
            with self.subTest(side=side, price=price):
                decision = dict(side=side, previous_price=Decimal(previous), price=Decimal(price),
                                orb_high=Decimal(high), orb_low=Decimal(low), tick_at=tick_at, policy=policy)
                self.assertEqual(candidate_entry(**decision), expected)
                self.assertEqual(candidate_entry(**decision, reserved_gross=Decimal("250000.00")),expected)
                self.assertIsNone(candidate_entry(**decision, open_positions=3))

    def test_one_share_cap_respects_capital_and_both_directions(self):
        policy = LivePolicy(time(9, 30), time(15, 15), 5, 5, 10,
                            Decimal("1000"), Decimal("1000"), "end", fixed_entry_quantity=1)
        tick_at = datetime(2026, 11, 5, 9, 17, tzinfo=IST)
        for side, previous, price in (("LONG", "100", "101"), ("SHORT", "95", "94")):
            with self.subTest(side=side):
                decision = dict(side=side, previous_price=Decimal(previous), price=Decimal(price),
                                orb_high=Decimal("100"), orb_low=Decimal("95"), tick_at=tick_at)
                self.assertEqual(candidate_entry(**decision, policy=policy), 1)
                self.assertEqual(candidate_entry(
                    **decision, policy=replace(policy, per_position=Decimal("90"))),1)
                self.assertEqual(candidate_entry(
                    **decision, policy=policy, reserved_gross=Decimal("950")),1)

    def test_sizing_matches_integer_paise_model_across_20000_cases(self):
        rng = Random(9301515)
        opening = datetime(2026, 11, 5, 9, 16, tzinfo=IST)
        offsets = (-1, 0, 1, 839_999_999, 840_000_000, 840_000_001)

        def rupees(paise):
            return Decimal(paise) / 100

        for i in range(20_000):
            side = ("LONG", "SHORT")[(i // len(offsets)) % 2]
            offset = offsets[i % len(offsets)]
            edge = rng.choice((1, 100, 10_000, 2_500_000, 100_000_000))
            previous, price = (max(1, edge + rng.randrange(-3, 4)) for _ in range(2))
            allocation = rng.randrange(100_000, 50_000_001)
            cap = allocation * rng.randrange(1, 7)
            max_long, max_short = rng.randrange(1, 6), rng.randrange(1, 6)
            max_total = rng.randrange(1, max_long + max_short + 1)
            longs, shorts = rng.randrange(0, 6), rng.randrange(0, 6)
            total, reserved = rng.randrange(0, 11), rng.randrange(cap + 1)
            policy = LivePolicy(time(9, 30), time(15, 15), max_long, max_short, max_total,
                                rupees(allocation), rupees(cap), "end")
            result = candidate_entry(side=side, previous_price=rupees(previous), price=rupees(price),
                                     orb_high=rupees(edge), orb_low=rupees(edge),
                                     tick_at=opening + timedelta(microseconds=offset), policy=policy,
                                     open_long=longs, open_short=shorts, open_positions=total,
                                     reserved_gross=rupees(reserved))
            shares = allocation // price
            crosses = (previous <= edge < price and longs < max_long if side == "LONG"
                       else previous >= edge > price and shorts < max_short)
            expected = shares if (0 <= offset < 840_000_000 and crosses and total < max_total
                                  and shares) else None
            self.assertEqual(result, expected, f"seed=9301515 case={i}")

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

    def test_calendar_fails_closed_outside_the_published_year(self):
        from live_desk.nse_calendar import calendar_supported, is_regular_session

        self.assertTrue(is_regular_session(date(2026, 11, 11)))
        self.assertFalse(is_regular_session(date(2026, 11, 10)))  # published holiday
        self.assertFalse(is_regular_session(date(2026, 11, 14)))  # Saturday
        self.assertFalse(calendar_supported(date(2027, 1, 4)))
        # An unknown year must never open an entry window.
        self.assertFalse(is_regular_session(date(2027, 1, 4)))


if __name__ == "__main__":
    unittest.main()
