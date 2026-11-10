import unittest

from live_desk.analytics import build_trade_journal


class TradeAnalyticsTests(unittest.TestCase):
    def test_only_broker_confirmed_round_trips_count_as_closed(self):
        intents = [
            {"session_date": "2026-11-04", "symbol": "LONGWIN", "phase": "ENTRY", "side": "BUY",
             "tag": "e1", "state": "FILLED", "filled_quantity": 10, "average_fill_price": "100",
             "broker_order_id": "O1", "updated_at": "2026-11-04T09:20:00+05:30"},
            {"session_date": "2026-11-04", "symbol": "LONGWIN", "phase": "EXIT", "side": "SELL",
             "tag": "x1", "state": "FILLED", "filled_quantity": 10, "average_fill_price": "102",
             "broker_order_id": "O2", "updated_at": "2026-11-04T15:15:00+05:30"},
            {"session_date": "2026-11-04", "symbol": "SHORTWIN", "phase": "ENTRY", "side": "SELL",
             "tag": "e2", "state": "FILLED", "filled_quantity": 5, "average_fill_price": "200",
             "broker_order_id": "O3", "updated_at": "2026-11-04T09:21:00+05:30"},
            {"session_date": "2026-11-04", "symbol": "SHORTWIN", "phase": "EXIT", "side": "BUY",
             "tag": "x2", "state": "FILLED", "filled_quantity": 5, "average_fill_price": "195",
             "broker_order_id": "O4", "updated_at": "2026-11-04T15:15:00+05:30"},
            {"session_date": "2026-11-04", "symbol": "OPEN", "phase": "ENTRY", "side": "BUY",
             "tag": "e3", "state": "FILLED", "filled_quantity": 4, "average_fill_price": "50",
             "broker_order_id": "O5", "updated_at": "2026-11-04T09:22:00+05:30"},
            {"session_date": "2026-11-04", "symbol": "PENDING", "phase": "ENTRY", "side": "BUY",
             "tag": "e4", "state": "SUBMISSION_UNKNOWN", "filled_quantity": 0,
             "average_fill_price": None, "broker_order_id": None, "updated_at": "2026-11-04T09:23:00+05:30"},
        ]
        events = [{"tag": "e1", "state": "FILLED", "details": "Kotak filled 10/10",
                   "observed_at": "2026-11-04T09:20:00+05:30"}]
        fills = [{"symbol": "LONGWIN", "fill_id": "F1", "quantity": 10}]

        result = build_trade_journal(intents, events, fills)
        trades = {row["symbol"]: row for row in result["trades"]}
        self.assertEqual(trades["LONGWIN"]["realized_gross"], 20.0)
        self.assertEqual(trades["SHORTWIN"]["realized_gross"], 25.0)
        self.assertEqual(trades["OPEN"]["status"], "OPEN")
        self.assertIsNone(trades["OPEN"]["realized_gross"])
        self.assertEqual(trades["PENDING"]["status"], "ENTRY PENDING")
        self.assertEqual(result["summary"]["closed_trade_count"], 2)
        self.assertEqual(result["summary"]["gross_realized_from_exits"], 45.0)
        self.assertEqual(result["summary"]["win_rate_pct"], 100.0)
        self.assertEqual(result["summary"]["closed_trade_return_pct"], 2.25)
        self.assertEqual(result["summary"]["gross_loss_total"], 0.0)
        self.assertIsNone(result["summary"]["net_after_fees"])
        self.assertEqual(result["events"][0]["symbol"], "LONGWIN")
        self.assertEqual(result["fills"], fills)

    def test_realized_loss_and_partial_exit_are_labeled_correctly(self):
        entry = {"session_date": "2026-11-04", "symbol": "LOSS", "phase": "ENTRY", "side": "BUY",
                 "tag": "e", "state": "FILLED", "filled_quantity": 10, "average_fill_price": "100",
                 "updated_at": "2026-11-04T10:00:00+05:30"}
        exit_order = {"session_date": "2026-11-04", "symbol": "LOSS", "phase": "EXIT", "side": "SELL",
                      "tag": "x", "state": "PARTIAL", "filled_quantity": 4, "average_fill_price": "95",
                      "updated_at": "2026-11-04T15:15:00+05:30"}
        result = build_trade_journal([entry, exit_order], [], [])
        trade = result["trades"][0]
        self.assertEqual(trade["status"], "PARTIALLY CLOSED")
        self.assertEqual(trade["remaining_quantity"], 6)
        self.assertEqual(trade["realized_gross"], -20.0)
        self.assertEqual(result["summary"]["open_trade_count"], 1)
        self.assertEqual(result["summary"]["losses"], 0,
                         "Partial closes are excluded from closed-trade win-rate statistics")

    def test_closed_capital_loss_and_drawdown_use_confirmed_trade(self):
        entry = {"session_date": "2026-11-04", "symbol": "LOSS", "phase": "ENTRY", "side": "BUY",
                 "tag": "e", "state": "FILLED", "filled_quantity": 10, "average_fill_price": "100",
                 "updated_at": "2026-11-04T10:00:00+05:30"}
        exit_order = {"session_date": "2026-11-04", "symbol": "LOSS", "phase": "EXIT", "side": "SELL",
                      "tag": "x", "state": "FILLED", "filled_quantity": 10, "average_fill_price": "90",
                      "updated_at": "2026-11-04T15:15:00+05:30"}
        summary = build_trade_journal([entry, exit_order], [], [])["summary"]
        self.assertEqual(summary["gross_loss_total"], -100.0)
        self.assertEqual(summary["closed_trade_return_pct"], -10.0)
        self.assertEqual(summary["max_closed_trade_drawdown_gross"], 100.0)


if __name__ == "__main__":
    unittest.main()
