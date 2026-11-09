"""Live-only Kotak Neo session supervisor.

REAL broker reports are authoritative for fills, orders, and net quantities.
Never imports a prediction model or simulates trade fills. Actual order
submission requires authenticated broker access, validated research,
live Dhan prices, and an explicit per-session arming operation.
"""
from __future__ import annotations
import hashlib, json, logging, os, threading
from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from sqlalchemy import select, text
from bridge.app import reports, signal_batches
from scripts.validate_signals import validate
from live_desk.dhan_data import DhanDataClient, DhanTokenSupervisor, IST
from live_desk.dhan_instruments import fetch_dhan_symbols
from live_desk.kotak_gateway import KotakCredentials, login_kotak, reconcile_kotak_tag
from live_desk.kotak_auth import KotakSession
from live_desk.kotak_instruments import lookup_kotak_equity
from live_desk.kotak_response import book_rows
from live_desk.ledger import LiveLedger
from live_desk.strategy import load_policy
from live_desk.orb import opening_range
from live_desk.runtime import LiveRuntime
from live_desk.ticker import LiveTicker
from live_desk import broker_view
from live_desk.analytics import build_trade_journal

LOG=logging.getLogger("news-kotak-live")
FEED_WARMUP=time(9,14)
WATCHLIST_LOCK=time(9,15)
START=time(9,16)
BOOK_REFRESH=timedelta(seconds=5)
BOOK_EXPIRY=timedelta(seconds=20)
PRICE_EXPIRY=timedelta(seconds=8)

