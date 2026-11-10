"""Race 20 reservations across ten processes against disposable PostgreSQL."""
from __future__ import annotations

import os
import secrets
import sys
from time import perf_counter
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime
from decimal import Decimal
from multiprocessing import get_context
from zoneinfo import ZoneInfo
from pathlib import Path

from sqlalchemy import delete

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from live_desk.ledger import LiveLedger, events, intents


def init_worker(gate) -> None:
    global start_gate
    start_gate = gate


def reserve_pair(url, day, stamp, candidates):
    ledger = LiveLedger(url)
    try:
        start_gate.wait(timeout=20)
        return [
            ledger.reserve_entry(session_date=day, symbol=symbol, side=side,
                                 quantity=1, reference_price=Decimal("100"), at=stamp,
                                 max_per_side=5, gross_cap=Decimal("100000"), max_total=10)
            for symbol, side in candidates
        ]
    finally:
        ledger.close()


def main() -> None:
    url = os.environ.get("LIVE_TEST_POSTGRES_URL")
    if not url or not url.startswith("postgresql"):
        raise SystemExit("Set LIVE_TEST_POSTGRES_URL to a disposable PostgreSQL database")
    base = LiveLedger(url)
    day = date.today()
    stamp = datetime.now(ZoneInfo("Asia/Kolkata"))
    prefix = "T" + secrets.token_hex(4).upper()
    candidates = [(f"{prefix}L{i}", "BUY") for i in range(5)] + [
        (f"{prefix}S{i}", "SELL") for i in range(5)
    ]
    context = get_context("spawn")
    gate = context.Barrier(len(candidates))

    try:
        started = perf_counter()
        with ProcessPoolExecutor(max_workers=len(candidates), mp_context=context,
                                 initializer=init_worker, initargs=(gate,)) as pool:
            futures = [
                pool.submit(reserve_pair, url, day, stamp,
                            [candidate, candidates[(i + 1) % len(candidates)]])
                for i, candidate in enumerate(candidates)
            ]
            results = [result for future in futures for result in future.result(timeout=40)]
        elapsed = perf_counter() - started
        rows = [row for row in base.snapshot(day) if row["symbol"].startswith(prefix)]
        assert sum(result is not None for result in results) == 10, "expected 10 accepted reservations"
        assert len(rows) == 10 and len({row["symbol"] for row in rows}) == 10
        assert sum(row["side"] == "BUY" for row in rows) == 5
        assert sum(row["side"] == "SELL" for row in rows) == 5
        print(f"PASS: 20 PostgreSQL reservations across 10 processes; 5 LONG + 5 SHORT, no duplicates ({elapsed:.3f}s)")
    finally:
        with base.engine.begin() as db:
            tags = db.execute(intents.select().with_only_columns(intents.c.tag).where(
                intents.c.session_date == day, intents.c.symbol.startswith(prefix))).scalars().all()
            if tags:
                db.execute(delete(events).where(events.c.tag.in_(tags)))
                db.execute(delete(intents).where(intents.c.tag.in_(tags)))
        base.close()


if __name__ == "__main__":
    main()
