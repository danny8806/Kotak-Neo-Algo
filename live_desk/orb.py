"""Read the 09:15 one-minute Dhan candle for the live strategy."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from live_desk.dhan_data import IST


def opening_range(dhan_client, security_id: str, session_date: date,
                  *, timestamp_mode: str) -> tuple[Decimal, Decimal]:
    if timestamp_mode not in {"start", "end"}:
        raise ValueError("Dhan timestamp convention must be audited: start or end")
    start = datetime.combine(session_date, time(9, 15), IST)
    data = dhan_client.get_intraday(security_id, start, start + timedelta(minutes=2), interval="1")
    keys = ("timestamp", "open", "high", "low", "close")
    if any(not isinstance(data.get(key), list) for key in keys):
        raise ValueError("Incomplete Dhan candle response")
    count = len(data["timestamp"])
    if not count or any(len(data[key]) != count for key in keys):
        raise ValueError("Misaligned Dhan candle arrays")
    expected = start + (timedelta(minutes=1) if timestamp_mode == "end" else timedelta())
    matches = [index for index, raw in enumerate(data["timestamp"])
               if datetime.fromtimestamp(raw, timezone.utc).astimezone(IST) == expected]
    if len(matches) != 1:
        raise ValueError("Exactly one 09:15 one-minute candle is required")
    i = matches[0]
    opened, high, low, closed = [Decimal(str(data[key][i])) for key in ("open", "high", "low", "close")]
    if not all(value.is_finite() and value > 0 for value in (opened, high, low, closed)):
        raise ValueError("Invalid opening candle price")
    if low > min(opened, closed) or high < max(opened, closed):
        raise ValueError("Inconsistent opening candle OHLC")
    return high, low
