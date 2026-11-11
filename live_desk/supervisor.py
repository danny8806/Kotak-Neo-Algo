"""Live-only Kotak Neo session supervisor.

REAL broker reports are authoritative for fills, orders, and net quantities.
Never imports a prediction model or simulates trade fills. Actual order
submission requires authenticated broker access, validated research,
live Dhan prices, and manual or explicitly configured automatic authorization.
"""
from __future__ import annotations
import json, logging, os, threading
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from neo_api_client.exceptions import ApiException
from requests import RequestException
from sqlalchemy import select, text
from bridge.app import reports
from scripts.validate_signals import validate
from live_desk.dhan_data import DhanDataClient, DhanTokenSupervisor, IST
from live_desk.dhan_instruments import fetch_dhan_symbols
from live_desk.kotak_gateway import KotakCredentials, login_kotak, reconcile_kotak_tag
from live_desk.kotak_auth import KotakSession
from live_desk.kotak_instruments import load_kotak_equity_master, lookup_kotak_equity
from live_desk.kotak_response import book_rows
from live_desk.ledger import LiveLedger
from live_desk.strategy import LATEST_ENTRY_CUTOFF, LATEST_EXIT_TIME, load_policy
from live_desk.orb import opening_range
from live_desk.runtime import MAX_ENTRY_TICK_AGE, LiveRuntime
from live_desk.ticker import LiveTicker
from live_desk import broker_view
from live_desk.analytics import build_trade_journal
from live_desk.nse_calendar import calendar_supported, is_regular_session
from live_desk.research_contract import (FINAL_CUTOFF, FINAL_DEADLINE, PIPELINE_VERSION,
                                         WATCHLIST_LOCK)

