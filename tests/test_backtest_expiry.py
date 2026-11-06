import unittest
from unittest.mock import patch

from tools import backtest_current_strategy as backtest


class BacktestExpiryOutcomeTests(unittest.TestCase):
    def candles(self, count=125, final_close=105.0):
        result = []
        for index in range(count):
            close = final_close if index == 101 else 100.0
            result.append({
                "time": f"2026-10-01T{index // 12:02d}:{(index % 12) * 5:02d}:00+00:00",
                "open": 100.0,
                "high": max(100.0, close) + 1.0,
                "low": min(100.0, close) - 1.0,
                "close": close,
            })
        return result

    def test_expired_buy_uses_close_to_calculate_r(self):
        signal = {"side": "BUY", "entry": 100.0, "sl": 90.0,
                  "tp": 120.0, "adx": 25.0, "score": 3}
        with patch.object(backtest, "build_signal", side_effect=[signal] + [None] * 30):
            result = backtest.run_backtest(self.candles(), expiry=2)
        trade = result["trades"][0]
        self.assertEqual(trade["outcome"], "EXPIRED")
        self.assertEqual(trade["exit_price"], 105.0)
        self.assertEqual(trade["result_r"], 0.5)

    def test_expired_sell_uses_close_to_calculate_r(self):
        signal = {"side": "SELL", "entry": 100.0, "sl": 110.0,
                  "tp": 80.0, "adx": 25.0, "score": 3}
        with patch.object(backtest, "build_signal", side_effect=[signal] + [None] * 30):
            result = backtest.run_backtest(self.candles(final_close=95.0), expiry=2)
        trade = result["trades"][0]
        self.assertEqual(trade["outcome"], "EXPIRED")
        self.assertEqual(trade["exit_price"], 95.0)
        self.assertEqual(trade["result_r"], 0.5)

    def test_incomplete_expiry_window_is_not_counted_as_expired(self):
        signal = {"side": "BUY", "entry": 100.0, "sl": 90.0,
                  "tp": 120.0, "adx": 25.0, "score": 3}
        with patch.object(backtest, "build_signal", return_value=signal):
            result = backtest.run_backtest(self.candles(count=101), expiry=2)
        self.assertEqual(result["summary"]["signals_traded"], 0)
        self.assertEqual(result["summary"]["expired"], 0)


    def test_hypothetical_cost_reduces_net_and_increases_drawdown(self):
        signal = {"side": "BUY", "entry": 100.0, "sl": 90.0,
                  "tp": 120.0, "adx": 25.0, "score": 3}
        with patch.object(backtest, "build_signal", side_effect=[signal] + [None] * 30):
            result = backtest.run_backtest(self.candles(), expiry=2,
                                           cost_per_trade_r=0.1)
        self.assertEqual(result["summary"]["cost_per_trade_r"], 0.1)
        self.assertEqual(result["summary"]["net_after_cost_r"], 0.4)
        self.assertEqual(result["summary"]["max_drawdown_after_cost_r"], 0.0)

    def test_negative_cost_is_rejected(self):
        with self.assertRaises(ValueError):
            backtest.run_backtest(self.candles(), expiry=2,
                                  cost_per_trade_r=-0.01)

    def test_filtered_signal_does_not_block_next_eligible_entry(self):
        rejected = {"side": "BUY", "entry": 100.0, "sl": 90.0,
                    "tp": 120.0, "adx": 25.0, "score": 4}
        accepted = {"side": "BUY", "entry": 100.0, "sl": 90.0,
                    "tp": 120.0, "adx": 25.0, "score": 3}
        with patch.object(backtest, "build_signal",
                          side_effect=[rejected, accepted] + [None] * 30):
            result = backtest.run_backtest(self.candles(), expiry=2,
                                           variant="XAU_BUY_SCORE3")
        self.assertEqual(result["summary"]["signals_traded"], 1)
        self.assertEqual(result["trades"][0]["score"], 3)
        self.assertEqual(result["trades"][0]["signal_time"], self.candles()[100]["time"])


if __name__ == "__main__":
    unittest.main()
