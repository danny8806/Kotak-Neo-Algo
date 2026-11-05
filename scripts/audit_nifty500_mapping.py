#!/usr/bin/env python3
"""Read-only full-universe Dhan/Kotak NSE cash-equity mapping audit."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_desk.universe import audit_nifty500, parse_nifty500

IST = ZoneInfo("Asia/Kolkata")
NIFTY_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
DHAN_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
MAX_BYTES = 50_000_000


def fetch(url: str, *, allowed_host: str) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != allowed_host:
        raise ValueError(f"Refusing non-HTTPS or unexpected master host: {allowed_host}")
    response = requests.get(url, timeout=60, stream=True,
                            headers={"User-Agent": "kotak-neo-live/1.0"})
    response.raise_for_status()
    body = bytearray()
    for chunk in response.iter_content(chunk_size=65536):
        body.extend(chunk)
        if len(body) > MAX_BYTES:
            raise ValueError(f"Master from {allowed_host} exceeds 50 MB")
    return bytes(body)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kotak-master-url", default=os.getenv("KOTAK_NEO_NSE_CM_MASTER_URL"),
                        help="Current NSE cash master URL returned by Kotak scrip_master API")
    parser.add_argument("--output", type=Path, help="Optional JSON report output path")
    args = parser.parse_args()
    if not args.kotak_master_url:
        parser.error("Provide --kotak-master-url from Kotak's scrip_master API; no URL is guessed")
    constituent_raw = fetch(NIFTY_URL, allowed_host="www.niftyindices.com")
    dhan_raw = fetch(DHAN_URL, allowed_host="images.dhan.co")
    kotak_raw = fetch(args.kotak_master_url, allowed_host="lapi.kotaksecurities.com")
    report = audit_nifty500(
        constituents=parse_nifty500(constituent_raw), constituent_csv=constituent_raw,
        dhan_csv=dhan_raw, kotak_csv=kotak_raw, as_of=datetime.now(IST),
        dhan_source=DHAN_URL, kotak_source=args.kotak_master_url)
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(json.dumps({key: report[key] for key in
                      ("as_of", "universe", "constituent_count", "mapped_count",
                       "unavailable_count", "ambiguous_count", "sources")},
                     indent=2, ensure_ascii=False))
    for row in report["instruments"]:
        if row["status"] != "MAPPED":
            print(f"{row['symbol']}: {row['status']} - {row['reason']}")
    if args.output:
        print(f"Full report: {args.output}")


if __name__ == "__main__":
    main()
