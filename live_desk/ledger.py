"""PostgreSQL-backed durable Kotak order intents and broker-confirmed fills."""

from __future__ import annotations

import threading
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (BigInteger, CheckConstraint, Column, Date, DateTime,
                        ForeignKey, Index, Integer, MetaData, Numeric, String, Table,
                        UniqueConstraint, create_engine, select, text, update)
from sqlalchemy.engine import Engine


metadata = MetaData()
intents = Table(
    "live_order_intents", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("tag", String(64), nullable=False, unique=True),
    Column("session_date", Date, nullable=False),
    Column("symbol", String(32), nullable=False),
    Column("phase", String(8), nullable=False),
    Column("side", String(4), nullable=False),
    Column("quantity", Integer, nullable=False),
    Column("reference_price", Numeric(24, 8), nullable=False),
    Column("reserved_notional", Numeric(24, 8), nullable=False),
    Column("broker_order_id", String(128)),
    Column("state", String(32), nullable=False),
    Column("filled_quantity", Integer, nullable=False, server_default="0"),
    Column("average_fill_price", Numeric(24, 8)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("phase IN ('ENTRY','EXIT')", name="ck_live_intent_phase"),
    CheckConstraint("side IN ('BUY','SELL')", name="ck_live_intent_side"),
    CheckConstraint("quantity > 0", name="ck_live_intent_quantity"),
    CheckConstraint("filled_quantity >= 0 AND filled_quantity <= quantity", name="ck_live_intent_filled_quantity"),
    UniqueConstraint("session_date", "symbol", "phase", name="uq_live_intent_session_symbol_phase"),
)
Index("ix_live_intents_session_phase", intents.c.session_date, intents.c.phase)
events = Table(
    "live_order_intent_events", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("tag", String(64), ForeignKey("live_order_intents.tag", ondelete="CASCADE"), nullable=False),
    Column("state", String(32), nullable=False),
    Column("details", String(512), nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
)
settings = Table(
    "live_order_settings", metadata,
    Column("name", String(64), primary_key=True),
    Column("value", String(128), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


class LiveLedger:
    """Persist order state in PostgreSQL with cross-worker exposure locks."""

    def __init__(self, database: str | Engine):
        if isinstance(database, Engine):
            self.engine = database
        else:
            if not str(database).startswith(("postgresql://", "postgresql+psycopg://")):
                raise ValueError("Live ledger requires PostgreSQL")
            self.engine = create_engine(str(database), pool_pre_ping=True, future=True)
        if self.engine.dialect.name != "postgresql":
            raise ValueError("Live ledger requires PostgreSQL")
        self.lock = threading.RLock()
        metadata.create_all(self.engine)

    def close(self) -> None:
        self.engine.dispose()

    def _lock_session(self, db, day: date) -> None:
        # A transaction-scoped advisory lock serializes cap checks across all workers/hosts.
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:day))"), {"day": day.isoformat()})

    def _event(self, db, tag: str, state: str, details: str, stamp: datetime) -> None:
        db.execute(events.insert().values(tag=tag, state=state, details=details, observed_at=stamp))

    def reserve_entry(self, *, session_date: date, symbol: str, side: str,
                      quantity: int, reference_price: Decimal, at: datetime,
                      max_per_side: int, gross_cap: Decimal, max_total: int = 20) -> dict | None:
        if side not in {"BUY", "SELL"} or type(quantity) is not int or quantity <= 0:
            raise ValueError("Invalid entry side or quantity")
        if not symbol or not reference_price.is_finite() or reference_price <= 0:
            raise ValueError("Invalid entry symbol or price")
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("Entry time must include timezone")
        day = session_date
        notional = reference_price * quantity
        if notional > gross_cap:
            return None
        with self.lock, self.engine.begin() as db:
            self._lock_session(db, day)
            existing = db.execute(select(intents.c.id).where(
                intents.c.session_date == day, intents.c.symbol == symbol,
                intents.c.phase == "ENTRY")).first()
            if existing:
                return None
            rows = db.execute(select(intents.c.side, intents.c.reserved_notional).where(
                intents.c.session_date == day, intents.c.phase == "ENTRY")).mappings().all()
            if (len(rows) >= max_total or sum(row["side"] == side for row in rows) >= max_per_side or
                    sum(Decimal(row["reserved_notional"]) for row in rows) + notional > gross_cap):
                return None
            tag, stamp = uuid.uuid4().hex, at
            db.execute(intents.insert().values(tag=tag, session_date=day, symbol=symbol,
                phase="ENTRY", side=side, quantity=quantity, reference_price=reference_price,
                reserved_notional=notional, state="RESERVED", filled_quantity=0,
                created_at=stamp, updated_at=stamp))
            self._event(db, tag, "RESERVED", "Entry intent persisted", stamp)
            return {"tag": tag, "symbol": symbol, "phase":"ENTRY", "side": side, "quantity": quantity}

    def reserve_exit(self, *, session_date: date, symbol: str, at: datetime) -> dict | None:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("Exit time must include timezone")
        day = session_date
        with self.lock, self.engine.begin() as db:
            self._lock_session(db, day)
            entry = db.execute(select(intents).where(intents.c.session_date == day,
                intents.c.symbol == symbol, intents.c.phase == "ENTRY")).mappings().first()
            prior = db.execute(select(intents.c.id).where(intents.c.session_date == day,
                intents.c.symbol == symbol, intents.c.phase == "EXIT")).first()
            if (not entry or prior or entry["filled_quantity"] <= 0 or
                    entry["state"] not in {"FILLED", "PARTIAL_CANCELLED"}):
                return None
            tag, stamp = uuid.uuid4().hex, at
            side, qty = ("SELL" if entry["side"] == "BUY" else "BUY"), entry["filled_quantity"]
            db.execute(intents.insert().values(tag=tag, session_date=day, symbol=symbol,
                phase="EXIT", side=side, quantity=qty, reference_price=entry["reference_price"],
                reserved_notional=Decimal("0"), state="RESERVED", filled_quantity=0,
                created_at=stamp, updated_at=stamp))
            self._event(db, tag, "RESERVED", "Exit intent persisted", stamp)
            return {"tag": tag, "symbol": symbol, "phase":"EXIT", "side": side, "quantity": qty}

    def mark_submission_unknown(self, tag: str, at: datetime) -> None:
        with self.lock, self.engine.begin() as db:
            row = db.execute(select(intents.c.state).where(intents.c.tag == tag).with_for_update()).first()
            if not row or row.state != "RESERVED":
                raise ValueError("Intent already submitted or unknown")
            stamp = at
            db.execute(update(intents).where(intents.c.tag == tag).values(
                state="SUBMISSION_UNKNOWN", updated_at=stamp))
            self._event(db, tag, "SUBMISSION_UNKNOWN", "Broker call may have reached Kotak", stamp)

    def mark_cancel_pending(self, tag: str, at: datetime) -> None:
        with self.lock, self.engine.begin() as db:
            row = db.execute(select(intents.c.phase, intents.c.state).where(
                intents.c.tag == tag).with_for_update()).first()
            if not row or row.phase != "ENTRY" or row.state not in {"ACKNOWLEDGED", "PARTIAL"}:
                raise ValueError("Entry is not cancellable")
            stamp = at
            db.execute(update(intents).where(intents.c.tag == tag).values(
                state="CANCEL_PENDING", updated_at=stamp))
            self._event(db, tag, "CANCEL_PENDING", "One broker cancellation attempt is in progress", stamp)

    def mark_not_submitted(self, tag: str, at: datetime) -> None:
        """An entry lost authorization/freshness while queued; no broker call was made."""
        with self.lock, self.engine.begin() as db:
            row=db.execute(select(intents.c.state).where(intents.c.tag==tag).with_for_update()).first()
            if not row or row.state!="RESERVED":
                raise ValueError("Only an unsent reservation can be aborted")
            db.execute(update(intents).where(intents.c.tag==tag).values(state="ABORTED",updated_at=at))
            self._event(db,tag,"ABORTED","Unsent entry no longer authorized or fresh",at)

    def apply_broker_report(self, report: dict, at: datetime) -> None:
        tag, filled = report["tag"], report["filled_quantity"]
        with self.lock, self.engine.begin() as db:
            row = db.execute(select(intents).where(intents.c.tag == tag).with_for_update()).mappings().first()
            if not row or row["state"] == "RESERVED":
                raise ValueError("Unknown or unsent intent")
            if (type(filled) is not int or not row["filled_quantity"] <= filled <= row["quantity"] or
                    report["trading_symbol"] != f'{row["symbol"]}-EQ' or
                    report.get("ordered_quantity") != row["quantity"] or
                    not str(report.get("broker_order_id") or "").strip() or
                    (row["broker_order_id"] and row["broker_order_id"]!=report["broker_order_id"])):
                raise ValueError("Broker report conflicts with intent")
            average=report.get("average_fill_price")
            average=Decimal(str(average)) if average is not None else None
            if filled and (average is None or not average.is_finite() or average<=0):
                raise ValueError("Broker fill price is invalid")
            if not filled:average=None
            state = "FILLED" if filled == row["quantity"] else "PARTIAL" if filled else "ACKNOWLEDGED"
            if str(report["status"]).lower() in {"rejected", "cancelled", "canceled"}:
                state = "REJECTED" if not filled else "PARTIAL_CANCELLED"
            elif row["state"] == "CANCEL_PENDING" and filled < row["quantity"]:
                state = "CANCEL_PENDING"
            if row["state"] in {"FILLED","REJECTED","PARTIAL_CANCELLED","ABORTED"} and state!=row["state"]:
                raise ValueError("Terminal broker state cannot regress")
            stamp = at
            db.execute(update(intents).where(intents.c.tag == tag).values(state=state,
                broker_order_id=report["broker_order_id"], filled_quantity=filled,
                average_fill_price=average, updated_at=stamp))
            if filled != row["filled_quantity"] or state != row["state"]:
                self._event(db, tag, state, "Kotak report reconciled", stamp)

    def snapshot(self, session_date: date) -> list[dict]:
        with self.engine.connect() as db:
            rows = db.execute(select(intents).where(
                intents.c.session_date == session_date).order_by(intents.c.id)).mappings()
            return [dict(row) for row in rows]

    def prior_session_recovery_count(self, day: date) -> int:
        """Uncertain submissions or unclosed fills from an earlier day block entries."""
        with self.engine.connect() as db:
            rows=db.execute(select(intents).where(intents.c.session_date<day)).mappings().all()
        exits={(r['session_date'],r['symbol']):r['filled_quantity'] for r in rows if r['phase']=='EXIT'}
        return sum(1 for r in rows if r['state'] in {
            'RESERVED','SUBMISSION_UNKNOWN','ACKNOWLEDGED','PARTIAL','CANCEL_PENDING'} or (
            r['phase']=='ENTRY' and r['filled_quantity']>exits.get((r['session_date'],r['symbol']),0)))

    def read_settings(self) -> dict[str, str]:
        with self.engine.connect() as db:
            return {row.name: row.value for row in db.execute(select(settings)).mappings()}

    def write_settings(self, values: dict[str, str], at: datetime) -> None:
        stamp = at
        with self.lock, self.engine.begin() as db:
            for key, value in values.items():
                db.execute(text("""INSERT INTO live_order_settings(name,value,updated_at)
                    VALUES(:name,:value,:updated_at) ON CONFLICT(name) DO UPDATE SET
                    value=excluded.value,updated_at=excluded.updated_at"""),
                    {"name": key, "value": value, "updated_at": stamp})
