"""Receive the repository's exact v1.0 JSON contract over an authenticated API."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text, create_engine, select, update
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from validate_signals import validate  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
metadata = MetaData()
reports = Table(
    "signal_reports", metadata,
    Column("id", Integer, primary_key=True),
    Column("session_date", String(10), nullable=False, unique=True),
    Column("idempotency_key", String(128), nullable=False, unique=True),
    Column("sha256", String(64), nullable=False),
    Column("status", String(16), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("original_json", Text, nullable=False),
)
signal_batches = Table(
    "signal_batches", metadata,
    Column("id", Integer, primary_key=True),
    Column("session_date", String(10), nullable=False),
    Column("idempotency_key", String(128), nullable=False, unique=True),
    Column("sha256", String(64), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("original_json", Text, nullable=False),
)


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def create_app(*, database_url: str | None = None, bearer_token: str | None = None,
               now_provider=None, allowed_symbols: set[str] | None = None) -> FastAPI:
    database_url = database_url or os.environ.get("SIGNAL_DATABASE_URL")
    bearer_token = bearer_token or os.environ.get("SIGNAL_BRIDGE_TOKEN")
    if not database_url or not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise RuntimeError("SIGNAL_DATABASE_URL must be a PostgreSQL connection URL")
    if not bearer_token or len(bearer_token) < 32:
        raise RuntimeError("A 32+ character SIGNAL_BRIDGE_TOKEN is required")
    engine = create_engine(database_url, pool_pre_ping=True)
    metadata.create_all(engine)
    clock = now_provider or (lambda: datetime.now(IST))
    app = FastAPI(title="Stock Signal Bridge", version="1.0.0")
    app.state.engine = engine

    def authorize(authorization: str | None = Header(default=None)) -> None:
        supplied = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
        if not hmac.compare_digest(supplied, bearer_token):
            raise HTTPException(status_code=401, detail="Unauthorized")

    @app.get("/api/v1/health")
    def health():
        return {"status": "up"}

    @app.post("/api/v1/signals/incoming", dependencies=[Depends(authorize)])
    async def incoming(request: Request, idempotency_key: str | None = Header(default=None)):
        if not idempotency_key or len(idempotency_key) > 128:
            raise HTTPException(status_code=400, detail="Idempotency-Key header is required (max 128 characters)")
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise HTTPException(status_code=415, detail="Content-Type must be application/json")
        body = await request.body()
        if len(body) > 256_000:
            raise HTTPException(status_code=413, detail="Signal report exceeds size limit")
        try:
            payload = json.loads(body, object_pairs_hook=reject_duplicate_keys)
            current = clock().astimezone(IST)
            validate(payload, expected_date=current.date().isoformat(),
                     allowed_symbols=allowed_symbols)
            if payload["session_date"] is None:
                raise ValueError("Bootstrap unavailable report is not a session delivery")
        except (ValueError, TypeError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        with engine.begin() as connection:
            existing_batch = connection.execute(select(signal_batches).where(
                signal_batches.c.idempotency_key == idempotency_key)).mappings().first()
            if existing_batch:
                if existing_batch["session_date"] == payload["session_date"] and existing_batch["sha256"] == digest:
                    return {"status": "DUPLICATE", "session_date": payload["session_date"],
                            "sha256": digest}
                raise HTTPException(status_code=409, detail="Conflicting idempotency key")
            existing_report = connection.execute(select(reports).where(
                reports.c.session_date == payload["session_date"])).mappings().first()
            aggregate = payload
            if existing_report:
                previous = json.loads(existing_report["original_json"])
                old_signals = previous.get("signals", []) if previous.get("status") == "READY" else []
                new_signals = payload.get("signals", []) if payload["status"] == "READY" else []
                if previous.get("status") == "READY" and payload["status"] != "READY":
                    raise HTTPException(status_code=409, detail="A later non-ready batch cannot replace session signals")
                if previous.get("status") != "READY" and payload["status"] != "READY":
                    raise HTTPException(status_code=409, detail="Conflicting non-ready report for this session")
                old_cutoff = datetime.fromisoformat(previous["news_cutoff_at"].replace("Z", "+00:00"))
                new_cutoff = datetime.fromisoformat(payload["news_cutoff_at"].replace("Z", "+00:00"))
                if old_signals and new_signals and new_cutoff < old_cutoff:
                    raise HTTPException(status_code=409, detail="Signal batch cutoff cannot move backwards")
                known = {item["symbol"]: item for item in old_signals}
                additions = []
                for item in new_signals:
                    old = known.get(item["symbol"])
                    if old is None:
                        additions.append(item)
                    elif old["side"] != item["side"]:
                        raise HTTPException(status_code=409, detail=f"Conflicting direction for {item['symbol']}")
                if not additions and old_signals:
                    return {"status": "DUPLICATE", "session_date": payload["session_date"],
                            "sha256": digest}
                combined = list(old_signals) + additions
                counts = {side: sum(item["side"] == side for item in combined)
                          for side in ("LONG", "SHORT")}
                if len(combined) > 10 or any(count > 5 for count in counts.values()):
                    raise HTTPException(status_code=422, detail="Session limit is five LONG and five SHORT symbols")
                if combined:
                    for side in ("LONG", "SHORT"):
                        ordered = [item for item in combined if item["side"] == side]
                        for rank, item in enumerate(ordered, 1):
                            item["rank"] = rank
                    aggregate = dict(payload)
                    aggregate["status"] = "READY"
                    aggregate.pop("reason", None)
                    aggregate["signals"] = combined
                raw = json.dumps(aggregate, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            try:
                connection.execute(signal_batches.insert().values(
                    session_date=payload["session_date"], idempotency_key=idempotency_key,
                    sha256=digest, received_at=current,
                    original_json=json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)))
                aggregate_sha = hashlib.sha256(raw.encode()).hexdigest()
                if existing_report:
                    connection.execute(update(reports).where(
                        reports.c.session_date == payload["session_date"]).values(
                            idempotency_key=idempotency_key, sha256=aggregate_sha,
                            status=aggregate["status"], received_at=current, original_json=raw))
                else:
                    connection.execute(reports.insert().values(
                        session_date=payload["session_date"], idempotency_key=idempotency_key,
                        sha256=aggregate_sha, status=aggregate["status"], received_at=current,
                        original_json=raw))
            except IntegrityError as exc:
                raise HTTPException(status_code=409, detail="Concurrent duplicate signal batch") from exc
        return {"status": "ACCEPTED", "session_date": payload["session_date"],
                "sha256": digest}

    @app.get("/api/v1/signals/latest", dependencies=[Depends(authorize)])
    def latest():
        current = clock().astimezone(IST)
        with engine.connect() as connection:
            row = connection.execute(select(reports).where(
                reports.c.session_date == current.date().isoformat())).mappings().first()
        if not row:
            return {"status": "UNAVAILABLE", "session_date": current.date().isoformat(),
                    "signals": [], "current": False}
        payload = json.loads(row["original_json"])
        return {"status": payload["status"], "session_date": row["session_date"],
                "current": True, "sha256": row["sha256"], "received_at": row["received_at"],
                "report": payload}

    @app.get("/api/v1/signals/status", dependencies=[Depends(authorize)])
    def status():
        current = clock().astimezone(IST)
        with engine.connect() as connection:
            row = connection.execute(select(reports.c.status, reports.c.received_at).where(
                reports.c.session_date == current.date().isoformat())).first()
        return {"session_date": current.date().isoformat(), "received": bool(row),
                "status": row.status if row else "UNAVAILABLE",
                "received_at": row.received_at if row else None}

    return app


def run() -> None:
    import uvicorn
    uvicorn.run(create_app(), host=os.environ.get("SIGNAL_BRIDGE_HOST", "127.0.0.1"),
                port=int(os.environ.get("SIGNAL_BRIDGE_PORT", "8000")))


if __name__ == "__main__":
    run()
