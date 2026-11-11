"""Internal VPS news-to-dashboard handoff: SQL read-only, no HTTP or broker calls."""
from __future__ import annotations
from datetime import datetime
import hashlib
import json
import threading
from sqlalchemy import create_engine, text
from bridge.app import IST, ingest_report
from live_desk.research_contract import FINAL_CUTOFF, FINAL_DEADLINE, PIPELINE_VERSION

class InternalNewsIntake:
    def __init__(self, news_engine, signal_engine, *, allowed_symbols, clock=None):
        self.news_engine, self.signal_engine = news_engine, signal_engine
        self.allowed_symbols = allowed_symbols
        self.clock = clock or (lambda: datetime.now(IST))
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._status = {'mode':'VPS_INTERNAL', 'status':'STARTING'}

    def snapshot(self):
        with self._lock:
            return dict(self._status)

    def _record(self, **values):
        with self._lock:
            self._status = {'mode':'VPS_INTERNAL', 'checked_at':self.clock().isoformat(), **values}
        return self.snapshot()

    def sync_once(self):
        day = self.clock().astimezone(IST).date().isoformat()
        with self.news_engine.connect() as conn:
            row = conn.execute(text('SELECT report, evidence FROM news_morning_reports '
                                    'WHERE session_date=:day'), {'day':day}).mappings().first()
        if not row or not row['report']:
            return self._record(status='WAITING_FOR_FINAL', session_date=day)
        evidence = row['evidence'] or {}
        if evidence.get('pipeline_version') != PIPELINE_VERSION:
            return self._record(status='LEGACY_REPORT_PRESERVED', session_date=day)
        payload = row['report']
        if payload.get('session_date') != day:
            return self._record(status='FINAL_DATE_MISMATCH', session_date=day)
        if payload.get('status') != 'READY' or not payload.get('signals'):
            return self._record(status='NO_NEW_CANDIDATES', session_date=day,
                                research_status=payload.get('status'))
        try:
            cutoff = datetime.fromisoformat(
                str(payload.get('news_cutoff_at')).replace('Z', '+00:00')).astimezone(IST)
            generated = datetime.fromisoformat(
                str(payload.get('generated_at')).replace('Z', '+00:00')).astimezone(IST)
        except (TypeError, ValueError):
            return self._record(status='FINAL_WINDOW_REJECTED', session_date=day,
                                reason='missing or invalid final timestamps')
        if (cutoff.date().isoformat() != day or generated.date().isoformat() != day
                or cutoff.timetz().replace(tzinfo=None) >= FINAL_CUTOFF
                or generated.timetz().replace(tzinfo=None) >= FINAL_DEADLINE
                or generated < cutoff):
            return self._record(status='FINAL_WINDOW_REJECTED', session_date=day,
                reason='final report is outside the decision cutoff / 09:12 deadline')
        raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        ack = ingest_report(self.signal_engine, payload, f'vps-final-{day}-{digest[:24]}',
                            clock=self.clock, allowed_symbols=self.allowed_symbols,
                            pipeline_version=PIPELINE_VERSION, strict_final=True)
        if ack.get('status') not in {'ACCEPTED','DUPLICATE'} or ack.get('session_date') != day or ack.get('sha256') != digest:
            raise ValueError('Internal acknowledgment mismatch')
        return self._record(**ack, symbols=len(payload['signals']))

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.sync_once()
            except Exception as exc:
                # Never log connection strings, raw SQL errors or environment values.
                self._record(status='INTAKE_BLOCKED', error_type=type(exc).__name__)
            self._stop.wait(5)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name='internal-news-intake', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        self.news_engine.dispose()

def configured_intake(signal_engine, allowed_symbols, clock=None):
    import os
    url = os.getenv('INTERNAL_NEWS_DATABASE_URL','')
    if os.getenv('SIGNAL_INGEST_MODE') != 'internal':
        return None
    if not url:
        raise RuntimeError('Internal mode requires the private read-only news database')
    news_engine = create_engine(url, pool_pre_ping=True,
                               connect_args={'connect_timeout':3,
                                             'options':'-c statement_timeout=5000 -c default_transaction_read_only=on'})
    return InternalNewsIntake(news_engine, signal_engine,
                              allowed_symbols=allowed_symbols, clock=clock)
