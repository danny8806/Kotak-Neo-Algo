"""Regression tests for exchange timestamps: never execute on old market trades.

Synthetic binary Dhan packets and explicitly fake broker/feed objects; no
network access, credentials, or live order execution.
"""
import json
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

    def test_wire_float_does_not_invent_a_sub_paise_price(self):
        trade=datetime(2026,11,4,9,16,tzinfo=IST)
        self.assertEqual(parse_ticker(ticker_packet(price=100.10,last_trade_at=trade))["ltp"],100.10)

    def test_subscription_added_while_connecting_is_sent_on_open(self):
        ticker=LiveTicker(type("Dhan",(),{"websocket_url":lambda _:"wss://example.invalid"})(),
                          {"HDFCBANK":"1333"},lambda _:None)
        sent=[]
        def fake_ws(_url,*,on_open,**_callbacks):
            class Socket:
                def send(self,message):
                    sent.append(json.loads(message))
                def run_forever(self,**_kwargs):
                    ticker.add_symbols({"TCS":"11536"})
                    if sent:
                        raise AssertionError("Subscribe attempted before WebSocket opened")
                    on_open(self)
                    ticker.stop_event.set()
            return Socket()
        with patch("websocket.WebSocketApp",side_effect=fake_ws):
            ticker._run()
        self.assertEqual({item["SecurityId"] for item in sent[0]["InstrumentList"]},{"1333","11536"})

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

    def test_bundled_message_routes_every_tick_and_applies_configured_offset(self):
        trade = datetime(2026, 11, 4, 9, 16, tzinfo=IST)
        encoded_time = trade + timedelta(hours=5, minutes=30)
        first = ticker_packet(security_id=1333, last_trade_at=encoded_time)
        prev_close = bytes([6]) + first[1:]
        second = ticker_packet(security_id=11536, last_trade_at=encoded_time + timedelta(seconds=1))
        received = []

        with patch.dict("os.environ", {"DHAN_TICK_TIMESTAMP_OFFSET_SECONDS": "19800"}):
            ticker = LiveTicker(type("Dhan", (), {"websocket_url": lambda _: "wss://example.invalid"})(),
                                {"HDFCBANK": "1333", "TCS": "11536"}, received.append)

        def fake_ws(url, *, on_message, **callbacks):
            class Socket:
                def run_forever(self, **kwargs):
                    on_message(self, prev_close + first + second)
                    ticker.stop_event.set()
            return Socket()

        with patch("websocket.WebSocketApp", side_effect=fake_ws):
            ticker._run()

        self.assertEqual([(tick["symbol"], tick["timestamp"]) for tick in received],
                         [("HDFCBANK", trade), ("TCS", trade + timedelta(seconds=1))])

    def test_conflicting_id_cannot_replace_existing_subscription(self):
        with self.assertRaisesRegex(ValueError, "share a Dhan security ID"):
            LiveTicker(None, {"HDFCBANK": "1333", "TCS": "1333"}, lambda _: None)
        ticker = LiveTicker(None, {"HDFCBANK": "1333"}, lambda _: None)
        with self.assertRaisesRegex(ValueError, "conflicts"):
            ticker.add_symbols({"TCS": "1333"})
        self.assertEqual(ticker.map, {"1333": "HDFCBANK"})


if __name__=="__main__":
    unittest.main()
