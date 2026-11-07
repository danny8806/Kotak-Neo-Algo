"""Send a schema-v1 research report DIRECTLY to a dashboard API (not through GitHub).

Usage (cloud-hosted HTTPS service):
    export SIGNAL_BRIDGE_TOKEN='<long secret>'
    python -m bridge.send --file /secure/path/to/report.json \
        --url https://dashboard.example.org/api/v1/signals/incoming

This is an opt-in publisher, NOT a market-data collector or scheduled news producer.
No broker or live-order integrations are invoked.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from validate_signals import validate

IST = ZoneInfo("Asia/Kolkata")
MAX_BYTES = 256_000


class DeliveryError(RuntimeError):
    pass


def canonical_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def deliver(payload: dict, *, url: str, token: str, timeout: float = 12.0,
            allow_http_localhost: bool = False, now: datetime | None = None,
            opener=urlopen) -> dict:
    """Reject malformed/stale input and unsafe URLs BEFORE attempting network I/O."""
    now = (now or datetime.now(IST)).astimezone(IST)
    validate(payload, expected_date=now.date().isoformat())
    if payload["session_date"] is None:
        raise DeliveryError("Bootstrap reports are not valid delivery payloads")
    if payload.get("sample") is True:
        raise DeliveryError("Sample reports are not allowed")
    if now.hour < 9:
        raise DeliveryError("Reports cannot be delivered before 09:00 IST")
    if payload["status"] == "READY" and now.time() >= datetime.strptime("15:00", "%H:%M").time():
        raise DeliveryError("READY delivery deadline passed (15:00 IST)")
    if not isinstance(token, str) or len(token) < 32:
        raise DeliveryError("A 32+ character SIGNAL_BRIDGE_TOKEN is required")

    uri = urlparse(url)
    insecure_local = (uri.scheme == "http" and uri.hostname in ("localhost", "127.0.0.1", "::1")
                      and allow_http_localhost)
    if not ((uri.scheme == "https" and uri.hostname) or insecure_local):
        raise DeliveryError("Use a HTTPS bridge URL (HTTP localhost requires explicit test opt-in)")
    if uri.username or uri.password or uri.fragment or uri.query:
        raise DeliveryError("URL must not contain credentials, query strings or fragments")
    if uri.path.rstrip("/") != "/api/v1/signals/incoming":
        raise DeliveryError("URL must use the /api/v1/signals/incoming endpoint")

    raw = canonical_bytes(payload)
    if len(raw) > MAX_BYTES:
        raise DeliveryError("Report exceeds the maximum allowed size")
    digest = hashlib.sha256(raw).hexdigest()
    idempotency_key = f"research-{payload['session_date']}-{digest[:24]}"
    request = Request(
        url,
        data=raw,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": "Bearer " + token,
            "Idempotency-Key": idempotency_key,
        },
    )
    try:
        with opener(request, timeout=timeout) as response:
            result = json.loads(response.read(MAX_BYTES + 1))
    except HTTPError as exc:
        raise DeliveryError(f"Bridge returned HTTP {exc.code}") from exc
    except (URLError, OSError, ValueError, UnicodeDecodeError) as exc:
        raise DeliveryError(f"Bridge delivery or response failed: {type(exc).__name__}") from exc

    if not isinstance(result, dict) or result.get("status") not in ("ACCEPTED", "DUPLICATE"):
        raise DeliveryError("Bridge did not acknowledge acceptance or a verified duplicate")
    if result.get("sha256") != digest or result.get("session_date") != payload["session_date"]:
        raise DeliveryError("Bridge acknowledgment mismatch; delivery not verified")
    return {"status": result["status"], "session_date": result["session_date"],
            "sha256": digest, "execution_enabled": False}


def main():
    parser = argparse.ArgumentParser(description="Authenticated direct API signal delivery (research only)")
    parser.add_argument("--file", required=True, type=Path, help="Input JSON file to deliver")
    parser.add_argument("--url", default=os.environ.get("SIGNAL_BRIDGE_URL"),
                        help="Public HTTPS receiver endpoint")
    parser.add_argument("--allow-http-localhost", action="store_true",
                        help="Opt-in only for tests on loopback")
    args = parser.parse_args()

    if not args.url:
        parser.error("Supply --url or SIGNAL_BRIDGE_URL")
    token = os.environ.get("SIGNAL_BRIDGE_TOKEN", "")
    try:
        payload = json.loads(args.file.read_text(encoding="utf-8"))
        result = deliver(payload, url=args.url, token=token,
                         allow_http_localhost=args.allow_http_localhost)
    except (ValueError, OSError, DeliveryError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
