import unittest
from gold_liquidity_research import Bar, aggregate
from gold_strategy_backtest import backtest, atr


class GedeonBacktestTests(unittest.TestCase):
    def bar(self, i, o=100, h=101, l=99, c=100):
        return Bar(i*300, o, h, l, c, o+0.5, h+0.5, l+0.5, c+0.5)

    def test_atr_warmup(self):
        values = atr([self.bar(i) for i in range(20)], 14)
        self.assertIsNone(values[12])
        self.assertIsNotNone(values[13])

    def test_only_m5_and_m15_automatic(self):
        with self.assertRaises(ValueError):
            backtest([], 30)

    def test_empty_history_returns_zero_trades(self):
        self.assertEqual(backtest([], 5)["trades"], 0)

    def test_aggregation_preserves_bid_ask(self):
        m1 = [Bar(i*60, 100, 101, 99, 100, 100.5, 101.5, 99.5, 100.5) for i in range(10)]
        m5 = aggregate(m1, 5)
        self.assertEqual(len(m5), 2)
        self.assertEqual(m5[0].ao-m5[0].o, 0.5)


if __name__ == "__main__":
    unittest.main()
