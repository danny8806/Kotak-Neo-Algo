import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from live_desk.ledger import LiveLedger
from postgres_test_db import create_test_database

NOW = datetime(2026, 11, 4, 15, 15, tzinfo=ZoneInfo("Asia/Kolkata"))


class BrokerFillPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.url, cleanup = create_test_database()
        self.addCleanup(cleanup)
        self.ledger = LiveLedger(self.url)
        self.addCleanup(self.ledger.close)

    def test_repeat_refresh_is_idempotent_and_identical_real_fills_are_preserved(self):
        first = {"symbol": "RELIANCE", "trading_symbol": "RELIANCE-EQ", "fill_id": "F-1",
                 "broker_order_id": "O-1", "side": "B", "filled_quantity": "10",
                 "fill_price": "100.5", "fill_time": "2026-11-04T09:20:00+05:30"}
        rows = [first, dict(first)]
        self.assertEqual(self.ledger.record_broker_fills(rows, NOW), 2)
        self.assertEqual(self.ledger.record_broker_fills(rows, NOW), 0)
        saved = self.ledger.journal_snapshot()["fills"]
        self.assertEqual(len(saved), 2)
        self.assertEqual({row["broker_fill_id"] for row in saved}, {"F-1"})
        self.assertTrue(all(row["symbol"] == "RELIANCE" for row in saved))

    def test_invalid_or_non_equity_fill_is_not_saved(self):
        rows = [
            {"symbol": "BAD", "trading_symbol": "BAD-OPT", "filled_quantity": "1", "fill_price": "10"},
            {"symbol": "BROKEN", "trading_symbol": "BROKEN-EQ", "filled_quantity": "1", "fill_price": "NaN"},
        ]
        self.assertEqual(self.ledger.record_broker_fills(rows, NOW), 0)
        self.assertEqual(self.ledger.journal_snapshot()["fills"], [])


if __name__ == "__main__":
    unittest.main()
