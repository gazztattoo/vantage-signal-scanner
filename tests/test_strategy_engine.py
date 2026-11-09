import math
import unittest
from datetime import datetime, timedelta, timezone

from strategy_engine import aggregate_candles, analyze


def trend_bars(count=1200, breakout=True, minutes=5, direction=1):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows, previous = [], 100.0 if direction > 0 else 150.0
    for i in range(count):
        close = (100 + direction * 0.02 * i
                 + 0.4 * math.sin(i * 0.35) + 0.1 * math.sin(i * 0.93))
        spread = 0.06 + 0.02 * abs(math.sin(i))
        if breakout and i == count - 1:
            if direction > 0:
                close = max(close, max(x["high"] for x in rows[-20:]) + 0.02)
            else:
                close = min(close, min(x["low"] for x in rows[-20:]) - 0.02)
        rows.append({
            "time": (start + timedelta(minutes=minutes * i)).isoformat(),
            "open": previous, "high": max(previous, close) + spread,
            "low": min(previous, close) - spread, "close": close,
        })
        previous = close
    return rows


def pre_signal_bars(count=1200):
    rows = trend_bars(count, breakout=False)
    boundary = max(x["high"] for x in rows[-20:-1])
    rows[-1]["close"] = boundary - 0.05
    rows[-1]["high"] = max(rows[-1]["open"], rows[-1]["close"]) + 0.03
    rows[-1]["low"] = min(rows[-1]["open"], rows[-1]["close"]) - 0.03
    return rows


def range_bars(count=1200):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows, previous = [], 100.0
    for i in range(count):
        close = 100 + 0.03 * math.sin(i * 1.7)
        spread = 0.02
        rows.append({
            "time": (start + timedelta(minutes=5 * i)).isoformat(),
            "open": previous, "high": max(previous, close) + spread,
            "low": min(previous, close) - spread, "close": close,
        })
        previous = close
    return rows


class AggregateCandleTests(unittest.TestCase):
    def test_only_complete_and_contiguous_groups_are_aggregated(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bars = []
        for i in range(12):
            if i == 5:
                continue
            price = 100 + i
            bars.append({
                "time": (start + timedelta(minutes=5 * i)).isoformat(),
                "open": price, "high": price + 1, "low": price - 1, "close": price + 0.5,
            })
        self.assertEqual(aggregate_candles(bars, 5, 60), [])

    def test_full_group_ohlc_is_correct(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bars = []
        for i in range(12):
            price = 100 + i
            bars.append({
                "time": (start + timedelta(minutes=5 * i)).isoformat(),
                "open": price, "high": price + 2, "low": price - 2, "close": price + 1,
            })
        result = aggregate_candles(bars, 5, 60)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["open"], 100)
        self.assertEqual(result[0]["close"], 112)
        self.assertEqual(result[0]["high"], 113)
        self.assertEqual(result[0]["low"], 98)


class MultiTimeframeEngineTests(unittest.TestCase):
    def test_confirmed_buy_requires_mtf_and_structure(self):
        result = analyze(trend_bars(), "5min", "TEST")
        self.assertEqual(result["signal"], "BUY")
        self.assertFalse(result["pre_signal"])
        self.assertEqual(result["structure"]["direction"], "BUY")
        self.assertTrue(result["mtf"]["aligned_buy"])
        self.assertGreaterEqual(result["score"], 6)

    def test_confirmed_sell_is_symmetric(self):
        result = analyze(trend_bars(direction=-1), "5min", "TEST")
        self.assertEqual(result["signal"], "SELL")
        self.assertTrue(result["mtf"]["aligned_sell"])
        self.assertGreater(result["stop_loss"], result["entry"])
        self.assertLess(result["take_profit"], result["entry"])
        self.assertAlmostEqual(
            (result["entry"] - result["take_profit"]) /
            (result["stop_loss"] - result["entry"]), 2.0, places=8
        )

    def test_stop_and_target_are_on_correct_side_at_two_r(self):
        result = analyze(trend_bars(), "5min", "TEST")
        self.assertLess(result["stop_loss"], result["entry"])
        self.assertGreater(result["take_profit"], result["entry"])
        risk = result["entry"] - result["stop_loss"]
        reward = result["take_profit"] - result["entry"]
        self.assertAlmostEqual(reward / risk, 2.0, places=8)

    def test_pre_signal_never_contains_entry_or_risk_levels(self):
        result = analyze(pre_signal_bars(), "5min", "TEST")
        self.assertEqual(result["signal"], "WAIT")
        self.assertTrue(result["pre_signal"])
        self.assertEqual(result["pre_signal_side"], "BUY")
        self.assertIsNone(result["entry"])
        self.assertIsNone(result["stop_loss"])
        self.assertIsNone(result["take_profit"])

    def test_lateral_market_blocks_confirmed_signals(self):
        result = analyze(range_bars(), "5min", "TEST")
        self.assertEqual(result["regime"], "RANGE")
        self.assertEqual(result["signal"], "WAIT")
        self.assertFalse(result["pre_signal"])
        self.assertIsNone(result["stop_loss"])
        self.assertIsNone(result["take_profit"])

    def test_insufficient_higher_timeframe_history_blocks_signals(self):
        result = analyze(trend_bars(300), "5min", "TEST")
        self.assertEqual(result["signal"], "WAIT")
        self.assertEqual(result["status_code"], "HISTORIAL_INSUFICIENTE")

    def test_all_scanned_timeframes_resolve_with_sufficient_history(self):
        for timeframe, minutes, count in (("5min", 5, 1200), ("15min", 15, 1000), ("30min", 30, 800)):
            with self.subTest(timeframe=timeframe):
                result = analyze(
                    trend_bars(count=count, minutes=minutes),
                    timeframe,
                    "TEST",
                )
                self.assertEqual(result["signal"], "BUY")
                self.assertIsNotNone(result["mtf"]["bias_1"])
                self.assertIsNotNone(result["mtf"]["bias_2"])

    def test_result_is_deterministic(self):
        bars = trend_bars()
        self.assertEqual(analyze(bars, "5min"), analyze(bars, "5min"))

    def test_duplicate_or_unsorted_candles_are_rejected(self):
        bars = trend_bars(20)
        bars[10] = dict(bars[9])
        with self.assertRaises(ValueError):
            analyze(bars, "5min")


if __name__ == "__main__":
    unittest.main()
