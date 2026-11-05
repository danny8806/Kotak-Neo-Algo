"""End-to-end DIRECT HTTP sender->API receiver->database->dashboard-read contract tests.

Tests use in-process FastAPI HTTP transport. No internet, GitHub signal write,
broker connection, or trade placement is performed.
"""
import copy
import json
import unittest
from datetime import datetime
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from bridge.app import create_app
from bridge.send import DeliveryError, deliver
from postgres_test_db import create_test_database

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 4, 9, 5, tzinfo=IST)
URL = "https://receiver.example.org/api/v1/signals/incoming"
TOKEN = "direct-bridge-test-secret-longer-than-32"


def no_trade_report():
    return {
        "schema_version": "1.0", "status": "NO_TRADE", "market": "NSE",
        "timezone": "Asia/Kolkata", "session_date": "2026-11-04",
        "news_cutoff_at": "2026-11-04T09:00:00+05:30",
        "generated_at": "2026-11-04T09:01:00+05:30",
        "execution_enabled": False, "signals": [],
        "reason": "Synthetic NO_TRADE integration test with no real stocks"
    }


class FakeResponse:
    def __init__(self, content):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=262144):
        return self.content


class DirectDeliveryTests(unittest.TestCase):
    def setUp(self):
        database_url, cleanup = create_test_database()
        self.addCleanup(cleanup)
        self.app = create_app(database_url=database_url,
                              bearer_token=TOKEN, now_provider=lambda: NOW)
        self.addCleanup(self.app.state.engine.dispose)
        self.client = TestClient(self.app)
        self.report = no_trade_report()
        self.calls = 0

    def sender(self, *, token=TOKEN, payload=None, url=URL, now=NOW):
        def opener(request, timeout):
            self.calls += 1
            result = self.client.post(
                "/api/v1/signals/incoming", content=request.data,
                headers=dict(request.header_items())
            )
            if result.status_code >= 400:
                raise HTTPError(request.full_url, result.status_code, "rejected", {}, None)
            return FakeResponse(result.content)

        return deliver(copy.deepcopy(payload or self.report), url=url,
                       token=token, now=now, opener=opener)

    def test_direct_http_delivery_and_database_readback(self):
        first = self.sender()
        self.assertEqual(first["status"], "ACCEPTED")
        self.assertFalse(first["execution_enabled"])
        latest = self.client.get("/api/v1/signals/latest",
                                 headers={"Authorization": "Bearer " + TOKEN})
        self.assertEqual(latest.status_code, 200)
        data = latest.json()
        self.assertTrue(data["current"])
        self.assertEqual(data["report"]["status"], "NO_TRADE")
        self.assertEqual(data["report"]["signals"], [])
        self.assertEqual(data["sha256"], first["sha256"])
        self.assertEqual(self.calls, 1)

    def test_idempotent_retry(self):
        self.assertEqual(self.sender()["status"], "ACCEPTED")
        self.assertEqual(self.sender()["status"], "DUPLICATE")

    def test_changed_payload_conflicts_not_overwrites(self):
        self.sender()
        altered = no_trade_report()
        altered["reason"] += " modified"
        with self.assertRaises(DeliveryError):
            self.sender(payload=altered)
        latest = self.client.get("/api/v1/signals/latest",
                                 headers={"Authorization": "Bearer " + TOKEN}).json()
        self.assertEqual(latest["report"]["reason"], self.report["reason"])

    def test_wrong_token_denied(self):
        with self.assertRaises(DeliveryError):
            self.sender(token="different-secret-longer-than-32-characters")

    def test_bare_http_url_blocked_before_network(self):
        with self.assertRaisesRegex(DeliveryError, "HTTPS"):
            self.sender(url="http://receiver.example.org/api/v1/signals/incoming")
        self.assertEqual(self.calls, 0)

    def test_live_execution_flag_blocked_before_network(self):
        unsafe = no_trade_report()
        unsafe["execution_enabled"] = True
        with self.assertRaises(ValueError):
            self.sender(payload=unsafe)
        self.assertEqual(self.calls, 0)

    def test_wrong_date_blocked_before_network(self):
        stale = no_trade_report()
        stale["session_date"] = "2026-11-03"
        with self.assertRaises(ValueError):
            self.sender(payload=stale)
        self.assertEqual(self.calls, 0)

    def test_ready_after_deadline_blocked(self):
        ready = no_trade_report()
        ready["status"] = "READY"
        ready["signals"] = [{
            "symbol": "TESTA", "side": "LONG", "rank": 1,
            "reason": "Synthetic event, for direct delivery tests only.",
            "news_summary": "Synthetic report, not real financial news.",
            "first_available_at": "2026-11-04T08:00:00+05:30",
            "sources": [{"url": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
                         "title": "Test URL; no actual stock event",
                         "published_at": "2026-11-04T08:00:00+05:30"}]
        }]
        late = NOW.replace(hour=9, minute=15)
        with self.assertRaises(DeliveryError):
            self.sender(payload=ready, now=late)
        self.assertEqual(self.calls, 0)

    def test_missing_token_blocked(self):
        with self.assertRaises(DeliveryError):
            self.sender(token="")
        self.assertEqual(self.calls, 0)


if __name__ == "__main__":
    unittest.main()
