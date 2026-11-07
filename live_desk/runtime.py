"""Live-only ORB candidate to Kotak order coordination.

Callers must supply authenticated Dhan/Kotak clients, today's validated signals,
same-day mappings and a verified completed opening candle before constructing.
No paper price or simulated fill path exists here.
"""

from __future__ import annotations

import threading
import time as wall_time
from concurrent.futures import ThreadPoolExecutor, Future, wait
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from live_desk.dhan_data import IST
from live_desk.kotak_gateway import cancel_kotak_order, place_kotak_order, reconcile_kotak_tag
from live_desk.kotak_instruments import KotakInstrument
from live_desk.ledger import LiveLedger
from live_desk.strategy import LivePolicy, candidate_entry


class LiveRuntime:
    def __init__(self, *, session_date: date, signals: list[dict],
                 dhan_ids: dict[str, str], kotak_instruments: dict[str, KotakInstrument],
                 opening_ranges: dict[str, tuple[Decimal, Decimal]],
                 policy: LivePolicy, ledger: LiveLedger, broker,
                 enabled: bool, clock=None, close_only: bool = False):
        symbols = {row["symbol"] for row in signals}
        if (not symbols or len(symbols) != len(signals) or
            symbols != set(dhan_ids) or symbols != set(kotak_instruments) or
            symbols != set(opening_ranges)):
            raise ValueError("Every signal needs exact Dhan/Kotak mapping and a completed ORB")
        if not close_only and policy.timestamp_mode not in {"start", "end"}:
            raise ValueError("Dhan 09:15 timestamp convention is not verified")
        for symbol, instrument in kotak_instruments.items():
            if (instrument.symbol != symbol or instrument.trading_symbol != f"{symbol}-EQ"
                    or not instrument.token.isdecimal()):
                raise ValueError(f"Kotak NSE EQ mapping does not match signal {symbol}")
        for symbol, security_id in dhan_ids.items():
            if not security_id.isdecimal():
                raise ValueError(f"Dhan NSE EQ mapping invalid for signal {symbol}")
        self.session_date = session_date
        self.signals = {row["symbol"]: row for row in signals}
        self.dhan_ids = dhan_ids
        self.kotak_instruments = kotak_instruments
        self.opening_ranges = opening_ranges
        self.policy = policy
        self.ledger = ledger
        self.broker = broker
        self.lock = threading.RLock()
        self._enabled = enabled
        self.close_only = close_only
        self.clock = clock or (lambda: datetime.now(IST))
        self.previous: dict[str, Decimal] = {}
        self.last_tick_at: dict[str, datetime] = {}
        self._errors: dict[str,str] = {}
        self.submissions = ThreadPoolExecutor(max_workers=20, thread_name_prefix="kotak-order")
        self.pending: set[Future] = set()

    def extend_signals(self, *, signals: list[dict], dhan_ids: dict[str, str],
                       kotak_instruments: dict[str, KotakInstrument],
                       opening_ranges: dict[str, tuple[Decimal, Decimal]]) -> set[str]:
        """Add new same-session candidates after validating their broker maps and ORBs."""
        incoming = {row["symbol"]: row for row in signals}
        symbols = set(incoming)
        if (not symbols or symbols != set(dhan_ids) or symbols != set(kotak_instruments)
                or symbols != set(opening_ranges)):
            raise ValueError("Every added signal needs exact Dhan/Kotak mapping and a completed ORB")
        with self.lock:
            additions = symbols - set(self.signals)
            for symbol in symbols & set(self.signals):
                if incoming[symbol]["side"] != self.signals[symbol]["side"]:
                    raise ValueError(f"Signal direction changed during the session for {symbol}")
            for symbol in additions:
                instrument = kotak_instruments[symbol]
                security_id = dhan_ids[symbol]
                if (instrument.symbol != symbol or instrument.trading_symbol != f"{symbol}-EQ"
                        or not instrument.token.isdecimal() or not security_id.isdecimal()):
                    raise ValueError(f"Broker mapping does not match signal {symbol}")
                self.signals[symbol] = incoming[symbol]
                self.dhan_ids[symbol] = security_id
                self.kotak_instruments[symbol] = instrument
                self.opening_ranges[symbol] = opening_ranges[symbol]
            return additions

    @property
    def errors(self):
        with self.lock:return list(self._errors.values())

    @property
    def enabled(self):
        with self.lock:return self._enabled

    @enabled.setter
    def enabled(self,value):
        with self.lock:self._enabled=bool(value)

    def _error(self,key,message):
        with self.lock:
            if len(self._errors)>=100 and key not in self._errors:
                self._errors.pop(next(iter(self._errors)))
            self._errors[key]=message

    def _clear_error(self,key):
        with self.lock:self._errors.pop(key,None)

    def on_tick(self, tick: dict) -> dict | None:
        """Process a real Dhan ticker packet after receipt; never infer a fill."""
        symbol = tick.get("symbol")
        if symbol not in self.signals:
            return None
        at = tick.get("timestamp")
        if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
            return None
        local = at.astimezone(IST)
        if local.date() != self.session_date or local.time() < time(9, 16):
            return None
        age = self.clock().astimezone(IST) - local
        if age < timedelta(seconds=-2) or age > timedelta(seconds=5):
            return None
        try:price = Decimal(str(tick.get("ltp")))
        except (ValueError,TypeError,ArithmeticError):return None
        if not price.is_finite() or price <= 0:
            return None
        with self.lock:
            prior_time = self.last_tick_at.get(symbol)
            if prior_time and at < prior_time:
                return None
            previous = self.previous.get(symbol)
            self.previous[symbol] = price
            self.last_tick_at[symbol] = at
            if not self.enabled or self.close_only or previous is None:
                return None
            side = self.signals[symbol]["side"]
            rows = self.ledger.snapshot(self.session_date)
            entries = [row for row in rows if row["phase"] == "ENTRY"]
            long_count = sum(row["side"] == "BUY" for row in entries)
            short_count = sum(row["side"] == "SELL" for row in entries)
            reserved = sum(Decimal(row["reserved_notional"]) for row in entries)
            high, low = self.opening_ranges[symbol]
            quantity = candidate_entry(
                side=side, previous_price=previous, price=price,
                orb_high=high, orb_low=low, tick_at=at, policy=self.policy,
                open_long=long_count, open_short=short_count, reserved_gross=reserved,
                open_positions=len(entries))
            if quantity is None:
                return None
            direction = "BUY" if side == "LONG" else "SELL"
            intent = self.ledger.reserve_entry(
                session_date=self.session_date, symbol=symbol, side=direction,
                quantity=quantity, reference_price=price, at=at,
                max_per_side=self.policy.max_long if side == "LONG" else self.policy.max_short,
                gross_cap=self.policy.gross_cap, max_total=self.policy.max_positions)
            if intent is None:
                return None
            future = self.submissions.submit(self._submit_and_reconcile, intent, at)
            self.pending.add(future)
            future.add_done_callback(self._submission_done)
            return {"tag": intent["tag"], "state": "SUBMISSION_QUEUED"}

    def _submission_done(self, future: Future) -> None:
        with self.lock:
            self.pending.discard(future)
        if not future.cancelled() and future.exception() is not None:
            self._error("SUBMISSION_WORKER","Submission worker failed; durable intent requires review")

    def wait_for_submissions(self, timeout: float = 30.0) -> bool:
        """Wait for in-flight Kotak submissions before reconciliation or EOD exits."""
        with self.lock:
            pending = set(self.pending)
        if not pending:
            return True
        _, unfinished = wait(pending, timeout=timeout)
        return not unfinished

    def _submit_and_reconcile(self, intent: dict, at: datetime) -> dict:
        tag = intent["tag"]
        with self.lock:
            now=self.clock().astimezone(IST)
            if intent.get("phase","ENTRY")=="ENTRY" and (
                    not self.enabled or self.close_only or now.date()!=self.session_date or
                    now.time()>=self.policy.entry_cutoff or now-at.astimezone(IST)>timedelta(seconds=5)):
                self.ledger.mark_not_submitted(tag,now)
                return {"tag":tag,"state":"ABORTED"}
            # This durable transition is the commit point before network I/O.
            self.ledger.mark_submission_unknown(tag, now)
        result=None
        try:
            result = place_kotak_order(self.broker, self.kotak_instruments[intent["symbol"]],
                                       intent["side"], intent["quantity"], tag)
        except Exception as exc:
            # Even a timeout can follow broker acceptance. Only retry READS.
            self._error(tag,f"{intent['symbol']}: {type(exc).__name__}; reconcile tag {tag}")
        for attempt in range(3):
            if attempt:wall_time.sleep(.05*attempt)
            try:
                report = reconcile_kotak_tag(self.broker, tag)
                self.ledger.apply_broker_report(report, self.clock())
                self._clear_error(tag)
                return {"tag": tag, "submission": result, "broker": report}
            except Exception as exc:
                self._error(tag,f"{intent['symbol']}: {type(exc).__name__}; reconcile tag {tag}")
        return {"tag": tag, "state": "SUBMISSION_UNKNOWN"}

    def reconcile_pending(self, at: datetime) -> None:
        with self.lock:
            if self.pending:
                return
        for row in self.ledger.snapshot(self.session_date):
            if row["state"] in {"SUBMISSION_UNKNOWN", "ACKNOWLEDGED", "PARTIAL", "CANCEL_PENDING"}:
                try:
                    report = reconcile_kotak_tag(self.broker, row["tag"])
                    self.ledger.apply_broker_report(report, at)
                    self._clear_error(row["tag"])
                except Exception:
                    self._error(row["tag"],f"{row['symbol']}: broker reconciliation pending")

    def exit_due(self, at: datetime) -> list[dict]:
        if at.astimezone(IST).date() != self.session_date or at.astimezone(IST).time() < self.policy.exit_time:
            return []
        if not self.wait_for_submissions(timeout=30.0):
            self._error("EOD_PENDING","EOD exit blocked: Kotak submissions still in flight")
            return []
        self._clear_error("EOD_PENDING")
        self.reconcile_pending(at)
        # Cancel any still-open entry order once, then require a terminal broker
        # report before closing its actually filled quantity.
        for row in self.ledger.snapshot(self.session_date):
            if row["phase"] != "ENTRY" or row["state"] not in {"ACKNOWLEDGED", "PARTIAL"}:
                continue
            if not row.get("broker_order_id"):
                self._error(row["tag"],f'{row["symbol"]}: missing order ID; EOD exit blocked')
                continue
            try:
                self.ledger.mark_cancel_pending(row["tag"], at)
                cancel_kotak_order(self.broker, row["broker_order_id"])
                report = reconcile_kotak_tag(self.broker, row["tag"])
                self.ledger.apply_broker_report(report, at)
            except Exception as exc:
                self._error(row["tag"],f'{row["symbol"]}: entry cancellation/reconciliation pending ({type(exc).__name__})')
        futures = []
        with self.lock:
            for symbol in self.signals:
                intent = self.ledger.reserve_exit(session_date=self.session_date, symbol=symbol, at=at)
                if intent:
                    future = self.submissions.submit(self._submit_and_reconcile, intent, at)
                    self.pending.add(future)
                    future.add_done_callback(self._submission_done)
                    futures.append(future)
        if not futures:
            return []
        _, unfinished = wait(futures, timeout=30.0)
        results=[]
        for future in futures:
            if future in unfinished:
                results.append({"state":"SUBMISSION_QUEUED"})
                continue
            try:
                results.append(future.result())
            except Exception:
                results.append({"state":"SUBMISSION_UNKNOWN"})
        return results

    def close(self) -> None:
        with self.lock:self.enabled=False
        self.wait_for_submissions(timeout=30.0)
        self.submissions.shutdown(wait=False, cancel_futures=False)
