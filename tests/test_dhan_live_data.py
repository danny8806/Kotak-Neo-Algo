from __future__ import annotations

import unittest
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from live_desk.dhan_data import API_ROOT, DhanDataClient, DhanTokenSupervisor, IST


class FakeResponse:
    status_code=200

    def __init__(self,data=None):
        self.data=data or {"data":{"IDX_I":{"21":{"last_price":14.25,
            "ohlc":{"open":14.0,"high":14.5,"low":13.9,"close":14.1}}}}}

    def raise_for_status(self):
        return None

    def json(self):
        return self.data


class FakeSession:
    def __init__(self):
        self.request_args=None
        self.auth_response=None

    def post(self,url,**kwargs):
        return self.auth_response or FakeResponse({"accessToken":"hidden-token",
            "dhanClientId":"client","expiryTime":"2026-11-06T00:00:00+05:30"})

    def request(self,method,url,**kwargs):
        self.request_args=(method,url,kwargs)
        return FakeResponse()


class DhanIndexDataTests(unittest.TestCase):
    def test_vix_ohlc_uses_index_segment_and_returns_real_quote_fields(self):
        session=FakeSession()
        client=DhanDataClient("client","pin","JBSWY3DPEHPK3PXP",session=session)
        client.token="test-token"

        result=client.get_ohlc({"INDIAVIX":"21"},exchange_segment="IDX_I")

        self.assertEqual(result["INDIAVIX"],{
            "open":14.0,"high":14.5,"low":13.9,"close":14.1,"ltp":14.25})
        method,url,kwargs=session.request_args
        self.assertEqual(method,"POST")
        self.assertEqual(url,API_ROOT+"/marketfeed/ohlc")
        self.assertEqual(kwargs["json"],{"IDX_I":[21]})

    def test_history_allows_five_parallel_reads_and_paces_sixth(self):
        arrivals=[]
        lock=threading.Lock()
        first_wave=threading.Barrier(5)
        class Session:
            def request(self,_method,_url,**kwargs):
                with lock:
                    arrivals.append(time.monotonic())
                    ordinal=len(arrivals)
                if ordinal<=5:
                    first_wave.wait(timeout=2)
                return FakeResponse({field:[1] for field in ("timestamp","open","high","low","close")})
        client=DhanDataClient("test","pin","test",session=Session())
        client.token="test-token"
        now=datetime(2026,11,5,9,15,tzinfo=IST)
        with ThreadPoolExecutor(6) as pool:
            results=list(pool.map(lambda n:client.get_intraday(str(n),now,now+timedelta(minutes=2)),range(6)))
        self.assertEqual(len(results),6)
        self.assertGreaterEqual(arrivals[-1]-arrivals[0],1.0)

    def test_ohlc_rejects_unconfigured_exchange_segments(self):
        client=DhanDataClient("client","pin","JBSWY3DPEHPK3PXP",session=FakeSession())
        with self.assertRaisesRegex(ValueError,"segment"):
            client.get_ohlc({"TEST":"1"},exchange_segment="MCX_COMM")

    def test_token_supervisor_authenticates_and_hides_access_token(self):
        done=threading.Event()
        now=datetime(2026,11,5,9,0,tzinfo=IST)
        class Client:
            token=""
            token_created_at=None
            token_expiry=None
            def login(self):
                self.token="do-not-show-this-token"
                self.token_created_at=now
                self.token_expiry=now+timedelta(hours=24)
                done.set()
        manager=DhanTokenSupervisor(Client(),clock=lambda:now)
        manager._set(error="stale authentication error")
        manager.start()
        try:
            self.assertTrue(done.wait(1))
            deadline=time.monotonic()+1
            while manager.snapshot().get("state")!="AUTHENTICATED" and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertEqual(manager.snapshot()["state"],"AUTHENTICATED")
            self.assertIsNone(manager.snapshot().get("error"))
            self.assertNotIn("do-not-show-this-token",str(manager.snapshot()))
        finally:manager.stop()

    def test_auth_rejection_redacts_credentials_and_totp(self):
        class RejectedSession:
            def post(self,url,**kwargs):
                return FakeResponse({"message":"bad PIN 12345 and code 654321"})
        client=DhanDataClient("client-id","12345","JBSWY3DPEHPK3PXP",session=RejectedSession())
        with patch("live_desk.dhan_data.totp_now",return_value="654321"):
            with self.assertRaises(RuntimeError) as caught:client.login()
        self.assertNotIn("12345",str(caught.exception))
        self.assertNotIn("654321",str(caught.exception))
        self.assertIn("bad PIN [redacted]",str(caught.exception))


if __name__=="__main__":
    unittest.main()
