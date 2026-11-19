import unittest
from gold_liquidity_research import Bar
from gold_strategy_backtest_v2 import atr, backtest_v2, chronological_split


def bar(i, tf=5, o=100, h=101, l=99, c=100, spread=0.5):
    return Bar(i*tf*60, o, h, l, c, o+spread, h+spread, l+spread, c+spread)


class BacktestV2Tests(unittest.TestCase):
    def test_atr_uses_gap_true_range(self):
        bars = [bar(i, o=100, h=101, l=99, c=100) for i in range(14)]
        bars.append(bar(14, o=110, h=111, l=109, c=110))
        values = atr(bars, 14)
        self.assertEqual(values[13], 2)
        self.assertGreater(values[14], values[13])

    def test_flat_history_does_not_invent_trades(self):
        bars = [bar(i, h=100.5, l=99.5) for i in range(300)]
        result = backtest_v2(bars, 5)
        self.assertEqual(result["trades"], 0)
        self.assertEqual(result["net_pnl_price_units"], 0)

    def test_rejects_manual_timeframe(self):
        with self.assertRaises(ValueError):
            backtest_v2([], 30)

    def test_rejects_negative_execution_cost(self):
        with self.assertRaises(ValueError):
            backtest_v2([], 5, slippage_per_side=-0.1)

    def test_commission_must_be_explicit_and_is_reported(self):
        result = backtest_v2([], 5, commission_price_units_per_side=0.2)
        self.assertTrue(result["commission_calibrated"])
        self.assertEqual(result["commission_price_units_per_side"], 0.2)

    def test_empty_chronological_split(self):
        self.assertEqual(chronological_split([], []), [])


if __name__ == "__main__":
    unittest.main()
