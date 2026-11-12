"""Read the 09:15 one-minute Dhan candle for the live strategy."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import threading

from live_desk.dhan_data import IST
AUDIT_LOCK = threading.Lock()


def opening_range(dhan_client, security_id: str, session_date: date,
                  *, timestamp_mode: str, clock=None) -> tuple[Decimal, Decimal]:
    if timestamp_mode != "start":
        raise ValueError("Dhan one-minute candles require start-labelled 09:15 timestamps")
    start = datetime.combine(session_date, time(9, 15), IST)
    now=(clock or (lambda:datetime.now(IST)))().astimezone(IST)
    if now < start+timedelta(minutes=1):
        raise ValueError("The 09:15 opening candle has not completed")
    # A buffer avoids losing the opening bar at a provider request boundary.
    # Providers may return wider arrays; select only the exact 09:15 bar below.
    request_start=start-timedelta(minutes=1)
    data = dhan_client.get_intraday(security_id, request_start, start + timedelta(minutes=2), interval="1")
    keys = ("timestamp", "open", "high", "low", "close")
    if any(not isinstance(data.get(key), list) for key in keys):
        raise ValueError("Incomplete Dhan candle response")
    count = len(data["timestamp"])
    if not count or any(len(data[key]) != count for key in keys):
        raise ValueError("Misaligned Dhan candle arrays")
    expected = start
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
    audit_dir=os.getenv('LIVE_ORB_AUDIT_DIR','').strip()
    if audit_dir:
        record={'session_date':session_date.isoformat(),'security_id':str(security_id),
            'candle_start':start.isoformat(),'candle_end':(start+timedelta(minutes=1)).isoformat(),
            'timestamp_mode':'start','open':str(opened),'high':str(high),'low':str(low),'close':str(closed),
            'volume':str(data['volume'][i]) if isinstance(data.get('volume'),list) and len(data['volume'])==count else None,
            'source':'Dhan /v2/charts/intraday','source_timestamp':data['timestamp'][i],
            'fetched_at':(clock or (lambda:datetime.now(IST)))().astimezone(IST).isoformat(),
            'request_from':request_start.isoformat(),'request_to':(start+timedelta(minutes=2)).isoformat()}
        folder=Path(audit_dir);folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        with AUDIT_LOCK:
            fd=os.open(folder/('opening-candles-'+session_date.isoformat()+'.jsonl'),os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
            with os.fdopen(fd,'a',encoding='utf-8') as log:
                log.write(json.dumps(record,separators=(',',':'))+'\n');log.flush();os.fsync(log.fileno())
    return high, low
