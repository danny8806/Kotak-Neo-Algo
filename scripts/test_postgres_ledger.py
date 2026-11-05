"""PostgreSQL-only 20-worker reservation race test (no broker network/orders)."""
from __future__ import annotations

import os
import secrets
import sys
from time import perf_counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal
from threading import Barrier
from zoneinfo import ZoneInfo
from pathlib import Path

from sqlalchemy import delete

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from live_desk.ledger import LiveLedger, events, intents


def main() -> None:
    url = os.environ.get("LIVE_TEST_POSTGRES_URL")
    if not url or not url.startswith("postgresql"):
        raise SystemExit("Set LIVE_TEST_POSTGRES_URL to a disposable PostgreSQL database")
    base = LiveLedger(url)
    day = date.today()
    stamp = datetime.now(ZoneInfo("Asia/Kolkata"))
    prefix = "T" + secrets.token_hex(4).upper()
    symbols = [f"{prefix}L{i}" for i in range(5)] + [f"{prefix}S{i}" for i in range(5)]
    ledgers = [LiveLedger(url) for _ in range(20)]
    gate = Barrier(20)

    def reserve(index: int):
        symbol = symbols[index // 2]
        side = "BUY" if "L" in symbol[len(prefix):] else "SELL"
        gate.wait(timeout=10)
        return ledgers[index].reserve_entry(session_date=day, symbol=symbol, side=side,
            quantity=1, reference_price=Decimal("100"), at=stamp,
            max_per_side=5, gross_cap=Decimal("100000"), max_total=10)

    try:
        started = perf_counter()
        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(reserve, range(20)))
        elapsed = perf_counter() - started
        rows = [row for row in base.snapshot(day) if row["symbol"].startswith(prefix)]
        assert sum(result is not None for result in results) == 10, "expected 10 accepted reservations"
        assert len(rows) == 10 and len({row["symbol"] for row in rows}) == 10
        assert sum(row["side"] == "BUY" for row in rows) == 5
        assert sum(row["side"] == "SELL" for row in rows) == 5
        print(f"PASS: PostgreSQL 20-worker race reserved 5 LONG + 5 SHORT, no duplicates ({elapsed:.3f}s)")
    finally:
        with base.engine.begin() as db:
            tags = db.execute(intents.select().with_only_columns(intents.c.tag).where(
                intents.c.session_date == day, intents.c.symbol.startswith(prefix))).scalars().all()
            if tags:
                db.execute(delete(events).where(events.c.tag.in_(tags)))
                db.execute(delete(intents).where(intents.c.tag.in_(tags)))
        for ledger in ledgers:
            ledger.close()
        base.close()


if __name__ == "__main__":
    main()
