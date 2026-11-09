"""Startup/daily Kotak login with single-flight refresh and bounded backoff.

Only the supervisor performs login. Broker calls use a stable authenticated
client reference without holding the auth lock during HTTP requests. Mutating
requests are never replayed, including when a response signals expired auth.
"""
from datetime import datetime, timedelta
import threading

from live_desk.dhan_data import IST
from live_desk.kotak_response import authentication_rejected
from live_desk.rate_limit import SlidingWindowLimiter


class KotakSessionUnavailable(RuntimeError):
    """No request was sent: the session is refreshing or unavailable."""


class KotakEntryNotSent(RuntimeError):
    """The entry guard failed after rate limiting, before calling the SDK."""


class KotakSession:
    def __init__(self, factory, *, clock=None):
        self.factory = factory
        self.clock = clock or (lambda: datetime.now(IST))
        self.lock = threading.RLock()
        self._client = None
        self._authenticating = False
        self._next_refresh = None
        self._next_attempt = None
        self._retry = 60
        self.order_limiter=SlidingWindowLimiter(10)
        self.read_limiter=SlidingWindowLimiter(20)
        self.status = {"state": "NOT_STARTED", "last_success": None,
                       "next_refresh": None, "next_attempt": None, "error": None}

    def snapshot(self):
        with self.lock:
            return dict(self.status)

    def refresh_due(self):
        now = self.clock().astimezone(IST)
        with self.lock:
            return self._client is None or self._next_refresh is None or now >= self._next_refresh

    def ensure_authenticated(self):
        now = self.clock().astimezone(IST)
        with self.lock:
            if not self.refresh_due():
                return True
            if self._authenticating or (self._next_attempt and now < self._next_attempt):
                return False
            self._authenticating = True
            self._client = None
            self.status.update(state="AUTHENTICATING", error=None)
        try:
            client = self.factory()
            if client is None:
                raise RuntimeError("Kotak login returned no client")
        except Exception as exc:
            with self.lock:
                self._next_attempt = now + timedelta(seconds=self._retry)
                self._retry = min(300, self._retry * 2)
                self.status.update(state="AUTH_ERROR", error=type(exc).__name__,
                                   next_attempt=self._next_attempt.isoformat())
            return False
        else:
            now = self.clock().astimezone(IST)
            nxt = now.replace(hour=8, minute=0, second=0, microsecond=0)
            if now >= nxt:
                nxt += timedelta(days=1)
            with self.lock:
                self._client = client
                self._next_refresh = nxt
                self._next_attempt = None
                self._retry = 60
                self.status.update(state="AUTHENTICATED", last_success=now.isoformat(),
                                   next_refresh=nxt.isoformat(), next_attempt=None, error=None)
            return True
        finally:
            with self.lock:
                self._authenticating = False

    def _invalidate(self, client):
        with self.lock:
            # A delayed failure from an old client cannot erase a new session.
            if self._client is client:
                self._client = None
                self._next_attempt = self.clock().astimezone(IST) + timedelta(seconds=30)
                self.status.update(state="AUTH_EXPIRED", error="Broker session rejected",
                                   next_attempt=self._next_attempt.isoformat())

    def _call(self, name, args, kwargs, *, before_send=None):
        limiter = self.order_limiter if name in {"place_order", "cancel_order"} else self.read_limiter
        limiter.acquire()
        with self.lock:
            client = self._client
        if client is None:
            raise KotakSessionUnavailable("Kotak authentication unavailable; request not sent")
        if before_send is not None and not before_send():
            raise KotakEntryNotSent("Entry authorization or tick freshness expired before dispatch")
        try:
            result = getattr(client, name)(*args, **kwargs)
        except Exception as exc:
            response = getattr(exc, "response", None)
            if getattr(response, "status_code", None) in {401, 403}:
                self._invalidate(client)
            raise
        if authentication_rejected(result):
            self._invalidate(client)
            raise RuntimeError("Kotak authentication rejected; request outcome requires reconciliation")
        return result

    def place_order(self, *args, before_send=None, **kwargs):
        return self._call("place_order", args, kwargs, before_send=before_send)

    def __getattr__(self, name):
        if name not in {"positions", "order_report", "trade_report", "limits",
                        "search_scrip", "cancel_order"}:
            raise AttributeError(name)
        return lambda *args, **kwargs: self._call(name, args, kwargs)
