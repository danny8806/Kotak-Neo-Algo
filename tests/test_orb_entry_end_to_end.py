"""Recorded candles -> supervisor -> live packets -> PG -> real SDK mock wire.

All orders remain inside httpx.MockTransport on an internal-only test network.
Valid/adversarial ticks are constructed; historical tick history is not invented.
"""
import json,os,struct,threading,unittest
from pathlib import Path
from datetime import datetime,timedelta
from decimal import Decimal
from time import perf_counter_ns
from urllib.parse import parse_qs
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import httpx
from neo_api_client import NeoAPI
from sqlalchemy import select
from bridge.app import create_app,ingest_report
from live_desk.supervisor import LiveSupervisor
from live_desk.dhan_data import IST
from live_desk.dhan_instruments import DhanMasterSnapshot
from live_desk.kotak_instruments import KotakInstrument
from live_desk.ledger import intents,events
from live_desk.ticker import parse_ticker
from postgres_test_db import create_test_database

FIXTURE=json.loads((Path(__file__).parent/'fixtures/orb-entry-e2e-2026-11-12.json').read_text())
START=datetime(2026,11,12,9,15,tzinfo=IST)
# Independent expectations from the retained raw 09:15 OHLC, not the selector.
EXPECTED={'CUPID':('362','356'),'COFORGE':('2020','1983.1'),
 'POWERGRID':('264.5','262.8'),'COCHINSHIP':('1209','1196'),'LT':('4210','4185.2'),
 'AUROPHARMA':('1813.1','1795.9'),'PIIND':('1759.1','1744'),
 'AEGISLOG':('1249.9','1225'),'EXIDEIND':('384.95','382.4'),'ARE&M':('763.95','753.65')}

