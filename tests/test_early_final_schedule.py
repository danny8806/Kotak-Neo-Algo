import unittest,copy
from datetime import time
from sqlalchemy import select
from bridge.app import signal_batches
from live_desk.research_contract import FINAL_START,FINAL_DEADLINE,WATCHLIST_LOCK
import test_internal_news_intake as existing

class EarlyFinalIntakeTests(unittest.TestCase):
 def setUp(self):
  self.f=existing.InternalNewsTests('test_direct_internal_delivery_and_restart_dedup')
  self.f.setUp();self.addCleanup(self.f.doCleanups)
 def report(self,cutoff='08:00:00',generated='08:02:00'):
  r=copy.deepcopy(self.f.report)
  r['news_cutoff_at']='2026-11-11T'+cutoff+'+05:30'
  r['generated_at']='2026-11-11T'+generated+'+05:30'
  return r
 def test_0800_report_is_accepted_and_deduplicated(self):
  self.f.put(report=self.report())
  a=self.f.intake.sync_once();b=self.f.intake.sync_once()
  self.assertEqual((a['status'],b['status']),('ACCEPTED','DUPLICATE'))
  self.assertEqual(a['sha256'],b['sha256'])
 def test_pre_0800_cutoff_rejected_before_storage(self):
  self.f.put(report=self.report('07:59:59'))
  self.assertEqual(self.f.intake.sync_once()['status'],'FINAL_WINDOW_REJECTED')
  with self.f.app.state.engine.connect() as c:self.assertEqual(c.execute(select(signal_batches)).all(),[])
 def test_0911_retry_can_finish_before_hard_deadline(self):
  self.f.put(report=self.report('09:11:00','09:11:45'))
  self.assertEqual(self.f.intake.sync_once()['status'],'ACCEPTED')
 def test_0912_generation_rejected(self):
  self.f.put(report=self.report('09:11:00','09:12:00'))
  self.assertEqual(self.f.intake.sync_once()['status'],'FINAL_WINDOW_REJECTED')
 def test_trade_lock_and_hard_deadline_are_unchanged(self):
  self.assertEqual(FINAL_START,time(8,0));self.assertEqual(FINAL_DEADLINE,time(9,12))
  self.assertEqual(WATCHLIST_LOCK,time(9,15))
