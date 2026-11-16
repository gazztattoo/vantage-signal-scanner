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

    def test_rejects_stale_and_timezone_naive_ticks(self):
        old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        self.assertIsNone(_extract_tick(self.make_tick(timestamp=old)))
        self.assertIsNone(_extract_tick(self.make_tick(timestamp="2026-11-16T12:00:00")))


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

    def test_seed_rejects_unknown_interval_and_symbol(self):
        self.assertEqual(seed_history("BTC/USD", "5min", []), 0)
        self.assertEqual(seed_history("EUR/USD", "1min", []), 0)

    def test_publish_updates_snapshot(self):
        self.assertTrue(publish_tick(self.make_tick()))
        data = snapshot()
        self.assertIn("EUR/USD", data["ticks"])
        self.assertEqual(data["provider"], "biquote")
        self.assertFalse(data["is_vantage_quote"])
        self.assertIsNotNone(data["stream"]["last_tick_at"])


if __name__ == "__main__":
    unittest.main()
