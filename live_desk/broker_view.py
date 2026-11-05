"""Sanitize Kotak Neo REAL broker books for the live-only dashboard.

Never generate transactions or infer exchange fills from a Dhan price.
Positions show Kotak-confirmed quantities. Gross mark-to-market is computed
ONLY when a fresh real quote exists; unavailable quotes produce null.
"""
from __future__ import annotations
from datetime import datetime
from decimal import Decimal, InvalidOperation
from live_desk.dhan_data import IST

def _decimal(v, field):
    try:
        d=Decimal(str(v))
    except (InvalidOperation,TypeError) as exc:
        raise ValueError(f"Broker {field} is invalid") from exc
    if not d.is_finite():raise ValueError(f"Broker {field} is not finite")
    return d

def _rows(result,label):
    if not isinstance(result,dict):
        raise ValueError(f"Kotak {label} response not a dict")
    if str(result.get("stat","")).lower() not in {"ok","success"}:
        raise ValueError(f"Kotak {label} unavailable")
    rows=result.get("data")
    if not isinstance(rows,list):
        raise ValueError(f"Kotak {label} returned no valid list")
    return rows

def positions(client,quotes:dict[str,dict]|None=None):
    rows=_rows(client.positions(),"positions")
    quotes=quotes or {}
    out=[]
    for item in rows:
        if not isinstance(item,dict):continue
        if item.get("exSeg")!="nse_cm":continue
        symbol=str(item.get("trdSym") or "")
        if not symbol.endswith("-EQ"):continue
        simple=symbol[:-3]
        qty=(int(item.get("cfBuyQty") or 0)+int(item.get("flBuyQty") or 0)
             -int(item.get("cfSellQty") or 0)-int(item.get("flSellQty") or 0))
        buy=_decimal(item.get("buyAmt",0),"buyAmt")+_decimal(item.get("cfBuyAmt",0),"cfBuyAmt")
        sell=_decimal(item.get("sellAmt",0),"sellAmt")+_decimal(item.get("cfSellAmt",0),"cfSellAmt")
        quote=quotes.get(simple)
        latest=quote.get("ltp") if isinstance(quote,dict) else None
        mtm=None
        if latest is not None:
            price=_decimal(latest,"LTP")
            if price<=0:raise ValueError("Price must be positive")
            mtm=round(float(sell-buy+Decimal(qty)*price),2)
        out.append({"symbol":simple,"trading_symbol":symbol,"side":"LONG" if qty>0 else "SHORT" if qty<0 else "FLAT",
                    "net_quantity":qty,"product":str(item.get("prod") or ""),
                    "broker_buy_amount":str(buy),"broker_sell_amount":str(sell),
                    "ltp":latest,"gross_mark_to_market":mtm,
                    "pnl_note":"Gross from broker quantities and amounts plus current market quote; excludes fees; unavailable when LTP missing",
                    "broker_updated_at":str(item.get("hsUpTm") or "")})
    return out

def orders(client):
    rows=_rows(client.order_report(),"orders")
    out=[]
    for item in rows:
        if not isinstance(item,dict):continue
        t=str(item.get("trdSym") or "")
        if not t.endswith("-EQ"):continue
        out.append({"symbol":t[:-3],"trading_symbol":t,
            "broker_order_id":str(item.get("nOrdNo") or ""),
            "status":str(item.get("ordSt") or ""),
            "side":str(item.get("trnsTp") or item.get("transaction_type") or ""),
            "order_quantity":str(item.get("qty") or ""),
            "product":str(item.get("prod") or ""),
            "order_time":str(item.get("ordDtTm") or ""),
            "tag":str(item.get("GuiOrdId") or "")})
    return out

def fills(client):
    rows=_rows(client.trade_report(),"trades")
    out=[]
    for item in rows:
        if not isinstance(item,dict):continue
        t=str(item.get("trdSym") or "")
        if not t.endswith("-EQ"):continue
        out.append({"symbol":t[:-3],"trading_symbol":t,
            "broker_order_id":str(item.get("nOrdNo") or ""),
            "fill_id":str(item.get("flId") or ""),
            "filled_quantity":str(item.get("fldQty") or ""),
            "fill_price":str(item.get("avgPrc") or ""),
            "fill_time":str(item.get("flDtTm") or item.get("ordDtTm") or "")})
    return out
