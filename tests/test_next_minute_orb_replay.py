"""Regression coverage for the ten-stock next-minute ORB replay tool."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_desk.dhan_data import IST
from scripts.replay_next_minute_orb import SYMBOLS, replay

AUDIT = ROOT / "reports/nifty500-instrument-audit-2026-11-09.json"


class NextMinuteOrbReplayTest(unittest.TestCase):
    def test_replay_submits_two_share_orders_inside_entry_window(self) -> None:
        result = replay(AUDIT, datetime(2026, 11, 9, 9, 20, 23, tzinfo=IST))

        self.assertEqual(result["mode"], "SIMULATED")
        self.assertEqual(result["candle_start"], "2026-11-09T09:21:00+05:30")
        self.assertEqual(result["candle_end"], "2026-11-09T09:22:00+05:30")
        self.assertEqual(len(result["orders"]), len(SYMBOLS))
        self.assertEqual({row["stock"] for row in result["orders"]}, set(SYMBOLS))
        self.assertTrue(all(row["quantity"] == 2 for row in result["orders"]))
        self.assertTrue(all(row["mock_order_id"].startswith("SIM-") for row in result["orders"]))

    def test_replay_rejects_a_candle_at_or_after_the_entry_cutoff(self) -> None:
        with self.assertRaisesRegex(ValueError, "entry cutoff"):
            replay(AUDIT, datetime(2026, 11, 9, 13, 4, 23, tzinfo=IST))
        with self.assertRaisesRegex(ValueError, "entry cutoff"):
            replay(AUDIT, datetime(2026, 11, 9, 9, 29, 30, tzinfo=IST))

    def test_replay_rejects_a_ready_time_before_the_entry_window(self) -> None:
        with self.assertRaisesRegex(ValueError, "regular entry session"):
            replay(AUDIT, datetime(2026, 11, 9, 9, 14, 30, tzinfo=IST))


if __name__ == "__main__":
    unittest.main()
