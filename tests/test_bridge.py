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
        self.now = datetime(2026, 11, 4, 9, 5, tzinfo=ZoneInfo("Asia/Kolkata"))
        self.app = create_app(database_url=database_url,
                              bearer_token="x" * 32, now_provider=lambda: self.now)
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
        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertEqual(sum(response.status_code == 200 and response.json()["status"] == "ACCEPTED"
                             for response in responses), 1)
        self.assertEqual(sum(response.json()["status"] == "DUPLICATE" for response in responses), 19)
        retry = self.client.post("/api/v1/signals/incoming", json=self.report, headers=self.headers)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()["status"], "DUPLICATE")

    def test_concurrent_batches_from_separate_workers_accumulate_without_loss(self):
        other_app = create_app(database_url=self.app.state.engine.url.render_as_string(hide_password=False),
                               bearer_token="x" * 32, now_provider=lambda: self.now)
        self.addCleanup(other_app.state.engine.dispose)
        other_client = TestClient(other_app)
        self.addCleanup(other_client.close)
        clients = (self.client, other_client)
        symbols = [f"STOCK{i:02}" for i in range(10)]
        gate = Barrier(len(symbols))

        def make_batch(index, symbol):
            payload = dict(self.report)
            payload.pop("reason")
            payload["status"] = "READY"
            side = "LONG" if index < 5 else "SHORT"
            payload["signals"] = [{
                "symbol": symbol, "side": side, "rank": 1,
                "reason": "Isolated concurrency regression fixture; not a trade recommendation.",
                "news_summary": "Synthetic unit-test event; no live signal is used.",
                "first_available_at": "2026-11-03T18:00:00+05:30",
                "sources": [{"url": "https://nse.example/test", "title": "Synthetic test fixture",
                             "published_at": "2026-11-03T18:00:00+05:30"}],
            }]
            headers = {**self.headers, "Idempotency-Key": f"worker-batch-{index}"}
            gate.wait(timeout=10)
            return clients[index % len(clients)].post(
                "/api/v1/signals/incoming", json=payload, headers=headers)

        with ThreadPoolExecutor(max_workers=len(symbols)) as pool:
            responses = list(pool.map(lambda item: make_batch(*item), enumerate(symbols)))
        self.assertTrue(all(response.status_code == 200 for response in responses),
                        [response.text for response in responses if response.status_code != 200])
        self.assertTrue(all(response.json()["status"] == "ACCEPTED" for response in responses))
        latest = self.client.get("/api/v1/signals/latest", headers=self.headers).json()["report"]
        self.assertEqual({row["symbol"] for row in latest["signals"]}, set(symbols))
        self.assertEqual(sum(row["side"] == "LONG" for row in latest["signals"]), 5)
        self.assertEqual(sum(row["side"] == "SHORT" for row in latest["signals"]), 5)
        self.assertEqual(sorted(row["rank"] for row in latest["signals"] if row["side"] == "LONG"),
                         [1, 2, 3, 4, 5])
        self.assertEqual(sorted(row["rank"] for row in latest["signals"] if row["side"] == "SHORT"),
                         [1, 2, 3, 4, 5])

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

    def test_incremental_batches_accumulate_and_keep_direction_immutable(self):
        first = dict(self.report, status="READY")
        first.pop("reason")
        first["signals"] = [{
            "symbol": "RELIANCE", "side": "LONG", "rank": 1,
            "reason": "Company announced a material order win after the prior session.",
            "news_summary": "The company filing reports a material contract with disclosed scope.",
            "first_available_at": "2026-11-03T18:00:00+05:30",
            "sources": [{"url": "https://nse.example/filing", "title": "Company filing",
                         "published_at": "2026-11-03T18:00:00+05:30"}]
        }]
        first_headers = {**self.headers, "Idempotency-Key": "first-batch"}
        self.assertEqual(self.client.post("/api/v1/signals/incoming", json=first,
                                          headers=first_headers).status_code, 200)

        self.now = datetime(2026, 11, 4, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        later = dict(first)
        later.update(news_cutoff_at="2026-11-04T10:00:00+05:30",
                     generated_at="2026-11-04T10:00:00+05:30")
        later["signals"] = [{
            "symbol": "TCS", "side": "SHORT", "rank": 1,
            "reason": "Company disclosed a material adverse business development.",
            "news_summary": "A verified exchange filing describes a newly reported issue.",
            "first_available_at": "2026-11-04T09:30:00+05:30",
            "sources": [{"url": "https://nse.example/filing-2", "title": "New company filing",
                         "published_at": "2026-11-04T09:30:00+05:30"}]
        }]
        second_headers = {**self.headers, "Idempotency-Key": "second-batch"}
        accepted = self.client.post("/api/v1/signals/incoming", json=later,
                                    headers=second_headers)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        latest = self.client.get("/api/v1/signals/latest", headers=self.headers).json()["report"]
        self.assertEqual([row["symbol"] for row in latest["signals"]], ["RELIANCE", "TCS"])
        self.assertEqual([row["rank"] for row in latest["signals"]], [1, 1])
        duplicate = self.client.post("/api/v1/signals/incoming", json=later,
                                     headers=second_headers)
        self.assertEqual(duplicate.json()["status"], "DUPLICATE")

        flipped = dict(later)
        flipped["signals"] = [dict(later["signals"][0], symbol="RELIANCE", side="SHORT")]
        conflict = self.client.post("/api/v1/signals/incoming", json=flipped,
                                    headers={**self.headers, "Idempotency-Key": "flipped-batch"})
        self.assertEqual(conflict.status_code, 409)


if __name__ == "__main__":
    unittest.main()
