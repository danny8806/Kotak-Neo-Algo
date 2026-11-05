import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from bridge.app import create_app
from postgres_test_db import create_test_database


class BridgeTests(unittest.TestCase):
    def setUp(self):
        database_url, cleanup = create_test_database()
        self.addCleanup(cleanup)
        now = datetime(2026, 11, 4, 9, 5, tzinfo=ZoneInfo("Asia/Kolkata"))
        self.app = create_app(database_url=database_url,
                              bearer_token="x" * 32, now_provider=lambda: now)
        self.addCleanup(self.app.state.engine.dispose)
        self.client = TestClient(self.app)
        self.headers = {"Authorization": "Bearer " + "x" * 32,
                        "Idempotency-Key": "2026-11-04-research-v1"}
        self.report = {"schema_version": "1.0", "status": "NO_TRADE", "market": "NSE",
                       "timezone": "Asia/Kolkata", "session_date": "2026-11-04",
                       "news_cutoff_at": "2026-11-04T09:00:00+05:30",
                       "generated_at": "2026-11-04T09:02:00+05:30",
                       "execution_enabled": False, "signals": [],
                       "reason": "No verified candidates today"}

    def test_auth_idempotency_and_current_session(self):
        url = "/api/v1/signals/incoming"
        self.assertEqual(self.client.post(url, json=self.report).status_code, 401)
        accepted = self.client.post(url, json=self.report, headers=self.headers)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["status"], "ACCEPTED")
        duplicate = self.client.post(url, json=self.report, headers=self.headers)
        self.assertEqual(duplicate.json()["status"], "DUPLICATE")
        self.assertEqual(self.client.get("/api/v1/signals/latest", headers=self.headers)
                         .json()["report"]["status"], "NO_TRADE")
        modified = dict(self.report, reason="Different report content")
        self.assertEqual(self.client.post(url, json=modified, headers=self.headers).status_code, 409)

    def test_twenty_concurrent_direct_deliveries_persist_one_idempotent_report(self):
        gate = Barrier(20)
        def send(_):
            gate.wait(timeout=10)
            return self.client.post("/api/v1/signals/incoming", json=self.report,
                                    headers=self.headers)
        with ThreadPoolExecutor(max_workers=20) as pool:
            responses = list(pool.map(send, range(20)))
        self.assertTrue(all(response.status_code in {200, 409} for response in responses))
        self.assertEqual(sum(response.status_code == 200 and response.json()["status"] == "ACCEPTED"
                             for response in responses), 1)
        retry = self.client.post("/api/v1/signals/incoming", json=self.report, headers=self.headers)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()["status"], "DUPLICATE")

    def test_stale_report_rejected(self):
        self.report["session_date"] = "2026-11-03"
        self.assertEqual(self.client.post("/api/v1/signals/incoming", json=self.report,
                                          headers=self.headers).status_code, 422)

    def test_duplicate_json_key_rejected_before_persistence(self):
        raw = json.dumps(self.report).replace('"status": "NO_TRADE",',
            '"status": "READY", "status": "NO_TRADE",', 1)
        response = self.client.post("/api/v1/signals/incoming", content=raw,
                                    headers={**self.headers, "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 422)
        self.assertFalse(self.client.get("/api/v1/signals/status",
                                         headers=self.headers).json()["received"])

    def test_non_json_media_type_rejected(self):
        response = self.client.post("/api/v1/signals/incoming",
                                    content=json.dumps(self.report),
                                    headers={**self.headers, "Content-Type": "text/plain"})
        self.assertEqual(response.status_code, 415)

    def test_configured_nifty_universe_allows_member_and_rejects_nonmember(self):
        ready = dict(self.report)
        ready.update(status="READY")
        ready.pop("reason", None)
        ready["signals"] = [{
            "symbol": "RELIANCE", "side": "LONG", "rank": 1,
            "reason": "Company announced a material order win after the prior session.",
            "news_summary": "The company filing reports a material contract with disclosed scope.",
            "first_available_at": "2026-11-03T18:00:00+05:30",
            "sources": [{"url": "https://nse.example/filing", "title": "Company filing",
                         "published_at": "2026-11-03T18:00:00+05:30"}]
        }]
        database_url, cleanup = create_test_database()
        self.addCleanup(cleanup)
        app = create_app(database_url=database_url,
                         bearer_token="y" * 32,
                         now_provider=lambda: datetime(2026, 11, 4, 9, 5,
                                                      tzinfo=ZoneInfo("Asia/Kolkata")),
                         allowed_symbols={"RELIANCE"})
        self.addCleanup(app.state.engine.dispose)
        client = TestClient(app)
        headers = {"Authorization": "Bearer " + "y" * 32,
                   "Idempotency-Key": "universe-accepted"}
        accepted = client.post("/api/v1/signals/incoming", json=ready, headers=headers)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        ready["signals"][0]["symbol"] = "NOTININDEX"
        blocked = client.post("/api/v1/signals/incoming", json=ready,
                              headers={**headers, "Idempotency-Key": "universe-blocked"})
        self.assertEqual(blocked.status_code, 422)
        self.assertIn("outside the current official Nifty 500", blocked.text)


if __name__ == "__main__":
    unittest.main()
