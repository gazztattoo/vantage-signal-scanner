import unittest

from liquidity_engine import analyze_liquidity


def candle(i, o, h, l, c):
    return {"time": f"2025-01-01T00:{i:02d}:00+00:00",
            "open": o, "high": h, "low": l, "close": c}


class LiquidityEngineTests(unittest.TestCase):
    def test_insufficient_history_is_not_available(self):
        result = analyze_liquidity([candle(i, 10, 11, 9, 10) for i in range(6)])
        self.assertFalse(result["available"])
        self.assertTrue(result["lookahead_safe"])

    def test_sweep_requires_a_confirmed_pivot_and_close_back_inside(self):
        bars = [
            candle(0, 4, 5, 3, 4),
            candle(1, 5, 6, 4, 5),
            candle(2, 4, 5, 3, 4),
            candle(3, 5, 8, 4, 6),   # pivot high, confirmed only after bar 4
            candle(4, 4, 5, 2, 3),
            candle(5, 3, 4, 0, 2),   # pivot low, confirmed after bar 6
            candle(6, 2, 3, 1, 2),
            candle(7, 5, 9, 4.5, 7),   # closes below pivot high: buy-side sweep
            candle(8, 6, 7, 4.5, 6),
            candle(9, 5, 6, 4.5, 5),
            candle(10, 4, 5, 4.5, 4),
            candle(11, 4, 5, 4.5, 4),
        ]
        result = analyze_liquidity(bars, atr_value=1.0, left_bars=1, right_bars=1)
        self.assertTrue(result["available"])
        self.assertIsNotNone(result["last_sweep"])
        self.assertEqual(result["last_sweep"]["pool"], "BUY_SIDE_LIQUIDITY")
        self.assertEqual(result["last_sweep"]["side"], "BEARISH")

    def test_pivot_is_not_usable_on_its_confirmation_bar(self):
        bars = [
            candle(0, 4, 5, 3, 4),
            candle(1, 5, 6, 4, 5),
            candle(2, 4, 5, 3, 4),
            candle(3, 5, 8, 4, 6),
            candle(4, 4, 5, 2, 3),
            candle(5, 3, 4, 0, 2),
        ]
        result = analyze_liquidity(bars, atr_value=1.0, left_bars=1, right_bars=1)
        self.assertIsNone(result["last_sweep"])

    def test_diagnostic_never_emits_trade_entry(self):
        bars = [candle(i, 10, 11, 9, 10) for i in range(30)]
        result = analyze_liquidity(bars, atr_value=1.0)
        self.assertNotIn("signal", result)
        self.assertNotIn("entry", result)


if __name__ == "__main__":
    unittest.main()
