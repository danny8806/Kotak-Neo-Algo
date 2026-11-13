"""Live-only ORB candidate to Kotak order coordination.

Callers must supply authenticated Dhan/Kotak clients, today's validated signals,
same-day mappings and a verified completed opening candle before constructing.
No paper price or simulated fill path exists here.
"""

from __future__ import annotations

import threading
import time as wall_time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, Future, wait
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from itertools import repeat

from live_desk.dhan_data import IST
from live_desk.kotak_auth import KotakEntryNotSent, KotakSession
from live_desk.kotak_gateway import cancel_kotak_order, place_kotak_order, reconcile_kotak_tag
from live_desk.kotak_instruments import KotakInstrument
from live_desk.kotak_response import book_rows
from live_desk.ledger import LiveLedger
from live_desk.strategy import LivePolicy, candidate_entry


MAX_ENTRY_TICK_AGE = timedelta(seconds=5)


class LiveRuntime:
    def __init__(self, *, session_date: date, signals: list[dict],
                 dhan_ids: dict[str, str], kotak_instruments: dict[str, KotakInstrument],
                 opening_ranges: dict[str, tuple[Decimal, Decimal]],
                 policy: LivePolicy, ledger: LiveLedger, broker,
                 enabled: bool, clock=None, close_only: bool = False, feed_is_current=None):
        symbols = {row["symbol"] for row in signals}
        if (not symbols or len(symbols) != len(signals) or
            symbols != set(dhan_ids) or symbols != set(kotak_instruments) or
            symbols != set(opening_ranges)):
            raise ValueError("Every signal needs exact Dhan/Kotak mapping and a completed ORB")
        if not close_only and policy.timestamp_mode != "start":
            raise ValueError("Entry runtime requires the completed start-labelled 09:15 candle")
        if not close_only:
            self._validate_opening_ranges(opening_ranges)
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
        self.feed_is_current = feed_is_current
        self.clock = clock or (lambda: datetime.now(IST))
        self.previous: dict[str, Decimal] = {}
        self.last_tick_at: dict[str, datetime] = {}
        self._errors: dict[str,str] = {}
        self.submissions = ThreadPoolExecutor(max_workers=20, thread_name_prefix="kotak-order")
        self.pending: set[Future] = set()
        self.pending_entries: set[str] = set()
        self.reserved_symbols: set[str] = set()
        self.entry_dispatch_ms = deque(maxlen=100)

    @staticmethod
    def _validate_opening_ranges(opening_ranges):
        for high, low in opening_ranges.values():
            high, low = Decimal(str(high)), Decimal(str(low))
            if not (high.is_finite() and low.is_finite() and high >= low > 0):
                raise ValueError("Invalid completed opening range")

    def extend_signals(self, *, signals: list[dict], dhan_ids: dict[str, str],
                       kotak_instruments: dict[str, KotakInstrument],
                       opening_ranges: dict[str, tuple[Decimal, Decimal]]) -> set[str]:
        """Add new same-session candidates after validating their broker maps and ORBs."""
        incoming = {row["symbol"]: row for row in signals}
        symbols = set(incoming)
        if (not symbols or symbols != set(dhan_ids) or symbols != set(kotak_instruments)
                or symbols != set(opening_ranges)):
            raise ValueError("Every added signal needs exact Dhan/Kotak mapping and a completed ORB")
        if not self.close_only:
            self._validate_opening_ranges(opening_ranges)
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

    def reset_crossing_baselines(self):
        with self.lock:
            self.previous.clear()
            self.last_tick_at.clear()

    def on_tick(self, tick: dict) -> dict | None:
        """Process a real Dhan ticker packet after receipt; never infer a fill."""
        symbol = tick.get("symbol")
        if symbol not in self.signals:
            return None
        at = tick.get("timestamp")
        if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
            return None
        try:price = Decimal(str(tick.get("ltp")))
        except (ValueError,TypeError,ArithmeticError):return None
        if not price.is_finite() or price <= 0:
            return None
        return self.on_market_tick(symbol,price,at,self.clock(),tick.get("received_ns"),tick.get("feed_generation"))

    def on_market_tick(self, symbol, price, at, now, received_ns=None, feed_generation=None):
        """Process a supervisor-validated price with no second parsing pass."""
        local=at.astimezone(IST)
        if (local.date()!=self.session_date or not time(9,16)<=local.time()<self.policy.entry_cutoff
                or now.astimezone(IST).time()>=self.policy.entry_cutoff):
            return None
        age=now-local
        if age<timedelta(seconds=-2) or age>MAX_ENTRY_TICK_AGE:
            return None
        with self.lock:
            prior_time = self.last_tick_at.get(symbol)
            if prior_time and at < prior_time:
                return None
            previous = self.previous.get(symbol)
            self.previous[symbol] = price
            self.last_tick_at[symbol] = at
            if (not self._enabled or self.close_only or previous is None or prior_time is None
                    or now-prior_time>MAX_ENTRY_TICK_AGE):
                return None
            if symbol in self.pending_entries or symbol in self.reserved_symbols:
                return None
            side = self.signals[symbol]["side"]
            high, low = self.opening_ranges[symbol]
            quantity = candidate_entry(
                side=side, previous_price=previous, price=price,
                orb_high=high, orb_low=low, tick_at=at, policy=self.policy,
            )
            if quantity is None:
                return None
            direction = "BUY" if side == "LONG" else "SELL"
            started=received_ns if received_ns is not None else wall_time.perf_counter_ns()
            future = self.submissions.submit(
                self._reserve_and_submit,symbol,direction,quantity,price,at,started,feed_generation,
                {'previous_price':str(previous),'previous_at':prior_time.isoformat(),
                 'trigger_price':str(price),'trigger_at':at.isoformat(),
                 'orb_high':str(high),'orb_low':str(low),'feed_generation':feed_generation})
            self.pending_entries.add(symbol)
            self.pending.add(future)
            future.add_done_callback(lambda done: self._submission_done(done, symbol))
            return {"symbol": symbol, "state": "SUBMISSION_QUEUED"}

    def _reserve_and_submit(self, symbol, direction, quantity, price, at, received_ns, feed_generation,trigger_context=None):
        if not self._entry_may_send(at,feed_generation):
            return
        # PostgreSQL still owns deduplication and every exposure limit.
        intent = self.ledger.reserve_entry(
            session_date=self.session_date, symbol=symbol, side=direction,
            quantity=quantity, reference_price=price, at=at,
            max_per_side=self.policy.max_long if direction == "BUY" else self.policy.max_short,
            gross_cap=self.policy.gross_cap if self.policy.enforce_capital_limits else None,
            max_total=self.policy.max_positions,trigger_context=trigger_context)
        if intent:
            with self.lock:self.reserved_symbols.add(symbol)
            self._submit_and_reconcile(intent,at,received_ns=received_ns,feed_generation=feed_generation)

    def _submission_done(self, future: Future, symbol: str | None = None) -> None:
        with self.lock:
            self.pending.discard(future)
            if symbol is not None:
                self.pending_entries.discard(symbol)
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

    def _entry_may_send(self, at: datetime, feed_generation=None) -> bool:
        now = self.clock().astimezone(IST)
        return (self.enabled and not self.close_only and now.date() == self.session_date
                and time(9,16) <= now.time() < self.policy.entry_cutoff
                and timedelta(seconds=-2) <= now - at.astimezone(IST) <= MAX_ENTRY_TICK_AGE
                and (self.feed_is_current is None or self.feed_is_current(feed_generation)))

    def _submit_and_reconcile(self, intent: dict, at: datetime, *, received_ns=None,
                              feed_generation=None) -> dict:
        tag = intent["tag"]
        is_entry = intent.get("phase", "ENTRY") == "ENTRY"
        if is_entry and not self._entry_may_send(at,feed_generation):
            self.ledger.mark_not_submitted(tag, self.clock())
            return {"tag": tag, "state": "ABORTED"}
        # Persist before network I/O, without holding the tick reader's lock.
        if is_entry:
            self.ledger.mark_submission_unknown(tag, self.clock())
        elif not self.ledger.claim_reserved_exit(tag,self.clock()):
            return {'tag':tag,'state':'ALREADY_CLAIMED'}
        if is_entry and not self._entry_may_send(at,feed_generation):
            self.ledger.mark_not_submitted(tag, self.clock(), committed_for_send=True)
            return {"tag": tag, "state": "ABORTED"}
        result=None
        dispatch_ns=None

        def before_send():
            nonlocal dispatch_ns
            if not self._entry_may_send(at,feed_generation):
                return False
            dispatch_ns=wall_time.perf_counter_ns()
            return True

        try:
            guard={}
            if is_entry and isinstance(self.broker,KotakSession):
                guard["before_send"]=before_send
            elif is_entry:
                if not before_send():
                    raise KotakEntryNotSent("Entry expired before dispatch")
            result = place_kotak_order(self.broker, self.kotak_instruments[intent["symbol"]],
                                       intent["side"], intent["quantity"], tag, **guard)
        except KotakEntryNotSent:
            self.ledger.mark_not_submitted(tag, self.clock(), committed_for_send=True)
            return {"tag": tag, "state": "ABORTED"}
        except Exception as exc:
            # Even a timeout can follow broker acceptance. Only retry READS.
            self._error(tag,f"{intent['symbol']}: {type(exc).__name__}; reconcile tag {tag}")
        finally:
            if dispatch_ns is not None and received_ns is not None:
                elapsed=(dispatch_ns-received_ns)/1_000_000
                with self.lock:self.entry_dispatch_ms.append(elapsed)
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

    def dispatch_latency(self):
        with self.lock: samples=sorted(self.entry_dispatch_ms)
        if not samples:
            return {"samples":0,"p50_ms":None,"p95_ms":None,"max_ms":None}
        def percentile(percent):
            return round(samples[(len(samples)*percent+99)//100-1],3)
        return {"samples":len(samples),"p50_ms":percentile(50),"p95_ms":percentile(95),
                "max_ms":round(samples[-1],3)}

    def reconcile_pending(self, at: datetime, *, books=None, observed_at=None) -> None:
        with self.lock:
            if self.pending:
                return
        pending = [
            row for row in self.ledger.snapshot(self.session_date)
            if row["state"] in {"SUBMISSION_UNKNOWN", "ACKNOWLEDGED", "PARTIAL", "CANCEL_PENDING"}
            and (observed_at is None or row["updated_at"] <= observed_at)
        ]
        if books is None and len(pending) > 1:
            try:
                books = (book_rows(self.broker.order_report(), "orders"),
                         book_rows(self.broker.trade_report(), "trades"))
            except Exception:
                for row in pending:
                    self._error(row["tag"], f"{row['symbol']}: broker reconciliation pending")
                return
        for row in pending:
            try:
                if books is None:
                    report = reconcile_kotak_tag(self.broker, row["tag"])
                else:
                    report = reconcile_kotak_tag(self.broker, row["tag"], books=books)
                self.ledger.apply_broker_report(report, at)
                self._clear_error(row["tag"])
            except Exception:
                self._error(row["tag"], f"{row['symbol']}: broker reconciliation pending")

    def exit_due(self, at: datetime, *, read_positions=None) -> list[dict]:
        if at.astimezone(IST).date() != self.session_date or at.astimezone(IST).time() < self.policy.exit_time:
            return []
        self.enabled = False
        # Each symbol must have a terminal entry and exact broker quantity.
        # An in-flight order on another symbol cannot delay a confirmed exit.
        self.reconcile_pending(at)
        self.cancel_working_entries(at)
        rows = self.ledger.snapshot(self.session_date)
        exits = {row["symbol"]: row for row in rows if row["phase"] == "EXIT"}
        candidates = []
        for entry in rows:
            if entry["phase"] != "ENTRY" or entry["state"] not in {"FILLED", "PARTIAL_CANCELLED"}:
                continue
            symbol = entry["symbol"]
            prior = exits.get(symbol)
            if prior:
                if prior["state"] in {"REJECTED", "PARTIAL_CANCELLED"} and prior["filled_quantity"] < entry["filled_quantity"]:
                    self._error(f"EOD:{symbol}", f"{symbol}: exit ended with shares remaining. Manual action required.")
                if prior['state']!='RESERVED':
                    continue
                if (prior['quantity']!=entry['filled_quantity']
                        or prior['side']!=('SELL' if entry['side']=='BUY' else 'BUY') or prior.get('broker_order_id')
                        or prior['filled_quantity']):
                    self._error(f'EOD:{symbol}',f'{symbol}: reserved exit conflicts with confirmed fills. Manual action required.')
                    continue
            if entry["filled_quantity"] > 0 and symbol in self.signals:
                candidates.append(entry)
        # Read positions after cancellations so late entry fills are included.
        positions = read_positions() if candidates and read_positions is not None else None
        futures = []
        with self.lock:
            for entry in candidates:
                symbol = entry["symbol"]
                prior=exits.get(symbol)
                if prior and positions is None:
                    self._error(f'EOD:{symbol}',f'{symbol}: reserved exit recovery requires fresh broker positions.')
                    continue
                if positions is not None:
                    matches = [row for row in positions if row["symbol"] == symbol and row["product"] == "MIS"]
                    expected = entry["filled_quantity"] if entry["side"] == "BUY" else -entry["filled_quantity"]
                    if len(matches) != 1 or matches[0]["net_quantity"] != expected:
                        self._error(f"EOD:{symbol}", f"{symbol}: Kotak MIS quantity does not match bot fills. Manual action required.")
                        continue
                    self._clear_error(f"EOD:{symbol}")
                intent = ({'tag':prior['tag'],'symbol':symbol,'phase':'EXIT','side':prior['side'],'quantity':prior['quantity']}
                          if prior else self.ledger.reserve_exit(session_date=self.session_date, symbol=symbol, at=at))
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

    def cancel_working_entries(self, at: datetime) -> None:
        local = at.astimezone(IST)
        if local.date() != self.session_date or local.time() < self.policy.entry_cutoff:
            return
        self.enabled = False
        self.ledger.abort_unsent_entries(self.session_date,at)
        # A durable transition prevents a second cancellation after a timeout.
        working_entries = [
            row for row in self.ledger.snapshot(self.session_date)
            if row["phase"] == "ENTRY" and row["state"] in {"ACKNOWLEDGED", "PARTIAL"}
        ]
        if working_entries:
            list(self.submissions.map(self._cancel_entry, working_entries, repeat(at)))

    def _cancel_entry(self, row: dict, at: datetime) -> None:
        tag = row["tag"]
        if not row.get("broker_order_id"):
            self._error(tag, f'{row["symbol"]}: missing order ID; EOD exit blocked')
            return
        try:
            self.ledger.mark_cancel_pending(tag, at)
            cancel_kotak_order(self.broker, row["broker_order_id"])
            report = reconcile_kotak_tag(self.broker, tag)
            self.ledger.apply_broker_report(report, at)
        except Exception as exc:
            self._error(tag, f'{row["symbol"]}: entry cancellation/reconciliation pending ({type(exc).__name__})')

    def close(self) -> None:
        with self.lock:self.enabled=False
        self.wait_for_submissions(timeout=30.0)
        self.submissions.shutdown(wait=False, cancel_futures=False)
