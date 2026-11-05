"""Regression tests for exchange timestamps: never execute on old market trades.

Synthetic binary Dhan packets and explicitly fake broker/feed objects; no
network access, credentials, or live order execution.
"""
import struct
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from live_desk.ticker import LiveTicker, parse_ticker

IST=ZoneInfo("Asia/Kolkata")


def ticker_packet(*, security_id=1333, price=101.25, last_trade_at):
    # Dhan v2: 8 byte response header + float32 LTP + int32 exchange LTT
    return struct.pack("<BhBifi", 2,16,1,security_id,price,
                       int(last_trade_at.timestamp()))


class TickerExchangeTimestampTests(unittest.TestCase):
    def test_v2_packet_contains_actual_last_trade_time(self):
        trade=datetime(2026,11,4,9,16,0,tzinfo=IST)
        result=parse_ticker(ticker_packet(last_trade_at=trade))
        self.assertEqual(result["security_id"],"1333")
        self.assertAlmostEqual(result["ltp"],101.25,places=3)
        self.assertEqual(datetime.fromisoformat(result["exchange_timestamp"]),trade)

    def test_old_exchange_trade_not_relabelled_as_current_tick(self):
        trade=datetime(2026,11,4,9,16,0,tzinfo=IST)
        received=[]
        class FakeDhanClient:
            def websocket_url(self):
                return "wss://example.invalid/test"  # never opened
        ticker=LiveTicker(FakeDhanClient(),{"TESTA":"1333"},received.append)
        def make_fake_ws(url,*,on_open,on_message,on_error,on_close):
            class FakeWebSocket:
                def run_forever(self,**kwargs):
                    on_message(self,ticker_packet(last_trade_at=trade))
                    ticker.stop_event.set()
                def close(self): pass
            return FakeWebSocket()
        with patch("websocket.WebSocketApp",side_effect=make_fake_ws):
            ticker._run()
        self.assertEqual(len(received),1)
        self.assertEqual(received[0]["symbol"],"TESTA")
        self.assertEqual(received[0]["timestamp"],trade)
        self.assertGreater(received[0]["received_at"],trade)
        self.assertEqual(ticker.snapshot()["last_exchange_trade_at"],trade.isoformat())

    def test_corrupt_or_wrong_response_packet_rejected(self):
        trade=datetime(2026,11,4,9,16,0,tzinfo=IST)
        packet=ticker_packet(last_trade_at=trade)
        self.assertIsNone(parse_ticker(b""))
        self.assertIsNone(parse_ticker(packet[:15]))
        self.assertIsNone(parse_ticker(bytes([4])+packet[1:]))
        self.assertIsNone(parse_ticker(ticker_packet(price=float("nan"),last_trade_at=trade)))


if __name__=="__main__":
    unittest.main()
