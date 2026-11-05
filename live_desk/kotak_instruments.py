"""Exact Kotak Neo NSE cash-equity mapping for broker orders."""

from __future__ import annotations

from dataclasses import dataclass


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
        if row.get("pExchSeg") != "nse_cm" or row.get("pGroup") != "EQ":
            continue
        reference = str(row.get("pScripRefKey") or row.get("pSymbolName") or "").strip().upper()
        trading_symbol = str(row.get("pTrdSymbol") or "")
        token = str(row.get("pSymbol") or "")
        if reference != symbol or trading_symbol != f"{symbol}-EQ" or not token.isdecimal():
            continue
        item = KotakInstrument(symbol, trading_symbol, token)
        valid[(trading_symbol, token)] = item
    if len(valid) != 1:
        raise ValueError(f"Kotak NSE EQ mapping missing or ambiguous for {symbol}")
    return next(iter(valid.values()))


def lookup_kotak_equity(client, symbol: str) -> KotakInstrument:
    if not symbol or symbol != symbol.upper():
        raise ValueError("Canonical uppercase NSE symbol required")
    rows = client.search_scrip(exchange_segment="nse_cm", symbol=symbol,
                               expiry="", option_type="", strike_price="")
    return resolve_kotak_search(symbol, rows)
