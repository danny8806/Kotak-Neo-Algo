import copy
import json
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine, text, select
from fastapi.testclient import TestClient
from unittest.mock import patch
from bridge.app import create_app, signal_batches
from live_desk.news_intake import InternalNewsIntake, PIPELINE_VERSION
from postgres_test_db import create_test_database

class InternalNewsTests(unittest.TestCase):
    def setUp(self):
        url,cleanup=create_test_database();self.addCleanup(cleanup)
        self.now=datetime(2026,11,11,9,10,tzinfo=ZoneInfo('Asia/Kolkata'))
        self.app=create_app(database_url=url,bearer_token='x'*32,now_provider=lambda:self.now,allowed_symbols={'IIFL'})
        self.addCleanup(self.app.state.engine.dispose)
        self.report={'schema_version':'1.0','status':'READY','market':'NSE','timezone':'Asia/Kolkata','session_date':'2026-11-11','news_cutoff_at':'2026-11-11T09:00:00+05:30','generated_at':'2026-11-11T09:01:00+05:30','execution_enabled':False,'signals':[{'symbol':'IIFL','side':'LONG','rank':1,'reason':'Source reports an increase in quarterly profit.','news_summary':'The dated source reports increased quarterly profit.','first_available_at':'2026-11-10T18:00:00+05:30','sources':[{'url':'https://nsearchives.nseindia.com/test-results.pdf','title':'Reported financial results','published_at':'2026-11-10T18:00:00+05:30'}]}]}
        self.news=create_engine(url)
        self.addCleanup(self.news.dispose)
        with self.news.begin() as c:c.execute(text('CREATE TABLE news_morning_reports (session_date TEXT PRIMARY KEY, report JSON, evidence JSON)'))
        self.intake=InternalNewsIntake(self.news,self.app.state.engine,allowed_symbols={'IIFL'},clock=lambda:self.now)
    def put(self,version=PIPELINE_VERSION,report=None):
        with self.news.begin() as c:c.execute(text('INSERT INTO news_morning_reports VALUES (:day,CAST(:report AS JSON),CAST(:evidence AS JSON))'),{'day':'2026-11-11','report':json.dumps(report or self.report),'evidence':json.dumps({'pipeline_version':version})})
    def test_direct_internal_delivery_and_restart_dedup(self):
        self.put();a=self.intake.sync_once();b=self.intake.sync_once()
        self.assertEqual((a['status'],b['status']),('ACCEPTED','DUPLICATE'))
        self.assertEqual(a['sha256'],b['sha256'])
        with self.app.state.engine.connect() as c:self.assertEqual(len(c.execute(select(signal_batches)).all()),1)
    def test_accepted_batch_records_pipeline_provenance(self):
        self.put();self.intake.sync_once()
        with self.app.state.engine.connect() as c:
            self.assertEqual(c.execute(select(signal_batches.c.pipeline_version)).scalar_one(),
                             PIPELINE_VERSION)
    def test_late_cutoff_is_rejected_before_storage(self):
        r={**self.report,'news_cutoff_at':'2026-11-11T09:12:00+05:30',
           'generated_at':'2026-11-11T09:12:00+05:30'}
        self.put(report=r)
        self.assertEqual(self.intake.sync_once()['status'],'FINAL_WINDOW_REJECTED')
        with self.app.state.engine.connect() as c:
            self.assertEqual(len(c.execute(select(signal_batches)).all()),0)
    def test_generation_after_deadline_is_rejected_before_storage(self):
        r={**self.report,'generated_at':'2026-11-11T09:13:00+05:30'}
        self.put(report=r)
        self.assertEqual(self.intake.sync_once()['status'],'FINAL_WINDOW_REJECTED')
        with self.app.state.engine.connect() as c:
            self.assertEqual(len(c.execute(select(signal_batches)).all()),0)
    def test_legacy_report_does_not_rewrite_locked_day(self):
        self.put('old');self.assertEqual(self.intake.sync_once()['status'],'LEGACY_REPORT_PRESERVED')
        with self.app.state.engine.connect() as c:self.assertEqual(len(c.execute(select(signal_batches)).all()),0)
    def test_missing_today_never_uses_yesterday(self):
        self.put();self.now=self.now.replace(day=12)
        self.assertEqual(self.intake.sync_once()['status'],'WAITING_FOR_FINAL')
    def test_no_trade_does_not_clear_existing_signals(self):
        r={**self.report,'status':'NO_TRADE','signals':[],'reason':'No source-supported candidates'}
        self.put(report=r);self.assertEqual(self.intake.sync_once()['status'],'NO_NEW_CANDIDATES')
    def test_invalid_symbol_is_blocked_before_storage(self):
        r=copy.deepcopy(self.report);r['signals'][0]['symbol']='INVALID';self.put(report=r)
        with self.assertRaises(ValueError):self.intake.sync_once()
    def test_external_post_retired_in_internal_mode(self):
        with patch.dict('os.environ',{'SIGNAL_INGEST_MODE':'internal'}):
            r=TestClient(self.app).post('/api/v1/signals/incoming',json=self.report,headers={'Authorization':'Bearer '+'x'*32,'Idempotency-Key':'old-route'})
        self.assertEqual(r.status_code,410)
    def test_internal_still_enforces_execution_disabled(self):
        self.report['execution_enabled']=True;self.put()
        with self.assertRaises(ValueError):self.intake.sync_once()

if __name__=='__main__':unittest.main()
