"""Live-only trading terminal safety tests.

All test data is mocked inside the test runner. No actual Kotak Neo order
is submitted and no research signals are published.
"""
from __future__ import annotations
import os
import json
import hashlib
import unittest
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from bridge.app import create_app, reports
from live_desk.app import create_live_app
from live_desk.broker_view import positions as parse_positions
from live_desk.kotak_instruments import KotakInstrument
from live_desk.ledger import LiveLedger
from live_desk.runtime import LiveRuntime
from live_desk.strategy import LivePolicy, candidate_entry
from live_desk.supervisor import LiveSupervisor
from postgres_test_db import create_test_database

IST=ZoneInfo("Asia/Kolkata")
TODAY=datetime(2026,11,4,9,18,tzinfo=IST)
TOKEN="this-is-a-strong-bridge-test-secret-012345"
PASSWORD="TestPassword-Strong-2026"
SESSION="this-is-a-separate-session-key-more-than-thirtytwo"


class FakeBroker:
    def __init__(self):
        self.order_calls=0
    def positions(self):
        return {"stat":"Ok","data":[{"exSeg":"nse_cm","trdSym":"TESTA-EQ",
            "cfBuyQty":0,"flBuyQty":3,"cfSellQty":0,"flSellQty":0,
            "buyAmt":"300","cfBuyAmt":"0","sellAmt":"0","cfSellAmt":"0","prod":"MIS"}]}
    def order_report(self):
        return {"stat":"Ok","data":[]}
    def trade_report(self):
        return {"stat":"Ok","data":[]}
    def limits(self):
        # Official kotakneoapi v3 signature, no legacy filter arguments.
        return {"stat":"Ok","Net":"1500000"}
    def place_order(self, **kwargs):
        self.order_calls+=1
        raise AssertionError("Real order must never be submitted in this test")


