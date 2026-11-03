import unittest
from datetime import datetime, timezone, timedelta

from external_technical_signal_adapters import normalize_sifting_signal

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


def payload(**overrides):
    result = {
        "data": {
            "summary": {"signal": "buy", "score": 0.42},
            "price": {"close": 2650.5, "bar_status": "closed"},
            "oscillators": {"signal": "neutral"},
            "moving_averages": {"signal": "buy"},
        },
        "meta": {
            "symbol": "XAUUSD", "interval": "5m",
            "as_of": (NOW - timedelta(minutes=2)).isoformat(),
        },
    }
    for key, value in overrides.items():
        if key == "summary":
            result["data"]["summary"] = value
        elif key == "price":
            result["data"]["price"] = value
        elif key == "meta":
            result["meta"].update(value)
    return result


class SiftingAdapterTests(unittest.TestCase):
    def test_accepts_recent_closed_candle(self):
        result = normalize_sifting_signal(payload(), symbol="XAU/USD",
                                          interval="5m", now=NOW)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["direction"], "BUY")
        self.assertEqual(result["role"], "EXTERNAL_TECHNICAL_CONTEXT_ONLY")

    def test_rejects_forming_candle(self):
        result = normalize_sifting_signal(
            payload(price={"close": 2650.5, "bar_status": "forming"}),
            symbol="XAU/USD", interval="5m", now=NOW)
        self.assertFalse(result["accepted"])

    def test_rejects_stale_signal(self):
        result = normalize_sifting_signal(
            payload(meta={"as_of": (NOW - timedelta(minutes=30)).isoformat()}),
            symbol="XAU/USD", interval="5m", now=NOW)
        self.assertFalse(result["accepted"])

    def test_rejects_wrong_symbol_or_interval(self):
        self.assertFalse(normalize_sifting_signal(
            payload(), symbol="GBP/USD", interval="5m", now=NOW)["accepted"])
        self.assertFalse(normalize_sifting_signal(
            payload(), symbol="XAU/USD", interval="1h", now=NOW)["accepted"])

    def test_rejects_out_of_range_score(self):
        result = normalize_sifting_signal(
            payload(summary={"signal": "buy", "score": 1.5}),
            symbol="XAU/USD", interval="5m", now=NOW)
        self.assertFalse(result["accepted"])


if __name__ == "__main__":
    unittest.main()
