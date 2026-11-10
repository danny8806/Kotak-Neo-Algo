"""Descriptive, broker-confirmed trade journal analytics.

This module reports historical outcomes. It does not train a model, change
strategy settings, infer fills, or issue trading instructions.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation


def _decimal(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if number.is_finite() else None


def build_trade_journal(intents: list[dict], events: list[dict], fills: list[dict]) -> dict:
    """Match durable entry/exit intents and summarize only confirmed fills."""
    grouped = defaultdict(dict)
    intent_by_tag = {}
    for item in intents:
        key = (str(item["session_date"]), item["symbol"])
        grouped[key][item["phase"]] = item
        intent_by_tag[item["tag"]] = item

    trades = []
    for (session_date, symbol), phases in grouped.items():
        entry = phases.get("ENTRY")
        if not entry:
            continue
        exit_order = phases.get("EXIT")
        entry_filled = int(entry.get("filled_quantity") or 0)
        exit_filled = min(int(exit_order.get("filled_quantity") or 0), entry_filled) if exit_order else 0
        remaining = max(0, entry_filled - exit_filled)
        entry_price = _decimal(entry.get("average_fill_price"))
        exit_price = _decimal(exit_order.get("average_fill_price")) if exit_order else None
        closed_quantity = min(entry_filled, exit_filled)
        gross = None
        if closed_quantity and entry_price is not None and exit_price is not None:
            direction = Decimal(1) if entry["side"] == "BUY" else Decimal(-1)
            gross = (exit_price - entry_price) * closed_quantity * direction

        if not entry_filled:
            status = "UNFILLED" if entry["state"] in {"REJECTED", "ABORTED"} else "ENTRY PENDING"
        elif remaining:
            status = "PARTIALLY CLOSED" if exit_filled else "EXIT PENDING" if exit_order and exit_order["state"] in {
                "RESERVED", "SUBMISSION_UNKNOWN", "ACKNOWLEDGED", "PARTIAL", "CANCEL_PENDING"
            } else "OPEN"
        else:
            status = "CLOSED"
        trades.append({
            "session_date": session_date,
            "symbol": symbol,
            "direction": "LONG" if entry["side"] == "BUY" else "SHORT",
            "status": status,
            "entry_tag": entry["tag"],
            "exit_tag": exit_order.get("tag") if exit_order else None,
            "entry_order_id": entry.get("broker_order_id"),
            "exit_order_id": exit_order.get("broker_order_id") if exit_order else None,
            "entry_state": entry["state"],
            "exit_state": exit_order.get("state") if exit_order else None,
            "entry_quantity": entry_filled,
            "exit_quantity": exit_filled,
            "remaining_quantity": remaining,
            "entry_average_price": float(entry_price) if entry_price is not None else None,
            "exit_average_price": float(exit_price) if exit_price is not None else None,
            "realized_gross": round(float(gross), 2) if gross is not None else None,
            "updated_at": str(exit_order.get("updated_at") if exit_order else entry.get("updated_at")),
        })
    trades.sort(key=lambda row: (row["session_date"], row["updated_at"]), reverse=True)

    closed = [row for row in trades if row["status"] == "CLOSED" and row["realized_gross"] is not None]
    wins = [row["realized_gross"] for row in closed if row["realized_gross"] > 0]
    losses = [row["realized_gross"] for row in closed if row["realized_gross"] < 0]
    realized_values = [row["realized_gross"] for row in trades if row["realized_gross"] is not None]
    closed.sort(key=lambda row: (row["session_date"], row["updated_at"]))
    cumulative = peak = max_drawdown = 0.0
    for row in closed:
        cumulative += row["realized_gross"]
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    closed_deployed = sum(
        (_decimal(row.get("entry_average_price")) or Decimal(0)) * row["entry_quantity"]
        for row in closed
    )
    closed_realized = sum(row["realized_gross"] for row in closed)
    summary = {
        "trade_count": len(trades),
        "closed_trade_count": len(closed),
        "open_trade_count": sum(row["remaining_quantity"] > 0 for row in trades),
        "gross_realized_from_exits": round(sum(realized_values), 2),
        "wins": len(wins),
        "losses": len(losses),
        "gross_loss_total": round(sum(losses), 2),
        "closed_trade_return_pct": round(100 * closed_realized / float(closed_deployed), 2) if closed_deployed else None,
        "max_closed_trade_drawdown_gross": round(max_drawdown, 2),
        "win_rate_pct": round(100 * len(wins) / len(closed), 2) if closed else None,
        "average_win": round(sum(wins) / len(wins), 2) if wins else None,
        "average_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "net_after_fees": None,
        "fees_status": "NOT AVAILABLE FROM CONFIRMED BROKER DATA",
        "method": "Matched Kotak-confirmed entry and exit fills; descriptive only",
    }

    timeline = []
    for item in events:
        intent = intent_by_tag.get(item["tag"], {})
        timeline.append({
            "observed_at": str(item["observed_at"]),
            "session_date": str(intent.get("session_date") or ""),
            "symbol": intent.get("symbol"),
            "phase": intent.get("phase"),
            "side": intent.get("side"),
            "state": item["state"],
            "tag": item["tag"],
            "details": item["details"],
        })
    return {"summary": summary, "trades": trades, "events": timeline, "fills": fills}
