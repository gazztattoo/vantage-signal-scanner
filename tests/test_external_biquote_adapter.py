import unittest
from datetime import datetime, timezone, timedelta

from external_biquote_adapter import normalize_biquote_ohlc, normalize_biquote_tick

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def make_payload(symbol="XAUUSD", interval="5m", bars=None):
    if bars is None:
        bars = [
            {
                "openTime": (NOW - timedelta(minutes=int(interval[:-1]))).isoformat(),
                "open": 2650.0, "high": 2652.0, "low": 2649.0,
                "close": 2651.0, "tickVolume": 50, "isOpen": False,
            },
            {
                "openTime": NOW.isoformat(),
                "open": 2651.0, "high": 2653.0, "low": 2650.0,
                "close": 2652.0, "tickVolume": 12, "isOpen": True,
            },
        ]
    return {"symbol": symbol, "interval": interval, "bars": bars}


class BiQuoteAdapterTests(unittest.TestCase):
    def test_returns_only_closed_bars(self):
        result = normalize_biquote_ohlc(
            make_payload(), symbol="XAU/USD", interval="5m", now=NOW
        )
        self.assertTrue(result["accepted"])
        self.assertEqual(result["closed_bars"], 1)
        self.assertEqual(result["role"], "RESEARCH_ONLY_UNTIL_BROKER_BASIS_VALIDATED")
        self.assertEqual(result["last_closed_age_minutes"], 0.0)
        self.assertEqual(result["last_closed_close_time"], NOW.isoformat())

    def test_accepts_all_supported_symbols_and_intervals(self):
        for symbol, provider_symbol in (
            ("XAU/USD", "XAUUSD"), ("EUR/USD", "EURUSD"), ("BTC/USD", "BTCUSD")
        ):
            for interval in ("5m", "15m", "30m"):
                result = normalize_biquote_ohlc(
                    make_payload(provider_symbol, interval),
                    symbol=symbol, interval=interval, now=NOW
                )
                self.assertTrue(result["accepted"], (symbol, interval, result))

    def test_rejects_wrong_symbol_or_interval(self):
        self.assertFalse(normalize_biquote_ohlc(
            make_payload(), symbol="EUR/USD", interval="5m", now=NOW
        )["accepted"])
        self.assertFalse(normalize_biquote_ohlc(
            make_payload(), symbol="XAU/USD", interval="30m", now=NOW
        )["accepted"])

    def test_rejects_bad_ohlc(self):
        bad = [{
            "openTime": (NOW - timedelta(minutes=5)).isoformat(),
            "open": 2650, "high": 2640, "low": 2645, "close": 2651,
            "isOpen": False,
        }]
        self.assertFalse(normalize_biquote_ohlc(
            make_payload(bars=bad), symbol="XAU/USD", interval="5m", now=NOW
        )["accepted"])

    def test_rejects_stale_candle_when_limit_is_set(self):
        old = [{
            "openTime": (NOW - timedelta(hours=2)).isoformat(),
            "open": 2650, "high": 2652, "low": 2649, "close": 2651,
            "isOpen": False,
        }]
        result = normalize_biquote_ohlc(
            make_payload(bars=old), symbol="XAU/USD", interval="5m",
            now=NOW, max_last_closed_age_minutes=10
        )
        self.assertFalse(result["accepted"])

    def test_accepts_fresh_bid_ask(self):
        tick = {
            "symbol": "EURUSD", "bid": 1.08, "ask": 1.0801,
            "timestamp": (NOW - timedelta(seconds=2)).isoformat(),
            "marketState": "open", "stale": False,
        }
        result = normalize_biquote_tick(tick, symbol="EUR/USD", now=NOW)
        self.assertTrue(result["accepted"])
        self.assertAlmostEqual(result["spread"], 0.0001)

    def test_rejects_closed_or_stale_tick(self):
        tick = {
            "symbol": "EURUSD", "bid": 1.08, "ask": 1.0801,
            "timestamp": (NOW - timedelta(minutes=10)).isoformat(),
            "marketState": "closed", "stale": True,
        }
        self.assertFalse(normalize_biquote_tick(
            tick, symbol="EUR/USD", now=NOW
        )["accepted"])


if __name__ == "__main__":
    unittest.main()
