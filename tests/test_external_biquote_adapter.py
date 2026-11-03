import unittest
from datetime import datetime, timezone, timedelta

from external_biquote_adapter import normalize_biquote_ohlc, normalize_biquote_tick

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


def candle_payload(**changes):
    data = {
        "symbol": "XAUUSD", "interval": "5m",
        "bars": [
            {"openTime": (NOW - timedelta(minutes=5)).isoformat(),
             "open": 2650.0, "high": 2652.0, "low": 2649.0,
             "close": 2651.0, "volume": 0, "tickVolume": 50, "isOpen": False},
            {"openTime": NOW.isoformat(), "open": 2651.0, "high": 2653.0,
             "low": 2650.0, "close": 2652.0, "volume": 0,
             "tickVolume": 12, "isOpen": True},
        ],
    }
    data.update(changes)
    return data


class BiquoteAdapterTests(unittest.TestCase):
    def test_keeps_closed_bars_and_drops_open_bar(self):
        result = normalize_biquote_ohlc(candle_payload(), symbol="XAU/USD",
                                         interval="5m", now=NOW)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["closed_bars"], 1)
        self.assertFalse(result["bars"][0].get("isOpen", False))
        self.assertEqual(result["role"], "RESEARCH_ONLY_UNTIL_BROKER_BASIS_VALIDATED")

    def test_rejects_wrong_instrument_or_interval(self):
        self.assertFalse(normalize_biquote_ohlc(
            candle_payload(), symbol="EUR/USD", interval="5m", now=NOW)["accepted"])
        self.assertFalse(normalize_biquote_ohlc(
            candle_payload(), symbol="XAU/USD", interval="30m", now=NOW)["accepted"])

    def test_rejects_no_closed_bars(self):
        data = candle_payload(bars=[candle_payload()["bars"][1]])
        self.assertFalse(normalize_biquote_ohlc(
            data, symbol="XAU/USD", interval="5m", now=NOW)["accepted"])

    def test_rejects_bad_ohlc(self):
        bad = candle_payload(bars=[{
            "openTime": (NOW - timedelta(minutes=5)).isoformat(),
            "open": 2650, "high": 2640, "low": 2645, "close": 2651,
            "isOpen": False,
        }])
        self.assertFalse(normalize_biquote_ohlc(
            bad, symbol="XAU/USD", interval="5m", now=NOW)["accepted"])

    def test_rejects_stale_closed_candle_when_limit_set(self):
        old = candle_payload(bars=[{
            "openTime": (NOW - timedelta(hours=2)).isoformat(),
            "open": 2650, "high": 2652, "low": 2649, "close": 2651,
            "isOpen": False,
        }])
        self.assertFalse(normalize_biquote_ohlc(
            old, symbol="XAU/USD", interval="5m", now=NOW,
            max_last_closed_age_minutes=10)["accepted"])

    def test_accepts_fresh_bid_ask(self):
        tick = {"symbol": "EURUSD", "bid": 1.08, "ask": 1.0801,
                "timestamp": (NOW - timedelta(seconds=2)).isoformat(),
                "marketState": "open", "stale": False}
        result = normalize_biquote_tick(tick, symbol="EUR/USD", now=NOW)
        self.assertTrue(result["accepted"])
        self.assertAlmostEqual(result["spread"], 0.0001)

    def test_rejects_stale_or_closed_quote(self):
        tick = {"symbol": "EURUSD", "bid": 1.08, "ask": 1.0801,
                "timestamp": (NOW - timedelta(minutes=10)).isoformat(),
                "marketState": "closed", "stale": True}
        self.assertFalse(normalize_biquote_tick(
            tick, symbol="EUR/USD", now=NOW)["accepted"])


if __name__ == "__main__":
    unittest.main()