class LiveTerminalTests(unittest.TestCase):
    def setUp(self):
        self.db_url, cleanup = create_test_database()
        self.addCleanup(cleanup)
        self.env=patch.dict(os.environ,{"LIVE_ORDERS_ALLOWED":"NO",
             "DASHBOARD_COOKIE_SECURE":"false"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.broker=FakeBroker()
        self.clock=lambda:TODAY
        self.bridge=create_app(
            database_url=self.db_url,
            bearer_token=TOKEN,now_provider=self.clock)
        self.addCleanup(self.bridge.state.engine.dispose)
        self.supervisor=LiveSupervisor(self.bridge.state.engine,
            ledger_path=self.db_url,
            clock=self.clock,kotak_factory=lambda:self.broker)
        self.addCleanup(self.supervisor.ledger.close)
        self.app=create_live_app(database_url=self.db_url,
            bearer_token=TOKEN,dashboard_password=PASSWORD,
            session_secret=SESSION,supervisor=self.supervisor,
            clock=self.clock,start_supervisor=False)
        self.addCleanup(self.app.state.engine.dispose)
        self.client=TestClient(self.app)

    def login(self):
        response=self.client.post("/api/auth/login",json={"password":PASSWORD})
        self.assertEqual(response.status_code,200,response.text)

    def test_login_required_for_broker_books(self):
        for path in ("/","/api/state","/api/live/orders","/api/live/positions","/api/live/fills","/api/live/journal"):
            with self.subTest(path=path):
                response=self.client.get(path,follow_redirects=False)
                self.assertIn(response.status_code,(303,401))
        self.assertEqual(self.client.get("/static/app.js").status_code,200)

    def test_login_and_real_only_dashboard(self):
        self.login()
        page=self.client.get("/")
        self.assertEqual(page.status_code,200)
        self.assertIn("LIVE Kotak positions",page.text)
        self.assertNotIn("Paper positions",page.text)
        self.assertNotIn("paper engine",page.text.lower())
        state=self.client.get("/api/state").json()
        self.assertEqual(state["mode"],"LIVE_KOTAK_NEO_ONLY")
        self.assertFalse(state["armed"])
        self.assertFalse(state["orders_permitted_by_server"])
        self.assertEqual(state["signal_status"],"UNAVAILABLE")

    def test_dashboard_subpath_supports_https_reverse_proxy(self):
        with patch.dict(os.environ,{"DASHBOARD_BASE_PATH":"/kotak-live"}):
            app=create_live_app(database_url=self.db_url,bearer_token=TOKEN,
                dashboard_password=PASSWORD,session_secret=SESSION,
                supervisor=self.supervisor,clock=self.clock,start_supervisor=False)
        self.addCleanup(app.state.engine.dispose)
        client=TestClient(app)
        redirect=client.get("/",follow_redirects=False)
        self.assertEqual(redirect.headers["location"],"/kotak-live/login")
        login_page=client.get("/login")
        self.assertEqual(login_page.status_code,200)
        self.assertIn('<base href="/kotak-live/"/>',login_page.text)
        self.assertRegex(login_page.text, r"route\((['\"])/api/auth/login\1\)")
        auth=client.post("/api/auth/login",json={"password":PASSWORD})
        self.assertEqual(auth.status_code,200)
        cookie=auth.cookies.get("kotak_live_session")
        index=client.get("/",cookies={"kotak_live_session":cookie})
        self.assertEqual(index.status_code,200)
        self.assertIn('<base href="/kotak-live/"/>',index.text)
        self.assertIn('src="static/app.js"',index.text)
        self.assertEqual(client.get("/api/state",cookies={"kotak_live_session":cookie}).status_code,200)

    def test_kotak_books_available_without_news_but_orders_blocked(self):
        self.supervisor.cycle()
        self.login()
        r=self.client.get("/api/live/positions")
        self.assertEqual(r.status_code,200)
        rows=r.json()["positions"]
        self.assertEqual(rows[0]["net_quantity"],3)
        self.assertIsNone(rows[0]["gross_mark_to_market"])
        self.assertFalse(self.client.get("/api/state").json()["armed"])
        self.assertEqual(self.broker.order_calls,0)

    def test_trade_journal_is_authenticated_and_uses_durable_records(self):
        self.login()
        response=self.client.get("/api/live/journal")
        self.assertEqual(response.status_code,200,response.text)
        body=response.json()
        self.assertTrue(body["real_only"])
        self.assertEqual(body["summary"]["trade_count"],0)
        self.assertIsNone(body["summary"]["net_after_fees"])
        self.assertEqual(body["summary"]["wins"],0)

    def test_live_order_arming_rejected_by_default(self):
        self.supervisor.cycle()
        self.login()
        r=self.client.post("/api/live/arm",headers={"Origin":"http://testserver"},
            json={"acknowledgement":"I AUTHORIZE LIVE KOTAK ORDERS","totp":"123456"})
        self.assertIn(r.status_code,(403,409))
        self.assertEqual(self.broker.order_calls,0)

    def test_live_settings_are_authenticated_validated_persistent_and_disarmed_only(self):
        self.login()
        headers={"Origin":"http://testserver"}
        payload={"per_position":"125500.25","max_positions":"3","gross_cap":"300000.25",
                 "max_long":"2","max_short":"1","entry_cutoff":"09:27","exit_time":"14:55"}
        response=self.client.post("/api/live/settings",headers=headers,json=payload)
        self.assertEqual(response.status_code,200,response.text)
        expected={"allocation_per_position":"125500.25","gross_cap":"300000.25",
                  "max_positions":3,"max_long":2,"max_short":1,
                  "entry_cutoff":"09:27:00","exit_time":"14:55:00"}
        self.assertEqual(response.json()["strategy"],expected)
        restarted=LiveSupervisor(self.bridge.state.engine,ledger_path=self.db_url,clock=self.clock,
                                 kotak_factory=lambda:self.broker)
        self.addCleanup(restarted.ledger.close)
        saved=restarted.snapshot()["strategy"]
        self.assertEqual({name:saved[name] for name in expected},expected)
        policy=restarted.policy
        price=Decimal("320.50")
        quantity=candidate_entry(side="LONG",previous_price=Decimal("320"),price=price,
                                 orb_high=Decimal("320"),orb_low=Decimal("300"),
                                 tick_at=TODAY,policy=policy)
        self.assertEqual(quantity,391)
        for symbol,side,side_cap in [("TESTA","BUY",policy.max_long),("TESTB","BUY",policy.max_long),
                                     ("TESTC","SELL",policy.max_short)]:
            reservation=restarted.ledger.reserve_entry(session_date=TODAY.date(),symbol=symbol,
                side=side,quantity=quantity,reference_price=price,at=TODAY,
                max_per_side=side_cap,gross_cap=policy.gross_cap,max_total=policy.max_positions)
            if symbol=="TESTC":
                self.assertIsNone(reservation)
            else:
                self.assertIsNotNone(reservation)
        self.assertEqual(self.broker.order_calls,0)
        invalid=[
            {"max_long":"6"},{"max_positions":"4"},{"max_short":1.5},{"max_long":True},
            {"entry_cutoff":"09:31"},{"exit_time":"09:25"},{"exit_time":"15:00:01"},{"exit_time":"15:15+05:30"},
            {"gross_cap":"125500.24"},{"per_position":"125500.251"},{"per_position":"NaN"},
        ]
        for override in invalid:
            with self.subTest(override=override):
                bad=self.client.post("/api/live/settings",headers=headers,json={**payload,**override})
                self.assertEqual(bad.status_code,400,bad.text)
        self.assertEqual(self.supervisor.ledger.read_settings()["entry_cutoff"],"09:27:00")
        self.supervisor.armed=True
        locked=self.client.post("/api/live/settings",headers=headers,json=payload)
        self.assertEqual(locked.status_code,400)

        legacy = self.supervisor.ledger.read_settings()
        self.supervisor.ledger.write_settings({**legacy, "exit_time": "15:15:00"}, TODAY)
        migrated = LiveSupervisor(self.bridge.state.engine, ledger_path=self.db_url,
                                  clock=self.clock, kotak_factory=lambda: self.broker)
        self.addCleanup(migrated.ledger.close)
        self.assertEqual(migrated.snapshot()["strategy"]["exit_time"], "15:00:00")

    def test_cross_origin_live_action_rejected(self):
        self.login()
        r=self.client.post("/api/live/disarm",headers={"Origin":"https://evil.example"})
        self.assertEqual(r.status_code,403)
        self.assertFalse(self.supervisor.armed)

    def test_automatic_mode_logout_keeps_trading_and_stop_persists_daily_pause(self):
        self.login()
        self.supervisor.auto_trading=True
        self.supervisor.armed=True
        response=self.client.post('/api/auth/logout',headers={'Origin':'http://testserver'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(self.supervisor.armed)
        self.login()
        response=self.client.post('/api/live/disarm',headers={'Origin':'http://testserver'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertFalse(self.supervisor.armed)
        self.assertEqual(self.supervisor.ledger.read_settings()['auto_trading_paused_on'],TODAY.date().isoformat())

    def test_websocket_rejects_cross_origin_even_with_login_cookie(self):
        self.login()
        with self.assertRaises(WebSocketDisconnect) as caught:
            with self.client.websocket_connect("/ws", headers={"Origin":"https://evil.example"}):
                pass
        self.assertEqual(caught.exception.code, 4403)

    def test_websocket_accepts_same_origin_after_login(self):
        self.login()
        with self.client.websocket_connect("/ws", headers={"Origin":"http://testserver"}) as ws:
            self.assertEqual(ws.receive_json()["mode"], "LIVE_KOTAK_NEO_ONLY")

    def insert_current_report(self,*,pipeline_version,cutoff="2026-11-04T09:00:00+05:30",
                              generated="2026-11-04T09:03:00+05:30",key="provenance-test",
                              received=None):
        report={"schema_version":"1.0","status":"READY","market":"NSE",
            "timezone":"Asia/Kolkata","session_date":"2026-11-04",
            "news_cutoff_at":cutoff,"generated_at":generated,"execution_enabled":False,
            "signals":[{"symbol":"TESTA","side":"LONG","rank":1,
                "reason":"Verified company event supports positive directional bias.",
                "news_summary":"A verified company filing provides a positive catalyst.",
                "first_available_at":"2026-11-03T16:00:00+05:30",
                "sources":[{"url":"https://www.nseindia.com/companies-listing/corporate-filings-announcements",
                    "title":"Issuer filing","published_at":"2026-11-03T16:00:00+05:30"}]}]}
        raw=json.dumps(report,sort_keys=True,separators=(",",":"),ensure_ascii=False)
        with self.bridge.state.engine.begin() as db:
            db.execute(reports.insert().values(session_date="2026-11-04",
                idempotency_key=key,sha256=hashlib.sha256(raw.encode()).hexdigest(),
                status="READY",received_at=received or TODAY.replace(minute=14),
                original_json=raw,pipeline_version=pipeline_version))
        return report

    def test_legacy_producer_never_enters_the_executable_watchlist(self):
        self.insert_current_report(pipeline_version=None,key="legacy-provenance")
        report,sha,eligible,_=self.supervisor.today_report()
        self.assertIsNotNone(report)
        self.assertFalse(eligible)
        self.assertEqual(self.supervisor.research_gate["status"],
                         "LEGACY_OR_UNVERIFIED_PRODUCER")

    def test_current_producer_before_lock_is_eligible(self):
        self.insert_current_report(pipeline_version="vps-codex-final-v1",key="current-provenance")
        report,sha,eligible,_=self.supervisor.today_report()
        self.assertTrue(eligible)
        self.assertEqual(self.supervisor.research_gate["status"],"ELIGIBLE")

    def test_late_final_cutoff_is_not_eligible(self):
        self.insert_current_report(pipeline_version="vps-codex-final-v1",key="late-cutoff",
            cutoff="2026-11-04T09:05:00+05:30",generated="2026-11-04T09:06:00+05:30")
        report,sha,eligible,_=self.supervisor.today_report()
        self.assertFalse(eligible)
        self.assertEqual(self.supervisor.research_gate["status"],"CUTOFF_AFTER_0900")

    def test_generation_after_0912_is_not_eligible(self):
        self.insert_current_report(pipeline_version="vps-codex-final-v1",key="late-generation",
            cutoff="2026-11-04T09:00:00+05:30",generated="2026-11-04T09:13:00+05:30")
        report,sha,eligible,_=self.supervisor.today_report()
        self.assertFalse(eligible)
        self.assertEqual(self.supervisor.research_gate["status"],"GENERATED_AFTER_0912")

    def test_websocket_serializes_date_and_decimal_fields(self):
        self.login()
        original=self.supervisor.snapshot
        self.supervisor.snapshot=lambda:{"mode":"LIVE_KOTAK_NEO_ONLY",
            "session_date":date(2026,11,4),"created_at":TODAY,"amount":Decimal("1.25")}
        self.addCleanup(setattr,self.supervisor,"snapshot",original)
        with self.client.websocket_connect("/ws",headers={"Origin":"http://testserver"}) as ws:
            payload=ws.receive_json()
        self.assertEqual(payload["session_date"],"2026-11-04")
        self.assertEqual(payload["amount"],1.25)

    def test_midnight_expires_an_already_connected_dashboard_websocket(self):
        self.login()
        with self.client.websocket_connect("/ws",headers={"Origin":"http://testserver"}) as ws:
            self.assertEqual(ws.receive_json()["session_date"],TODAY.date().isoformat())
            self.supervisor.clock=lambda:TODAY+timedelta(days=1)
            with self.assertRaises(WebSocketDisconnect) as caught:
                ws.receive_json()
            self.assertEqual(caught.exception.code,4401)
        self.assertEqual(self.client.get("/api/state").status_code,401)

    def test_missing_news_never_triggers_kotak_order(self):
        self.supervisor.cycle()
        self.assertIsNone(self.supervisor.runtime)
        self.assertEqual(self.broker.order_calls,0)

    def test_weekend_signal_is_mapped_without_starting_market_runtime(self):
        from live_desk.dhan_instruments import DhanMasterSnapshot

        now=datetime(2026,11,7,10,0,tzinfo=IST)
        report={"schema_version":"1.0","status":"READY","market":"NSE",
            "timezone":"Asia/Kolkata","session_date":"2026-11-07",
            "news_cutoff_at":"2026-11-07T09:00:00+05:30",
            "generated_at":"2026-11-07T10:00:00+05:30","execution_enabled":False,
            "signals":[{"symbol":"TESTA","side":"LONG","rank":1,
                "reason":"Verified company event supports positive directional bias.",
                "news_summary":"A verified company filing provides a positive catalyst.",
                "first_available_at":"2026-11-06T16:00:00+05:30",
                "sources":[{"url":"https://www.nseindia.com/companies-listing/corporate-filings-announcements",
                    "title":"Issuer filing","published_at":"2026-11-06T16:00:00+05:30"}]}]}
        raw=json.dumps(report,sort_keys=True,separators=(",",":"),ensure_ascii=False)
        with self.bridge.state.engine.begin() as db:
            db.execute(reports.insert().values(session_date="2026-11-07",
                idempotency_key="off-session-dhan-map-test",
                sha256=hashlib.sha256(raw.encode()).hexdigest(),status="READY",
                received_at=now,original_json=raw,pipeline_version="vps-codex-final-v1"))

        class Dhan:
            token="test-authenticated-token"

        class Auth:
            def snapshot(self):return {"state":"AUTHENTICATED"}
            def stop(self):pass

        master=lambda symbols,now:DhanMasterSnapshot(now.date().isoformat(),now,"master-sha",
            {symbol:"12345" for symbol in symbols})
        desk=LiveSupervisor(self.bridge.state.engine,ledger_path=self.db_url,clock=lambda:now,
            kotak_factory=lambda:None,master_fetch=master)
        self.addCleanup(desk.stop)
        desk.dhan=Dhan();desk.dhan_auth=Auth()
        desk.cycle()

        self.assertEqual(desk.status,"SIGNALS_MAPPED_OFF_SESSION")
        self.assertEqual(desk.dhan_ids,{"TESTA":"12345"})
        self.assertEqual(desk.kotak_ids,{})
        self.assertIsNone(desk.runtime)
        self.assertIsNone(desk.ticker)
        self.assertFalse(desk.armed)
        self.assertEqual(self.broker.order_calls,0)

    def test_after_cutoff_weekday_signal_maps_without_starting_orb_or_feed(self):
        from live_desk.dhan_instruments import DhanMasterSnapshot

        now=datetime(2026,11,6,15,31,tzinfo=IST)
        stamp=now.isoformat()
        report={"schema_version":"1.0","status":"READY","market":"NSE",
            "timezone":"Asia/Kolkata","session_date":"2026-11-06",
            "news_cutoff_at":stamp,"generated_at":stamp,"execution_enabled":False,
            "signals":[{"symbol":"TESTA","side":"LONG","rank":1,
                "reason":"Verified company event supports positive directional bias.",
                "news_summary":"A verified company filing provides a positive catalyst.",
                "first_available_at":"2026-11-06T14:00:00+05:30",
                "sources":[{"url":"https://www.nseindia.com/companies-listing/corporate-filings-announcements",
                    "title":"Issuer filing","published_at":"2026-11-06T14:00:00+05:30"}]}]}
        raw=json.dumps(report,sort_keys=True,separators=(",",":"),ensure_ascii=False)
        with self.bridge.state.engine.begin() as db:
            db.execute(reports.insert().values(session_date="2026-11-06",
                idempotency_key="after-cutoff-dhan-map-test",
                sha256=hashlib.sha256(raw.encode()).hexdigest(),status="READY",
                received_at=now,original_json=raw,pipeline_version="vps-codex-final-v1"))

        class Dhan:
            token="test-authenticated-token"

        class Auth:
            def snapshot(self):return {"state":"AUTHENTICATED"}
            def stop(self):pass

        master=lambda symbols,now:DhanMasterSnapshot(now.date().isoformat(),now,"master-sha",
            {symbol:"12345" for symbol in symbols})
        desk=LiveSupervisor(self.bridge.state.engine,ledger_path=self.db_url,
            clock=lambda:now,kotak_factory=lambda:None,master_fetch=master)
        self.addCleanup(desk.stop)
        desk.dhan=Dhan();desk.dhan_auth=Auth()
        desk.cycle()

        self.assertEqual(desk.status,"SIGNALS_MAPPED_OFF_SESSION")
        self.assertEqual(desk.dhan_ids,{"TESTA":"12345"})
        self.assertIsNone(desk.runtime)
        self.assertIsNone(desk.ticker)
        self.assertFalse(desk.armed)
        self.assertEqual(self.broker.order_calls,0)

    def test_lock_preserves_the_final_watchlist_on_restart(self):
        from test_failure_cases import ready_fixture

        report=ready_fixture("2026-11-04")
        report["news_cutoff_at"]="2026-11-04T09:00:00+05:30"
        report["generated_at"]="2026-11-04T09:03:00+05:30"
        published_at="2026-11-03T16:15:00+05:30"
        report["signals"][0].update(symbol="TESTA",side="LONG",
                                    first_available_at=published_at)
        report["signals"][0]["sources"][0]["published_at"]=published_at
        raw=json.dumps(report,sort_keys=True,separators=(",",":"),ensure_ascii=False)
        digest=hashlib.sha256(raw.encode()).hexdigest()
        with self.bridge.state.engine.begin() as db:
            db.execute(reports.insert().values(session_date="2026-11-04",
                idempotency_key="final-lock-restart-test",sha256=digest,status="READY",
                received_at=TODAY.replace(hour=9,minute=14,second=59),original_json=raw,
                pipeline_version="vps-codex-final-v1"))

        now=[TODAY.replace(hour=9,minute=16)]
        self.supervisor.clock=lambda:now[0]
        self.assertTrue(self.supervisor._load_report())
        state=self.supervisor.snapshot()
        self.assertTrue(state["watchlist_locked"])
        self.assertEqual(state["report_hash"],digest)
        self.assertEqual([(s["symbol"],s["side"],s["entry_eligible"]) for s in state["signals"]],
                         [("TESTA","LONG",True)])
        self.assertIsNone(self.supervisor.runtime)

        now[0]=TODAY.replace(hour=9,minute=17)
        restarted=LiveSupervisor(self.bridge.state.engine,ledger_path=self.db_url,
                                 clock=lambda:now[0],allowed_symbols={"TESTA"})
        self.addCleanup(restarted.ledger.close)
        self.assertTrue(restarted._load_report())
        restarted_state=restarted.snapshot()
        self.assertTrue(restarted_state["watchlist_locked"])
        self.assertEqual(restarted_state["report_hash"],digest)
        self.assertEqual([(s["symbol"],s["side"],s["entry_eligible"])
                          for s in restarted_state["signals"]],[("TESTA","LONG",True)])

    def test_missing_kotak_configuration_is_quiet_and_disarmed(self):
        keys=("KOTAK_NEO_CONSUMER_KEY","KOTAK_NEO_MOBILE_NUMBER","KOTAK_NEO_UCC",
              "KOTAK_NEO_MPIN","KOTAK_NEO_TOTP_SECRET")
        with patch.dict(os.environ,{key:"" for key in keys}):
            desk=LiveSupervisor(self.bridge.state.engine,
                ledger_path=self.db_url,clock=self.clock)
            self.addCleanup(desk.ledger.close)
            desk.cycle()
        self.assertEqual(desk.status,"NEEDS_KOTAK_CREDENTIALS")
        self.assertEqual(desk.snapshot()["supervisor_status"],"NEEDS_KOTAK_CREDENTIALS")
        self.assertIsNone(desk.error)
        self.assertIsNone(desk.broker)
        self.assertFalse(desk.armed)

    def test_real_dhan_mapping_and_orb_monitor_continue_without_kotak(self):
        from live_desk.dhan_instruments import DhanMasterSnapshot
        from live_desk.orb import IST as ORB_IST
        from datetime import datetime as dt
        from unittest.mock import patch

        report={"schema_version":"1.0","status":"READY","market":"NSE",
            "timezone":"Asia/Kolkata","session_date":"2026-11-04",
            "news_cutoff_at":"2026-11-04T09:00:00+05:30",
            "generated_at":"2026-11-04T09:03:00+05:30","execution_enabled":False,
            "signals":[{"symbol":"TESTA","side":"LONG","rank":1,
                "reason":"Verified company event supports positive directional bias.",
                "news_summary":"A verified company filing provides a positive catalyst.",
                "first_available_at":"2026-11-03T16:00:00+05:30",
                "sources":[{"url":"https://www.nseindia.com/companies-listing/corporate-filings-announcements",
                    "title":"Issuer filing","published_at":"2026-11-03T16:00:00+05:30"}]}]}
        raw=json.dumps(report,sort_keys=True,separators=(",",":"),ensure_ascii=False)
        with self.bridge.state.engine.begin() as db:
            db.execute(reports.insert().values(session_date="2026-11-04",
                idempotency_key="dhan-orb-monitor-test",sha256=hashlib.sha256(raw.encode()).hexdigest(),
                status="READY",received_at=TODAY.replace(minute=14),original_json=raw,
                pipeline_version="vps-codex-final-v1"))

        class Dhan:
            token="test-authenticated-token"
            def get_ohlc(self,securities,exchange_segment="NSE_EQ"):
                return {}
            def get_intraday(self,security_id,start,end,interval="1"):
                candle=dt(2026,11,4,9,16,tzinfo=ORB_IST)
                return {"timestamp":[int(candle.timestamp())],"open":[98],"high":[100],
                    "low":[95],"close":[99],"volume":[100]}

        class Auth:
            def snapshot(self):return {"state":"AUTHENTICATED"}
            def stop(self):pass

        class Ticker:
            def __init__(self,dhan,ids,callback):self.ids=ids;self.callback=callback;self.state="NOT_STARTED"
            def start(self):self.state="CONNECTED"
            def stop(self):self.state="STOPPED"
            def snapshot(self):return {"state":self.state}

        ticker_box={}
        def make_ticker(dhan,ids,callback):
            ticker_box["ticker"]=Ticker(dhan,ids,callback);return ticker_box["ticker"]
        master=lambda symbols,now:DhanMasterSnapshot(now.date().isoformat(),now,"master-sha",
            {symbol:"12345" for symbol in symbols})
        keys=("KOTAK_NEO_CONSUMER_KEY","KOTAK_NEO_MOBILE_NUMBER","KOTAK_NEO_UCC",
              "KOTAK_NEO_MPIN","KOTAK_NEO_TOTP_SECRET")
        with patch.dict(os.environ,{**{key:"" for key in keys},"LIVE_ORDERS_ALLOWED":"NO"}):
            desk=LiveSupervisor(self.bridge.state.engine,ledger_path=self.db_url,clock=self.clock,
                master_fetch=master,ticker_factory=make_ticker)
            self.addCleanup(desk.stop)
            desk.dhan=Dhan();desk.dhan_auth=Auth()
            desk.cycle()
            self.assertEqual(desk.status,"MONITORING_DHAN_ORB_KOTAK_PENDING")
            self.assertEqual(desk.dhan_ids,{"TESTA":"12345"})
            self.assertEqual(desk.opening_ranges["TESTA"],(Decimal("100"),Decimal("95")))
            self.assertIsNone(desk.runtime)
            self.assertEqual(ticker_box["ticker"].state,"CONNECTED")
            self.assertFalse(desk.armed)
            self.assertFalse(desk.ready())
            ticker_box["ticker"].callback({"symbol":"TESTA","ltp":"100","timestamp":TODAY})
            self.assertIn("TESTA",desk.quote_data,"fresh Dhan callback did not reach supervisor")
            ticker_box["ticker"].callback({"symbol":"TESTA","ltp":"100.01",
                "timestamp":TODAY.replace(second=1)})
            self.assertEqual(desk._orb_previous.get("TESTA"),Decimal("100.01"),
                f"tick processing stopped early: ids={desk.dhan_ids}, ranges={desk.opening_ranges}")
            event=desk.snapshot()["orb_monitor"]["breakouts"][0]
            self.assertEqual(event["status"],"ORB_BREAKOUT_OBSERVED")
            self.assertEqual(event["side"],"LONG")
            self.assertEqual(event["execution"],"MONITOR_ONLY")
            self.assertEqual(desk.ledger.snapshot(TODAY.date()),[])

    def test_operator_disarm_clears_entry_gate(self):
        self.supervisor.armed=True
        self.supervisor.disarm("Test")
        self.assertFalse(self.supervisor.armed)
        self.assertEqual(self.broker.order_calls,0)

    def test_market_position_parse_without_price_does_not_fabricate_mtm(self):
        result=parse_positions(self.broker,{})
        self.assertEqual(result[0]["side"],"LONG")
        self.assertIsNone(result[0]["gross_mark_to_market"])

    def test_vix_index_quote_failure_does_not_drop_stock_ohlc(self):
        from unittest.mock import Mock
        self.supervisor.dhan_ids={"TESTA":"123"}
        self.supervisor.dhan=type("Dhan",(),{})()
        self.supervisor.dhan_auth=type("DhanAuth",(),{
            "snapshot":lambda _self:{"state":"AUTHENTICATED"}})()
        self.supervisor.dhan.get_ohlc=Mock(side_effect=[
            RuntimeError("index quote unavailable"),
            {"TESTA":{"open":100,"high":102,"low":99,"close":101,"ltp":101}}])
        self.supervisor.on_tick({"symbol":"TESTA","ltp":103,"timestamp":TODAY})
        self.supervisor._refresh_vix_context(TODAY)
        self.supervisor.poll_ohlc()
        quote=self.supervisor.quote_data["TESTA"]
        self.assertEqual(quote["open"],100)
        self.assertEqual(quote["ltp"],103)
        self.assertEqual(quote["tick_at"],TODAY.isoformat())
        self.assertIsNotNone(self.supervisor.last_ohlc_at)

    def test_live_policy_has_no_implicit_daily_loss_limit(self):
        self.assertFalse(hasattr(self.supervisor,"loss_cap"))

    def test_old_dhan_quote_never_generates_live_mtm(self):
        self.supervisor.session_date=TODAY.date().isoformat()
        self.supervisor.broker=self.broker
        self.supervisor.quote_data={"TESTA":{
            "ltp":125,"tick_at":"2026-11-04T09:00:00+05:30"}}
        self.supervisor.refresh_broker()
        self.assertEqual(len(self.supervisor.broker_positions),1)
        self.assertIsNone(self.supervisor.broker_positions[0]["gross_mark_to_market"])
        self.assertEqual(self.broker.order_calls,0)

    def test_broker_status_includes_real_books_only(self):
        self.supervisor.cycle()
        snapshot=self.supervisor.snapshot()
        self.assertEqual(snapshot["positions"][0]["symbol"],"TESTA")
        self.assertEqual(snapshot["orders"],[])
        self.assertEqual(snapshot["fills"],[])
        self.assertFalse(snapshot["armed"])

    def test_unarmed_exit_at_eod_cannot_call_broker(self):
        class Guard:
            def exit_due(self,at):
                raise AssertionError("Unarmed exit must not submit orders")
        self.supervisor.allowed=True
        self.supervisor.runtime=Guard()
        self.supervisor.session_authorized=False
        self.supervisor._manage_authorized_exits(TODAY.replace(hour=15,minute=20))
        self.assertEqual(self.broker.order_calls,0)

    def test_persisted_entry_allows_close_only_eod_recovery_after_restart(self):
        entry=self.supervisor.ledger.reserve_entry(session_date=TODAY.date(),symbol="TESTA",
            side="BUY",quantity=3,reference_price=Decimal("100"),at=TODAY,
            max_per_side=5,gross_cap=Decimal("100000"))
        self.supervisor.ledger.mark_submission_unknown(entry["tag"],TODAY)
        self.supervisor.ledger.apply_broker_report({"tag":entry["tag"],
            "broker_order_id":"ENTRY-1","status":"filled","ordered_quantity":3,
            "filled_quantity":3,"average_fill_price":"100","trading_symbol":"TESTA-EQ"},TODAY)
        self.supervisor.allowed=True
        self.supervisor.session_authorized=False
        self.supervisor.broker=self.broker
        self.supervisor.runtime=None
        self.supervisor.last_book_at=TODAY.replace(hour=15,minute=19,second=55)
        self.supervisor.broker_positions=parse_positions(self.broker,{})
        with patch("live_desk.supervisor.lookup_kotak_equity",
                   return_value=KotakInstrument("TESTA","TESTA-EQ","456")), \
             patch("live_desk.supervisor.LiveRuntime") as recovered:
            recovered.return_value.exit_due.return_value=[]
            self.supervisor._manage_authorized_exits(TODAY.replace(hour=15,minute=20))
        self.assertTrue(recovered.call_args.kwargs["close_only"])
        self.assertFalse(recovered.call_args.kwargs["enabled"])
        recovered.return_value.exit_due.assert_called_once()


class KotakOrderSafetyTests(unittest.TestCase):
    def setUp(self):
        self.db_url, cleanup = create_test_database()
        self.addCleanup(cleanup)
        self.ledger=LiveLedger(self.db_url)
        self.addCleanup(self.ledger.close)
        self.policy=LivePolicy(time(10),time(15,15),5,5,10,
            Decimal("100000"),Decimal("1000000"),"start")

    def runtime(self,enabled=False):
        return LiveRuntime(
            session_date=date(2026,11,4),
            signals=[{"symbol":"TESTA","side":"LONG"}],
            dhan_ids={"TESTA":"123"},
            kotak_instruments={"TESTA":KotakInstrument("TESTA","TESTA-EQ","456")},
            opening_ranges={"TESTA":(Decimal("100"),Decimal("95"))},
            policy=self.policy,ledger=self.ledger,
            broker=FakeBroker(),enabled=enabled,clock=lambda:TODAY)

    def test_disabled_runtime_never_calls_broker(self):
        runtime=self.runtime(False)
        self.assertIsNone(runtime.on_tick({"symbol":"TESTA","ltp":100,"timestamp":TODAY}))
        self.assertIsNone(runtime.on_tick({"symbol":"TESTA","ltp":101,"timestamp":TODAY}))
        self.assertEqual(runtime.broker.order_calls,0)
        self.assertEqual(self.ledger.snapshot(TODAY.date()),[])

    def test_invalid_or_stale_tick_ignored(self):
        runtime=self.runtime(False)
        for bad in ({"symbol":"TESTA","ltp":-1,"timestamp":TODAY},
                    {"symbol":"TESTA","ltp":100,"timestamp":TODAY.replace(minute=10)},
                    {"symbol":"WRONG","ltp":100,"timestamp":TODAY}):
            self.assertIsNone(runtime.on_tick(bad))
        self.assertEqual(runtime.broker.order_calls,0)

    def test_long_breakout_uses_real_ticks_and_notional_cap(self):
        args=dict(side="LONG",previous_price=Decimal("100"),price=Decimal("101"),
             orb_high=Decimal("100"),orb_low=Decimal("95"),tick_at=TODAY,
             policy=self.policy,open_long=0,open_short=0,reserved_gross=Decimal(0))
        self.assertEqual(candidate_entry(**args),990)
        self.assertIsNone(candidate_entry(**{**args,"open_long":5}))
        self.assertIsNone(candidate_entry(**{**args,"tick_at":TODAY.replace(hour=10)}))

    def test_direct_cross_requires_price_to_move_through_orb_edge(self):
        base=dict(orb_high=Decimal("100"),orb_low=Decimal("95"),tick_at=TODAY,
            policy=self.policy,open_long=0,open_short=0,reserved_gross=Decimal(0))
        long={**base,"side":"LONG"}
        self.assertIsNone(candidate_entry(**{**long,"previous_price":Decimal("99"),"price":Decimal("100")}))
        self.assertIsNotNone(candidate_entry(**{**long,"previous_price":Decimal("100"),"price":Decimal("100.01")}))
        short={**base,"side":"SHORT"}
        self.assertIsNone(candidate_entry(**{**short,"previous_price":Decimal("96"),"price":Decimal("95")}))
        self.assertIsNotNone(candidate_entry(**{**short,"previous_price":Decimal("95"),"price":Decimal("94.99")}))

    def test_twenty_symbol_parallel_entry_fill_eod_exit_and_restart_are_idempotent(self):
        from unittest.mock import patch
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        symbols=[f"LONG{i}" for i in range(1,11)]+[f"SHORT{i}" for i in range(1,11)]
        signals=[{"symbol":s,"side":"LONG" if s.startswith("LONG") else "SHORT"} for s in symbols]
        ids={s:str(i+1) for i,s in enumerate(symbols)}
        instruments={s:KotakInstrument(s,s+"-EQ",str(i+100)) for i,s in enumerate(symbols)}
        ranges={s:(Decimal("100"),Decimal("95")) for s in symbols}
        custom_policy=LivePolicy(time(10),time(15,15),10,10,20,
            Decimal("50000"),Decimal("1000000"),"start",entry_quantity_cap=1)
        runtime=LiveRuntime(session_date=date(2026,11,4),signals=signals,dhan_ids=ids,
            kotak_instruments=instruments,opening_ranges=ranges,policy=custom_policy,
            ledger=self.ledger,broker=FakeBroker(),enabled=True,clock=lambda:TODAY)
        entry_barrier=Barrier(20)
        entries_in_parallel={"enabled":True}
        def submit(_broker,instrument,side,quantity,tag):
            if entries_in_parallel["enabled"]:entry_barrier.wait(timeout=5)
            return {"broker_order_id":"OID-"+tag,"tag":tag,"requires_reconciliation":True}
        def reconcile(_broker,tag):
            row=next(x for x in self.ledger.snapshot(TODAY.date()) if x["tag"]==tag)
            return {"tag":tag,"broker_order_id":"OID-"+tag,"status":"filled",
                "ordered_quantity":row["quantity"],"filled_quantity":row["quantity"],
                "average_fill_price":row["reference_price"],"trading_symbol":row["symbol"]+"-EQ"}
        with patch("live_desk.runtime.place_kotak_order",side_effect=submit), \
             patch("live_desk.runtime.reconcile_kotak_tag",side_effect=reconcile):
            for symbol in symbols:
                side=next(x["side"] for x in signals if x["symbol"]==symbol)
                edge=Decimal("100") if side=="LONG" else Decimal("95")
                cross=Decimal("100.01") if side=="LONG" else Decimal("94.99")
                runtime.on_tick({"symbol":symbol,"ltp":str(edge),"timestamp":TODAY})
            with ThreadPoolExecutor(max_workers=20) as pool:
                results=list(pool.map(lambda item:runtime.on_tick(item),[
                    {"symbol":s,"ltp":"100.01" if s.startswith("LONG") else "94.99","timestamp":TODAY}
                    for s in symbols]))
            self.assertTrue(all(result and result["state"]=="SUBMISSION_QUEUED" for result in results))
            self.assertTrue(runtime.wait_for_submissions())
            entries=[x for x in self.ledger.snapshot(TODAY.date()) if x["phase"]=="ENTRY"]
            self.assertEqual(len(entries),20)
            self.assertTrue(all(x["state"]=="FILLED" for x in entries))
            entries_in_parallel["enabled"]=False
            exit_at=TODAY.replace(hour=15,minute=0)
            self.assertEqual(runtime.exit_due(exit_at - timedelta(microseconds=1)), [])
            exits=runtime.exit_due(exit_at)
            self.assertEqual(len(exits),20)
            restarted=LiveRuntime(session_date=date(2026,11,4),signals=signals,dhan_ids=ids,
                kotak_instruments=instruments,opening_ranges=ranges,policy=self.policy,
                ledger=LiveLedger(self.db_url),broker=FakeBroker(),
                enabled=False,clock=lambda:exit_at)
            self.addCleanup(restarted.ledger.close)
            self.assertEqual(restarted.exit_due(exit_at),[])
            rows=restarted.ledger.snapshot(TODAY.date())
            self.assertEqual(sum(x["phase"]=="EXIT" for x in rows),20)
            self.assertTrue(all(x["state"]=="FILLED" and x["quantity"]==1 for x in rows))

    def test_thirty_simultaneous_breakouts_with_duplicates_reserve_at_most_ten_entries(self):
        """Sixty simultaneous ticks across 30 stocks cannot exceed the 5+5 cap."""
        from concurrent.futures import ThreadPoolExecutor
        from dataclasses import replace
        from threading import Barrier
        symbols=[f"LONG{i}" for i in range(1,16)]+[f"SHORT{i}" for i in range(1,16)]
        signals=[{"symbol":s,"side":"LONG" if s.startswith("LONG") else "SHORT"} for s in symbols]
        ids={s:str(i+1) for i,s in enumerate(symbols)}
        instruments={s:KotakInstrument(s,s+"-EQ",str(i+100)) for i,s in enumerate(symbols)}
        ranges={s:(Decimal("100"),Decimal("95")) for s in symbols}
        runtime=LiveRuntime(session_date=TODAY.date(),signals=signals,dhan_ids=ids,
            kotak_instruments=instruments,opening_ranges=ranges,policy=replace(self.policy, entry_quantity_cap=1),
            ledger=self.ledger,broker=FakeBroker(),enabled=True,clock=lambda:TODAY)
        self.addCleanup(runtime.close)
        submit_tags=[]
        def submit(_broker,instrument,side,quantity,tag):
            submit_tags.append((instrument.symbol,tag,quantity))
            return {"broker_order_id":"OID-"+tag,"tag":tag,"requires_reconciliation":True}
        def reconcile(_broker,tag):
            row=next(x for x in self.ledger.snapshot(TODAY.date()) if x["tag"]==tag)
            return {"tag":tag,"broker_order_id":"OID-"+tag,"status":"filled",
                "ordered_quantity":row["quantity"],"filled_quantity":row["quantity"],
                "average_fill_price":row["reference_price"],"trading_symbol":row["symbol"]+"-EQ"}
        for symbol in symbols:
            edge=Decimal("100") if symbol.startswith("LONG") else Decimal("95")
            self.assertIsNone(runtime.on_tick({"symbol":symbol,"ltp":str(edge),"timestamp":TODAY}))
        gate=Barrier(60)
        ticks=[]
        for symbol in symbols:
            cross="100.01" if symbol.startswith("LONG") else "94.99"
            ticks.extend([{"symbol":symbol,"ltp":cross,"timestamp":TODAY} for _ in range(2)])
        def deliver(tick):
            gate.wait(timeout=5)
            return runtime.on_tick(tick)
        with patch("live_desk.runtime.place_kotak_order",side_effect=submit), \
             patch("live_desk.runtime.reconcile_kotak_tag",side_effect=reconcile), \
             ThreadPoolExecutor(max_workers=60) as pool:
            results=list(pool.map(deliver,ticks))
            self.assertTrue(runtime.wait_for_submissions(timeout=10))
        rows=[r for r in self.ledger.snapshot(TODAY.date()) if r["phase"]=="ENTRY"]
        self.assertGreaterEqual(sum(result is not None for result in results),10)
        self.assertEqual(len(rows),10)
        self.assertEqual(len({row["symbol"] for row in rows}),10)
        self.assertEqual(sum(row["side"]=="BUY" for row in rows),5)
        self.assertEqual(sum(row["side"]=="SELL" for row in rows),5)
        self.assertEqual(len(submit_tags),10)
        self.assertTrue(all(row["quantity"] == 1 for row in rows))
        self.assertEqual(set(submit_tags), {(row["symbol"], row["tag"], 1) for row in rows})
        self.assertTrue(all(row["state"]=="FILLED" for row in rows))

    def test_independent_ledger_workers_serialize_position_caps_without_deadlock(self):
        """Separate app/worker engines must share PostgreSQL cap reservations."""
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        import time as wall_clock

        second_worker = LiveLedger(self.db_url)
        self.addCleanup(second_worker.close)
        ledgers = (self.ledger, second_worker)
        symbols = [f"PARLONG{i}" for i in range(10)] + [f"PARSHORT{i}" for i in range(10)]
        gate = Barrier(len(symbols))

        def reserve(index, symbol):
            side = "BUY" if symbol.startswith("PARLONG") else "SELL"
            gate.wait(timeout=8)
            return ledgers[index % 2].reserve_entry(
                session_date=TODAY.date(), symbol=symbol, side=side, quantity=1,
                reference_price=Decimal("100"), at=TODAY, max_per_side=5,
                gross_cap=Decimal("10000"), max_total=10)

        started = wall_clock.monotonic()
        with ThreadPoolExecutor(max_workers=len(symbols)) as pool:
            results = list(pool.map(lambda item: reserve(*item), enumerate(symbols)))
        elapsed = wall_clock.monotonic() - started
        accepted = [row for row in results if row is not None]
        self.assertLess(elapsed, 10, "Concurrent reservations stalled; possible lock contention/deadlock")
        self.assertEqual(len(accepted), 10)
        self.assertEqual(sum(row["side"] == "BUY" for row in accepted), 5)
        self.assertEqual(sum(row["side"] == "SELL" for row in accepted), 5)
        self.assertEqual(len({row["symbol"] for row in accepted}), 10)
        persisted = [row for row in self.ledger.snapshot(TODAY.date()) if row["phase"] == "ENTRY"]
        self.assertEqual(len(persisted), 10)

    def test_durable_intent_rejects_duplicate_entry(self):
        args=dict(session_date=TODAY.date(),symbol="TESTA",side="BUY",
                  quantity=10,reference_price=Decimal("100"),at=TODAY,
                  max_per_side=5,gross_cap=Decimal("100000"))
        first=self.ledger.reserve_entry(**args)
        self.assertTrue(first)
        self.assertIsNone(self.ledger.reserve_entry(**args))
        self.ledger.mark_submission_unknown(first["tag"],TODAY)
        with self.assertRaises(ValueError):
            self.ledger.mark_submission_unknown(first["tag"],TODAY)

    def test_eod_cancels_open_partial_entry_then_exits_only_confirmed_fill(self):
        from unittest.mock import patch
        entry=self.ledger.reserve_entry(session_date=TODAY.date(),symbol="TESTA",side="BUY",
            quantity=10,reference_price=Decimal("100"),at=TODAY,max_per_side=5,
            gross_cap=Decimal("100000"))
        self.ledger.mark_submission_unknown(entry["tag"],TODAY)
        self.ledger.apply_broker_report({"tag":entry["tag"],"broker_order_id":"ENTRY-1",
            "status":"open","ordered_quantity":10,"filled_quantity":3,
            "average_fill_price":"100","trading_symbol":"TESTA-EQ"},TODAY)
        broker=FakeBroker()
        broker.cancel_order=lambda **kwargs:{"stat":"Ok","nOrdNo":kwargs["order_id"]}
        runtime=self.runtime(False)
        runtime.broker=broker
        reports={"entry":0}
        def reconcile(_broker,tag):
            row=next(x for x in self.ledger.snapshot(TODAY.date()) if x["tag"]==tag)
            if row["phase"]=="ENTRY":
                reports["entry"]+=1
                status="open" if reports["entry"]==1 else "cancelled"
                return {"tag":tag,"broker_order_id":"ENTRY-1","status":status,
                    "ordered_quantity":10,"filled_quantity":3,"average_fill_price":"100",
                    "trading_symbol":"TESTA-EQ"}
            return {"tag":tag,"broker_order_id":"EXIT-1","status":"filled",
                "ordered_quantity":row["quantity"],"filled_quantity":row["quantity"],
                "average_fill_price":"101","trading_symbol":"TESTA-EQ"}
        with patch("live_desk.runtime.cancel_kotak_order") as cancel, \
             patch("live_desk.runtime.place_kotak_order",return_value={"broker_order_id":"EXIT-1"}), \
             patch("live_desk.runtime.reconcile_kotak_tag",side_effect=reconcile):
            results=runtime.exit_due(TODAY.replace(hour=15,minute=15))
        cancel.assert_called_once_with(broker,"ENTRY-1")
        self.assertEqual(len(results),1)
        rows=self.ledger.snapshot(TODAY.date())
        exit_row=next(x for x in rows if x["phase"]=="EXIT")
        self.assertEqual(exit_row["quantity"],3)
        self.assertEqual(exit_row["state"],"FILLED")


if __name__=="__main__":
    unittest.main()
