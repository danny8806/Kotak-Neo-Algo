"""Regression tests for EOD decisions with simulated Kotak books."""
from concurrent.futures import Future
from datetime import datetime, time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from live_desk.dhan_data import IST
from live_desk.runtime import LiveRuntime
from live_desk.supervisor import LiveSupervisor


AT = datetime(2026, 11, 9, 15, 0, tzinfo=IST)


def entry(symbol, state, filled, side="BUY"):
    return dict(symbol=symbol, tag=symbol, phase="ENTRY", state=state,
                filled_quantity=filled, side=side, broker_order_id="ID-" + symbol)


class SquareOffTests(unittest.TestCase):
    def test_supervisor_runs_square_off_for_zero_fill_working_entry(self):
        desk = object.__new__(LiveSupervisor)
        desk.allowed = True
        desk.armed = False
        desk.policy = SimpleNamespace(entry_cutoff=time(9, 30), exit_time=AT.time())
        desk.broker = Mock()
        desk.ledger = Mock()
        desk.ledger.snapshot.return_value = [entry("TCS", "ACKNOWLEDGED", 0)]
        desk.runtime = Mock()

        desk._manage_authorized_exits(AT)

        desk.runtime.exit_due.assert_called_once()

    def test_cancel_first_and_close_only_matching_mis_positions(self):
        ledger = Mock()
        initial = [entry("TCS", "ACKNOWLEDGED", 0), entry("INFY", "FILLED", 10),
                   entry("RELIANCE", "FILLED", 8, "SELL")]
        after_cancel = [entry("TCS", "PARTIAL_CANCELLED", 2), *initial[1:]]
        ledger.snapshot.side_effect = [initial, after_cancel]
        ledger.reserve_exit.return_value = None
        broker = Mock()
        positions = [
            dict(symbol="TCS", product="MIS", net_quantity=2),
            dict(symbol="INFY", product="CNC", net_quantity=10),
            dict(symbol="RELIANCE", product="MIS", net_quantity=-8),
        ]
        runtime = LiveRuntime(
            session_date=AT.date(), signals=[
                dict(symbol=row["symbol"], side="LONG") for row in initial
            ],
            dhan_ids={row["symbol"]: str(i + 1) for i, row in enumerate(initial)},
            kotak_instruments={
                row["symbol"]: SimpleNamespace(symbol=row["symbol"],
                                               trading_symbol=row["symbol"] + "-EQ",
                                               token=str(i + 1))
                for i, row in enumerate(initial)
            },
            opening_ranges={row["symbol"]: (100, 90) for row in initial},
            policy=SimpleNamespace(timestamp_mode="end", entry_cutoff=time(9, 30), exit_time=AT.time()),
            ledger=ledger, broker=broker, enabled=True,
        )
        self.addCleanup(runtime.close)
        # An independent order awaiting a broker reply cannot stall these exits.
        in_flight=Future()
        runtime.pending.add(in_flight)
        self.addCleanup(in_flight.set_result,None)
        with patch.object(runtime, "reconcile_pending"), \
                patch("live_desk.runtime.cancel_kotak_order") as cancel, \
                patch("live_desk.runtime.reconcile_kotak_tag"):
            def read_positions():
                cancel.assert_called_once_with(broker, "ID-TCS")
                return positions

            runtime.exit_due(AT, read_positions=read_positions)

        self.assertFalse(runtime.enabled)
        self.assertEqual(
            [call.kwargs["symbol"] for call in ledger.reserve_exit.call_args_list],
            ["TCS", "RELIANCE"],
        )
        self.assertTrue(any("INFY" in error and "Manual action" in error
                            for error in runtime.errors))