class LiveSupervisor:
    def __init__(self,signal_engine,*,ledger_path=None,policy_path=None,clock=None,
                 dhan_factory=None,kotak_factory=None,master_fetch=None,ticker_factory=None,
                 allowed_symbols=None):
        self.database=signal_engine
        self.allowed_symbols=set(allowed_symbols) if allowed_symbols is not None else None
        self.clock=clock or (lambda:datetime.now(IST))
        self.policy=load_policy(Path(policy_path or Path(__file__).with_name("policy.json")))
        ledger_database = ledger_path or os.getenv("LIVE_DATABASE_URL") or os.getenv("SIGNAL_DATABASE_URL")
        if not ledger_database:
            raise RuntimeError("Set LIVE_DATABASE_URL to a PostgreSQL connection URL")
        if not str(ledger_database).startswith(("postgresql://", "postgresql+psycopg://")):
            raise RuntimeError("Live order ledger must use PostgreSQL")
        self.ledger=LiveLedger(ledger_database)
        stored=self.ledger.read_settings()
        if stored:
            self.policy=replace(self.policy, entry_cutoff=time.fromisoformat(stored["entry_cutoff"]),
                exit_time=time.fromisoformat(stored["exit_time"]),
                max_long=int(stored["max_long"]), max_short=int(stored["max_short"]),
                max_positions=int(stored["max_positions"]),
                per_position=Decimal(stored["per_position"]), gross_cap=Decimal(stored["gross_cap"]))
        self.dhan_factory=dhan_factory or DhanDataClient.from_environment
        self.dhan_auth=None
        self.kotak_factory=kotak_factory or (lambda:login_kotak(KotakCredentials.from_environment()))
        self.kotak_from_environment=kotak_factory is None
        self.kotak_auth=(KotakSession(self.kotak_factory,clock=self.clock)
                         if self.kotak_from_environment else None)
        self.master_fetch=master_fetch or fetch_dhan_symbols
        self.ticker_factory=ticker_factory or LiveTicker
        self.allowed=os.getenv("LIVE_ORDERS_ALLOWED","").strip()=="YES"
        self.arm_secret=os.getenv("LIVE_ARM_TOTP_SECRET","")
        self.vix_security_id=os.getenv("DHAN_VIX_SECURITY_ID","21").strip()
        self.lock=threading.RLock()
        self.armed=False
        self.session_authorized=False
        self.status="NEEDS_SETUP"
        self.error=None
        self.blocked_stage=None
        self.prior_session_recovery=0
        self.report=None
        self.report_sha=None
        self.entry_watchlist=False
        self.research_report=None
        self.research_dhan_ids={}
        self.session_date=None
        self.dhan=None
        self.broker=None
        self.ticker=None
        self.ticker_symbols=set()
        self.position_ticker=None
        self.position_dhan_ids={}
        self.position_feed_error=None
        self.position_feed_retry_at=None
        self.runtime=None
        self.dhan_ids={}
        self.kotak_ids={}
        self.opening_ranges={}
        self._pending_opening_ranges={}
        self.orb_events={}
        self._orb_previous={}
        self._orb_last_tick={}
        self.quote_data={}
        self.last_quote_at=None
        self.last_book_at=None
        self.last_ohlc_at=None
        self.last_vix_attempt=None
        self.vix_error=None
        self.broker_positions=[]
        self.broker_orders=[]
        self.broker_fills=[]
        self.available_cash=None
        self.completed_exit=False
        self.thread=None
        self.stop_event=threading.Event()
        self.refresh_after=None

    def now(self):
        return self.clock().astimezone(IST)

    def update_settings(self, payload):
        with self.lock:
            if self.armed:
                raise ValueError("Disarm live orders before changing settings")
            try:
                proposed=replace(self.policy,
                    entry_cutoff=time.fromisoformat(str(payload["entry_cutoff"])),
                    exit_time=time.fromisoformat(str(payload["exit_time"])),
                    max_long=int(payload["max_long"]), max_short=int(payload["max_short"]),
                    max_positions=int(payload["max_positions"]),
                    per_position=Decimal(str(payload["per_position"])),
                    gross_cap=Decimal(str(payload["gross_cap"])))
            except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
                raise ValueError("Settings have invalid or missing values") from exc
            if not time(9,16)<proposed.entry_cutoff<proposed.exit_time<=time(15,30):
                raise ValueError("Entry cutoff must follow 09:16 and precede the 15:30 exit limit")
            if (not 1<=proposed.max_long<=5 or not 1<=proposed.max_short<=5 or
                not 1<=proposed.max_positions<=10 or
                proposed.max_positions>proposed.max_long+proposed.max_short):
                raise ValueError("Position limits must fit within up to 5 LONG and 5 SHORT signals")
            if (not proposed.per_position.is_finite() or not proposed.gross_cap.is_finite() or
                proposed.per_position<Decimal("1000") or proposed.per_position>Decimal("20000000") or
                proposed.gross_cap<proposed.per_position or proposed.gross_cap>Decimal("100000000")):
                raise ValueError("Position amount and gross cap are outside supported risk limits")
            values={"entry_cutoff":proposed.entry_cutoff.isoformat(),"exit_time":proposed.exit_time.isoformat(),
                "max_long":str(proposed.max_long),"max_short":str(proposed.max_short),
                "max_positions":str(proposed.max_positions),"per_position":str(proposed.per_position),
                "gross_cap":str(proposed.gross_cap)}
            self.ledger.write_settings(values,self.now())
            self.policy=proposed
            if self.runtime:self.runtime.policy=proposed
            return proposed

    def today_report(self):
        now=self.now()
        lock_at=datetime.combine(now.date(), WATCHLIST_LOCK, tzinfo=IST)
        with self.database.begin() as conn:
            if now >= lock_at:
                # Wait for any pre-open ingest transaction to commit before freezing.
                conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('signal-ingest'), hashtext(:day))"),
                             {"day": now.date().isoformat()})
            record=conn.execute(select(reports).where(
                reports.c.session_date==now.date().isoformat())).mappings().first()
            if not record:
                return None, None, False, None
            aggregate=json.loads(record["original_json"])
            data=aggregate
            digest=record["sha256"]
            eligible=record["received_at"] < lock_at and data["status"]=="READY"
            if not eligible and now.weekday()<5:
                batches=conn.execute(select(signal_batches.c.original_json).where(
                    signal_batches.c.session_date==now.date().isoformat(),
                    signal_batches.c.received_at < lock_at,
                ).order_by(signal_batches.c.id)).scalars()
                signals={}
                ready_batches=0
                for raw in batches:
                    batch=json.loads(raw)
                    if batch["status"]!="READY":
                        continue
                    ready_batches+=1
                    for item in batch["signals"]:
                        prior=signals.setdefault(item["symbol"], item)
                        if prior["side"]!=item["side"]:
                            raise ValueError("Conflicting direction in pre-open watchlist")
                    data=batch
                if signals:
                    if ready_batches>1:
                        counts={"LONG":0,"SHORT":0}
                        for item in signals.values():
                            counts[item["side"]]+=1
                            item["rank"]=counts[item["side"]]
                        data={**data,"signals":list(signals.values())}
                    raw=json.dumps(data,sort_keys=True,separators=(",",":"),ensure_ascii=False)
                    digest=hashlib.sha256(raw.encode()).hexdigest()
                    eligible=True
        validate(data,expected_date=now.date().isoformat(),
                 allowed_symbols=self.allowed_symbols)
        return data,digest,eligible and now.weekday()<5,aggregate

    def _new_day(self,day):
        with self.lock:
            self.armed=False
            self.session_authorized=False
            if self.runtime:
                self.runtime.enabled=False
                self.runtime.close()
            if self.ticker:self.ticker.stop()
            if self.position_ticker:self.position_ticker.stop()
            self.ticker=None
            self.ticker_symbols=set()
            self.position_ticker=None
            self.position_dhan_ids={}
            self.position_feed_retry_at=None
            self.position_feed_error=None
            self.runtime=None
            self.broker=None
            self.report=None
            self.report_sha=None
            self.entry_watchlist=False
            self.research_report=None
            self.research_dhan_ids={}
            self.dhan_ids={}
            self.kotak_ids={}
            self.opening_ranges={}
            self._pending_opening_ranges={}
            self.orb_events={}
            self._orb_previous={}
            self._orb_last_tick={}
            self.quote_data={}
            self.last_book_at=None
            self.last_quote_at=None
            self.last_ohlc_at=None
            self.last_vix_attempt=None
            self.vix_error=None
            self.broker_orders=[]
            self.broker_positions=[]
            self.broker_fills=[]
            self.available_cash=None
            self.completed_exit=False
            self.status="WAITING_FOR_SIGNALS"
            self.error=None
            self.session_date=day

    def _load_report(self):
        report,sha,eligible,research=self.today_report()
        if not report or report["status"]!="READY":
            with self.lock:
                self.status="NO_ELIGIBLE_SIGNALS"
                self.report=report
                self.research_report=research
                self.entry_watchlist=False
            return False
        with self.lock:
            if self.entry_watchlist and self.report:
                prior={row["symbol"]:row["side"] for row in self.report.get("signals",[])}
                current={row["symbol"]:row["side"] for row in report.get("signals",[])}
                if any(current.get(symbol)!=side for symbol,side in prior.items()):
                    raise RuntimeError("Session signals were removed or changed direction")
            self.report=report
            self.research_report=research
            self.report_sha=sha
            self.entry_watchlist=eligible
            self.status="SIGNALS_VERIFIED" if eligible else "LATE_RESEARCH_ONLY"
        return True

    def _authenticate(self):
        if self.broker is None:
            if self.kotak_auth:
                if not self.kotak_auth.ensure_authenticated():
                    raise RuntimeError("Kotak authentication unavailable")
                self.broker=self.kotak_auth
            else:self.broker=self.kotak_factory()
        if self.dhan is None:
            self._ensure_dhan_auth()
        if not self.dhan or not self.dhan.token:
            raise RuntimeError("Dhan data authentication is not ready")

    def _ensure_dhan_auth(self):
        if self.dhan is not None:return
        try:
            client=self.dhan_factory()
        except ValueError:
            return
        self.dhan=client
        self.dhan_auth=DhanTokenSupervisor(client,clock=self.clock)
        self.dhan_auth.start()

    def _refresh_vix_context(self,now):
        if (not self.dhan or not self.dhan_auth or
            self.dhan_auth.snapshot().get("state")!="AUTHENTICATED" or
            now.weekday()>=5 or not time(9,15)<=now.time()<time(15,30)):
            return
        if self.last_vix_attempt and now-self.last_vix_attempt<timedelta(seconds=20):return
        self.last_vix_attempt=now
        try:
            result=self.dhan.get_ohlc({"INDIAVIX":self.vix_security_id},exchange_segment="IDX_I")
            with self.lock:
                quote=result.get("INDIAVIX")
                if quote:
                    self.quote_data["INDIAVIX"]={**self.quote_data.get("INDIAVIX",{}),
                        **quote,"ohlc_at":now.isoformat()}
                self.vix_error=None
        except Exception as exc:
            self.vix_error=type(exc).__name__
            LOG.warning("Dhan India VIX quote unavailable (%s)",type(exc).__name__)

    def _map_instruments(self,now):
        wanted={item["symbol"] for item in self.report["signals"]}
        dhan_ids=dict(self.dhan_ids)
        missing=wanted-set(dhan_ids)
        if missing:
            dhan=self.master_fetch(missing,now=now)
            if dhan.session_date!=now.date().isoformat() or set(dhan.security_ids)!=missing:
                raise RuntimeError("NSE EQ Dhan symbol master incomplete")
            dhan_ids.update(dhan.security_ids)
        if len(set(dhan_ids.values())) != len(dhan_ids):
            raise RuntimeError("Duplicate Dhan security IDs in the session watchlist")
        kotak=dict(self.kotak_ids)
        if self.broker is not None:
            for symbol in sorted(wanted-set(kotak)):
                kotak[symbol]=lookup_kotak_equity(self.broker,symbol)
        with self.lock:
            self.dhan_ids=dhan_ids
            self.kotak_ids=kotak

    def _map_late_research(self,now):
        if not self.entry_watchlist or not self.research_report:
            return
        missing={row["symbol"] for row in self.research_report["signals"]}-self.dhan_ids.keys()
        missing-=self.research_dhan_ids.keys()
        if not missing:
            return
        try:
            master=self.master_fetch(missing,now=now)
            ids=master.security_ids
            combined={**self.dhan_ids,**self.research_dhan_ids,**ids}
            if (master.session_date!=now.date().isoformat() or set(ids)!=missing or
                    len(set(combined.values()))!=len(combined)):
                raise RuntimeError("Late research Dhan mappings are incomplete or ambiguous")
        except Exception as exc:
            LOG.warning("Late research Dhan mapping unavailable (%s)",type(exc).__name__)
            return
        with self.lock:
            self.research_dhan_ids.update(ids)

    def _build_runtime(self):
        now=self.now()
        if (not self.entry_watchlist or now.weekday()>=5 or
                not FEED_WARMUP<=now.time()<self.policy.entry_cutoff):
            return
        if self.runtime:
            self.status="MONITORING_LIVE"
            return
        if self.ticker is None:
            ticker=self.ticker_factory(self.dhan,self.dhan_ids,self.on_tick)
            ticker.start()
            self.ticker=ticker
            self.ticker_symbols=set(self.dhan_ids)
        else:
            missing=set(self.dhan_ids)-self.ticker_symbols
            if missing:
                self.ticker.add_symbols({symbol:self.dhan_ids[symbol] for symbol in missing})
                self.ticker_symbols.update(missing)
        if now.time()<START:
            self.status="WARMING_DHAN_FEED"
            return
        if self.policy.timestamp_mode not in {"start","end"}:
            raise RuntimeError("Dhan 09:15 candle timestamp mode not yet verified")

        pending=self._pending_opening_ranges
        for symbol,security_id in self.dhan_ids.items():
            if symbol in pending:
                continue
            try:
                pending[symbol]=opening_range(self.dhan,security_id,now.date(),
                                              timestamp_mode=self.policy.timestamp_mode)
            except Exception as exc:
                self.status="WAITING_FOR_OPENING_CANDLE"
                LOG.info("Dhan opening candle unavailable for %s (%s)",symbol,type(exc).__name__)
                return
        opening=dict(pending)
        runtime=None
        if self.broker is not None:
            runtime=LiveRuntime(
                session_date=now.date(),signals=self.report["signals"],dhan_ids=self.dhan_ids,
                kotak_instruments=self.kotak_ids,opening_ranges=opening,
                policy=self.policy,ledger=self.ledger,broker=self.broker,
                enabled=False,clock=self.now)
        with self.lock:
            received_at=self.now()
            for symbol in opening:
                quote=self.quote_data.get(symbol,{})
                if "tick_at" not in quote:
                    continue
                tick_at=datetime.fromisoformat(quote["tick_at"])
                if not timedelta(0)<=received_at-tick_at<=timedelta(seconds=5):
                    continue
                price=Decimal(str(quote["ltp"]))
                if symbol not in self.opening_ranges:
                    self._orb_previous[symbol]=price
                    self._orb_last_tick[symbol]=tick_at
                if runtime:
                    runtime.previous[symbol]=price
                    runtime.last_tick_at[symbol]=tick_at
            self.opening_ranges=opening
            self.runtime=runtime
            self.status="MONITORING_LIVE" if runtime else "MONITORING_DHAN_ORB_KOTAK_PENDING"

    def _broker_cash(self):
        response=self.broker.limits()  # kotakneoapi v3: all segments, no arguments
        if not isinstance(response,dict) or str(response.get("stat","")).lower()!="ok":
            raise RuntimeError("Kotak cash/margin limits unavailable")
        amount=Decimal(str(response.get("Net","NaN")))
        if not amount.is_finite():
            raise RuntimeError("Kotak available cash not verifiable")
        return amount

    def refresh_broker(self):
        if self.broker is None:return
        now=self.now()
        with self.lock:
            # Never calculate brokerage MTM from stale Dhan prices.
            current_quotes={}
            for symbol,q in self.quote_data.items():
                ts=q.get("tick_at")
                if not ts:continue
                try:age=now-datetime.fromisoformat(ts).astimezone(IST)
                except (ValueError,TypeError):continue
                if timedelta(0)<=age<=PRICE_EXPIRY:
                    current_quotes[symbol]=dict(q)
        position=broker_view.positions(self.broker,current_quotes)
        observed_at = self.now()
        order_rows = book_rows(self.broker.order_report(), "orders")
        trade_rows = book_rows(self.broker.trade_report(), "trades")
        books = (order_rows, trade_rows)
        orders=broker_view.orders(self.broker, rows=order_rows)
        fills=broker_view.fills(self.broker, rows=trade_rows)
        self.ledger.record_broker_fills(fills, now)
        cash=self._broker_cash()
        prior_recovery=self.ledger.prior_session_recovery_count(now.date())
        now=self.now()
        if self.runtime:
            self.runtime.reconcile_pending(now, books=books, observed_at=observed_at)
        else:
            # Unknown outcomes survive restarts even without today's research.
            for row in self.ledger.snapshot(now.date()):
                if row["updated_at"] <= observed_at and row["state"] in {"SUBMISSION_UNKNOWN","ACKNOWLEDGED","PARTIAL","CANCEL_PENDING"}:
                    try:self.ledger.apply_broker_report(reconcile_kotak_tag(self.broker,row["tag"],books=books),now)
                    except Exception:LOG.warning("Persisted Kotak intent still requires reconciliation")
        with self.lock:
            self.broker_positions=position
            self.broker_orders=orders
            self.broker_fills=fills
            self.available_cash=str(cash)
            self.prior_session_recovery=prior_recovery
            self.last_book_at=now
            if self.status!="MONITORING_LIVE":self.status="BROKER_CONNECTED"
        self._refresh_position_feed(now)

    def _refresh_position_feed(self,now):
        """Read-only ticks for account positions outside the signal watchlist."""
        symbols={row["symbol"] for row in self.broker_positions if row["net_quantity"]}
        symbols-=set(self.dhan_ids)
        if not time(9,15)<=now.time()<time(15,30) or now.weekday()>=5:symbols=set()
        if not symbols:
            if self.position_ticker:self.position_ticker.stop()
            self.position_ticker=None;self.position_dhan_ids={}
            return
        if not self.dhan or not getattr(self.dhan,"token",""):return
        if symbols==set(self.position_dhan_ids) and self.position_ticker:return
        if self.position_feed_retry_at and now<self.position_feed_retry_at:return
        try:
            master=self.master_fetch(symbols,now=now)
            if master.session_date!=now.date().isoformat() or set(master.security_ids)!=symbols:
                raise RuntimeError("Position Dhan mappings incomplete")
            if self.position_ticker:self.position_ticker.stop()
            self.position_dhan_ids=master.security_ids
            self.position_ticker=self.ticker_factory(self.dhan,self.position_dhan_ids,self.on_position_tick)
            self.position_ticker.start()
            self.position_feed_error=None
            self.position_feed_retry_at=None
        except Exception as exc:
            self.position_feed_error="Position quotes unavailable ("+type(exc).__name__+")"
            self.position_feed_retry_at=now+timedelta(seconds=60)

    def on_position_tick(self,tick):
        """Account valuation only; this callback cannot submit an order."""
        now=self.now();ts=tick.get("timestamp");symbol=tick.get("symbol")
        if symbol not in self.position_dhan_ids or not isinstance(ts,datetime) or ts.utcoffset() is None:return
        if not timedelta(seconds=-2)<=now-ts.astimezone(IST)<=PRICE_EXPIRY:return
        try:value=Decimal(str(tick.get("ltp")))
        except (ValueError,TypeError,ArithmeticError):return
        if not value.is_finite() or value<=0:return
        with self.lock:
            old=self.quote_data.get(symbol,{})
            if old.get("tick_at") and ts.astimezone(IST)<datetime.fromisoformat(old["tick_at"]):return
            self.quote_data[symbol]={**old,"ltp":float(value),"tick_at":ts.astimezone(IST).isoformat()}

    def poll_ohlc(self):
        now=self.now()
        if not self.dhan or not self.dhan_ids or now.weekday()>=5:
            return
        if not time(9,15)<=now.time()<time(15,30):return
        if self.last_ohlc_at and now-self.last_ohlc_at<timedelta(seconds=20):
            return
        result=self.dhan.get_ohlc(self.dhan_ids)
        with self.lock:
            for symbol,quote in result.items():
                old=self.quote_data.get(symbol,{})
                self.quote_data[symbol]={**old,**{key:value for key,value in quote.items() if key!="ltp"}}
            self.last_ohlc_at=now

    def on_tick(self,tick):
        now=self.now()
        ts=tick.get("timestamp")
        if not isinstance(ts,datetime) or ts.tzinfo is None:return
        delta=now-ts.astimezone(IST)
        if delta<timedelta(seconds=-2) or delta>PRICE_EXPIRY:
            return
        symbol=str(tick.get("symbol") or "")
        if symbol not in self.dhan_ids:return
        try:value=Decimal(str(tick.get("ltp")))
        except (ValueError,TypeError,ArithmeticError):return
        if not value.is_finite() or value<=0:return
        with self.lock:
            previous=self.quote_data.get(symbol,{})
            if previous.get("tick_at") and ts.astimezone(IST)<datetime.fromisoformat(previous["tick_at"]):return
            self.quote_data[symbol]={**previous,"ltp":float(value),
                                     "tick_at":ts.astimezone(IST).isoformat()}
            self.last_quote_at=now
            ready=self.armed and self._health_check_locked(now)
            if self.runtime:self.runtime.enabled=ready
            runtime=self.runtime
        self._observe_orb_crossing(symbol,value,ts,now)
        if runtime:
            # Preserve the previous price while monitoring so the first tick
            # after arming can qualify. Runtime.enabled independently gates orders.
            runtime.on_tick(tick)
        # Position exits follow the configured 15:15 IST square-off policy.
        # Loss limits disarm new entries; they do not create an implicit stop.

    def _observe_orb_crossing(self,symbol,price,tick_at,now):
        local=tick_at.astimezone(IST)
        if (local.date().isoformat()!=self.session_date or local.time()<time(9,16) or
            local.time()>=self.policy.entry_cutoff or symbol not in self.opening_ranges):
            return
        with self.lock:
            previous_at=self._orb_last_tick.get(symbol)
            if previous_at and tick_at<previous_at:
                return
            previous=self._orb_previous.get(symbol)
            self._orb_previous[symbol]=price
            self._orb_last_tick[symbol]=tick_at
            if previous is None or symbol in self.orb_events:
                return
            signal=self.report and next((x for x in self.report.get("signals",[])
                                         if x["symbol"]==symbol),None)
            if not signal:
                return
            high,low=self.opening_ranges[symbol]
            side=signal["side"]
            crossed=(side=="LONG" and previous<=high and price>high) or (
                    side=="SHORT" and previous>=low and price<low)
            if not crossed:
                return
            self.orb_events[symbol]={
                "symbol":symbol,"side":side,"status":"ORB_BREAKOUT_OBSERVED",
                "triggered_at":local.isoformat(timespec="seconds"),
                "trigger_price":str(price),"orb_high":str(high),"orb_low":str(low),
                "execution":"MONITOR_ONLY" if not self.runtime or not self.armed else "ARMED_RUNTIME",
            }

    def _health_check_locked(self,now):
        return not self._readiness_blockers_locked(now)

    def _readiness_blockers_locked(self,now):
        blocked=[]
        if not self.allowed:blocked.append("SERVER_ORDERS_DISABLED")
        if not self.entry_watchlist:blocked.append("CURRENT_SESSION_SIGNALS")
        if not self.broker or (self.kotak_auth and self.kotak_auth.snapshot()["state"]!="AUTHENTICATED"):
            blocked.append("KOTAK_AUTHENTICATION")
        if not self.dhan or not getattr(self.dhan,"token","") or (
                self.dhan_auth and self.dhan_auth.snapshot()["state"]!="AUTHENTICATED"):
            blocked.append("DHAN_AUTHENTICATION")
        wanted={row["symbol"] for row in self.report["signals"]} if self.report and self.report["status"]=="READY" else set()
        if not wanted or set(self.dhan_ids)!=wanted or set(self.kotak_ids)!=wanted:
            blocked.append("INSTRUMENT_MAPPING")
        if not wanted or set(self.opening_ranges)!=wanted:blocked.append("OPENING_RANGE")
        ticker=self.ticker.snapshot() if self.ticker else {}
        if ticker.get("state")!="CONNECTED" or ticker.get("error"):blocked.append("MARKET_FEED")
        if self.last_quote_at is None or not timedelta(0)<=now-self.last_quote_at<=PRICE_EXPIRY:
            blocked.append("FRESH_QUOTES")
        if self.last_book_at is None or not timedelta(0)<=now-self.last_book_at<=BOOK_EXPIRY:
            blocked.append("BROKER_BOOKS")
        if not START<=now.time()<self.policy.entry_cutoff or now.weekday()>=5:
            blocked.append("MARKET_ENTRY_WINDOW")
        if not self.available_cash or Decimal(self.available_cash)<self.policy.gross_cap:blocked.append("FUNDS")
        if not self.runtime or self.runtime.errors:blocked.append("EXECUTION_RUNTIME")
        if self.error:blocked.append("SUPERVISOR_ERROR")
        if self.prior_session_recovery:blocked.append("PRIOR_SESSION_RECOVERY")
        return blocked

    def ready(self):
        with self.lock:return self._health_check_locked(self.now())

    def arm(self):
        now=self.now()
        with self.lock:
            if not self._health_check_locked(now):
                raise RuntimeError("Live orders cannot arm: feeds, broker, funds or session not verified")
            if any(x["net_quantity"]!=0 and x["symbol"] in self.dhan_ids
                   for x in self.broker_positions):
                raise RuntimeError("Existing Kotak holdings/positions overlap selected symbols")
            unresolved={"RESERVED","SUBMISSION_UNKNOWN","ACKNOWLEDGED","PARTIAL","CANCEL_PENDING"}
            if any(x["state"] in unresolved for x in self.ledger.snapshot(now.date())):
                raise RuntimeError("Unreconciled Kotak intent exists")
            self.armed=True
            self.session_authorized=True
            self.runtime.enabled=True
            self.status="LIVE_ARMED"
        return {"armed":True,"day":now.date().isoformat()}

    def disarm(self,reason="Operator disarmed new orders"):
        with self.lock:
            self.armed=False
            if self.runtime:self.runtime.enabled=False
            self.status="DISARMED"
        LOG.warning("%s",reason)

    def _manage_authorized_exits(self,now):
        """Square off only durable bot entries after exact broker position reconciliation.

        A process restart clears the in-memory arm flag; the persisted entry intent
        plus matching live position is sufficient authorization for EOD recovery.
        """
        if (not self.allowed or now.time()<self.policy.exit_time or now.weekday()>=5):
            return
        if self.armed:
            self.disarm("End-of-day square-off started")
        intent_rows=self.ledger.snapshot(now.date())
        exited={r["symbol"]:r["filled_quantity"] for r in intent_rows if r["phase"]=="EXIT"}
        entries=[r for r in intent_rows if r["phase"]=="ENTRY" and (
            r["filled_quantity"]>exited.get(r["symbol"],0) or
            r["state"] in {"SUBMISSION_UNKNOWN","ACKNOWLEDGED","PARTIAL","CANCEL_PENDING"})]
        if not entries or self.broker is None:
            return
        if self.runtime is None:
            # Recover a close-only runtime from persisted bot intents. Dummy Dhan
            # IDs/ORB values are inert because enabled=False and EOD calls exit_due.
            rows=entries
            symbols=[r["symbol"] for r in rows]
            kotak={s:lookup_kotak_equity(self.broker,s) for s in symbols}
            self.runtime=LiveRuntime(session_date=now.date(),
                signals=[{"symbol":r["symbol"],"side":"LONG" if r["side"]=="BUY" else "SHORT"} for r in rows],
                dhan_ids={s:"0" for s in symbols},kotak_instruments=kotak,
                opening_ranges={s:(Decimal("1"),Decimal("1")) for s in symbols},
                policy=self.policy,ledger=self.ledger,broker=self.broker,enabled=False,
                clock=self.clock,close_only=True)
        self.runtime.exit_due(now, read_positions=lambda: broker_view.positions(self.broker))

    def cycle(self):
        now=self.now()
        if self.session_date!=now.date().isoformat():
            self._new_day(now.date().isoformat())
        # Monitor the actual Kotak brokerage account even when news is missing.
        # No signals must NEVER be interpreted as fabricated positions.
        kotak_missing=False
        if self.kotak_auth:
            try:KotakCredentials.from_environment()
            except ValueError:kotak_missing=True
            if not kotak_missing:
                if self.kotak_auth.refresh_due() and self.armed:
                    self.disarm("Kotak session refresh; re-arming required")
                self.blocked_stage="KOTAK_AUTHENTICATION"
                authenticated=self.kotak_auth.ensure_authenticated()
                self.broker=self.kotak_auth if authenticated else None
                if not authenticated:
                    self.last_book_at=None
                    self.status="KOTAK_AUTHENTICATING"
            else:
                self.broker=None
                self.status="NEEDS_KOTAK_CREDENTIALS"
        elif self.broker is None:
            self.broker=self.kotak_factory()
        self._manage_authorized_exits(now)
        self._ensure_dhan_auth()
        # Off-session signals may arrive at any time and are mapped for the
        # dashboard. No ORB/feed starts outside the entry window.
        if now.weekday()>=5:
            self.blocked_stage="CURRENT_SESSION_SIGNALS"
            has_report=self._load_report()
            if not has_report:
                return
            if not self.dhan or not getattr(self.dhan,"token",""):
                self.status="DHAN_AUTHENTICATING" if self.dhan else "NEEDS_DHAN_CREDENTIALS"
                self.blocked_stage="DHAN_AUTHENTICATION"
                return
            self.blocked_stage="INSTRUMENT_MAPPING"
            self._map_instruments(now)
            with self.lock:
                self.status="SIGNALS_MAPPED_OFF_SESSION"
                self.blocked_stage=None
                self.armed=False
                if self.runtime:self.runtime.enabled=False
            return
        if self.ticker and hasattr(self.ticker,"refresh_authentication"):
            if self.ticker.refresh_authentication() and self.armed:
                self.disarm("Dhan feed token refreshed; re-arming required")
        if self.position_ticker and hasattr(self.position_ticker,"refresh_authentication"):
            self.position_ticker.refresh_authentication()
        if self.broker is not None and (self.last_book_at is None or now-self.last_book_at>=BOOK_REFRESH):
            self.blocked_stage="BROKER_BOOKS"
            self.refresh_broker()
        self.blocked_stage="CURRENT_SESSION_SIGNALS"
        has_report=self._load_report()
        if has_report:
            if self.dhan and self.dhan.token:
                self.blocked_stage="INSTRUMENT_MAPPING"
                self._map_instruments(now)
                self.blocked_stage="OPENING_RANGE_AND_FEED"
                self._build_runtime()
                if self.ticker is None or self.opening_ranges or now.time()>=self.policy.entry_cutoff:
                    self._map_late_research(now)
                    self.poll_ohlc()
                out_of_window=(now.time()<FEED_WARMUP or now.time()>=self.policy.entry_cutoff)
                if not self.runtime and out_of_window:
                    with self.lock:
                        self.status="SIGNALS_MAPPED_OFF_SESSION"
                        self.blocked_stage=None
                        self.armed=False
                elif not self.entry_watchlist:
                    self.blocked_stage="CURRENT_SESSION_SIGNALS"
                else:
                    self.blocked_stage=("KOTAK_AUTHENTICATION" if not self.broker else
                                        "OPENING_RANGE_AND_FEED" if not self.runtime else None)
            elif self.dhan is None:
                self.status="NEEDS_DHAN_CREDENTIALS"
        else:
            if not kotak_missing:
                self.disarm("No qualified current-session signals")
            else:
                with self.lock:
                    self.armed=False
                    self.session_authorized=False
                    if self.runtime:self.runtime.enabled=False
                    self.status="NEEDS_KOTAK_CREDENTIALS"
                    self.blocked_stage="NEEDS_KOTAK_CREDENTIALS"
        self._refresh_vix_context(now)
        if self.armed and not self.ready():
            self.disarm("Broker or market data health degraded")
        # Entries cannot be recreated after restart without manual arming.

    def start(self):
        if self.thread and self.thread.is_alive():return
        self.stop_event.clear()
        self.thread=threading.Thread(target=self._run,name="live-kotak-supervisor",daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self.cycle()
                with self.lock:self.error=None
            except Exception as exc:
                with self.lock:
                    self.error=f"Live supervisor failed ({type(exc).__name__})"
                self.disarm("Live supervisor safety halt")
                LOG.exception("Supervisor error; new orders disarmed")
            now = self.now()
            boundaries=(FEED_WARMUP, WATCHLIST_LOCK, START, self.policy.exit_time)
            waits=[(datetime.combine(now.date(), at, tzinfo=IST)-now).total_seconds()
                   for at in boundaries if at>now.time()]
            self.stop_event.wait(min([3, *waits]))

    def stop(self):
        self.stop_event.set()
        self.disarm("Service stopped")
        if self.ticker:self.ticker.stop()
        if self.position_ticker:self.position_ticker.stop()
        if self.runtime:self.runtime.close()
        if self.dhan_auth:self.dhan_auth.stop()
        if self.thread and self.thread.is_alive() and self.thread!=threading.current_thread():
            self.thread.join(timeout=8)
        self.ledger.close()

    def snapshot(self):
        now=self.now()
        with self.lock:
            entry_symbols={row["symbol"] for row in self.report["signals"]} if self.entry_watchlist else set()
            research=self.research_report or self.report
            signals=[{**row,"entry_eligible":row["symbol"] in entry_symbols}
                     for row in research.get("signals",[])] if research else []
            quotes=dict(self.quote_data)
            positions=list(self.broker_positions)
            fresh_quotes={symbol:q for symbol,q in quotes.items() if q.get("tick_at") and
                          timedelta(0)<=now-datetime.fromisoformat(q["tick_at"])<=PRICE_EXPIRY}
            positions=broker_view.value_positions(positions,fresh_quotes)
            books_fresh=bool(self.broker and self.last_book_at and timedelta(0)<=now-self.last_book_at<=BOOK_EXPIRY)
            if not books_fresh:
                for row in positions:
                    row.update(gross_mark_to_market=None,realized_gross=None,unrealized_gross=None,pnl_status="BOOKS_STALE")
            orders=list(self.broker_orders)
            fills=list(self.broker_fills)
            ticker=self.ticker.snapshot() if self.ticker else {"state":"NOT_STARTED"}
            monitoring=self._health_check_locked(now)
            intents=self.ledger.snapshot(now.date())
            trades=build_trade_journal(intents,[],[])["trades"]
            result={
                "mode":"LIVE_KOTAK_NEO_ONLY","server_time":now.isoformat(),
                "supervisor_status":self.status,
                "blocked_stage":self.blocked_stage,
                "session_date":now.date().isoformat(),
                "signal_status":self.report["status"] if self.report else "UNAVAILABLE",
                "signals":signals,
                "watchlist_locked":now.time()>=WATCHLIST_LOCK,
                "watchlist_eligible":self.entry_watchlist,
                "instrument_mappings":[{
                    "symbol":symbol,
                    "dhan_security_id":self.dhan_ids.get(symbol) or self.research_dhan_ids.get(symbol),
                    "kotak_trading_symbol":self.kotak_ids[symbol].trading_symbol if symbol in self.kotak_ids else None,
                    "kotak_token":self.kotak_ids[symbol].token if symbol in self.kotak_ids else None,
                    "status":"RESEARCH_ONLY" if symbol not in entry_symbols else
                            "READY" if symbol in self.dhan_ids and symbol in self.kotak_ids else
                            "DHAN_READY_KOTAK_PENDING" if symbol in self.dhan_ids else "UNAVAILABLE"
                } for symbol in sorted({row["symbol"] for row in signals})],
                "orb_monitor":{
                    "state":"MONITORING" if self.ticker and self.opening_ranges else
                            "WARMING" if self.ticker else "NOT_STARTED",
                    "opening_ranges":[{"symbol":symbol,"high":str(values[0]),"low":str(values[1])}
                                      for symbol,values in sorted(self.opening_ranges.items())],
                    "breakouts":list(self.orb_events.values()),
                    "order_submission_enabled":bool(self.armed and self.runtime),
                },
                "research_cutoff_at":self.report.get("news_cutoff_at") if self.report else None,
                "report_hash":self.report_sha,"armed":self.armed,
                "session_authorized":self.session_authorized,
                "orders_permitted_by_server":self.allowed,
                "market_feed":ticker,"broker_connected":books_fresh,
                "position_market_feed":self.position_ticker.snapshot() if self.position_ticker else {"state":"NOT_REQUIRED"},
                "position_feed_error":self.position_feed_error,
                "pnl":broker_view.pnl_summary(positions,books_fresh=books_fresh),
                "dhan_authentication":self.dhan_auth.snapshot() if self.dhan_auth else {"state":"NEEDS_SETUP"},
                "kotak_authentication":self.kotak_auth.snapshot() if self.kotak_auth else {
                    "state":"AUTHENTICATED" if self.broker else "NOT_STARTED"},
                "broker_books_updated_at":self.last_book_at.isoformat() if self.last_book_at else None,
                "available_cash":self.available_cash if books_fresh else None,
                "readiness":monitoring,
                "readiness_blockers":self._readiness_blockers_locked(now),
                "prior_session_recovery_count":self.prior_session_recovery,
                "strategy":{"opening_range":"09:15 one-minute candle","entry_cutoff":self.policy.entry_cutoff.isoformat(),
                            "exit_time":self.policy.exit_time.isoformat(),
                            "allocation_per_position":str(self.policy.per_position),
                            "gross_cap":str(self.policy.gross_cap),
                            "max_long":self.policy.max_long,"max_short":self.policy.max_short,
                            "max_positions":self.policy.max_positions,
                            "tick_stop_pct":None},
                "positions":positions,"orders":orders,"fills":fills,
                "intents":intents,
                "closed_trades":[row for row in trades if row["status"]=="CLOSED"],
                "error":self.error,"runtime_errors":self.runtime.errors[-10:] if self.runtime else [],
                "quotes":{symbol:{**q,"fresh":bool(
                    (symbol=="INDIAVIX" and q.get("ohlc_at") and
                     now-datetime.fromisoformat(q["ohlc_at"])<=timedelta(seconds=60)) or
                    (symbol!="INDIAVIX" and q.get("tick_at") and
                     now-datetime.fromisoformat(q["tick_at"])<=PRICE_EXPIRY))}
                         for symbol,q in quotes.items()}
            }
        return result
