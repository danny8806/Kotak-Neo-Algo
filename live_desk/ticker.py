"""Real Dhan v2 ticker feed for LIVE Kotak order decisions; never generates prices.

Only exchange NSE_EQ subscribed symbols reach the runtime. Broker orders are
submitted separately through Kotak Neo. Connection failures suppress entries.
"""
from __future__ import annotations
import json, math, struct, threading
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Callable
from live_desk.dhan_data import DhanDataClient, IST

def parse_ticker(packet:bytes):
    if not isinstance(packet,bytes) or len(packet)<16:return None
    response=packet[0]
    declared=struct.unpack_from("<h",packet,1)[0]
    if response!=2 or declared<16 or declared>len(packet):return None
    segment=packet[3]
    security_id=struct.unpack_from("<i",packet,4)[0]
    price=struct.unpack_from("<f",packet,8)[0]
    timestamp=struct.unpack_from("<i",packet,12)[0]
    if security_id<=0 or not math.isfinite(price) or price<=0:return None
    return {"security_id":str(security_id),"ltp":float(price),
            "exchange_timestamp":datetime.fromtimestamp(timestamp,IST).isoformat(),
            "segment_code":segment}

class LiveTicker:
    def __init__(self,client:DhanDataClient,securities:dict[str,str],on_tick:Callable[[dict],None]):
        self.client=client
        self.map={str(security_id):symbol for symbol,security_id in securities.items()}
        self.on_tick=on_tick
        self.lock=threading.RLock()
        self.stop_event=threading.Event()
        self.thread=None
        self.socket=None
        self.token_created_at=None
        self.status={"state":"NOT_STARTED","last_tick":None,"error":None}

    def snapshot(self):
        with self.lock:return dict(self.status)

    def _set(self,**updates):
        with self.lock:self.status.update(updates)

    def refresh_authentication(self):
        """Reconnect an existing socket after the Dhan supervisor rotates tokens."""
        with self.lock:
            changed=(self.token_created_at is not None and
                     self.token_created_at!=getattr(self.client,"token_created_at",None))
            ws=self.socket
            if changed:
                self.token_created_at=getattr(self.client,"token_created_at",None)
                self.status.update(state="RECONNECTING",error="Dhan token refreshed")
        if changed and ws:
            ws.close()
        return changed

    def start(self):
        if self.thread and self.thread.is_alive():return
        if not self.map:raise ValueError("No Dhan securities to subscribe")
        self.stop_event.clear()
        self.thread=threading.Thread(target=self._run,daemon=True,name="live-dhan-ticker")
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        with self.lock:ws=self.socket
        if ws:
            try:ws.close()
            except Exception:pass
        if self.thread and self.thread.is_alive() and self.thread!=threading.current_thread():
            self.thread.join(timeout=5)
        self._set(state="STOPPED")

    def _run(self):
        import websocket
        retry=1
        while not self.stop_event.is_set():
            try:
                url=self.client.websocket_url()
                self.token_created_at=getattr(self.client,"token_created_at",None)
                def on_open(ws):
                    ws.send(json.dumps({
                        "RequestCode":15,"InstrumentCount":len(self.map),
                        "InstrumentList":[{"ExchangeSegment":"NSE_EQ","SecurityId":id_}
                                          for id_ in self.map]}))
                    self._set(state="CONNECTED",error=None)
                def on_message(ws,message):
                    nonlocal retry
                    if isinstance(message,str):return
                    tick=parse_ticker(message)
                    if tick:
                        symbol=self.map.get(tick["security_id"])
                        if symbol is None:return
                        if tick["segment_code"]!=1:return
                        retry=1
                        now=datetime.now(IST)
                        # Dhan ticker packet carries the exchange's Last Trade Time.
                        # NEVER replace it with local receipt time: doing so could
                        # turn delayed or illiquid prices into executable "fresh" ticks.
                        exchange_at=datetime.fromisoformat(tick["exchange_timestamp"])
                        tick.update(symbol=symbol,timestamp=exchange_at,
                                    received_at=now)
                        self._set(last_tick=now.isoformat(),error=None,
                                  last_exchange_trade_at=exchange_at.isoformat(),
                                  state="CONNECTED")
                        try:self.on_tick(tick)
                        except Exception as exc:
                            self._set(error=f"Tick callback failed ({type(exc).__name__})")
                    elif isinstance(message,bytes) and message and message[0]==50:
                        self._set(state="RECONNECTING",error="Dhan feed disconnected")
                        ws.close()
                def on_error(ws,error):
                    self._set(state="RECONNECTING",error="Market feed transport failure")
                def on_close(ws,code,msg):
                    if not self.stop_event.is_set():
                        self._set(state="RECONNECTING",error=f"Market feed ended ({code})")
                app=websocket.WebSocketApp(url,on_open=on_open,on_message=on_message,
                    on_error=on_error,on_close=on_close)
                with self.lock:self.socket=app
                self._set(state="CONNECTING")
                app.run_forever(ping_interval=20,ping_timeout=10)
            except Exception as exc:
                self._set(state="RECONNECTING",error=f"Market feed connection ({type(exc).__name__})")
            finally:
                with self.lock:self.socket=None
            if not self.stop_event.is_set():
                retry=min(retry*2,60)
                self.stop_event.wait(retry)
