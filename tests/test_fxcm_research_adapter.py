import unittest
from datetime import datetime, timezone, timedelta

from fxcm_research_adapter import normalize_fxcm_candles, RESEARCH_ROLE

NOW = datetime(2026, 11, 24, 12, 10, tzinfo=timezone.utc)


def payload(period="m5", candles=None):
    if candles is None:
        candles = [
            [
                (NOW - timedelta(minutes=10)).timestamp(),
                2650.0, 2651.0, 2652.0, 2649.0,
                2650.2, 2651.2, 2652.2, 2649.2, 120,
            ],
            [
                (NOW - timedelta(minutes=5)).timestamp(),
                2651.0, 2652.0, 2653.0, 2650.0,
                2651.2, 2652.2, 2653.2, 2650.2, 98,
            ],
            [
                NOW.timestamp(),
                2652.0, 2653.0, 2654.0, 2651.0,
                2652.2, 2653.2, 2654.2, 2651.2, 10,
            ],
        ]
    return {
        "response": {"executed": True, "error": ""},
        "instrument_id": "123",
        "period_id": period,
        "candles": candles,
    }


class FXCMResearchAdapterTests(unittest.TestCase):
    def test_parses_bid_and_ask_and_excludes_open_candle(self):
        result = normalize_fxcm_candles(
            payload(), symbol="XAU/USD", period="m5", now=NOW
        )
        self.assertTrue(result["accepted"], result)
        self.assertEqual(result["closed_bars"], 2)
        self.assertEqual(result["bars"][0]["bid"]["open"], 2650.0)
        self.assertEqual(result["bars"][0]["ask"]["open"], 2650.2)
        self.assertEqual(result["bars"][0]["tick_qty"], 120)
        self.assertEqual(result["role"], RESEARCH_ROLE)

    def test_accepts_m15(self):
        bars = [[
            (NOW - timedelta(minutes=15)).timestamp(),
            2650, 2651, 2652, 2649, 2650.2, 2651.2, 2652.2, 2649.2, 50
        ]]
        result = normalize_fxcm_candles(
            payload(period="m15", candles=bars),
            symbol="XAU/USD", period="m15", now=NOW
        )
        self.assertTrue(result["accepted"], result)
        self.assertEqual(result["closed_bars"], 1)

    def test_rejects_failed_response_or_wrong_period(self):
        bad = payload()
        bad["response"]["executed"] = False
        self.assertFalse(normalize_fxcm_candles(
            bad, symbol="XAU/USD", period="m5", now=NOW
        )["accepted"])
        self.assertFalse(normalize_fxcm_candles(
            payload(period="m15"), symbol="XAU/USD", period="m5", now=NOW
        )["accepted"])

    def test_rejects_ask_below_bid(self):
        bars = [[
            (NOW - timedelta(minutes=5)).timestamp(),
            2650, 2651, 2652, 2649, 2649.9, 2650.9, 2651.9, 2648.9, 50
        ]]
        result = normalize_fxcm_candles(
            payload(candles=bars), symbol="XAU/USD", period="m5", now=NOW
        )
        self.assertFalse(result["accepted"])

    def test_rejects_stale_closed_candle(self):
        bars = [[
            (NOW - timedelta(minutes=30)).timestamp(),
            2650, 2651, 2652, 2649, 2650.2, 2651.2, 2652.2, 2649.2, 50
        ]]
        result = normalize_fxcm_candles(
            payload(candles=bars), symbol="XAU/USD", period="m5",
            now=NOW, max_closed_bar_age_minutes=10
        )
        self.assertFalse(result["accepted"])

    def test_rejects_misaligned_bar_timestamp(self):
        bars = [[
            (NOW - timedelta(minutes=9, seconds=30)).timestamp(),
            2650, 2651, 2652, 2649, 2650.2, 2651.2, 2652.2, 2649.2, 50
        ]]
        result = normalize_fxcm_candles(
            payload(candles=bars), symbol="XAU/USD", period="m5", now=NOW
        )
        self.assertFalse(result["accepted"])


if __name__ == "__main__":
    unittest.main()
