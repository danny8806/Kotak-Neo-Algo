"""Regression against actual 12-Nov opening bars; never calls a broker."""
import json,os,tempfile,unittest
from pathlib import Path
from datetime import date,datetime,time,timedelta
from decimal import Decimal
from unittest.mock import Mock,patch
from sqlalchemy import select
from live_desk.dhan_data import IST
from live_desk.orb import opening_range
from live_desk.strategy import load_policy,candidate_entry
from live_desk.ledger import LiveLedger,events
from postgres_test_db import create_test_database

DAY=date(2026,11,12)
START=datetime(2026,11,12,9,15,tzinfo=IST)
ROWS=json.loads((Path(__file__).parent/'fixtures/orb-opening-2026-11-12.json').read_text())

def data_for(row):
    bars=[row['first_OHLC'],row['second_OHLC']]
    return {'timestamp':[START.timestamp(),(START+timedelta(minutes=1)).timestamp()],
            **{k:[r[k] for r in bars] for k in ['open','high','low','close']},'volume':[1000,1]}

class OpeningCandleAuditTests(unittest.TestCase):
    def test_all_ten_real_first_bars_are_selected_and_nine_bad_entries_rejected(self):
        policy=load_policy(Path(__file__).parents[1]/'live_desk/policy.json')
        self.assertEqual(policy.timestamp_mode,'start')
        for row in ROWS:
            with self.subTest(symbol=row['symbol']):
                client=Mock();client.get_intraday.return_value=data_for(row)
                high,low=opening_range(client,'123',DAY,timestamp_mode='start',clock=lambda:START+timedelta(minutes=1,seconds=2))
                self.assertEqual((high,low),(Decimal(str(row['first_OHLC']['high'])),Decimal(str(row['first_OHLC']['low']))))
                request=client.get_intraday.call_args.args
                self.assertEqual(request[1],START-timedelta(minutes=1))
                if row['trigger_price'] is not None:
                    price=Decimal(row['trigger_price'])
                    self.assertIsNone(candidate_entry(side=row['side'],previous_price=high if row['side']=='LONG' else low,
                        price=price,orb_high=high,orb_low=low,tick_at=START+timedelta(minutes=1,seconds=10),policy=policy))
                for side,bound,delta in [('LONG',high,Decimal('.05')),('SHORT',low,Decimal('-.05'))]:
                    self.assertEqual(candidate_entry(side=side,previous_price=bound,price=bound+delta,
                        orb_high=high,orb_low=low,tick_at=START+timedelta(minutes=1,seconds=10),policy=policy),2)

    def test_end_label_configuration_is_rejected_without_fetch(self):
        client=Mock()
        with self.assertRaisesRegex(ValueError,'start-labelled'):
            opening_range(client,'123',DAY,timestamp_mode='end')
        client.get_intraday.assert_not_called()

    def test_unfinished_first_minute_is_rejected_without_fetch(self):
        client=Mock()
        with self.assertRaisesRegex(ValueError,'not completed'):
            opening_range(client,'123',DAY,timestamp_mode='start',clock=lambda:START+timedelta(seconds=59))
        client.get_intraday.assert_not_called()

    def test_missing_or_duplicate_first_bar_cannot_fall_forward_to_0916(self):
        for stamps in [[START.timestamp()+60],[START.timestamp(),START.timestamp(),START.timestamp()+60]]:
            client=Mock();client.get_intraday.return_value={'timestamp':stamps,
                **{k:[100]*len(stamps) for k in ['open','high','low','close']}}
            with self.assertRaisesRegex(ValueError,'Exactly one'):
                opening_range(client,'123',DAY,timestamp_mode='start',clock=lambda:START+timedelta(minutes=2))

    def test_full_source_bar_is_saved_for_future_entry_audits(self):
        client=Mock();client.get_intraday.return_value=data_for(ROWS[0])
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'LIVE_ORB_AUDIT_DIR':folder}):
            opening_range(client,'123',DAY,timestamp_mode='start',clock=lambda:START+timedelta(minutes=1))
            record=json.loads(next(Path(folder).glob('*.jsonl')).read_text())
        self.assertEqual(record['candle_start'],START.isoformat())
        self.assertEqual(record['candle_end'],(START+timedelta(minutes=1)).isoformat())
        self.assertEqual(record['high'],str(Decimal(str(ROWS[0]['first_OHLC']['high']))))
        self.assertEqual(record['close'],str(Decimal(str(ROWS[0]['first_OHLC']['close']))))

    def test_previous_tick_pair_is_persisted_with_the_order_reservation(self):
        url,cleanup=create_test_database();self.addCleanup(cleanup)
        ledger=LiveLedger(url);self.addCleanup(ledger.close)
        trigger={'previous_price':'362.0','previous_at':(START+timedelta(minutes=1)).isoformat(),
                 'trigger_price':'362.05','trigger_at':(START+timedelta(minutes=1,seconds=1)).isoformat(),
                 'orb_high':'362.0','orb_low':'356.0','feed_generation':1}
        intent=ledger.reserve_entry(session_date=DAY,symbol='CUPID',side='BUY',quantity=2,
            reference_price=Decimal('362.05'),at=START+timedelta(minutes=1,seconds=1),
            max_per_side=5,gross_cap=None,max_total=10,trigger_context=trigger)
        with ledger.engine.connect() as c:
            row=c.execute(select(events).where(events.c.tag==intent['tag'],events.c.state=='ORB_TRIGGER')).mappings().one()
        self.assertEqual(json.loads(row['details']),trigger)

if __name__=='__main__':unittest.main()
