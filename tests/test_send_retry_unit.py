import hashlib
import json
import unittest
from datetime import datetime
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

from bridge.send import DeliveryError, canonical_bytes, deliver

NOW = datetime(2026, 11, 4, 9, 5, tzinfo=ZoneInfo("Asia/Kolkata"))
URL = "https://receiver.example.org/api/v1/signals/incoming"
TOKEN = "direct-bridge-test-secret-longer-than-32"


def report():
    return {
        "schema_version": "1.0", "status": "NO_TRADE", "market": "NSE",
        "timezone": "Asia/Kolkata", "session_date": "2026-11-04",
        "news_cutoff_at": "2026-11-04T09:00:00+05:30",
        "generated_at": "2026-11-04T09:01:00+05:30",
        "execution_enabled": False, "signals": [],
        "reason": "Synthetic retry test payload; no symbols and no trading",
    }


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.body


class SendRetryUnitTests(unittest.TestCase):
    def setUp(self):
        self.payload = report()
        self.calls = []
        self.delays = []

    def ack(self):
        return FakeResponse(json.dumps({
            "status": "ACCEPTED",
            "session_date": self.payload["session_date"],
            "sha256": hashlib.sha256(canonical_bytes(self.payload)).hexdigest(),
        }).encode())

    def test_network_error_retries_same_idempotency_key(self):
        def opener(request, timeout):
            self.calls.append((request, timeout))
            if len(self.calls) == 1:
                raise URLError("temporary connection reset")
            return self.ack()

        result = deliver(self.payload, url=URL, token=TOKEN, now=NOW,
                         opener=opener, sleep_fn=self.delays.append)
        self.assertEqual(result["status"], "ACCEPTED")
        keys = [request.get_header("Idempotency-key") for request, _ in self.calls]
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(self.delays, [0.75])

    def test_server_overload_retries_and_caps_retry_after(self):
        def opener(request, timeout):
            self.calls.append(request)
            if len(self.calls) == 1:
                raise HTTPError(request.full_url, 503, "overloaded", {"Retry-After": "30"}, None)
            return self.ack()

        result = deliver(self.payload, url=URL, token=TOKEN, now=NOW,
                         opener=opener, sleep_fn=self.delays.append)
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(self.delays, [5])
        self.assertEqual(self.calls[0].get_header("Idempotency-key"),
                         self.calls[1].get_header("Idempotency-key"))

    def test_invalid_success_body_retries_idempotently(self):
        def opener(request, timeout):
            self.calls.append(request)
            return FakeResponse(b"not-json") if len(self.calls) == 1 else self.ack()

        result = deliver(self.payload, url=URL, token=TOKEN, now=NOW,
                         opener=opener, sleep_fn=self.delays.append)
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(len(self.calls), 2)

    def test_permanent_unauthorized_does_not_retry(self):
        def opener(request, timeout):
            self.calls.append(request)
            raise HTTPError(request.full_url, 401, "unauthorized", {}, None)

        with self.assertRaisesRegex(DeliveryError, "HTTP 401 after 1 attempt"):
            deliver(self.payload, url=URL, token=TOKEN, now=NOW,
                    opener=opener, sleep_fn=self.delays.append)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.delays, [])


if __name__ == "__main__":
    unittest.main()
