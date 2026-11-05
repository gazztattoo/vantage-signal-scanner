"""Deterministic synthetic sequence checks; not a market-history backtest.

Signals are evaluated on each completed candle using only information available
at that point. Outcomes are checked only against subsequent synthetic candles.
"""
import unittest

from precision_engine_v2 import evaluate_m15_m5_setup


def m15_bull():
    return {
        "ema20": 101.0, "ema50": 100.0, "adx": 24.0, "rsi": 60.0,
        "macd_hist": 0.3, "close": 102.0, "atr": 1.0,
        "candle_age_seconds": 30, "timeframe_seconds": 900,
    }


def evaluate_closed_m5(candle, prior_candles):
    previous_high = max(item["high"] for item in prior_candles)
    swing_low = min(item["low"] for item in prior_candles)
    return evaluate_m15_m5_setup(
        symbol="XAU/USD",
        m15=m15_bull(),
        m5={
            "ema20": 101.0, "ema50": 100.5, "adx": 20.0,
            "rsi": 62.0, "macd_hist": 0.25, "close": candle["close"],
            "atr": 1.0, "candle_age_seconds": 10,
            "timeframe_seconds": 300, "previous_high": previous_high,
            "swing_low": swing_low,
        },
    )


def resolve_future_outcome(candidate, future_candles):
    """Conservative OHLC resolver: if SL and TP touch in one bar, count SL."""
    stop = candidate["stop_loss_reference"]
    target = candidate["take_profit_reference"]
    for candle in future_candles:
        hit_stop = candle["low"] <= stop
        hit_target = candle["high"] >= target
        if hit_stop and hit_target:
            return "SL_AMBIGUOUS"
        if hit_stop:
            return "SL"
        if hit_target:
            return "TP"
    return "UNRESOLVED"


class SyntheticSequenceSimulationTests(unittest.TestCase):
    def setUp(self):
        self.history = [
            {"open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5},
            {"open": 100.5, "high": 101.2, "low": 100.0, "close": 101.0},
        ]
        self.breakout = {
            "open": 101.1, "high": 102.0, "low": 100.9, "close": 101.8,
        }

    def test_closed_breakout_is_detected_then_future_tp_is_counted(self):
        candidate = evaluate_closed_m5(self.breakout, self.history)
        self.assertEqual(candidate["signal"], "BUY")
        self.assertEqual(candidate["quality"], "CANDIDATA")
        future = [
            {"open": 101.8, "high": 103.0, "low": 101.2, "close": 102.8},
            {"open": 102.8, "high": candidate["take_profit_reference"] + 0.1,
             "low": 102.4, "close": candidate["take_profit_reference"]},
        ]
        self.assertEqual(resolve_future_outcome(candidate, future), "TP")

    def test_false_breakout_then_future_stop_is_counted(self):
        candidate = evaluate_closed_m5(self.breakout, self.history)
        self.assertEqual(candidate["signal"], "BUY")
        future = [
            {"open": 101.8, "high": 102.0,
             "low": candidate["stop_loss_reference"] - 0.1,
             "close": candidate["stop_loss_reference"]},
        ]
        self.assertEqual(resolve_future_outcome(candidate, future), "SL")

    def test_same_future_bar_touching_both_levels_is_pessimistic(self):
        candidate = evaluate_closed_m5(self.breakout, self.history)
        future = [
            {"open": 101.8, "high": candidate["take_profit_reference"] + 0.2,
             "low": candidate["stop_loss_reference"] - 0.2, "close": 102.0},
        ]
        self.assertEqual(resolve_future_outcome(candidate, future), "SL_AMBIGUOUS")

    def test_no_future_touch_is_unresolved_not_assumed_win_or_loss(self):
        candidate = evaluate_closed_m5(self.breakout, self.history)
        future = [{"open": 101.8, "high": 103.0, "low": 101.0, "close": 102.5}]
        self.assertEqual(resolve_future_outcome(candidate, future), "UNRESOLVED")

    def test_no_breakout_close_means_no_candidate(self):
        no_breakout = {**self.breakout, "close": 101.1}
        result = evaluate_closed_m5(no_breakout, self.history)
        self.assertEqual(result["signal"], "ESPERAR")
        self.assertNotEqual(result.get("quality"), "CANDIDATA")


if __name__ == "__main__":
    unittest.main()
