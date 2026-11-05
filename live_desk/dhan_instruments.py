"""Exact NSE cash-equity symbol mapping from Dhan's official compact master."""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

IST = ZoneInfo("Asia/Kolkata")
MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
MAX_MASTER_BYTES = 40_000_000
REQUIRED_COLUMNS = {"SEM_EXM_EXCH_ID", "SEM_SEGMENT", "SEM_SMST_SECURITY_ID",
                    "SEM_INSTRUMENT_NAME", "SEM_TRADING_SYMBOL", "SEM_SERIES"}


@dataclass(frozen=True)
class DhanMasterSnapshot:
    session_date: str
    fetched_at: datetime
    sha256: str
    security_ids: dict[str, str]


def resolve_dhan_symbols(raw_csv: bytes, symbols: set[str], fetched_at: datetime) -> DhanMasterSnapshot:
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ValueError("Master receipt time needs timezone")
    if not raw_csv or len(raw_csv) > MAX_MASTER_BYTES:
        raise ValueError("Dhan master missing or exceeds size limit")
    requested = {symbol.strip().upper() for symbol in symbols}
    if not requested or any(not symbol for symbol in requested):
        raise ValueError("NSE symbols are required")
    reader = csv.DictReader(io.StringIO(raw_csv.decode("utf-8-sig")))
    if not REQUIRED_COLUMNS.issubset(reader.fieldnames or []):
        raise ValueError("Dhan compact master columns changed")
    matches: dict[str, set[str]] = {symbol: set() for symbol in requested}
    for row in reader:
        if (row["SEM_EXM_EXCH_ID"] != "NSE" or row["SEM_SEGMENT"] != "E" or
            row["SEM_INSTRUMENT_NAME"] != "EQUITY" or row["SEM_SERIES"] != "EQ"):
            continue
        symbol = row["SEM_TRADING_SYMBOL"].strip().upper()
        if symbol in matches:
            security_id = row["SEM_SMST_SECURITY_ID"].strip()
            if not security_id.isdecimal():
                raise ValueError(f"Invalid Dhan security ID for {symbol}")
            matches[symbol].add(security_id)
    missing = sorted(symbol for symbol, ids in matches.items() if not ids)
    ambiguous = sorted(symbol for symbol, ids in matches.items() if len(ids) != 1)
    if missing or ambiguous:
        raise ValueError(f"Dhan mapping unresolved: missing={missing}, ambiguous={ambiguous}")
    return DhanMasterSnapshot(fetched_at.astimezone(IST).date().isoformat(), fetched_at,
                              hashlib.sha256(raw_csv).hexdigest(),
                              {symbol: next(iter(ids)) for symbol, ids in matches.items()})


def fetch_dhan_symbols(symbols: set[str], *, now: datetime,
                       session: requests.Session | None = None) -> DhanMasterSnapshot:
    client = session or requests.Session()
    response = client.get(MASTER_URL, timeout=30, stream=True)
    response.raise_for_status()
    body = bytearray()
    for chunk in response.iter_content(chunk_size=65536):
        body.extend(chunk)
        if len(body) > MAX_MASTER_BYTES:
            raise ValueError("Dhan master exceeds size limit")
    return resolve_dhan_symbols(bytes(body), symbols, now)
