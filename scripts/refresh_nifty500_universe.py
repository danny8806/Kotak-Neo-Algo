#!/usr/bin/env python3
"""Fetch and validate the official Nifty 500 universe for dashboard intake."""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from live_desk.universe import eligible_nifty500_symbols, parse_nifty500  # noqa: E402

URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"


def main() -> None:
    response = requests.get(URL, timeout=30,
                            headers={"User-Agent": "kotak-neo-live/1.0"})
    response.raise_for_status()
    official_constituents = parse_nifty500(response.content)
    symbols = eligible_nifty500_symbols(official_constituents)
    if not symbols:
        raise ValueError("Official Nifty 500 file contains no eligible NSE EQ shares")
    report = {
        "universe": "NIFTY 500",
        "as_of": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(timespec="seconds"),
        "source_url": URL,
        "source_sha256": hashlib.sha256(response.content).hexdigest(),
        "constituent_count": len(symbols),
        "official_row_count": len(official_constituents),
        "excluded_non_nse_eq_count": len(official_constituents) - len(symbols),
        "symbols": sorted(symbols),
    }
    destination = ROOT / "live_desk" / "nifty500_universe.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    print(f"Refreshed official Nifty 500 NSE EQ snapshot: {len(symbols)} stocks "
          f"({len(official_constituents) - len(symbols)} non-EQ/placeholder rows excluded); "
          f"SHA-256 {report['source_sha256']}")


if __name__ == "__main__":
    main()
