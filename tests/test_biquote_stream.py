import unittest
from datetime import datetime, timedelta, timezone

from biquote_stream import SYMBOLS, SYMBOL_MAP, _extract_tick, publish_tick, snapshot


class BiQuoteStreamTests(unittest.TestCase):
    def setUp(self):
        import biquote_stream
        with biquote_stream._lock:
            biquote_stream._latest.clear()
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

    def test_publish_updates_snapshot(self):
        self.assertTrue(publish_tick(self.make_tick()))
        data = snapshot()
        self.assertIn("EUR/USD", data["ticks"])
        self.assertEqual(data["provider"], "biquote")
        self.assertFalse(data["is_vantage_quote"])
        self.assertIsNotNone(data["stream"]["last_tick_at"])


if __name__ == "__main__":
    unittest.main()
