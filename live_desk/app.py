"""Authenticated LIVE Kotak Neo dashboard + direct news-signal receiver.

No synthetic trades, no local paper ledger, no prediction model, no mock fills.
Real entries require a separately approved, per-session operator arming step.
"""
from __future__ import annotations
import asyncio, hashlib, hmac, os
from datetime import datetime
from pathlib import Path
from fastapi import HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from bridge.app import create_app as bridge_app
from live_desk.dhan_data import IST, totp_now
from live_desk.supervisor import LiveSupervisor
from live_desk.analytics import build_trade_journal
from live_desk.universe import load_nifty500_symbols

ROOT=Path(__file__).resolve().parent
COOKIE="kotak_live_session"

def create_live_app(*,database_url=None,bearer_token=None,dashboard_password=None,
                    session_secret=None,supervisor=None,clock=None,start_supervisor=True):
    password=dashboard_password if dashboard_password is not None else os.getenv("DASHBOARD_PASSWORD","")
    secret=session_secret if session_secret is not None else os.getenv("DASHBOARD_SESSION_SECRET","")
    if len(password)<12 or len(secret)<32:
        raise RuntimeError("Set DASHBOARD_PASSWORD (12+ chars) and DASHBOARD_SESSION_SECRET (32+ chars)")
    if supervisor is not None:
        allowed_symbols=supervisor.allowed_symbols
    else:
        universe_path=Path(os.getenv("NIFTY500_UNIVERSE_PATH",str(ROOT/"nifty500_universe.json")))
        allowed_symbols=load_nifty500_symbols(universe_path)
    app=bridge_app(database_url=database_url,bearer_token=bearer_token,
                   now_provider=clock,allowed_symbols=allowed_symbols)
    desk=supervisor or LiveSupervisor(app.state.engine,clock=clock,
                                       allowed_symbols=allowed_symbols)
    app.state.live=desk
    app.title="LIVE Kotak Neo · News Signal Trading Terminal"
    app.mount("/static",StaticFiles(directory=ROOT/"static"),name="static")

    def expected_cookie():
        day=desk.now().date().isoformat()
        return hmac.new(secret.encode(),("kotak-live:"+day).encode(),hashlib.sha256).hexdigest()

    def logged_in(cookie):
        return hmac.compare_digest(str(cookie or ""),expected_cookie())

    def require(request:Request):
        if not logged_in(request.cookies.get(COOKIE)):
            raise HTTPException(status_code=401,detail="Sign in to live dashboard")

    def origin_allowed(headers, scheme):
        origin=headers.get("origin","").rstrip("/")
        configured=os.getenv("DASHBOARD_PUBLIC_ORIGIN","").strip().rstrip("/")
        if scheme == "ws":scheme = "http"
        elif scheme == "wss":scheme = "https"
        permitted={configured} if configured else {
            f"{scheme}://{headers.get('host','')}"}
        return bool(origin) and origin in permitted

    def check_origin(request:Request):
        # Origin is required for every state-changing browser request.
        if not origin_allowed(request.headers, request.url.scheme):
            raise HTTPException(status_code=403,detail="Invalid action origin")

    @app.on_event("startup")
    def startup():
        if start_supervisor:desk.start()

    @app.on_event("shutdown")
    def shutdown():
        if start_supervisor:desk.stop()
        app.state.engine.dispose()

    @app.get("/health")
    def health():
        return {"status":"up","mode":"LIVE_ONLY","orders_armed":False if not desk else desk.armed}

    @app.get("/ready")
    def ready():
        if not desk.ready():
            raise HTTPException(status_code=503,detail="Live feeds, funds, instruments or session not ready")
        return {"status":"monitoring_live","armed":desk.armed}

    @app.get("/login")
    def login_page(request:Request):
        if logged_in(request.cookies.get(COOKIE)):
            return RedirectResponse("/",status_code=303)
        return FileResponse(ROOT/"static"/"login.html")

    @app.post("/api/auth/login")
    async def login(payload:dict,response:Response):
        if not hmac.compare_digest(str(payload.get("password","")).encode(),password.encode()):
            raise HTTPException(status_code=401,detail="Authentication failed")
        response.set_cookie(COOKIE,expected_cookie(),httponly=True,
                            secure=os.getenv("DASHBOARD_COOKIE_SECURE","true").lower()=="true",
                            samesite="strict",max_age=12*3600,path="/")
        return {"authenticated":True}

    @app.post("/api/auth/logout")
    async def logout(request:Request,response:Response):
        require(request)
        check_origin(request)
        response.delete_cookie(COOKIE,path="/")
        desk.disarm("Operator signed out of live dashboard")
        return {"authenticated":False,"armed":False}

    @app.get("/")
    def index(request:Request):
        if not logged_in(request.cookies.get(COOKIE)):
            return RedirectResponse("/login",status_code=303)
        return FileResponse(ROOT/"static"/"index.html")

    @app.get("/api/state")
    def state(request:Request):
        require(request)
        return desk.snapshot()

    @app.post("/api/live/settings")
    async def update_settings(request:Request,payload:dict):
        require(request)
        check_origin(request)
        try:
            policy=desk.update_settings(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400,detail=str(exc)) from exc
        return {"saved":True,"strategy":{"entry_cutoff":policy.entry_cutoff.isoformat(),
            "exit_time":policy.exit_time.isoformat(),"max_long":policy.max_long,
            "max_short":policy.max_short,"max_positions":policy.max_positions,
            "allocation_per_position":str(policy.per_position),"gross_cap":str(policy.gross_cap)}}

    @app.get("/api/live/orders")
    def order_book(request:Request):
        require(request)
        s=desk.snapshot()
        return {"as_of":s["broker_books_updated_at"],"orders":s["orders"],"real_only":True}

    @app.get("/api/live/positions")
    def position_book(request:Request):
        require(request)
        s=desk.snapshot()
        return {"as_of":s["broker_books_updated_at"],"positions":s["positions"],"real_only":True}

    @app.get("/api/live/fills")
    def fill_book(request:Request):
        require(request)
        s=desk.snapshot()
        return {"as_of":s["broker_books_updated_at"],"fills":s["fills"],"real_only":True}

    @app.get("/api/live/journal")
    def trade_journal(request:Request,limit:int=Query(default=500,ge=1,le=5000)):
        require(request)
        stored=desk.ledger.journal_snapshot(limit)
        return {"as_of":desk.now(),"real_only":True,
                **build_trade_journal(stored["intents"],stored["events"],stored["fills"])}

    @app.post("/api/live/arm")
    async def arm(request:Request,payload:dict):
        require(request)
        check_origin(request)
        if str(payload.get("acknowledgement",""))!="I AUTHORIZE LIVE KOTAK ORDERS":
            raise HTTPException(status_code=400,detail="Explicit real-money order acknowledgement required")
        code=str(payload.get("totp",""))
        secret_value=desk.arm_secret
        if not desk.allowed or not secret_value or len(code)!=6 or not code.isdecimal():
            raise HTTPException(status_code=403,detail="Live order arming disabled or operator verification missing")
        if not hmac.compare_digest(code,totp_now(secret_value)):
            raise HTTPException(status_code=403,detail="Operator verification rejected")
        try:
            return desk.arm()
        except RuntimeError as exc:
            raise HTTPException(status_code=409,detail=str(exc)) from exc

    @app.post("/api/live/disarm")
    async def disarm(request:Request):
        require(request)
        check_origin(request)
        desk.disarm("Operator disabled new live orders")
        return {"armed":False,"note":"Existing real positions must still be monitored and reconciled"}

    @app.websocket("/ws")
    async def realtime(websocket:WebSocket):
        if not logged_in(websocket.cookies.get(COOKIE)):
            await websocket.close(code=4401)
            return
        if not origin_allowed(websocket.headers, websocket.url.scheme):
            await websocket.close(code=4403)
            return
        await websocket.accept()
        try:
            while True:
                await websocket.send_json(desk.snapshot())
                await asyncio.sleep(2)
        except (WebSocketDisconnect,RuntimeError):
            return

    return app

def run():
    import uvicorn
    uvicorn.run(create_live_app(),host=os.getenv("LIVE_HOST","127.0.0.1"),
                port=int(os.getenv("LIVE_PORT","8010")))

if __name__=="__main__":
    run()
