"""Exact Kotak Neo NSE cash-equity mapping for broker orders."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from urllib.parse import urlparse

import requests


def _equity(row):
    if row.get("pExchSeg") != "nse_cm" or row.get("pGroup") != "EQ":
        return None
    symbol = str(row.get("pScripRefKey") or row.get("pSymbolName") or "").strip().upper()
    trading_symbol = str(row.get("pTrdSymbol") or "").strip()
    token = str(row.get("pSymbol") or "").strip()
    if symbol and trading_symbol == f"{symbol}-EQ" and token.isdecimal():
        return KotakInstrument(symbol, trading_symbol, token)


@dataclass(frozen=True)
class KotakInstrument:
    symbol: str
    trading_symbol: str  # pTrdSymbol for place_order
    token: str           # pSymbol for Kotak market/order feeds


def resolve_kotak_search(symbol: str, rows: object) -> KotakInstrument:
    if not symbol or symbol != symbol.upper():
        raise ValueError("Canonical uppercase NSE symbol required")
    if not isinstance(rows, list):
        raise ValueError("Kotak scrip search did not return a list")
    valid: dict[tuple[str, str], KotakInstrument] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = _equity(row)
        if item and item.symbol == symbol:
            valid[(item.trading_symbol, item.token)] = item
    if len(valid) != 1:
        raise ValueError(f"Kotak NSE EQ mapping missing or ambiguous for {symbol}")
    return next(iter(valid.values()))


def lookup_kotak_equity(client, symbol: str) -> KotakInstrument:
    if not symbol or symbol != symbol.upper():
        raise ValueError("Canonical uppercase NSE symbol required")
    rows = client.search_scrip(exchange_segment="nse_cm", symbol=symbol,
                               expiry="", option_type="", strike_price="")
    return resolve_kotak_search(symbol, rows)


def load_kotak_equity_master(client) -> dict[str, KotakInstrument]:
    """Download and index Kotak's current NSE cash master once per session."""
    url = client.scrip_master(exchange_segment="nse_cm")
    parsed = urlparse(url) if isinstance(url, str) else None
    if not parsed or parsed.scheme != "https" or parsed.hostname != "lapi.kotaksecurities.com":
        raise ValueError("Unexpected Kotak NSE cash master URL")
    raw = bytearray()
    with requests.get(url, timeout=(4, 10), stream=True, allow_redirects=False) as response:
        if response.status_code != 200:
            raise RuntimeError("Kotak NSE cash master download failed")
        for chunk in response.iter_content(chunk_size=65536):
            raw.extend(chunk)
            if len(raw) > 25_000_000:
                raise ValueError("Kotak NSE cash master exceeds size limit")
    rows = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    if rows.fieldnames is not None:
        rows.fieldnames = [field.strip() for field in rows.fieldnames]
    fields = set(rows.fieldnames or ())
    if not {"pExchSeg", "pGroup", "pTrdSymbol", "pSymbol"} <= fields or not (
            {"pScripRefKey", "pSymbolName"} & fields):
        raise ValueError("Kotak NSE cash master schema changed")
    index = {}
    ambiguous = set()
    for row in rows:
        item = _equity(row)
        if item and index.setdefault(item.symbol, item) != item:
            ambiguous.add(item.symbol)
    return {symbol: item for symbol, item in index.items() if symbol not in ambiguous}
