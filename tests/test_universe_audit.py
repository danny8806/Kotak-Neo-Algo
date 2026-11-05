from __future__ import annotations

import unittest
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from live_desk.universe import (audit_nifty500, eligible_nifty500_symbols,
                                load_nifty500_symbols, parse_nifty500)


class Nifty500UniverseAuditTests(unittest.TestCase):
    def setUp(self):
        self.nifty = (b"Company Name,Industry,Symbol,Series,ISIN Code\n"
                      b"A Ltd,IT,AAA,EQ,INE000A01000\n"
                      b"B REIT,REIT,BBB,RR,INE000B01000\n")
        self.dhan = (b"SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,"
                     b"SEM_TRADING_SYMBOL,SEM_SERIES,SEM_EXCH_INSTRUMENT_TYPE\n"
                     b"NSE,E,111,EQUITY,AAA,EQ,ES\n"
                     b"NSE,E,222,EQUITY,BBB,RR,REIT\n")
        self.kotak = (b"pSymbol,pGroup,pExchSeg,pSymbolName,pTrdSymbol,pScripRefKey,"
                      b"pExchange,pSegment\n"
                      b"333,EQ,nse_cm,AAA,AAA-EQ,AAA,NSE,CASH\n"
                      b"444,RR,nse_cm,BBB,BBB-RR,BBB-RR,NSE,CASH\n")

    def test_official_universe_is_parsed_and_non_equity_is_reported(self):
        constituents = parse_nifty500(self.nifty)
        result = audit_nifty500(constituents=constituents, constituent_csv=self.nifty,
            dhan_csv=self.dhan, kotak_csv=self.kotak,
            as_of=datetime(2026, 11, 5, 9, tzinfo=ZoneInfo("Asia/Kolkata")),
            dhan_source="https://images.dhan.co/api-data/api-scrip-master.csv",
            kotak_source="https://lapi.kotaksecurities.com/master.csv")
        self.assertEqual((result["constituent_count"], result["mapped_count"],
                          result["unavailable_count"]), (2, 1, 1))
        self.assertEqual(result["instruments"][0]["dhan_security_id"], "111")
        self.assertEqual(result["instruments"][0]["kotak_token"], "333")
        self.assertIn("REIT", result["instruments"][1]["reason"])
        self.assertEqual(len(result["sources"]["constituents"]["sha256"]), 64)

    def test_duplicate_symbols_fail_closed(self):
        raw = (b"Company Name,Industry,Symbol,Series,ISIN Code\n"
               b"A Ltd,IT,AAA,EQ,INE000A01000\nA Ltd,IT,AAA,EQ,INE000A01000\n")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            parse_nifty500(raw)

    def test_operational_universe_keeps_only_real_nse_eq_stocks(self):
        raw = (b"Company Name,Industry,Symbol,Series,ISIN Code\n"
               b"A Ltd,IT,AAA,EQ,INE000A01000\n"
               b"A REIT,Realty,AREIT,RR,INE000B01000\n"
               b"B Ltd,Telecom,BBB,BE,INE000C01000\n"
               b"Placeholder,Capital Goods,DUMMY,EQ,DUM000D01000\n")
        self.assertEqual(eligible_nifty500_symbols(parse_nifty500(raw)), {"AAA"})

    def test_naive_audit_clock_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            audit_nifty500(constituents=parse_nifty500(self.nifty), constituent_csv=self.nifty,
                dhan_csv=self.dhan, kotak_csv=self.kotak,
                as_of=datetime(2026, 11, 5), dhan_source="dhan", kotak_source="kotak")

    def test_universe_snapshot_is_loaded_and_stale_snapshot_rejected(self):
        now = datetime(2026, 11, 5, 9, tzinfo=ZoneInfo("Asia/Kolkata"))
        data = {"universe": "NIFTY 500", "as_of": now.isoformat(),
                "source_url": "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
                "constituent_count": 2, "symbols": ["AAA", "BBB"]}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "universe.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(load_nifty500_symbols(path, now=now), {"AAA", "BBB"})
            with self.assertRaisesRegex(ValueError, "stale"):
                load_nifty500_symbols(path, now=now + timedelta(days=46))


if __name__ == "__main__":
    unittest.main()
