"""Nifty 500 universe and exact Dhan/Kotak cash-equity master audit.

This module is read-only. It never creates or submits a broker order.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Constituent:
    symbol: str
    company: str
    industry: str
    series: str
    isin: str


def _csv_rows(raw: bytes, label: str) -> list[dict[str, str]]:
    if not raw or len(raw) > 50_000_000:
        raise ValueError(f"{label} missing or exceeds 50 MB")
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        if not reader.fieldnames:
            raise ValueError(f"{label} has no CSV header")
        return [{str(k).strip(): str(v or "").strip() for k, v in row.items()}
                for row in reader]
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ValueError(f"{label} is not a valid UTF-8 CSV") from exc


def parse_nifty500(raw: bytes) -> list[Constituent]:
    rows = _csv_rows(raw, "Nifty 500 constituent file")
    required = {"Company Name", "Industry", "Symbol", "Series", "ISIN Code"}
    if not required.issubset(rows[0] if rows else {}):
        raise ValueError("Nifty 500 constituent columns changed")
    constituents = [Constituent(
        symbol=row["Symbol"].upper(), company=row["Company Name"],
        industry=row["Industry"], series=row["Series"].upper(),
        isin=row["ISIN Code"].upper()) for row in rows]
    if not constituents or any(not c.symbol or not c.isin for c in constituents):
        raise ValueError("Nifty 500 file contains a blank symbol or ISIN")
    symbols = [c.symbol for c in constituents]
    if len(symbols) != len(set(symbols)):
        raise ValueError("Nifty 500 file contains duplicate symbols")
    return constituents


def eligible_nifty500_symbols(constituents: Iterable[Constituent]) -> set[str]:
    """Return standard NSE EQ-share constituents suitable for this cash strategy.

    The official constituent feed can include REIT units, trade-to-trade series,
    and placeholder rows. Keep those out of the regular NSE EQ order universe.
    """
    return {item.symbol for item in constituents
            if item.series == "EQ" and item.isin.startswith("INE")}


def load_nifty500_symbols(path: str | Path, *, now: datetime | None = None,
                          max_age: timedelta = timedelta(days=45)) -> set[str]:
    """Load a previously fetched official constituent snapshot, failing closed if stale."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("universe") != "NIFTY 500":
        raise ValueError("Nifty 500 universe snapshot is invalid")
    if data.get("source_url") != "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv":
        raise ValueError("Nifty 500 snapshot source is not the official constituent feed")
    stamp = datetime.fromisoformat(str(data.get("as_of", "")).replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("Nifty 500 snapshot timestamp must include a timezone")
    current = now or datetime.now(stamp.tzinfo)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Current time must include a timezone")
    age = current - stamp
    if age < timedelta(minutes=-5) or age > max_age:
        raise ValueError("Nifty 500 constituent snapshot is stale or future-dated; refresh it")
    symbols = data.get("symbols")
    count = data.get("constituent_count")
    if (not isinstance(symbols, list) or not symbols or len(symbols) != count or
            any(not isinstance(s, str) or not s for s in symbols) or
            len(set(symbols)) != len(symbols)):
        raise ValueError("Nifty 500 snapshot contains invalid or duplicate symbols")
    return set(symbols)


def _by_symbol(rows: Iterable[dict[str, str]], wanted: set[str], *, dhan: bool):
    mapped: dict[str, set[tuple[str, str]]] = {symbol: set() for symbol in wanted}
    observed: dict[str, set[str]] = {symbol: set() for symbol in wanted}
    for row in rows:
        if dhan:
            if (row.get("SEM_EXM_EXCH_ID", "").upper() != "NSE" or
                    row.get("SEM_SEGMENT", "").upper() != "E" or
                    row.get("SEM_INSTRUMENT_NAME", "").upper() != "EQUITY"):
                continue
            symbol = row.get("SEM_TRADING_SYMBOL", "").upper()
            series = row.get("SEM_SERIES", "").upper()
            token = row.get("SEM_SMST_SECURITY_ID", "")
            kind = row.get("SEM_EXCH_INSTRUMENT_TYPE", "").upper()
            if symbol in wanted:
                observed[symbol].add(f"{series or 'UNKNOWN'}:{kind or 'UNKNOWN'}")
                if series == "EQ" and token.isdecimal():
                    mapped[symbol].add((symbol, token))
        else:
            if (row.get("pExchSeg", "").lower() != "nse_cm" or
                    row.get("pExchange", "").upper() != "NSE" or
                    row.get("pSegment", "").upper() != "CASH"):
                continue
            reference = row.get("pScripRefKey", "").upper()
            symbol_name = row.get("pSymbolName", "").upper()
            symbol = reference if reference in wanted else symbol_name
            group = row.get("pGroup", "").upper()
            trading = row.get("pTrdSymbol", "")
            token = row.get("pSymbol", "")
            if symbol in wanted:
                observed[symbol].add(f"{group or 'UNKNOWN'}:{trading or 'UNKNOWN'}")
                if group == "EQ" and trading == f"{symbol}-EQ" and token.isdecimal():
                    mapped[symbol].add((trading, token))
    return mapped, observed


def audit_nifty500(*, constituents: list[Constituent], constituent_csv: bytes, dhan_csv: bytes,
                   kotak_csv: bytes, as_of: datetime,
                   dhan_source: str, kotak_source: str) -> dict:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("Audit time must be timezone-aware")
    wanted = {item.symbol for item in constituents}
    dhan, dhan_seen = _by_symbol(_csv_rows(dhan_csv, "Dhan master"), wanted, dhan=True)
    kotak, kotak_seen = _by_symbol(_csv_rows(kotak_csv, "Kotak master"), wanted, dhan=False)
    items = []
    for constituent in constituents:
        ds, ks = dhan[constituent.symbol], kotak[constituent.symbol]
        if len(ds) > 1 or len(ks) > 1:
            status, reason = "AMBIGUOUS", "Multiple exact NSE cash-equity IDs in a broker master"
        elif len(ds) == 1 and len(ks) == 1:
            status, reason = "MAPPED", "Exact NSE EQ cash-equity mapping in both broker masters"
        else:
            status = "UNAVAILABLE"
            categories = sorted(dhan_seen[constituent.symbol] | kotak_seen[constituent.symbol])
            reason = ("No exact eligible NSE EQ mapping in one or both masters" +
                      (f"; observed non-EQ/other instruments: {', '.join(categories)}" if categories else
                       "; symbol not found in either current cash-equity master"))
        items.append({
            **asdict(constituent), "status": status, "reason": reason,
            "dhan_security_id": next(iter(ds))[1] if len(ds) == 1 else None,
            "kotak_trading_symbol": next(iter(ks))[0] if len(ks) == 1 else None,
            "kotak_token": next(iter(ks))[1] if len(ks) == 1 else None,
            "dhan_observed_instruments": sorted(dhan_seen[constituent.symbol]),
            "kotak_observed_instruments": sorted(kotak_seen[constituent.symbol]),
        })
    count = len(items)
    mapped = sum(row["status"] == "MAPPED" for row in items)
    return {
        "report_version": "1.0", "as_of": as_of.isoformat(),
        "universe": "NIFTY 500", "constituent_count": count,
        "mapped_count": mapped, "unavailable_count": count - mapped,
        "ambiguous_count": sum(row["status"] == "AMBIGUOUS" for row in items),
        "sources": {
            "constituents": {"url": "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
                              "sha256": hashlib.sha256(constituent_csv).hexdigest()},
            "dhan_master": {"url": dhan_source, "sha256": hashlib.sha256(dhan_csv).hexdigest()},
            "kotak_master": {"url": kotak_source, "sha256": hashlib.sha256(kotak_csv).hexdigest()},
        },
        "instruments": items,
    }
