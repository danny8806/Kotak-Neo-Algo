"""Read-only Dhan data access for real Kotak Neo trading signals.

No order methods exist. All Dhan credentials are environment-only.
"""
from __future__ import annotations
import base64, hashlib, hmac, math, os, threading, time
from datetime import datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import requests
from live_desk.rate_limit import SlidingWindowLimiter

IST = ZoneInfo("Asia/Kolkata")
AUTH_ROOT = "https://auth.dhan.co"
API_ROOT = "https://api.dhan.co/v2"

def totp_now(secret:str, *, at:float|None=None)->str:
    s = secret.strip().replace(" ","").upper()
    key = base64.b32decode(s + "="*((8-len(s)%8)%8))
    counter = int((time.time() if at is None else at)//30)
    result = hmac.new(key,counter.to_bytes(8,"big"),hashlib.sha1).digest()
    offset = result[-1] & 15
    return f"{(int.from_bytes(result[offset:offset+4],'big')&0x7fffffff)%1000000:06d}"

class DhanDataClient:
    def __init__(self,client_id:str,pin:str,totp_secret:str,*,session=None):
        if not all((client_id,pin,totp_secret)):
            raise ValueError("Dhan market-data credentials missing")
        self.client_id,self.pin,self.totp_secret=client_id,pin,totp_secret
        self.session=session or requests.Session()
        self.token=""
        self.token_created_at=None
        self.token_expiry=None
        self.lock=threading.RLock()
        self.quote_lock=threading.Lock()
        self.history_limiter=SlidingWindowLimiter(5,period=1.05)
        self.history_connections=threading.local()
        self._provided_session=session
        self._quote_last=0.0

    @classmethod
    def from_environment(cls):
        return cls(os.getenv("DHAN_CLIENT_ID",""),
                   os.getenv("DHAN_PIN",""),
                   os.getenv("DHAN_TOTP_SECRET",""))

    def login(self):
        one_time_code=totp_now(self.totp_secret)
        response=self.session.post(AUTH_ROOT+"/app/generateAccessToken",
            params={"dhanClientId":self.client_id,"pin":self.pin,
                    "totp":one_time_code},timeout=20)
        response.raise_for_status()
        data=response.json()
        token=data.get("accessToken") if isinstance(data,dict) else None
        if not token or str(data.get("dhanClientId"))!=self.client_id:
            code=str(data.get("errorCode") or data.get("code") or "")
            detail=str(data.get("errorMessage") or data.get("message") or data.get("error") or "")
            for secret in (self.client_id,self.pin,self.totp_secret,one_time_code,str(token or "")):
                if secret:detail=detail.replace(secret,"[redacted]")
            detail=" ".join(detail.split())[:120]
            suffix=(" ["+code+"]" if code else "")+(": "+detail if detail else "")
            raise RuntimeError("Dhan authentication rejected"+suffix)
        created=datetime.now(IST)
        raw_expiry=data.get("expiryTime")
        expiry=None
        if raw_expiry:
            parsed=datetime.fromisoformat(str(raw_expiry).replace("Z","+00:00"))
            expiry=parsed.replace(tzinfo=IST) if parsed.tzinfo is None else parsed.astimezone(IST)
        if expiry is None or expiry<=created:expiry=created+timedelta(hours=24)
        with self.lock:
            self.token=str(token)
            self.token_created_at=created
            self.token_expiry=expiry
        return self.profile()

    def invalidate_token(self,rejected_token:str|None=None):
        with self.lock:
            if rejected_token is None or self.token==rejected_token:
                self.token="";self.token_created_at=None;self.token_expiry=None

    def _request(self,method:str,url:str,*,json_body=None,session=None,timeout=25):
        with self.lock:token=self.token
        if not token:raise RuntimeError("Dhan token unavailable")
        response=(session if session is not None else self.session).request(method,url,headers={
            "access-token":token,"client-id":self.client_id,
            "Accept":"application/json"},json=json_body,timeout=timeout)
        if response.status_code in (401,403):
            self.invalidate_token(token)
            raise RuntimeError("Dhan token expired or denied")
        response.raise_for_status()
        return response.json()

    def profile(self):
        data=self._request("GET",API_ROOT+"/profile")
        if str(data.get("dhanClientId"))!=self.client_id:
            raise RuntimeError("Dhan profile belongs to different client")
        return {"authenticated":True}

    def get_ohlc(self,securities:dict[str,str],*,exchange_segment:str="NSE_EQ")->dict[str,dict]:
        if not securities:return {}
        if exchange_segment not in {"NSE_EQ","IDX_I"}:
            raise ValueError("Unsupported Dhan quote segment")
        with self.quote_lock:
            delay=1.05-(time.monotonic()-self._quote_last)
            if delay>0:time.sleep(delay)
            self._quote_last=time.monotonic()
            data=self._request("POST",API_ROOT+"/marketfeed/ohlc",
                json_body={exchange_segment:[int(x) for x in securities.values()]})
        book=data.get("data",{}).get(exchange_segment,{})
        reverse={str(sid):symbol for symbol,sid in securities.items()}
        output={}
        for sid,item in book.items():
            symbol=reverse.get(str(sid))
            if symbol is None or not isinstance(item,dict):continue
            try:
                ohlc=item["ohlc"]
                values={k:float(ohlc[k]) for k in ("open","high","low","close")}
                values["ltp"]=float(item["last_price"])
                if all(math.isfinite(v) and v>0 for v in values.values()):
                    output[symbol]=values
            except (ValueError,TypeError,KeyError):continue
        return output

    def get_intraday(self,security_id:str,start:datetime,end:datetime,*,interval:str="1",
                     exchange_segment:str="NSE_EQ",instrument:str="EQUITY")->dict:
        if interval not in {"1","5","15","25","60"}:raise ValueError("Unsupported candle interval")
        if start.utcoffset() is None or end.utcoffset() is None or end<=start:
            raise ValueError("Invalid candle request")
        session=self._provided_session
        if session is None:
            if not hasattr(self.history_connections,"session"):
                self.history_connections.session=requests.Session()
            session=self.history_connections.session
        self.history_limiter.acquire()
        data=self._request("POST",API_ROOT+"/charts/intraday",session=session,timeout=(3,5),json_body={
            "securityId":str(security_id),"exchangeSegment":exchange_segment,
            "instrument":instrument,"interval":interval,"oi":False,
            "fromDate":start.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S"),
            "toDate":end.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")})
        keys=("timestamp","open","high","low","close")
        if any(not isinstance(data.get(k),list) for k in keys):
            raise RuntimeError("Dhan returned incomplete candles")
        if len({len(data[k]) for k in keys})!=1:
            raise RuntimeError("Dhan returned misaligned candles")
        if len(data.get("volume",[]))!=len(data["timestamp"]):
            data["volume"]=[0]*len(data["timestamp"])
        return data

    def websocket_url(self):
        with self.lock:token=self.token
        if not token:raise RuntimeError("Dhan data token unavailable")
        return "wss://api-feed.dhan.co/?"+urlencode({
            "version":"2","token":token,"clientId":self.client_id,"authType":"2"})


class DhanTokenSupervisor:
    """Generate tokens on startup and refresh daily before the 24-hour expiry."""
    def __init__(self,client:DhanDataClient,*,clock=None):
        self.client=client
        self.clock=clock or (lambda:datetime.now(IST))
        self._stop=threading.Event()
        self._thread=None
        self._lock=threading.RLock()
        self._last_refresh_date=None
        self.status={"state":"NOT_STARTED","last_success":None,
                     "expires_at":None,"next_refresh":None}

    def snapshot(self):
        with self._lock:return dict(self.status)

    def _set(self,**values):
        with self._lock:self.status.update(values)

    @staticmethod
    def refresh_due(now,token,expiry,created,last_refresh_date):
        return (not token or last_refresh_date is None or
                (now.hour>=8 and last_refresh_date!=now.date()) or
                (created is not None and now.hour>=8 and
                 created.astimezone(IST)<now.replace(hour=8,minute=0,second=0,microsecond=0)) or
                (expiry is not None and now>=expiry.astimezone(IST)-timedelta(minutes=15)) or
                (created is not None and now>=created.astimezone(IST)+timedelta(hours=23)))

    @staticmethod
    def next_refresh_time(now:datetime,expiry:datetime|None,created:datetime|None):
        scheduled=now.replace(hour=8,minute=0,second=0,microsecond=0)
        if now>=scheduled:scheduled+=timedelta(days=1)
        points=[scheduled]
        if expiry:points.append(expiry.astimezone(IST)-timedelta(minutes=15))
        if created:points.append(created.astimezone(IST)+timedelta(hours=23))
        future=[point for point in points if point>now]
        return min(future) if future else now+timedelta(seconds=1)

    def start(self):
        if self._thread and self._thread.is_alive():return
        self._stop.clear()
        self._thread=threading.Thread(target=self._run,name="dhan-token-supervisor",daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive() and self._thread!=threading.current_thread():
            self._thread.join(timeout=5)

    def _run(self):
        retry=60
        while not self._stop.is_set():
            now=self.clock().astimezone(IST)
            expiry=self.client.token_expiry
            created=self.client.token_created_at
            due=self.refresh_due(now,self.client.token,expiry,created,self._last_refresh_date)
            if due:
                self._set(state="AUTHENTICATING")
                try:
                    self.client.login()
                    now=self.clock().astimezone(IST)
                    self._last_refresh_date=now.date()
                    expiry=self.client.token_expiry
                    created=self.client.token_created_at
                    retry=60
                    nxt=self.next_refresh_time(now,expiry,created)
                    self._set(state="AUTHENTICATED",last_success=now.isoformat(timespec="seconds"),
                              expires_at=expiry.isoformat(timespec="seconds") if expiry else None,
                              error=None,
                              next_refresh=nxt.isoformat(timespec="seconds"))
                except Exception as exc:
                    cooldown="cooldown" in str(exc).lower()
                    delay=125 if cooldown else retry
                    safe_error=(str(exc)[:160] if isinstance(exc,RuntimeError) and
                                str(exc).startswith("Dhan authentication rejected") else
                                "token cooldown" if cooldown else type(exc).__name__)
                    self._set(state="AUTH_ERROR",last_success=None,error=safe_error)
                    self._stop.wait(delay)
                    retry=125 if cooldown else min(retry*2,300)
                    continue
            nxt=self.next_refresh_time(self.clock().astimezone(IST),self.client.token_expiry,
                                       self.client.token_created_at)
            self._set(next_refresh=nxt.isoformat(timespec="seconds"))
            self._stop.wait(max(1,min(60,(nxt-self.clock().astimezone(IST)).total_seconds())))
