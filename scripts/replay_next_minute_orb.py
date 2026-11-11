"""Replay a ten-stock next-minute ORB using the real SDK and a mock broker."""
from __future__ import annotations

import argparse
import json
import struct
import sys
import threading
from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs

import httpx
from neo_api_client import NeoAPI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_desk.dhan_data import IST
from live_desk.kotak_instruments import KotakInstrument
from live_desk.runtime import LiveRuntime
from live_desk.strategy import candidate_entry, load_policy
from live_desk.ticker import parse_ticker

SYMBOLS = ("RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN",
           "AXISBANK", "BHARTIARTL", "LT", "ITC")


def replay(audit_path: Path, ready_at: datetime) -> dict:
    start = ready_at.replace(second=0, microsecond=0) + timedelta(minutes=1)
    end = start + timedelta(minutes=1)
    policy = load_policy(ROOT / "live_desk/policy.json")
    if start.date() != end.date() or start.time() < time(9, 16):
        raise ValueError("Choose a replay minute during the regular entry session")
    if end.time() >= policy.entry_cutoff:
        raise ValueError("The replay candle must close before the entry cutoff")

    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    mappings = {row["symbol"]: row for row in audit["instruments"] if row["status"] == "MAPPED"}
    ids = {symbol: mappings[symbol]["dhan_security_id"] for symbol in SYMBOLS}
    instruments = {
        symbol: KotakInstrument(symbol, mappings[symbol]["kotak_trading_symbol"],
                                mappings[symbol]["kotak_token"])
        for symbol in SYMBOLS
    }
    if len(set(ids.values())) != len(SYMBOLS) or any(
        ids[symbol] != instruments[symbol].token for symbol in SYMBOLS
    ):
        raise ValueError("Dhan and Kotak IDs must be unique and match the audit")
    by_id = {security_id: symbol for symbol, security_id in ids.items()}
    sides = {symbol: ("LONG" if i % 2 == 0 else "SHORT") for i, symbol in enumerate(SYMBOLS)}

    def tick(symbol, price, at):
        packet = struct.pack("<BhBifi", 2, 16, 1, int(ids[symbol]), price, int(at.timestamp()))
        parsed = parse_ticker(packet)
        if parsed is None or parsed["segment_code"] != 1:
            raise AssertionError("Fixture ticker packet failed to decode")
        return {**parsed, "symbol": by_id[parsed["security_id"]],
                "timestamp": datetime.fromisoformat(parsed["exchange_timestamp"])}

    ranges = {}
    for i, symbol in enumerate(SYMBOLS):
        candles = [tick(symbol, 100 + i * 10 + delta, start + timedelta(seconds=second))
                   for second, delta in ((0, 0), (12, 2), (26, -2), (59, 0))]
        prices = [Decimal(str(row["ltp"])) for row in candles if start <= row["timestamp"] < end]
        ranges[symbol] = (max(prices), min(prices))

    lock = threading.Lock()
    requests = []
    orders = []
    reserved = {}

    def capture(request):
        if request.url.host != "broker.test" or request.url.path != "/quick/order/rule/ms/place":
            raise AssertionError("Unexpected endpoint in the test transport")
        payload = json.loads(parse_qs(request.content.decode())["jData"][0])
        with lock:
            order_id = f"SIM-{len(requests) + 1:02}"
            requests.append(payload)
            orders.append({"GuiOrdId": payload["ig"], "nOrdNo": order_id, "qty": payload["qt"],
                           "ordSt": "open", "trdSym": payload["ts"]})
        return httpx.Response(200, json={"stat": "Ok", "stCode": 200, "nOrdNo": order_id})

    client = NeoAPI(consumer_key="fixture", environment="prod", transport=httpx.MockTransport(capture))
    client.configuration.edit_token = "fixture"
    client.configuration.edit_sid = "fixture"
    client.configuration.edit_rid = "fixture"
    client.configuration.base_url = "https://broker.test"
    client.order_report = lambda: {"stat": "Ok", "data": list(orders)}
    client.trade_report = lambda: {"stat": "Ok", "data": []}
    ledger = Mock()

    def reserve(**entry):
        with lock:
            symbol = entry["symbol"]
            side_count = sum(row["side"] == entry["side"] for row in reserved.values())
            gross = sum(row["quantity"] * row["reference_price"] for row in reserved.values())
            if (symbol in reserved or len(reserved) >= entry["max_total"]
                    or side_count >= entry["max_per_side"]
                    or (entry["gross_cap"] is not None and gross + entry["quantity"] * entry["reference_price"] > entry["gross_cap"])):
                return None
            reserved[symbol] = {**entry, "tag": "next-minute-" + symbol, "phase": "ENTRY"}
            return reserved[symbol]

    ledger.reserve_entry.side_effect = reserve
    runtime = LiveRuntime(session_date=end.date(), signals=[
        {"symbol": symbol, "side": sides[symbol]} for symbol in SYMBOLS
    ], dhan_ids=ids, kotak_instruments=instruments, opening_ranges=ranges,
        policy=policy, ledger=ledger, broker=client, enabled=True, clock=lambda: end)

    def two_share_candidate(**kwargs):
        test_policy = replace(kwargs["policy"], per_position=kwargs["price"] * 2)
        return candidate_entry(**{**kwargs, "policy": test_policy})

    try:
        with patch("live_desk.runtime.candidate_entry", side_effect=two_share_candidate):
            for symbol, (high, low) in ranges.items():
                runtime.on_tick(tick(symbol, (high + low) / 2, end - timedelta(seconds=1)))
            for _ in range(2):
                for symbol, (high, low) in ranges.items():
                    price = high + 1 if sides[symbol] == "LONG" else low - 1
                    runtime.on_tick(tick(symbol, price, end))
            assert runtime.wait_for_submissions(timeout=10)

        assert len(requests) == len(SYMBOLS) and not runtime.errors, runtime.errors
        by_symbol = {payload["ts"]: payload for payload in requests}
        assert len(by_symbol) == len(SYMBOLS)
        results = []
        for symbol, instrument in instruments.items():
            payload = by_symbol[instrument.trading_symbol]
            side = "B" if sides[symbol] == "LONG" else "S"
            assert (payload["tt"], payload["qt"], payload["es"], payload["pc"], payload["pt"]) == (
                side, "2", "nse_cm", "MIS", "MKT")
            assert payload["ig"] == reserved[symbol]["tag"]
            order = next(row for row in orders if row["GuiOrdId"] == payload["ig"])
            results.append({"stock": symbol, "dhan_id": ids[symbol], "kotak_symbol": payload["ts"],
                            "side": sides[symbol], "quantity": 2, "mock_order_id": order["nOrdNo"]})
        assert ledger.apply_broker_report.call_count == len(SYMBOLS)
        return {"mode": "SIMULATED", "candle_start": start.isoformat(), "candle_end": end.isoformat(),
                "orders": results}
    finally:
        runtime.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--ready-at", default="2026-11-09T09:20:23+05:30",
                        help="Simulated feed-ready timestamp inside the 09:16-09:30 entry window, including its UTC offset")
    args = parser.parse_args()
    ready_at = datetime.fromisoformat(args.ready_at)
    if ready_at.utcoffset() is None:
        parser.error("--ready-at needs a UTC offset")
    print(json.dumps(replay(args.audit, ready_at.astimezone(IST)), indent=2))


if __name__ == "__main__":
    main()