class EntryPathTests(unittest.TestCase):
    def setUp(self):
        self.url,cleanup=create_test_database();self.addCleanup(cleanup)
        self.env=patch.dict(os.environ,{'LIVE_ORDERS_ALLOWED':'YES','LIVE_AUTO_TRADING':'NO',
            'LIVE_FIXED_ENTRY_QUANTITY':'2','LIVE_ENTRY_QUANTITY_CAP':'',
            'LIVE_ORB_AUDIT_DIR':'','NEO_LOG_FILE_ENABLED':'false','NEO_LOG_LEVEL':'NOLOG'})
        self.env.start();self.addCleanup(self.env.stop)
        self.now=START-timedelta(minutes=1);self.wire=[];self.order_rows=[];self.history=[];self.checks=[]
        self.lock=threading.Lock()
        def transport(request):
            if request.url.host!='broker.test' or request.url.path!='/quick/order/rule/ms/place':
                raise AssertionError('Unexpected external API path in isolated test')
            body=json.loads(parse_qs(request.content.decode())['jData'][0])
            with self.lock:
                oid='ISOLATED-'+str(len(self.wire)+1);self.wire.append(body)
                self.order_rows.append({'GuiOrdId':body['ig'],'nOrdNo':oid,
                    'qty':str(body['qt']),'ordSt':'open','trdSym':body['ts']})
            return httpx.Response(200,json={'stat':'Ok','stCode':200,'nOrdNo':oid})
        self.broker=NeoAPI(consumer_key='isolated-fixture',environment='prod',transport=httpx.MockTransport(transport))
        self.broker.configuration.edit_token='isolated-fixture'
        self.broker.configuration.edit_sid='isolated-fixture'
        self.broker.configuration.edit_rid='isolated-fixture'
        self.broker.configuration.base_url='https://broker.test'
        self.broker.order_report=lambda:{'stat':'Ok','data':list(self.order_rows)}
        self.broker.trade_report=lambda:{'stat':'Ok','data':[]}
        self.broker.positions=lambda:{'stat':'Ok','data':[]}
        self.broker.limits=lambda:{'stat':'Ok','Net':'0'}
        self.mappings={m['symbol']:m for m in FIXTURE['mappings']}
        self.signals={s['symbol']:s['side'] for s in FIXTURE['report']['signals']}
        self.ids={s:m['dhan_security_id'] for s,m in self.mappings.items()}
        by_id={str(v):k for k,v in self.ids.items()};old={r['symbol']:r for r in FIXTURE['incorrect_ranges']}
        source={r['symbol']:r['candles'] for r in FIXTURE['routes']}
        test=self
        class Dhan:
            token='isolated-data-token'
            def get_intraday(self,security_id,start,end,*,interval):
                test.history.append((str(security_id),start.isoformat(),end.isoformat(),interval))
                symbol=by_id[str(security_id)];bars=[dict(x) for x in source[symbol]]
                # Reproduce the forming-bar hazard with recorded partial H/L.
                # The partial close here is a fixture value, not historical fact.
                for bar in bars:
                    if bar['timestamp'][11:19]=='09:16:00':
                        bar.update(high=float(old[symbol]['high']),low=float(old[symbol]['low']),close=bar['open'])
                return {'timestamp':[b['epoch'] for b in bars],
                    **{k:[b[k] for b in bars] for k in ['open','high','low','close','volume']}}
            def get_ohlc(self,*args,**kwargs):return {}
        class Auth:
            def snapshot(self):return {'state':'AUTHENTICATED'}
            def stop(self):pass
        class Ticker:
            def __init__(self,*args):self.running=False
            def start(self):self.running=True
            def stop(self):self.running=False
            def snapshot(self):return {'state':'CONNECTED','error':None,'connection_generation':1}
            def add_symbols(self,*args):pass
            def connection_is_current(self,generation):return generation==1
        self.app=create_app(database_url=self.url,bearer_token='isolated-test-token-long-enough-000',
            now_provider=lambda:self.now,allowed_symbols=set(self.signals))
        self.addCleanup(self.app.state.engine.dispose)
        ingest_report(self.app.state.engine,FIXTURE['report'],'isolated-recorded-report',clock=lambda:self.now,
            allowed_symbols=set(self.signals),pipeline_version='vps-codex-final-v1',strict_final=True)
        self.desk=LiveSupervisor(self.app.state.engine,ledger_path=self.url,clock=lambda:self.now,
            kotak_factory=lambda:self.broker,ticker_factory=Ticker,
            master_fetch=lambda symbols,now:DhanMasterSnapshot(now.date().isoformat(),now,'isolated-map',
                {s:self.ids[s] for s in symbols}),allowed_symbols=set(self.signals))
        self.addCleanup(self.desk.stop)
        self.desk._new_day(START.date().isoformat())
        self.desk.dhan=Dhan();self.desk.dhan_auth=Auth()
        self.desk._kotak_master={s:KotakInstrument(s,m['kotak_trading_symbol'],m['kotak_token']) for s,m in self.mappings.items()}
        self.desk.cycle()

    def tearDown(self):
        self.desk.runtime and self.desk.runtime.wait_for_submissions()
        output=os.getenv('ENTRY_RETEST_OUTPUT_DIR')
        if output:
            with self.desk.ledger.engine.connect() as c:
                db=[{k:str(v) for k,v in r.items()} for r in c.execute(select(intents)).mappings()]
                proof=[{k:str(v) for k,v in r.items()} for r in c.execute(select(events).where(events.c.state=='ORB_TRIGGER')).mappings()]
            Path(output).mkdir(exist_ok=True,parents=True)
            (Path(output)/(self._testMethodName+'.json')).write_text(json.dumps(
                {'case':self._testMethodName,'sdk_mock_requests':self.wire,'checks':self.checks,
                 'source_requests':self.history,'intents':db,'tick_proofs':proof},default=str,indent=2))

    def emit(self,symbol,price,*,age=0,generation=1,advance=True):
        if advance:self.now+=timedelta(seconds=1)
        self.desk.refresh_broker()
        at=self.now-timedelta(seconds=age)
        packet=struct.pack('<BhBifi',2,16,1,int(self.ids[symbol]),float(price),int(at.timestamp()))
        tick=parse_ticker(packet);self.assertIsNotNone(tick)
        tick.update(symbol=symbol,timestamp=datetime.fromisoformat(tick['exchange_timestamp']),
            received_ns=perf_counter_ns(),feed_generation=generation)
        self.desk.on_tick(tick)
        if self.desk.runtime:self.assertTrue(self.desk.runtime.wait_for_submissions(3))

    def prepare(self):
        self.now=START+timedelta(seconds=59);self.desk.cycle()
        self.assertIsNone(self.desk.runtime);self.assertEqual(self.history,[])
        for route in FIXTURE['routes']:
            first=next(b for b in route['candles'] if b['timestamp'][11:19]=='09:15:00')
            self.emit(route['symbol'],first['close'],advance=False)
        self.now=START+timedelta(minutes=1);self.desk.cycle()
        self.assertIsNotNone(self.desk.runtime)
        self.desk.arm();self.assertTrue(self.desk.armed)

    def assert_zero(self,label):
        self.checks.append({'step':label,'expected_orders':0,'observed_orders':len(self.wire)})
        self.assertEqual(len(self.wire),0,label)
        with self.desk.ledger.engine.connect() as c:self.assertEqual(len(c.execute(select(intents)).all()),0)

    def assert_one_payload(self,symbol,expected_count):
        self.assertEqual(len(self.wire),expected_count)
        payload=self.wire[-1]
        self.assertEqual(payload['ts'],symbol+'-EQ');self.assertEqual(int(payload['qt']),2)
        self.assertEqual(payload['tt'],'B' if self.signals[symbol]=='LONG' else 'S')
        text=json.dumps(payload);self.assertIn('MIS',text);self.assertIn('MKT',text)

    def test_actual_first_bar_and_close_alone_never_submit(self):
        self.prepare()
        for symbol,(high,low) in EXPECTED.items():
            self.assertEqual(self.desk.opening_ranges[symbol],(Decimal(high),Decimal(low)))
        self.assertTrue(all(r[1].endswith('09:14:00+05:30') for r in self.history))
        for route in FIXTURE['routes']:
            close=next(b['close'] for b in route['candles'] if b['timestamp'][11:19]=='09:15:00')
            self.emit(route['symbol'],close)
        self.assert_zero('first minute completed; no actual range breakout')

    def test_rejects_todays_nine_wrong_entry_prices(self):
        self.prepare()
        old={r['symbol']:r for r in FIXTURE['incorrect_ranges']}
        for entry in FIXTURE['bad_entries']:
            symbol=entry['symbol'];bound=old[symbol]['high' if self.signals[symbol]=='LONG' else 'low']
            self.emit(symbol,bound);self.emit(symbol,entry['reference_price'])
        self.assert_zero('all nine recorded premature entries with fresh eligible previous ticks')

    def test_opposite_signal_direction_never_submits(self):
        self.prepare()
        for symbol,(h,l) in EXPECTED.items():
            high,low=Decimal(h),Decimal(l)
            boundary,delta=(low,Decimal('-.05')) if self.signals[symbol]=='LONG' else (high,Decimal('.05'))
            self.emit(symbol,boundary);self.emit(symbol,boundary+delta)
        self.assert_zero('opposite-direction breaks for all ten stocks')

    def test_touching_boundary_is_not_a_breakout(self):
        self.prepare()
        for symbol,(h,l) in EXPECTED.items():
            high,low=Decimal(h),Decimal(l);inside=(high+low)/2
            self.emit(symbol,inside);self.emit(symbol,high if self.signals[symbol]=='LONG' else low)
        self.assert_zero('touching high/low without strict crossing for all ten stocks')

    def test_valid_signal_breakouts_produce_exact_sdk_payload_and_proof_once(self):
        self.prepare();count=0
        for symbol,(h,l) in EXPECTED.items():
            boundary=Decimal(h if self.signals[symbol]=='LONG' else l)
            delta=Decimal('.05' if self.signals[symbol]=='LONG' else '-.05')
            self.emit(symbol,boundary);self.emit(symbol,boundary+delta)
            count+=1;self.assert_one_payload(symbol,count)
            self.emit(symbol,boundary);self.emit(symbol,boundary+delta)
            self.assertEqual(len(self.wire),count,'duplicate re-cross must not resubmit')
        with self.desk.ledger.engine.connect() as c:
            proof=c.execute(select(events).where(events.c.state=='ORB_TRIGGER')).mappings().all()
            actual=c.execute(select(intents)).mappings().all()
        self.assertEqual(len(proof),10);self.assertEqual(len(actual),10)
        self.assertEqual(len({r['ig'] for r in self.wire}),10)
        for e in proof:
            p=json.loads(e['details']);row=next(x for x in actual if x['tag']==e['tag'])
            prev,price=Decimal(p['previous_price']),Decimal(p['trigger_price'])
            if row['side']=='BUY':self.assertTrue(prev<=Decimal(p['orb_high'])<price)
            else:self.assertTrue(prev>=Decimal(p['orb_low'])>price)

    def test_first_tick_already_outside_range_needs_a_fresh_cross(self):
        self.prepare();self.desk.runtime.reset_crossing_baselines()
        self.emit('CUPID','362.05');self.emit('CUPID','362.10')
        self.assert_zero('already outside without a fresh previous tick')
        self.emit('CUPID','362.00');self.emit('CUPID','362.05')
        self.assert_one_payload('CUPID',1)

    def test_stale_or_future_tick_never_submits(self):
        self.prepare()
        self.emit('CUPID','362.00');self.emit('CUPID','362.05',age=10)
        self.emit('CUPID','362.05',age=-3)
        self.assert_zero('stale or future exchange timestamps')

    def test_stale_previous_tick_cannot_create_cross(self):
        self.prepare();self.emit('CUPID','362.00');self.now+=timedelta(seconds=6)
        self.emit('CUPID','362.05');self.emit('CUPID','362.10')
        self.assert_zero('previous tick gap exceeds crossing freshness window')

    def test_reconnect_does_not_reuse_prior_crossing_baseline(self):
        self.prepare();self.emit('CUPID','362.00');self.emit('CUPID','362.05',generation=2)
        self.assert_zero('feed generation changed');self.assertFalse(self.desk.armed)

    def test_no_entry_at_or_after_cutoff(self):
        self.prepare();self.emit('CUPID','362.00')
        self.now=START.replace(minute=30);self.emit('CUPID','362.05',advance=False)
        self.assert_zero('exact 09:30 cutoff')

    def test_missing_first_bar_blocks_runtime_without_using_second_bar(self):
        original=self.desk.dhan.get_intraday
        def missing(security_id,*args,**kwargs):
            data=original(security_id,*args,**kwargs)
            if str(security_id)==str(self.ids['CUPID']):
                keep=[i for i,t in enumerate(data['timestamp']) if t!=START.timestamp()]
                data={k:[v[i] for i in keep] for k,v in data.items()}
            return data
        self.desk.dhan.get_intraday=missing
        self.now=START+timedelta(minutes=1);self.desk.cycle()
        self.assertIsNone(self.desk.runtime)
        self.assertEqual(set(self.desk.opening_ranges),set(self.signals)-{'CUPID'})
        with self.assertRaises(RuntimeError):self.desk.arm()
        self.emit('CUPID','362.05')
        self.assert_zero('09:15 missing for one stock; never substitute its forming 09:16 candle')

if __name__=='__main__':unittest.main()
