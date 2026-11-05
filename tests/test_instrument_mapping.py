from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from live_desk.dhan_instruments import resolve_dhan_symbols
from live_desk.kotak_instruments import lookup_kotak_equity, resolve_kotak_search


class InstrumentMappingTests(unittest.TestCase):
    def test_kotak_resolves_only_exact_nse_cash_equity(self):
        rows=[
            {"pExchSeg":"nse_cm","pGroup":"EQ","pScripRefKey":"RELIANCE",
             "pTrdSymbol":"RELIANCE-EQ","pSymbol":"2885"},
            {"pExchSeg":"nse_cm","pGroup":"BL","pScripRefKey":"RELIANCE",
             "pTrdSymbol":"RELIANCE-BL","pSymbol":"9001"},
            {"pExchSeg":"bse_cm","pGroup":"EQ","pScripRefKey":"RELIANCE",
             "pTrdSymbol":"RELIANCE-EQ","pSymbol":"2885"},
        ]
        mapped=resolve_kotak_search("RELIANCE",rows)
        self.assertEqual((mapped.symbol,mapped.trading_symbol,mapped.token),("RELIANCE","RELIANCE-EQ","2885"))

    def test_kotak_missing_or_ambiguous_mapping_fails_closed(self):
        row={"pExchSeg":"nse_cm","pGroup":"EQ","pScripRefKey":"TCS",
             "pTrdSymbol":"TCS-EQ","pSymbol":"11536"}
        with self.assertRaises(ValueError):resolve_kotak_search("tcs",[row])
        with self.assertRaises(ValueError):resolve_kotak_search("TCS",[row,{**row,"pSymbol":"11537"}])

    def test_kotak_lookup_uses_nse_cash_search(self):
        class Client:
            def search_scrip(self,**kwargs):
                self.kwargs=kwargs
                return [{"pExchSeg":"nse_cm","pGroup":"EQ","pScripRefKey":"TCS",
                         "pTrdSymbol":"TCS-EQ","pSymbol":"11536"}]
        client=Client();lookup_kotak_equity(client,"TCS")
        self.assertEqual(client.kwargs["exchange_segment"],"nse_cm")
        self.assertEqual(client.kwargs["symbol"],"TCS")

    def test_dhan_nse_equity_master_maps_exact_symbols_and_rejects_missing(self):
        raw=("SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,SEM_TRADING_SYMBOL,SEM_SERIES\n"
             "NSE,E,2885,EQUITY,RELIANCE,EQ\nNSE,E,2886,EQUITY,RELIANCE,BE\n"
             "BSE,E,11536,EQUITY,TCS,EQ\nNSE,E,11536,EQUITY,TCS,EQ\n").encode()
        now=datetime(2026,11,5,9,0,tzinfo=ZoneInfo("Asia/Kolkata"))
        result=resolve_dhan_symbols(raw,{"RELIANCE","TCS"},now)
        self.assertEqual(result.security_ids,{"RELIANCE":"2885","TCS":"11536"})
        with self.assertRaisesRegex(ValueError,"missing"):
            resolve_dhan_symbols(raw,{"MISSING"},now)


if __name__=="__main__":
    unittest.main()
