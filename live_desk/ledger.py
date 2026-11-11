"""PostgreSQL-backed durable Kotak order intents and broker-confirmed fills."""

from __future__ import annotations

import threading
import uuid
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (BigInteger, CheckConstraint, Column, Date, DateTime,
                        ForeignKey, Index, Integer, MetaData, Numeric, String, Table,
                        UniqueConstraint, create_engine, select, text, update)
from sqlalchemy.engine import Engine
from sqlalchemy.dialects.postgresql import insert as pg_insert


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
broker_fills = Table(
    "live_broker_fills", metadata,
    Column("fill_key", String(64), primary_key=True),
    Column("broker_fill_id", String(128), nullable=False, server_default=""),
    Column("broker_order_id", String(128), nullable=False, server_default=""),
    Column("session_date", Date, nullable=False),
    Column("symbol", String(32), nullable=False),
    Column("trading_symbol", String(48), nullable=False),
    Column("side", String(16), nullable=False, server_default=""),
    Column("quantity", Integer, nullable=False),
    Column("price", Numeric(24, 8), nullable=False),
    Column("broker_time", String(64), nullable=False, server_default=""),
    Column("first_seen_at", DateTime(timezone=True), nullable=False),
)
Index("ix_live_broker_fills_session", broker_fills.c.session_date, broker_fills.c.first_seen_at)
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
                      max_per_side: int, gross_cap: Decimal | None, max_total: int = 20) -> dict | None:
        if side not in {"BUY", "SELL"} or type(quantity) is not int or quantity <= 0:
            raise ValueError("Invalid entry side or quantity")
        if not symbol or not reference_price.is_finite() or reference_price <= 0:
            raise ValueError("Invalid entry symbol or price")
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("Entry time must include timezone")
        day = session_date
        notional = reference_price * quantity
        if gross_cap is not None and notional > gross_cap:
            return None
        with self.engine.begin() as db:
            self._lock_session(db, day)
            rows = db.execute(select(intents.c.symbol, intents.c.side, intents.c.reserved_notional).where(
                intents.c.session_date == day, intents.c.phase == "ENTRY")).mappings().all()
            if (any(row["symbol"] == symbol for row in rows) or
                    len(rows) >= max_total or sum(row["side"] == side for row in rows) >= max_per_side or
                    (gross_cap is not None and sum(Decimal(row["reserved_notional"]) for row in rows) + notional > gross_cap)):
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
        with self.engine.begin() as db:
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

    def mark_not_submitted(self, tag: str, at: datetime, *, committed_for_send: bool = False) -> None:
        """An entry lost authorization/freshness while queued; no broker call was made."""
        with self.lock, self.engine.begin() as db:
            row=db.execute(select(intents.c.state).where(intents.c.tag==tag).with_for_update()).first()
            expected = "SUBMISSION_UNKNOWN" if committed_for_send else "RESERVED"
            if not row or row.state != expected:
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
                details = (f"Kotak {str(report['status']).upper()}; filled {filled}/{row['quantity']}; "
                           f"average {average if average is not None else 'unavailable'}; "
                           f"order {report['broker_order_id']}")
                self._event(db, tag, state, details, stamp)

    def record_broker_fills(self, rows: list[dict], observed_at: datetime) -> int:
        """Persist real Kotak fills idempotently; repeated book refreshes add no duplicates."""
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("Fill observation time must include timezone")
        normalized = []
        occurrences: dict[str, int] = {}
        for item in rows:
            symbol = str(item.get("symbol") or "").strip().upper()
            trading_symbol = str(item.get("trading_symbol") or "").strip().upper()
            if not symbol or trading_symbol != f"{symbol}-EQ":
                continue
            try:
                quantity = int(item.get("filled_quantity"))
                price = Decimal(str(item.get("fill_price")))
            except (ValueError, TypeError, ArithmeticError):
                continue
            if quantity <= 0 or not price.is_finite() or price <= 0:
                continue
            record = {
                "broker_fill_id": str(item.get("fill_id") or "").strip(),
                "broker_order_id": str(item.get("broker_order_id") or "").strip(),
                "session_date": observed_at.date(),
                "symbol": symbol,
                "trading_symbol": trading_symbol,
                "side": str(item.get("side") or "").strip().upper(),
                "quantity": quantity,
                "price": price,
                "broker_time": str(item.get("fill_time") or "").strip(),
            }
            identity = {key: str(value) for key, value in record.items()}
            fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            occurrences[fingerprint] = occurrences.get(fingerprint, 0) + 1
            fill_key = hashlib.sha256(f"{fingerprint}:{occurrences[fingerprint]}".encode()).hexdigest()
            normalized.append({"fill_key": fill_key, **record, "first_seen_at": observed_at})
        if not normalized:
            return 0
        with self.lock, self.engine.begin() as db:
            inserted = db.execute(pg_insert(broker_fills).values(normalized).on_conflict_do_nothing(
                index_elements=[broker_fills.c.fill_key]).returning(broker_fills.c.fill_key))
            return len(inserted.scalars().all())

    def journal_snapshot(self, limit: int = 1000) -> dict:
        """Return durable order lifecycle events, broker fills, and source intents."""
        bounded = min(max(int(limit), 1), 5000)
        with self.engine.connect() as db:
            intent_rows = db.execute(select(intents).order_by(
                intents.c.session_date.desc(), intents.c.id.desc()).limit(bounded)).mappings().all()
            tags = [row["tag"] for row in intent_rows]
            event_rows = (db.execute(select(events).where(events.c.tag.in_(tags)).order_by(
                events.c.observed_at.desc(), events.c.id.desc()).limit(bounded * 5)).mappings().all()
                if tags else [])
            fill_rows = db.execute(select(broker_fills).order_by(
                broker_fills.c.first_seen_at.desc(), broker_fills.c.fill_key.desc()).limit(bounded)).mappings().all()
        return {"intents": [dict(row) for row in intent_rows],
                "events": [dict(row) for row in event_rows],
                "fills": [dict(row) for row in fill_rows]}

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
