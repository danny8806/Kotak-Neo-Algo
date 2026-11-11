"""Deterministic direct-cross decisions from validated, fresh live ticks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from live_desk.dhan_data import IST


LATEST_ENTRY_CUTOFF = time(9, 30)
LATEST_EXIT_TIME = time(15)


@dataclass(frozen=True)
class LivePolicy:
    entry_cutoff: time
    exit_time: time
    max_long: int
    max_short: int
    max_positions: int
    per_position: Decimal
    gross_cap: Decimal
    timestamp_mode: str | None
    entry_quantity_cap: int | None = None
    fixed_entry_quantity: int | None = None

    def __post_init__(self):
        if self.fixed_entry_quantity not in (None, 1):
            raise ValueError("Fixed entry quantity must be exactly one share")
        # Apply current deadlines to settings persisted by older releases.
        if self.entry_cutoff > LATEST_ENTRY_CUTOFF:
            object.__setattr__(self, "entry_cutoff", LATEST_ENTRY_CUTOFF)
        if self.exit_time > LATEST_EXIT_TIME:
            object.__setattr__(self, "exit_time", LATEST_EXIT_TIME)


def load_policy(path: Path, *, entry_quantity_cap: str = "", fixed_entry_quantity: str = "") -> LivePolicy:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1 or data.get("exchange_timezone") != "Asia/Kolkata":
        raise ValueError("Unsupported live strategy config")
    if data.get("opening_candle_start") != "09:15" or data.get("stop_loss") is not None:
        raise ValueError("Unexpected opening range or stop rule")
    cutoff = time.fromisoformat(data["entry_cutoff"])
    exit_at = time.fromisoformat(data["exit_time"])
    if not time(9, 16) < cutoff < exit_at <= time(15, 30):
        raise ValueError("Invalid live trading window")
    max_long, max_short = int(data["max_long_positions"]), int(data["max_short_positions"])
    max_positions = int(data.get("max_open_positions", max_long + max_short))
    per_position = Decimal(str(data["allocation_per_position_inr"]))
    gross = Decimal(str(data["gross_cap_inr"]))
    if (not 1 <= max_long <= 5 or not 1 <= max_short <= 5 or
        not 1 <= max_positions <= 10 or per_position <= 0 or gross <= 0):
        raise ValueError("Invalid live exposure limits")
    if entry_quantity_cap and (not entry_quantity_cap.isascii()
                               or not entry_quantity_cap.isdecimal()
                               or int(entry_quantity_cap) < 1):
        raise ValueError("LIVE_ENTRY_QUANTITY_CAP must be a positive whole number")
    if fixed_entry_quantity not in ("", "1"):
        raise ValueError("LIVE_FIXED_ENTRY_QUANTITY must be empty or 1")
    return LivePolicy(cutoff, exit_at, max_long, max_short, max_positions, per_position, gross,
                      data.get("dhan_candle_timestamp_mode"), int(entry_quantity_cap) if entry_quantity_cap else None,
                      int(fixed_entry_quantity) if fixed_entry_quantity else None)


def candidate_entry(*, side: str, previous_price: Decimal, price: Decimal,
                    orb_high: Decimal, orb_low: Decimal, tick_at: datetime,
                    policy: LivePolicy, open_long: int = 0, open_short: int = 0,
                    reserved_gross: Decimal = Decimal(0), open_positions: int = 0) -> int | None:
    if tick_at.tzinfo is None or tick_at.utcoffset() is None:
        raise ValueError("Tick time must include timezone")
    local = tick_at.astimezone(IST)
    if not time(9, 16) <= local.time() < policy.entry_cutoff:
        return None
    if not all(x.is_finite() and x > 0 for x in (previous_price, price, orb_high, orb_low)):
        raise ValueError("Invalid live tick or ORB price")
    if side == "LONG":
        crossed = previous_price <= orb_high < price
        available = open_long < policy.max_long
    elif side == "SHORT":
        crossed = previous_price >= orb_low > price
        available = open_short < policy.max_short
    else:
        raise ValueError("Invalid side")
    if not crossed or not available or open_positions >= policy.max_positions:
        return None
    quantity = (policy.fixed_entry_quantity if policy.fixed_entry_quantity is not None else
                int((policy.per_position / price).to_integral_value(rounding=ROUND_DOWN)))
    if policy.entry_quantity_cap is not None:
        quantity = min(quantity, policy.entry_quantity_cap)
    if quantity < 1 or reserved_gross + quantity * price > policy.gross_cap:
        return None
    return quantity
