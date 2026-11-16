import unittest
from datetime import datetime, timedelta, timezone

import biquote_stream
from biquote_stream import (
    SYMBOLS, SYMBOL_MAP, _extract_tick, publish_tick, snapshot,
    seed_history, closed_candles, _update_local_candles,
)


class BiQuoteStreamTests(unittest.TestCase):
    def setUp(self):
        import biquote_stream
        with biquote_stream._lock:
            biquote_stream._latest.clear()
            biquote_stream._CLOSED_CANDLES.clear()
            biquote_stream._FORMING_CANDLES.clear()
            biquote_stream._state["last_tick_at"] = None

    def make_tick(self, **overrides):
        tick = {
            "symbol": "EURUSD",
            "bid": 1.10000,
            "ask": 1.10010,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "marketState": "open",
        }
        tick.update(overrides)
        return tick

    def test_normalizes_tick_and_calculates_mid(self):
        tick = _extract_tick([self.make_tick()])
        self.assertEqual(tick["symbol"], "EUR/USD")
        self.assertAlmostEqual(tick["mid"], 1.10005, places=7)
        self.assertAlmostEqual(tick["spread"], 0.0001, places=7)
        self.assertFalse(tick["is_vantage_quote"])

    def test_accepts_gold_symbol(self):
        tick = _extract_tick(self.make_tick(symbol="XAU/USD", bid=2000, ask=2000.5))
        self.assertEqual(tick["symbol"], "XAU/USD")

    def test_rejects_unknown_symbol_and_btc(self):
        self.assertNotIn("BTCUSD", SYMBOL_MAP)
        self.assertNotIn("BTCUSD", SYMBOLS)
        self.assertIsNone(_extract_tick(self.make_tick(symbol="BTCUSD")))

    def test_rejects_invalid_bid_ask(self):
        self.assertIsNone(_extract_tick(self.make_tick(bid=0)))
        self.assertIsNone(_extract_tick(self.make_tick(bid=2, ask=1)))

    def test_accepts_biquote_documented_time_field(self):
        stamp = datetime.now(timezone.utc).isoformat()
        tick = self.make_tick()
        tick.pop("timestamp")
        tick["time"] = stamp
        parsed = _extract_tick(tick)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["timestamp"], datetime.fromisoformat(stamp).isoformat())
        self.assertEqual(parsed["symbol"], "EUR/USD")

    def test_rejects_stale_timezone_naive_and_undated_ticks(self):
        old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        self.assertIsNone(_extract_tick(self.make_tick(timestamp=old)))
        self.assertIsNone(_extract_tick(self.make_tick(timestamp="2026-11-16T12:00:00")))
        self.assertIsNone(_extract_tick(self.make_tick(timestamp=None)))
        self.assertIsNone(_extract_tick(self.make_tick(timestamp="", lastQuoteAt=None)))



    def test_publish_rejects_stale_and_out_of_order_ticks(self):
        fresh = self.make_tick()
        self.assertTrue(publish_tick(fresh))
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        self.assertFalse(publish_tick(self.make_tick(timestamp=old_time, bid=1.09, ask=1.0901)))
        self.assertFalse(publish_tick(self.make_tick(stale=True, bid=1.08, ask=1.0801)))
        latest = snapshot()["ticks"]["EUR/USD"]
        self.assertEqual(latest["bid"], 1.1)

    def test_seed_history_and_tick_builder_close_bars_without_future_candle(self):
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        base = now - timedelta(minutes=60)
        historical = []
        for index in range(10):
            stamp = base + timedelta(minutes=5 * index)
            price = 1.10 + index * 0.0001
            historical.append({
                "time": stamp.isoformat(), "open": price, "high": price + 0.0002,
                "low": price - 0.0002, "close": price + 0.0001,
            })
        self.assertEqual(seed_history("EUR/USD", "5min", historical), 10)
        self.assertEqual(len(closed_candles("EUR/USD", "5min")), 10)

        first = now - timedelta(minutes=10)
        first = first.replace(minute=(first.minute // 5) * 5)
        second = first + timedelta(minutes=5)
        _update_local_candles({"symbol": "EUR/USD", "mid": 1.101, "timestamp": first.isoformat()})
        _update_local_candles({"symbol": "EUR/USD", "mid": 1.102, "timestamp": (first + timedelta(minutes=2)).isoformat()})
        _update_local_candles({"symbol": "EUR/USD", "mid": 1.103, "timestamp": second.isoformat()})
        bars = closed_candles("EUR/USD", "5min")
        closed = [bar for bar in bars if bar.get("source") == "biquote_live_ticks"]
        self.assertTrue(closed)
        self.assertEqual(closed[-1]["open"], 1.101)
        self.assertEqual(closed[-1]["high"], 1.102)
        self.assertEqual(closed[-1]["close"], 1.102)
        self.assertNotIn(second.isoformat(), [bar["time"] for bar in closed])


    def test_aggregates_15m_and_30m_only_after_next_bucket(self):
        base = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        for interval, minutes in (("15min", 15), ("30min", 30)):
            with self.subTest(interval=interval):
                with biquote_stream._lock:
                    biquote_stream._CLOSED_CANDLES.clear()
                    biquote_stream._FORMING_CANDLES.clear()
                start = base.replace(minute=(base.minute // minutes) * minutes)
                first = start + timedelta(seconds=5)
                next_bucket = start + timedelta(minutes=minutes)
                _update_local_candles({"symbol": "XAU/USD", "mid": 2000.0, "timestamp": first.isoformat()})
                _update_local_candles({"symbol": "XAU/USD", "mid": 2001.0, "timestamp": (first + timedelta(minutes=2)).isoformat()})
                self.assertFalse(any(x["source"] == "biquote_live_ticks" for x in closed_candles("XAU/USD", interval)))
                _update_local_candles({"symbol": "XAU/USD", "mid": 2002.0, "timestamp": next_bucket.isoformat()})
                bars = [x for x in closed_candles("XAU/USD", interval) if x["source"] == "biquote_live_ticks"]
                self.assertEqual(len(bars), 1)
                self.assertEqual((bars[0]["open"], bars[0]["high"], bars[0]["close"]), (2000.0, 2001.0, 2001.0))

    def test_late_tick_cannot_reopen_a_closed_candle(self):
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        start = now - timedelta(minutes=10)
        start = start.replace(minute=(start.minute // 5) * 5)
        next_bucket = start + timedelta(minutes=5)
        _update_local_candles({"symbol": "EUR/USD", "mid": 1.1, "timestamp": start.isoformat()})
        _update_local_candles({"symbol": "EUR/USD", "mid": 1.2, "timestamp": next_bucket.isoformat()})
        before = closed_candles("EUR/USD", "5min")
        _update_local_candles({"symbol": "EUR/USD", "mid": 9.9, "timestamp": (start + timedelta(minutes=1)).isoformat()})
        after = closed_candles("EUR/USD", "5min")
        self.assertEqual(after, before)
        self.assertEqual(after[-1]["close"], 1.1)

    def test_seed_rejects_unknown_interval_and_symbol(self):
        self.assertEqual(seed_history("BTC/USD", "5min", []), 0)
        self.assertEqual(seed_history("EUR/USD", "1min", []), 0)

    def test_snapshot_marks_old_cached_tick_as_not_fresh(self):
        old = (datetime.now(timezone.utc) - timedelta(seconds=150)).isoformat()
        self.assertTrue(publish_tick(self.make_tick(timestamp=old)))
        data = snapshot()
        self.assertFalse(data["ticks"]["EUR/USD"]["fresh"])
        self.assertEqual(data["stream"]["quote_health"], "no_fresh_quotes")

    def test_start_stream_does_not_overwrite_worker_startup_state(self):
        from unittest.mock import Mock, patch
        fake_thread = Mock()
        fake_thread.is_alive.return_value = False
        with biquote_stream._lock:
            biquote_stream._thread = None
        try:
            with patch("biquote_stream.threading.Thread", return_value=fake_thread):
                self.assertTrue(biquote_stream.start_stream())
            self.assertEqual(snapshot()["stream"]["status"], "starting")
            fake_thread.start.assert_called_once()
        finally:
            with biquote_stream._lock:
                biquote_stream._thread = None
                biquote_stream._state.update({
                    "enabled": False, "status": "stopped", "connected": False,
                })

    def test_retry_delay_is_exponential_and_capped(self):
        from unittest.mock import patch
        from biquote_stream import _retry_delay
        with patch("biquote_stream.random.uniform", return_value=1.0):
            self.assertEqual(_retry_delay(1), 3.0)
            self.assertEqual(_retry_delay(2), 6.0)
            self.assertEqual(_retry_delay(3), 12.0)
            self.assertEqual(_retry_delay(20), 60.0)

    def test_publish_updates_snapshot(self):
        self.assertTrue(publish_tick(self.make_tick()))
        data = snapshot()
        self.assertIn("EUR/USD", data["ticks"])
        self.assertEqual(data["provider"], "biquote")
        self.assertFalse(data["is_vantage_quote"])
        self.assertIsNotNone(data["stream"]["last_tick_at"])
        self.assertIn("EUR/USD|5min", data["candles"])


if __name__ == "__main__":
    unittest.main()
