"""Live-only Kotak Neo session supervisor.

REAL broker reports are authoritative for fills, orders, and net quantities.
Never imports a prediction model or simulates trade fills. Actual order
submission requires authenticated broker access, validated research,
live Dhan prices, and an explicit per-session arming operation.
"""
from __future__ import annotations
import json, logging, os, threading
from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from sqlalchemy import select
from bridge.app import reports
from scripts.validate_signals import validate
from live_desk.dhan_data import DhanDataClient, DhanTokenSupervisor, IST
from live_desk.dhan_instruments import fetch_dhan_symbols
from live_desk.kotak_gateway import KotakCredentials, login_kotak
from live_desk.kotak_instruments import lookup_kotak_equity
from live_desk.ledger import LiveLedger
from live_desk.strategy import load_policy
from live_desk.orb import opening_range
from live_desk.runtime import LiveRuntime
from live_desk.ticker import LiveTicker
from live_desk import broker_view

LOG=logging.getLogger("news-kotak-live")
START=time(9,17)
BOOK_REFRESH=timedelta(seconds=15)
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
        self.report=None
        self.report_sha=None
        self.session_date=None
        self.dhan=None
        self.broker=None
        self.ticker=None
        self.runtime=None
        self.dhan_ids={}
        self.kotak_ids={}
        self.opening_ranges={}
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
        with self.database.connect() as conn:
            record=conn.execute(select(reports).where(
                reports.c.session_date==now.date().isoformat())).mappings().first()
        if not record:return None,None
        data=json.loads(record["original_json"])
        validate(data,expected_date=now.date().isoformat(),
                 allowed_symbols=self.allowed_symbols)
        return data,record["sha256"]

    def _new_day(self,day):
        with self.lock:
            self.armed=False
            self.session_authorized=False
            if self.runtime:
                self.runtime.enabled=False
                self.runtime.close()
            if self.ticker:self.ticker.stop()
            self.ticker=None
            self.runtime=None
            self.broker=None
            self.report=None
            self.report_sha=None
            self.dhan_ids={}
            self.kotak_ids={}
            self.opening_ranges={}
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
        report,sha=self.today_report()
        if not report or report["status"]!="READY":
            with self.lock:
                self.status="NO_ELIGIBLE_SIGNALS"
                self.report=report
            return False
        if self.report_sha and self.report_sha!=sha:
            raise RuntimeError("Immutable session research report unexpectedly changed")
        with self.lock:
            self.report=report
            self.report_sha=sha
            self.status="SIGNALS_VERIFIED"
        return True

    def _authenticate(self):
        if self.broker is None:self.broker=self.kotak_factory()
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

    def _build_runtime(self):
        now=self.now()
        if now.weekday()>=5 or now.time()<START:
            return
        if self.runtime:return
        if self.ticker:
            if self.broker is None:
                return
            self.ticker.stop()
            self.ticker=None
        if not self.dhan or not self.dhan.token:
            self.status="DHAN_AUTHENTICATING"
            return
        if self.policy.timestamp_mode not in {"start","end"}:
            raise RuntimeError("Dhan 09:15 candle timestamp mode not yet verified")
        candidates=self.report["signals"]
        wanted={item["symbol"] for item in candidates}
        dhan=self.master_fetch(wanted,now=now)
        if dhan.session_date!=now.date().isoformat() or set(dhan.security_ids)!=wanted:
            raise RuntimeError("NSE EQ Dhan symbol master incomplete")
        opening={}
        for symbol,id_ in dhan.security_ids.items():
            opening[symbol]=opening_range(self.dhan,id_,now.date(),
                                           timestamp_mode=self.policy.timestamp_mode)
        kotak={}
        runtime=None
        if self.broker is not None:
            kotak={s:lookup_kotak_equity(self.broker,s) for s in wanted}
            runtime=LiveRuntime(
                session_date=now.date(),signals=candidates,dhan_ids=dhan.security_ids,
                kotak_instruments=kotak,opening_ranges=opening,
                policy=self.policy,ledger=self.ledger,broker=self.broker,
                enabled=False,clock=self.now)
        ticker=self.ticker_factory(self.dhan,dhan.security_ids,self.on_tick)
        with self.lock:
            self.runtime=runtime
            self.ticker=ticker
            self.dhan_ids=dhan.security_ids
            self.kotak_ids=kotak
            self.opening_ranges=opening
            self._orb_previous={}
            self._orb_last_tick={}
            self.orb_events={}
            self.status="MONITORING_LIVE" if runtime else "MONITORING_DHAN_ORB_KOTAK_PENDING"
        ticker.start()

    def _broker_cash(self):
        response=self.broker.limits()  # kotakneoapi v3: all segments, no arguments
        if not isinstance(response,dict) or str(response.get("stat","")).lower()!="ok":
            raise RuntimeError("Kotak cash/margin limits unavailable")
        amount=Decimal(str(response.get("Net","NaN")))
        if not amount.is_finite() or amount<0:
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
        orders=broker_view.orders(self.broker)
        fills=broker_view.fills(self.broker)
        cash=self._broker_cash()
        now=self.now()
        if self.runtime:self.runtime.reconcile_pending(now)
        with self.lock:
            self.broker_positions=position
            self.broker_orders=orders
            self.broker_fills=fills
            self.available_cash=str(cash)
            self.last_book_at=now
            if self.status!="MONITORING_LIVE":self.status="BROKER_CONNECTED"

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
                self.quote_data[symbol]={**old,**quote}
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
        value=Decimal(str(tick.get("ltp")))
        if not value.is_finite() or value<=0:return
        with self.lock:
            previous=self.quote_data.get(symbol,{})
            self.quote_data[symbol]={**previous,"ltp":float(value),
                                     "tick_at":ts.astimezone(IST).isoformat()}
            self.last_quote_at=now
            ready=self.armed and self._health_check_locked(now)
            if self.runtime:self.runtime.enabled=ready
            runtime=self.runtime
        self._observe_orb_crossing(symbol,value,ts,now)
        if runtime and ready:
            # Real orders can be submitted only by the separately armed runtime.
            runtime.on_tick(tick)
        # Position exits follow the configured 15:15 IST square-off policy.
        # Loss limits disarm new entries; they do not create an implicit stop.

    def _observe_orb_crossing(self,symbol,price,tick_at,now):
        local=tick_at.astimezone(IST)
        if (local.date().isoformat()!=self.session_date or local.time()<time(9,16) or
            local.time()>self.policy.entry_cutoff or symbol not in self.opening_ranges):
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
        if not self.allowed or not self.report or self.report["status"]!="READY":
            return False
        if not self.broker or not self.dhan or not self.runtime:
            return False
        if not self.ticker or self.ticker.snapshot().get("state")!="CONNECTED":
            return False
        if self.last_quote_at is None or now-self.last_quote_at>PRICE_EXPIRY:
            return False
        if self.last_book_at is None or now-self.last_book_at>BOOK_REFRESH:
            return False
        if not time(9,17)<=now.time()<self.policy.entry_cutoff or now.weekday()>=5:
            return False
        if not self.available_cash or Decimal(self.available_cash)<self.policy.gross_cap:
            return False
        if self.error:return False
        if self.runtime.errors:return False
        return True

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
            unresolved={"RESERVED","SUBMISSION_UNKNOWN","ACKNOWLEDGED","PARTIAL"}
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
        intent_rows=self.ledger.snapshot(now.date())
        entries={r["symbol"]:r for r in intent_rows
                 if r["phase"]=="ENTRY" and r["filled_quantity"]>0}
        if not entries:
            return
        if not self.last_book_at or now-self.last_book_at>BOOK_REFRESH:
            raise RuntimeError("Broker position book stale; cannot safely submit exit orders")
        positions={p["symbol"]:p for p in self.broker_positions}
        for symbol,entry in entries.items():
            position=positions.get(symbol)
            expected=entry["filled_quantity"] if entry["side"]=="BUY" else -entry["filled_quantity"]
            if position is None or int(position["net_quantity"])!=expected:
                raise RuntimeError("Kotak position reconciliation failed; exit blocked")
        if self.runtime is None:
            # Recover a close-only runtime from persisted bot intents. Dummy Dhan
            # IDs/ORB values are inert because enabled=False and EOD calls exit_due.
            rows=[r for r in intent_rows if r["phase"]=="ENTRY" and r["state"] not in {"REJECTED"}]
            symbols=[r["symbol"] for r in rows]
            kotak={s:lookup_kotak_equity(self.broker,s) for s in symbols}
            self.runtime=LiveRuntime(session_date=now.date(),
                signals=[{"symbol":r["symbol"],"side":"LONG" if r["side"]=="BUY" else "SHORT"} for r in rows],
                dhan_ids={s:"0" for s in symbols},kotak_instruments=kotak,
                opening_ranges={s:(Decimal("1"),Decimal("1")) for s in symbols},
                policy=self.policy,ledger=self.ledger,broker=self.broker,enabled=False,
                clock=self.clock,close_only=True)
        self.runtime.exit_due(now)

    def cycle(self):
        now=self.now()
        if self.session_date!=now.date().isoformat():
            self._new_day(now.date().isoformat())
        if now.weekday()>=5:return
        self._ensure_dhan_auth()
        self._refresh_vix_context(now)
        # Monitor the actual Kotak brokerage account even when news is missing.
        # No signals must NEVER be interpreted as fabricated positions.
        kotak_missing=False
        if self.broker is None:
            if self.kotak_from_environment:
                try:
                    KotakCredentials.from_environment()
                except ValueError:
                    kotak_missing=True
                    with self.lock:self.status="NEEDS_KOTAK_CREDENTIALS"
            if not kotak_missing:
                self.broker=self.kotak_factory()
        if self.broker is not None and (self.last_book_at is None or now-self.last_book_at>=BOOK_REFRESH):
            self.refresh_broker()
        has_report=self._load_report()
        if has_report:
            if self.dhan and self.dhan.token:
                self._build_runtime()
                self.poll_ohlc()
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
        self._manage_authorized_exits(now)
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
            self.stop_event.wait(3)

    def stop(self):
        self.stop_event.set()
        self.disarm("Service stopped")
        if self.ticker:self.ticker.stop()
        if self.runtime:self.runtime.close()
        if self.dhan_auth:self.dhan_auth.stop()
        if self.thread and self.thread.is_alive() and self.thread!=threading.current_thread():
            self.thread.join(timeout=8)
        self.ledger.close()

    def snapshot(self):
        now=self.now()
        with self.lock:
            signals=self.report["signals"] if self.report and self.report["status"]=="READY" else []
            quotes=dict(self.quote_data)
            positions=list(self.broker_positions)
            orders=list(self.broker_orders)
            fills=list(self.broker_fills)
            ticker=self.ticker.snapshot() if self.ticker else {"state":"NOT_STARTED"}
            monitoring=self._health_check_locked(now)
            result={
                "mode":"LIVE_KOTAK_NEO_ONLY","server_time":now.isoformat(),
                "supervisor_status":self.status,
                "session_date":now.date().isoformat(),
                "signal_status":self.report["status"] if self.report else "UNAVAILABLE",
                "signals":signals,
                "instrument_mappings":[{
                    "symbol":symbol,
                    "dhan_security_id":self.dhan_ids.get(symbol),
                    "kotak_trading_symbol":self.kotak_ids[symbol].trading_symbol if symbol in self.kotak_ids else None,
                    "kotak_token":self.kotak_ids[symbol].token if symbol in self.kotak_ids else None,
                    "status":"READY" if symbol in self.dhan_ids and symbol in self.kotak_ids else
                            "DHAN_READY_KOTAK_PENDING" if symbol in self.dhan_ids else "UNAVAILABLE"
                } for symbol in sorted({row["symbol"] for row in signals})],
                "orb_monitor":{
                    "state":"MONITORING" if self.ticker and self.opening_ranges else "NOT_STARTED",
                    "opening_ranges":[{"symbol":symbol,"high":str(values[0]),"low":str(values[1])}
                                      for symbol,values in sorted(self.opening_ranges.items())],
                    "breakouts":list(self.orb_events.values()),
                    "order_submission_enabled":bool(self.armed and self.runtime),
                },
                "research_cutoff_at":self.report.get("news_cutoff_at") if self.report else None,
                "report_hash":self.report_sha,"armed":self.armed,
                "session_authorized":self.session_authorized,
                "orders_permitted_by_server":self.allowed,
                "market_feed":ticker,"broker_connected":self.broker is not None and self.last_book_at is not None,
                "dhan_authentication":self.dhan_auth.snapshot() if self.dhan_auth else {"state":"NEEDS_SETUP"},
                "broker_books_updated_at":self.last_book_at.isoformat() if self.last_book_at else None,
                "available_cash":self.available_cash,
                "readiness":monitoring,
                "strategy":{"opening_range":"09:15 one-minute candle","entry_cutoff":self.policy.entry_cutoff.isoformat(),
                            "exit_time":self.policy.exit_time.isoformat(),
                            "allocation_per_position":str(self.policy.per_position),
                            "gross_cap":str(self.policy.gross_cap),
                            "max_long":self.policy.max_long,"max_short":self.policy.max_short,
                            "max_positions":self.policy.max_positions,
                            "tick_stop_pct":None},
                "positions":positions,"orders":orders[-70:],"fills":fills[-70:],
                "intents":self.ledger.snapshot(now.date()),
                "error":self.error,"runtime_errors":self.runtime.errors[-10:] if self.runtime else [],
                "quotes":{symbol:{**q,"fresh":bool(
                    (symbol=="INDIAVIX" and q.get("ohlc_at") and
                     now-datetime.fromisoformat(q["ohlc_at"])<=timedelta(seconds=60)) or
                    (symbol!="INDIAVIX" and q.get("tick_at") and
                     now-datetime.fromisoformat(q["tick_at"])<=PRICE_EXPIRY))}
                         for symbol,q in quotes.items()}
            }
        return result
