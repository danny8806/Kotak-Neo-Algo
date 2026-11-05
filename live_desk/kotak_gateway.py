"""Kotak Neo trade session and single-attempt order API boundary."""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal

from live_desk.dhan_data import totp_now
from live_desk.kotak_instruments import KotakInstrument
from live_desk.kotak_response import book_rows


@dataclass(frozen=True)
class KotakCredentials:
    consumer_key: str
    mobile_number: str
    ucc: str
    mpin: str
    totp_secret: str

    @classmethod
    def from_environment(cls) -> "KotakCredentials":
        keys = ("KOTAK_NEO_CONSUMER_KEY", "KOTAK_NEO_MOBILE_NUMBER", "KOTAK_NEO_UCC",
                "KOTAK_NEO_MPIN", "KOTAK_NEO_TOTP_SECRET")
        values = [os.environ.get(key, "").strip() for key in keys]
        if not all(values):
            raise ValueError("Kotak Neo credentials are incomplete")
        return cls(*values)


def login_kotak(credentials: KotakCredentials, *, client=None):
    if client is None:
        from neo_api_client import NeoAPI
        client = NeoAPI(consumer_key=credentials.consumer_key, environment="prod")
    first = client.totp_login(mobile_number=credentials.mobile_number,
                              ucc=credentials.ucc, totp=totp_now(credentials.totp_secret))
    if not isinstance(first, dict) or not isinstance(first.get("data"), dict) or not first["data"].get("token"):
        raise RuntimeError("Kotak TOTP login did not establish a view session")
    final = client.totp_validate(mpin=credentials.mpin)
    data = final.get("data") if isinstance(final, dict) else None
    if not isinstance(data, dict) or data.get("status") != "success" or not data.get("token"):
        raise RuntimeError("Kotak trade session validation failed")
    if data.get("ucc") != credentials.ucc:
        raise RuntimeError("Kotak trade session belongs to another UCC")
    return client


def place_kotak_order(client, instrument: KotakInstrument, side: str,
                      quantity: int, tag: str) -> dict:
    """Send once. An exception or missing order ID is ambiguous, never retried here."""
    if side not in {"BUY", "SELL"} or type(quantity) is not int or quantity <= 0:
        raise ValueError("Invalid Kotak order direction or quantity")
    if (not tag or instrument.trading_symbol != f"{instrument.symbol}-EQ" or
            not instrument.symbol or not instrument.token.isdecimal()):
        raise ValueError("Exact Kotak instrument mapping and unique tag required")
    response = client.place_order(exchange_segment="nse_cm", product="MIS", price="0",
                                  order_type="MKT", quantity=str(quantity), validity="DAY",
                                  trading_symbol=instrument.trading_symbol,
                                  transaction_type="B" if side == "BUY" else "S",
                                  amo="NO", tag=tag)
    if (not isinstance(response, dict) or str(response.get("stat", "")).lower() != "ok" or
            not str(response.get("nOrdNo") or "").strip()):
        raise RuntimeError("Kotak order submission was not acknowledged; reconcile before retry")
    return {"broker_order_id": str(response.get("nOrdNo") or ""),
            "requires_reconciliation": True, "tag": tag}


def reconcile_kotak_tag(client, tag: str) -> dict:
    """Read actual Kotak order state and trade fills for one caller tag."""
    if not tag:
        raise ValueError("Order tag is required")
    orders = book_rows(client.order_report(), "orders")
    matching = [row for row in orders if row.get("GuiOrdId") == tag]
    if len(matching) != 1:
        raise RuntimeError("Kotak order tag missing or ambiguous")
    order = matching[0]
    order_id = str(order.get("nOrdNo") or "")
    if not order_id:
        raise RuntimeError("Kotak order ID missing")
    trades = book_rows(client.trade_report(), "trades")
    fills = [row for row in trades if
             str(row.get("nOrdNo") or "") == order_id]
    fill_ids = set()
    quantity = 0
    notional = Decimal(0)
    for fill in fills:
        fill_id = str(fill.get("flId") or "")
        if not fill_id or fill_id in fill_ids or fill.get("trdSym") != order.get("trdSym"):
            raise RuntimeError("Kotak fill identity or symbol mismatch")
        fill_ids.add(fill_id)
        count = int(fill["fldQty"])
        price = Decimal(str(fill["avgPrc"]))
        if count <= 0 or not price.is_finite() or price <= 0:
            raise RuntimeError("Invalid Kotak fill quantity or price")
        quantity += count
        notional += price * count
    if quantity > int(order["qty"]):
        raise RuntimeError("Kotak fills exceed order quantity")
    return {"tag": tag, "broker_order_id": order_id, "status": str(order.get("ordSt") or ""),
            "ordered_quantity": int(order["qty"]), "filled_quantity": quantity,
            "average_fill_price": str(notional / quantity) if quantity else None,
            "trading_symbol": order.get("trdSym")}


def cancel_kotak_order(client, order_id: str) -> None:
    """Send a single cancellation request; the subsequent order report is authoritative."""
    if not order_id:
        raise ValueError("Kotak order ID is required for cancellation")
    response = client.cancel_order(order_id=order_id)
    if not isinstance(response, dict) or response.get("stat") != "Ok":
        raise RuntimeError("Kotak cancellation was not acknowledged; reconcile before proceeding")
