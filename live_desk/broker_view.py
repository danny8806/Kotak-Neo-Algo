"""Sanitize Kotak Neo REAL broker books for the live-only dashboard.

Never generate transactions or infer exchange fills from a Dhan price.
Positions show Kotak-confirmed quantities. Gross mark-to-market is computed
ONLY when a fresh real quote exists; unavailable quotes produce null.
"""
from __future__ import annotations
from datetime import datetime
from decimal import Decimal, InvalidOperation
from live_desk.dhan_data import IST
from live_desk.kotak_response import book_rows

def _decimal(v, field):
    try:
        d=Decimal(str(v))
    except (InvalidOperation,TypeError) as exc:
        raise ValueError(f"Broker {field} is invalid") from exc
    if not d.is_finite():raise ValueError(f"Broker {field} is not finite")
    return d

def _rows(result,label):
    return book_rows(result,label)

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
        buy_qty=int(item.get("cfBuyQty") or 0)+int(item.get("flBuyQty") or 0)
        sell_qty=int(item.get("cfSellQty") or 0)+int(item.get("flSellQty") or 0)
        qty=buy_qty-sell_qty
        buy=_decimal(item.get("buyAmt",0),"buyAmt")+_decimal(item.get("cfBuyAmt",0),"cfBuyAmt")
        sell=_decimal(item.get("sellAmt",0),"sellAmt")+_decimal(item.get("cfSellAmt",0),"cfSellAmt")
        out.append({"symbol":simple,"trading_symbol":symbol,"side":"LONG" if qty>0 else "SHORT" if qty<0 else "FLAT",
                    "net_quantity":qty,"product":str(item.get("prod") or ""),
                    "broker_buy_amount":str(buy),"broker_sell_amount":str(sell),
                    "broker_buy_quantity":buy_qty,"broker_sell_quantity":sell_qty,
                    "cost_basis_verified":not (
                        (int(item.get("cfBuyQty") or 0)>0 and _decimal(item.get("cfBuyAmt",0),"cfBuyAmt")<=0) or
                        (int(item.get("cfSellQty") or 0)>0 and _decimal(item.get("cfSellAmt",0),"cfSellAmt")<=0)),
                    "pnl_note":"Gross broker-book P&L excludes fees. Open P&L requires a fresh exchange tick; unverified carry-forward costs remain unavailable.",
                    "broker_updated_at":str(item.get("hsUpTm") or "")})
    return value_positions(out,quotes)


def value_positions(positions,quotes):
    """Value the latest immutable broker books against current, prefiltered quotes."""
    output=[]
    for original in positions:
        row=dict(original);symbol=row['symbol'];qty=row['net_quantity']
        quote=quotes.get(symbol,{})
        latest=quote.get('ltp')
        price=_decimal(latest,'LTP') if latest is not None else None
        if price is not None and price<=0:price=None
        buy=Decimal(row['broker_buy_amount']);sell=Decimal(row['broker_sell_amount'])
        buy_qty=row.get('broker_buy_quantity',0);sell_qty=row.get('broker_sell_quantity',0)
        realized=unrealized=total=None
        status='COST_BASIS_UNVERIFIED'
        if row.get('cost_basis_verified',False):
            matched=min(buy_qty,sell_qty)
            realized=(sell/Decimal(sell_qty)-buy/Decimal(buy_qty))*matched if matched else Decimal(0)
            if not qty:
                realized=total=sell-buy;unrealized=Decimal(0);status='CLOSED'
            elif price is not None:
                total=sell-buy+Decimal(qty)*price
                unrealized=total-realized;status='LIVE'
            else:status='QUOTE_UNAVAILABLE'
        row.update(ltp=float(price) if price is not None else None,
                   gross_mark_to_market=round(float(total),2) if total is not None else None,
                   realized_gross=round(float(realized),2) if realized is not None else None,
                   unrealized_gross=round(float(unrealized),2) if unrealized is not None else None,
                   pnl_status=status)
        output.append(row)
    return output


def pnl_summary(positions,*,books_fresh):
    def total(key):
        if not books_fresh or any(row.get(key) is None for row in positions):return None
        return round(sum(row[key] for row in positions),2)
    return {'gross_realized':total('realized_gross'),'gross_unrealized':total('unrealized_gross'),
            'gross_total':total('gross_mark_to_market'),'net_after_fees':None,
            'fees_status':'NOT_AVAILABLE','books_fresh':books_fresh,
            'unpriced_open_positions':sum(bool(row['net_quantity']) and row.get('unrealized_gross') is None for row in positions)}

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
            "side":str(item.get("trnsTp") or item.get("transaction_type") or ""),
            "filled_quantity":str(item.get("fldQty") or ""),
            "fill_price":str(item.get("avgPrc") or ""),
            "fill_time":str(item.get("flDtTm") or item.get("ordDtTm") or "")})
    return out