LOG=logging.getLogger("news-kotak-live")
FEED_WARMUP=time(9,14)
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
        self.policy=load_policy(Path(policy_path or Path(__file__).with_name("policy.json")),
                                entry_quantity_cap=os.getenv("LIVE_ENTRY_QUANTITY_CAP", "").strip(),
                                fixed_entry_quantity=os.getenv("LIVE_FIXED_ENTRY_QUANTITY", "2").strip())
        ledger_database = ledger_path or os.getenv("LIVE_DATABASE_URL") or os.getenv("SIGNAL_DATABASE_URL")
        if not ledger_database:
            raise RuntimeError("Set LIVE_DATABASE_URL to a PostgreSQL connection URL")
        if not str(ledger_database).startswith(("postgresql://", "postgresql+psycopg://")):
            raise RuntimeError("Live order ledger must use PostgreSQL")
        self.ledger=LiveLedger(ledger_database)
        stored=self.ledger.read_settings()
        if "entry_cutoff" in stored:
            self.policy=replace(self.policy, entry_cutoff=time.fromisoformat(stored["entry_cutoff"]),
                exit_time=time.fromisoformat(stored["exit_time"]),
                max_long=int(stored["max_long"]), max_short=int(stored["max_short"]),
                max_positions=int(stored["max_positions"]),
                per_position=Decimal(stored["per_position"]), gross_cap=Decimal(stored["gross_cap"]),
                fixed_entry_quantity=(None if stored.get('sizing_mode')=='capital' else
                                      int(stored.get('fixed_entry_quantity', self.policy.fixed_entry_quantity or 2))))
        self.dhan_factory=dhan_factory or DhanDataClient.from_environment
        self.dhan_auth=None
        self.kotak_factory=kotak_factory or (lambda:login_kotak(KotakCredentials.from_environment()))
        self.kotak_from_environment=kotak_factory is None
        self.kotak_auth=(KotakSession(self.kotak_factory,clock=self.clock)
                         if self.kotak_from_environment else None)
        self.master_fetch=master_fetch or fetch_dhan_symbols
        self.ticker_factory=ticker_factory or LiveTicker
        self.allowed=os.getenv("LIVE_ORDERS_ALLOWED","").strip()=="YES"
        self.auto_trading=os.getenv("LIVE_AUTO_TRADING","").strip()=="YES"
        self.auto_paused_on=stored.get("auto_trading_paused_on")
        self.auto_arm_error=None
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
        self.research_gate=None
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
        self._kotak_master=None
        self.preparation=ThreadPoolExecutor(max_workers=10,thread_name_prefix="live-market-setup")
        self.opening_ranges={}
        self.orb_events={}
        self._orb_previous={}
        self._orb_last_tick={}
        self.quote_data={}
        self._entry_generation=None
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
                cutoff=time.fromisoformat(str(payload["entry_cutoff"]))
                exit_at=time.fromisoformat(str(payload["exit_time"]))
                if cutoff.tzinfo is not None or exit_at.tzinfo is not None:
                    raise ValueError("Use IST clock times without a timezone suffix")
                counts={name:str(payload[name]) for name in ("max_long","max_short","max_positions")}
                if any(not value.isascii() or not value.isdecimal() for value in counts.values()):
                    raise ValueError("Position limits must be whole numbers")
                proposed=replace(self.policy,
                    entry_cutoff=cutoff, exit_time=exit_at,
                    **{name:int(value) for name,value in counts.items()},
                    per_position=Decimal(str(payload["per_position"])),
                    gross_cap=Decimal(str(payload.get("gross_cap",self.policy.gross_cap))),
                    fixed_entry_quantity=(None if payload.get('sizing_mode')=='capital' else
                        int(str(payload.get('fixed_entry_quantity',self.policy.fixed_entry_quantity or 2)))))
                if payload.get('sizing_mode','quantity') not in {'quantity','capital'}:
                    raise ValueError('Sizing mode must be quantity or capital')
                raw_quantity=str(payload.get('fixed_entry_quantity',self.policy.fixed_entry_quantity or 2))
                if proposed.fixed_entry_quantity is not None and (not raw_quantity.isascii() or not raw_quantity.isdecimal()):
                    raise ValueError('Quantity must be a positive whole number')
            except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
                raise ValueError("Settings have invalid or missing values") from exc
            if cutoff>LATEST_ENTRY_CUTOFF:
                raise ValueError("Entry cutoff cannot be later than 09:30 IST")
            if not time(9,16)<cutoff<exit_at<=LATEST_EXIT_TIME:
                raise ValueError("Exit must follow the entry cutoff and be no later than 15:00 IST")
            if (not 1<=proposed.max_long<=5 or not 1<=proposed.max_short<=5 or
                not 1<=proposed.max_positions<=10 or
                proposed.max_positions>proposed.max_long+proposed.max_short):
                raise ValueError("Position limits must fit within up to 5 LONG and 5 SHORT signals")
            if (not proposed.per_position.is_finite() or not proposed.gross_cap.is_finite() or
                proposed.per_position<=0 or proposed.per_position>Decimal("20000000")):
                raise ValueError("Capital per stock must be a positive amount")
            if any(value != value.quantize(Decimal("0.01"))
                   for value in (proposed.per_position, proposed.gross_cap)):
                raise ValueError("Capital amounts must have no more than two decimal places")
            values={"entry_cutoff":proposed.entry_cutoff.isoformat(),"exit_time":proposed.exit_time.isoformat(),
                "max_long":str(proposed.max_long),"max_short":str(proposed.max_short),
                "max_positions":str(proposed.max_positions),"per_position":str(proposed.per_position),
                "gross_cap":str(proposed.gross_cap),
                "sizing_mode":"capital" if proposed.fixed_entry_quantity is None else "quantity",
                "fixed_entry_quantity":str(proposed.fixed_entry_quantity or '')}
            self.ledger.write_settings(values,self.now())
            self.policy=proposed
            if self.runtime:self.runtime.policy=proposed
            return proposed

    def today_report(self):
        now=self.now()
        regular=is_regular_session(now.date())
        lock_at=datetime.combine(now.date(), WATCHLIST_LOCK, tzinfo=IST)
        gate={"status":"NO_REPORT" if regular else "NOT_A_REGULAR_NSE_SESSION"}
        with self.database.begin() as conn:
            if regular and now >= lock_at:
                # Wait for any pre-open ingest transaction to commit before freezing.
                conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('signal-ingest'), hashtext(:day))"),
                             {"day": now.date().isoformat()})
            record=conn.execute(select(reports).where(
                reports.c.session_date==now.date().isoformat())).mappings().first()
            if not record:
                self.research_gate=gate
                return None, None, False, None
            aggregate=json.loads(record["original_json"])
            data=aggregate
            digest=record["sha256"]
            provenance=record.get("pipeline_version")
            eligible=False
            if not regular:
                gate={"status":("NOT_A_REGULAR_NSE_SESSION" if calendar_supported(now.date())
                                else "CALENDAR_UPDATE_REQUIRED"),
                      "pipeline_version":provenance}
            elif provenance != PIPELINE_VERSION:
                gate={"status":"LEGACY_OR_UNVERIFIED_PRODUCER",
                      "pipeline_version":provenance,"required":PIPELINE_VERSION}
            elif record["received_at"] >= lock_at:
                gate={"status":"RECEIVED_AFTER_LOCK","pipeline_version":provenance,
                      "received_at":record["received_at"].isoformat()}
            elif data["status"] != "READY":
                gate={"status":"NOT_READY","pipeline_version":provenance}
            else:
                try:
                    cutoff=datetime.fromisoformat(
                        str(data["news_cutoff_at"]).replace("Z","+00:00")).astimezone(IST)
                    generated=datetime.fromisoformat(
                        str(data["generated_at"]).replace("Z","+00:00")).astimezone(IST)
                except (KeyError,TypeError,ValueError):
                    gate={"status":"INVALID_RESEARCH_TIMESTAMPS",
                          "pipeline_version":provenance}
                else:
                    if cutoff.date()!=now.date() or generated.date()!=now.date():
                        gate={"status":"RESEARCH_DATE_MISMATCH",
                              "pipeline_version":provenance}
                    elif cutoff.timetz().replace(tzinfo=None)>=FINAL_CUTOFF:
                        gate={"status":"CUTOFF_AFTER_DECISION_DEADLINE","pipeline_version":provenance,
                              "news_cutoff_at":cutoff.isoformat()}
                    elif generated.timetz().replace(tzinfo=None)>=FINAL_DEADLINE:
                        gate={"status":"GENERATED_AFTER_0912","pipeline_version":provenance,
                              "generated_at":generated.isoformat()}
                    elif generated<cutoff:
                        gate={"status":"GENERATED_BEFORE_CUTOFF",
                              "pipeline_version":provenance}
                    else:
                        eligible=True
                        gate={"status":"ELIGIBLE","pipeline_version":provenance,
                              "news_cutoff_at":cutoff.isoformat(),
                              "generated_at":generated.isoformat(),
                              "received_at":record["received_at"].isoformat()}
        self.research_gate=gate
        validate(data,expected_date=now.date().isoformat(),
                 allowed_symbols=self.allowed_symbols,strict_final=eligible)
        return data,digest,eligible,aggregate

    def _new_day(self,day):
        with self.lock:
            self.armed=False
            self.session_authorized=False
            runtime,ticker,position_ticker=self.runtime,self.ticker,self.position_ticker
            if runtime:runtime.enabled=False
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
            self.research_gate=None
            self.research_report=None
            self.research_dhan_ids={}
            self.dhan_ids={}
            self.kotak_ids={}
            self._kotak_master=None
            self.opening_ranges={}
            self.orb_events={}
            self._orb_previous={}
            self._orb_last_tick={}
            self.quote_data={}
            self.last_book_at=None
            self._entry_generation=None
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

        if ticker:ticker.stop()
        if position_ticker:position_ticker.stop()
        if runtime:runtime.close()

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
            not is_regular_session(now.date()) or not time(9,15)<=now.time()<self.policy.entry_cutoff):
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

    def _load_kotak_master(self):
        if self._kotak_master is None:
            self._kotak_master=load_kotak_equity_master(self.broker)
        return self._kotak_master

    def _lookup_kotak_instruments(self, symbols):
        if callable(getattr(self.broker,"scrip_master",None)):
            master=self._load_kotak_master()
            missing=symbols-master.keys()
            if missing:
                raise ValueError(f"Kotak exact NSE EQ mapping missing or ambiguous for {sorted(missing)}")
            return {symbol:master[symbol] for symbol in symbols}
        return {symbol:lookup_kotak_equity(self.broker,symbol) for symbol in sorted(symbols)}

    def _map_instruments(self,now):
        wanted={item["symbol"] for item in self.report["signals"]}
        missing=wanted-self.dhan_ids.keys()
        kotak_missing=wanted-self.kotak_ids.keys()
        kotak_job=(self.preparation.submit(self._lookup_kotak_instruments,kotak_missing)
                   if self.broker is not None and kotak_missing else None)
        try:
            if missing:
                dhan=self.master_fetch(missing,now=now)
                if dhan.session_date!=now.date().isoformat() or set(dhan.security_ids)!=missing:
                    raise RuntimeError("NSE EQ Dhan symbol master incomplete")
                dhan_ids={**self.dhan_ids,**dhan.security_ids}
                if len(set(dhan_ids.values())) != len(dhan_ids):
                    raise RuntimeError("Duplicate Dhan security IDs in the session watchlist")
                with self.lock:self.dhan_ids=dhan_ids
            self._warm_entry_feed(self.now())
        finally:
            if kotak_job:
                try:
                    kotak={**self.kotak_ids,**kotak_job.result()}
                    if len({item.token for item in kotak.values()})!=len(kotak):
                        raise RuntimeError("Duplicate Kotak tokens in the session watchlist")
                except Exception as exc:
                    LOG.warning("Kotak watchlist mapping unavailable (%s)",type(exc).__name__)
                else:
                    with self.lock:self.kotak_ids=kotak

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

    def _warm_entry_feed(self,now):
        if (self.stop_event.is_set() or not self.entry_watchlist or not self.dhan_ids or
                not is_regular_session(now.date()) or
                not FEED_WARMUP<=now.time()<self.policy.entry_cutoff):
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

    def _build_runtime(self):
        now=self.now()
        if not self.entry_watchlist or self.ticker is None or now.time()>=self.policy.entry_cutoff:
            return
        if self.runtime:
            self.status="MONITORING_LIVE"
            return
        if now.time()<START:
            self.status="WARMING_DHAN_FEED"
            return
        if self.policy.timestamp_mode not in {"start","end"}:
            raise RuntimeError("Dhan 09:15 candle timestamp mode not yet verified")

        jobs={self.preparation.submit(opening_range,self.dhan,security_id,now.date(),
                                      timestamp_mode=self.policy.timestamp_mode):symbol
              for symbol,security_id in self.dhan_ids.items() if symbol not in self.opening_ranges}
        for job in as_completed(jobs):
            symbol=jobs[job]
            try:
                bounds=job.result()
            except Exception as exc:
                LOG.info("Dhan opening candle unavailable for %s (%s)",symbol,type(exc).__name__)
                continue
            with self.lock:
                baseline=self._opening_baseline(symbol,self.now())
                if baseline:
                    self._orb_previous[symbol],self._orb_last_tick[symbol]=baseline
                self.opening_ranges[symbol]=bounds
        if self.opening_ranges.keys()!=self.dhan_ids.keys():
            self.status="WAITING_FOR_OPENING_CANDLE"
            return
        if self.broker is None or self.kotak_ids.keys()!=self.dhan_ids.keys():
            self.status="MONITORING_DHAN_ORB_KOTAK_PENDING"
            return
        if self.stop_event.is_set() or self.now().time()>=self.policy.entry_cutoff:
            return
        runtime=LiveRuntime(
            session_date=now.date(),signals=self.report["signals"],dhan_ids=self.dhan_ids,
            kotak_instruments=self.kotak_ids,opening_ranges=dict(self.opening_ranges),
            policy=self.policy,ledger=self.ledger,broker=self.broker,enabled=False,clock=self.now,
            feed_is_current=getattr(self.ticker,"connection_is_current",None))
        with self.lock:
            received_at=self.now()
            for symbol in self.opening_ranges:
                baseline=self._opening_baseline(symbol,received_at)
                if baseline:
                    runtime.previous[symbol],runtime.last_tick_at[symbol]=baseline
            self.runtime=runtime
            self.status="MONITORING_LIVE"

    def _opening_baseline(self,symbol,now):
        quote=self.quote_data.get(symbol,{})
        if "tick_at" not in quote:
            return None
        tick_at=datetime.fromisoformat(quote["tick_at"])
        if timedelta(0)<=now-tick_at<=MAX_ENTRY_TICK_AGE:
            return Decimal(str(quote["ltp"])),tick_at
        return None

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
        observed_at=self.now()
        positions_job=self.preparation.submit(broker_view.positions,self.broker,current_quotes)
        cash_job=getattr(self,'_cash_job',None)
        if cash_job is None or cash_job.done():
            cash_job=self.preparation.submit(self._broker_cash)
            self._cash_job=cash_job
        try:
            try:
                order_rows=book_rows(self.broker.order_report(),"orders")
                trade_rows=book_rows(self.broker.trade_report(),"trades")
            finally:
                wait((positions_job,))
            position=positions_job.result()
            try:cash=cash_job.result(timeout=0.25)
            except (TimeoutError,ApiException,RequestException,ArithmeticError,RuntimeError,ValueError):cash=None
        except (ApiException, RequestException, ArithmeticError, RuntimeError, ValueError) as exc:
            with self.lock:
                self.last_book_at=None
                if self.armed:
                    self.disarm("Kotak account books unavailable")
            LOG.warning("Kotak book refresh failed (%s)",type(exc).__name__)
            return
        books=(order_rows,trade_rows)
        orders=broker_view.orders(self.broker,rows=order_rows)
        fills=broker_view.fills(self.broker,rows=trade_rows)
        self.ledger.record_broker_fills(fills,now)
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
            self.available_cash=str(cash) if cash is not None else None
            self.prior_session_recovery=prior_recovery
            self.last_book_at=observed_at
            if self.status!="MONITORING_LIVE":self.status="BROKER_CONNECTED"
        self._refresh_position_feed(now)

    def _refresh_position_feed(self,now):
        """Read-only ticks for account positions outside the signal watchlist."""
        symbols={row["symbol"] for row in self.broker_positions if row["net_quantity"]}
        symbols-=set(self.dhan_ids)
        if not time(9,15)<=now.time()<time(15,30) or not is_regular_session(now.date()):symbols=set()
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
        if not self.dhan or not self.dhan_ids or not is_regular_session(now.date()):
            return
        if not time(9,15)<=now.time()<self.policy.entry_cutoff:return
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
        entry_closed=now.time()>=self.policy.entry_cutoff
        # Reuse the subscription for open-position valuation only after cutoff.
        if entry_closed and not any(row["symbol"]==symbol and row["net_quantity"]
                                    for row in self.broker_positions):
            return
        try:value=Decimal(str(tick.get("ltp")))
        except (ValueError,TypeError,ArithmeticError):return
        if not value.is_finite() or value<=0:return
        generation=tick.get("feed_generation")
        with self.lock:
            if not entry_closed and generation is not None and generation!=self._entry_generation:
                if self._entry_generation is not None:
                    if generation<self._entry_generation:return
                    self._orb_previous.clear()
                    self._orb_last_tick.clear()
                    self.last_quote_at=None
                    if self.runtime:self.runtime.reset_crossing_baselines()
                    if self.armed:self.disarm("Dhan feed reconnected; re-arming required")
                self._entry_generation=generation
            previous=self.quote_data.get(symbol,{})
            if previous.get("tick_at") and ts.astimezone(IST)<datetime.fromisoformat(previous["tick_at"]):return
            self.quote_data[symbol]={**previous,"ltp":float(value),
                                     "tick_at":ts.astimezone(IST).isoformat()}
            if entry_closed:
                return
            self.last_quote_at=max(ts, self.last_quote_at or ts)
            ready=self.armed and self._health_check_locked(now)
            if self.runtime:self.runtime.enabled=ready
            runtime=self.runtime
        if runtime:
            runtime.on_market_tick(symbol,value,ts,now,tick.get("received_ns"),generation)
        self._observe_orb_crossing(symbol,value,ts,now)

    def _observe_orb_crossing(self,symbol,price,tick_at,now):
        local=tick_at.astimezone(IST)
        if (local.date().isoformat()!=self.session_date or local.time()<time(9,16) or
            now.time()>=self.policy.entry_cutoff or local.time()>=self.policy.entry_cutoff
            or symbol not in self.opening_ranges):
            return
        with self.lock:
            previous_at=self._orb_last_tick.get(symbol)
            if previous_at and tick_at<previous_at:
                return
            previous=self._orb_previous.get(symbol)
            self._orb_previous[symbol]=price
            self._orb_last_tick[symbol]=tick_at
            if (previous is None or previous_at is None or now-previous_at>MAX_ENTRY_TICK_AGE
                    or symbol in self.orb_events):
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
        if (self.last_quote_at is None or not timedelta(0)<=now-self.last_quote_at<=MAX_ENTRY_TICK_AGE
                or ticker.get("connection_generation",self._entry_generation)!=self._entry_generation):
            blocked.append("FRESH_QUOTES")
        if self.last_book_at is None or not timedelta(0)<=now-self.last_book_at<=BOOK_EXPIRY:
            blocked.append("BROKER_BOOKS")
        if not START<=now.time()<self.policy.entry_cutoff or not is_regular_session(now.date()):
            blocked.append("MARKET_ENTRY_WINDOW")
        # Both configured sizing modes delegate balance/margin acceptance to
        # Kotak. Legacy capital enforcement is disabled in the live policy.
        if self.policy.enforce_capital_limits and (
                not self.available_cash or Decimal(self.available_cash)<self.policy.gross_cap):
            blocked.append("FUNDS")
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
                raise RuntimeError("Live orders cannot arm: feeds, broker or session not ready")
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

    def disarm(self,reason="Operator disarmed new orders",*,pause_automatic=False):
        with self.lock:
            pause=(getattr(self,"auto_trading",False) and (pause_automatic or self.armed))
            self.armed=False
            if self.runtime:self.runtime.enabled=False
            self.status="DISARMED"
            if pause:
                self.auto_paused_on=self.now().date().isoformat()
        if pause:
            self.ledger.write_settings({"auto_trading_paused_on":self.auto_paused_on},self.now())
        LOG.warning("%s",reason)

    def _maybe_auto_arm(self,now):
        """Use explicit server authorization only after the normal live checks pass."""
        if (not getattr(self,"auto_trading",False) or not self.allowed or self.armed or
                self.auto_paused_on==now.date().isoformat() or
                not self.ready()):
            return
        try:
            self.arm()
            self.auto_arm_error=None
        except RuntimeError as exc:
            # arm() still checks overlapping positions and unresolved intents.
            self.auto_arm_error=str(exc)

    def _manage_authorized_exits(self,now):
        """Cancel working bot entries at cutoff and close confirmed fills at EOD.

        A persisted bot entry is sufficient to recover these operations after a restart.
        Square-off also requires an exact match against the live broker position.
        """
        if not self.allowed or now.time()<self.policy.entry_cutoff or not is_regular_session(now.date()):
            return
        square_off=now.time()>=self.policy.exit_time
        if self.armed:
            self.disarm("End-of-day square-off started" if square_off else "Entry cutoff reached")
        if not square_off and self.runtime:
            if self.broker is not None:
                self.runtime.cancel_working_entries(now)
            return
        intent_rows=self.ledger.snapshot(now.date())
        exited={r["symbol"]:r["filled_quantity"] for r in intent_rows if r["phase"]=="EXIT"}
        entries=[r for r in intent_rows if r["phase"]=="ENTRY" and (
            r["filled_quantity"]>exited.get(r["symbol"],0) or
            r["state"] in {"SUBMISSION_UNKNOWN","ACKNOWLEDGED","PARTIAL","CANCEL_PENDING"})]
        if not entries or self.broker is None:
            return
        if not square_off and not any(row["state"] in {"SUBMISSION_UNKNOWN","ACKNOWLEDGED","PARTIAL"} for row in entries):
            return
        if self.runtime is None:
            # Recovery never uses these inert Dhan IDs or ORB values for entries.
            rows=entries
            symbols=[r["symbol"] for r in rows]
            kotak=self._lookup_kotak_instruments(set(symbols))
            self.runtime=LiveRuntime(session_date=now.date(),
                signals=[{"symbol":r["symbol"],"side":"LONG" if r["side"]=="BUY" else "SHORT"} for r in rows],
                dhan_ids={s:"0" for s in symbols},kotak_instruments=kotak,
                opening_ranges={s:(Decimal("1"),Decimal("1")) for s in symbols},
                policy=self.policy,ledger=self.ledger,broker=self.broker,enabled=False,
                clock=self.clock,close_only=True)
        if square_off:
            self.runtime.exit_due(now, read_positions=lambda: broker_view.positions(self.broker))
        else:
            self.runtime.cancel_working_entries(now)

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
        self._manage_authorized_exits(self.now())
        self._ensure_dhan_auth()
        # Off-session signals may arrive at any time and are mapped for the
        # dashboard. No ORB/feed starts outside the entry window.
        if not is_regular_session(now.date()):
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
        refresh_due=self.broker is not None and (self.last_book_at is None or now-self.last_book_at>=BOOK_REFRESH)
        # Recently verified books can wait a few seconds for opening preparation.
        opening_boundary=self.last_book_at is not None and now-self.last_book_at<BOOK_EXPIRY and (
            time(9,15,55)<=now.time()<time(9,16,5))
        if refresh_due and not opening_boundary:
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
                                        "INSTRUMENT_MAPPING" if self.kotak_ids.keys()!=self.dhan_ids.keys() else
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
        if refresh_due and opening_boundary and self.now().time()>=START:
            self.refresh_broker()
        current=self.now()
        if not time(9,15)<=current.time()<time(9,16,5):
            self._refresh_vix_context(current)
        if self.armed and not self.ready():
            self.disarm("Broker or market data health degraded")
        self._maybe_auto_arm(current)

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
            boundaries=(FEED_WARMUP, WATCHLIST_LOCK, START, self.policy.entry_cutoff, self.policy.exit_time)
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
        self.preparation.shutdown(wait=False,cancel_futures=True)
        self.ledger.close()

    def snapshot(self):
        now=self.now()
        with self.lock:
            entry_closed=now.time()>=self.policy.entry_cutoff
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
            blockers=self._readiness_blockers_locked(now)
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
                    "state":"ENTRY_WINDOW_CLOSED" if entry_closed else
                            "MONITORING" if self.ticker and self.opening_ranges else
                            "WARMING" if self.ticker else "NOT_STARTED",
                    "opening_ranges":[{"symbol":symbol,"high":str(values[0]),"low":str(values[1])}
                                      for symbol,values in sorted(self.opening_ranges.items())],
                    "breakouts":list(self.orb_events.values()),
                    "order_submission_enabled":bool(not entry_closed and self.armed and self.runtime),
                },
                "research_cutoff_at":self.report.get("news_cutoff_at") if self.report else None,
                "research_gate":self.research_gate,
                "report_hash":self.report_sha,"armed":self.armed,
                "session_authorized":self.session_authorized,
                "automatic_trading":{"enabled":getattr(self,"auto_trading",False),
                    "paused_today":getattr(self,"auto_paused_on",None)==now.date().isoformat(),
                    "error":getattr(self,"auto_arm_error",None)},
                "orders_permitted_by_server":self.allowed,
                "market_feed":ticker,"broker_connected":books_fresh,
                "last_entry_quote_at":self.last_quote_at.isoformat() if self.last_quote_at else None,
                "position_market_feed":self.position_ticker.snapshot() if self.position_ticker else {"state":"NOT_REQUIRED"},
                "position_feed_error":self.position_feed_error,
                "pnl":broker_view.pnl_summary(positions,books_fresh=books_fresh),
                "dhan_authentication":self.dhan_auth.snapshot() if self.dhan_auth else {"state":"NEEDS_SETUP"},
                "kotak_authentication":self.kotak_auth.snapshot() if self.kotak_auth else {
                    "state":"AUTHENTICATED" if self.broker else "NOT_STARTED"},
                "broker_books_updated_at":self.last_book_at.isoformat() if self.last_book_at else None,
                "available_cash":self.available_cash if books_fresh else None,
                "readiness":not blockers,
                "readiness_blockers":blockers,
                "prior_session_recovery_count":self.prior_session_recovery,
                "strategy":{"opening_range":"09:15 one-minute candle","entry_cutoff":self.policy.entry_cutoff.isoformat(),
                            "exit_time":self.policy.exit_time.isoformat(),
                            "allocation_per_position":str(self.policy.per_position),
                            "gross_cap":str(self.policy.gross_cap),
                            "entry_quantity_cap":self.policy.entry_quantity_cap,
                            "fixed_entry_quantity":self.policy.fixed_entry_quantity,
                            "sizing_mode":"capital" if self.policy.fixed_entry_quantity is None else "quantity",
                            "capital_balance_gate":False,
                            "max_long":self.policy.max_long,"max_short":self.policy.max_short,
                            "max_positions":self.policy.max_positions,
                            "tick_stop_pct":None},
                "positions":positions,"orders":orders,"fills":fills,
                "entry_dispatch_latency":self.runtime.dispatch_latency() if self.runtime else None,
                "error":self.error,"runtime_errors":self.runtime.errors[-10:] if self.runtime else [],
                "quotes":{symbol:{**q,"fresh":bool(
                    (symbol=="INDIAVIX" and q.get("ohlc_at") and
                     now-datetime.fromisoformat(q["ohlc_at"])<=timedelta(seconds=60)) or
                    (symbol!="INDIAVIX" and q.get("tick_at") and
                     now-datetime.fromisoformat(q["tick_at"])<=PRICE_EXPIRY))}
                         for symbol,q in quotes.items()}
            }
        intents=self.ledger.snapshot(now.date())
        trades=build_trade_journal(intents,[],[])["trades"]
        result["intents"]=intents
        submitted={row["symbol"] for row in intents if row["phase"]=="ENTRY"}
        observed={row["symbol"] for row in result["orb_monitor"]["breakouts"]}
        for signal in result["signals"]:
            symbol=signal["symbol"]
            signal["entry_status"]=(
                "RESEARCH_ONLY" if not signal["entry_eligible"] else
                "ENTRY_RECORDED" if symbol in submitted else
                "BREAKOUT_OBSERVED" if symbol in observed else
                "EXPIRED" if entry_closed else "WAITING"
            )
        result["closed_trades"]=[row for row in trades if row["status"]=="CLOSED"]
        return result
