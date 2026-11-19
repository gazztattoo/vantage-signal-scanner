import unittest
from gold_liquidity_research import Bar, aggregate, ema, confirmed_pivots, liquidity_sweeps


class ResearchPrimitivesTests(unittest.TestCase):
    def mk(self, i, o=10, h=11, l=9, c=10):
        return Bar(i * 60, o, h, l, c, o + .2, h + .2, l + .2, c + .2)

    def test_aggregate_requires_complete_buckets(self):
        bars = [self.mk(i) for i in range(10)]
        self.assertEqual(len(aggregate(bars, 5)), 2)
        self.assertEqual(len(aggregate(bars[:9], 5)), 1)

    def test_aggregate_does_not_bridge_gap_or_misalignment(self):
        bars = [self.mk(i) for i in range(5)] + [self.mk(i + 1) for i in range(5, 10)]
        self.assertEqual(len(aggregate(bars, 5)), 1)

    def test_ema_warmup(self):
        self.assertEqual(ema([1, 2, 3, 4], 3), [None, None, 2.0, 3.0])

    def test_pivots_only_after_right_confirmation(self):
        bars = [
            self.mk(0, h=10, l=8), self.mk(1, h=12, l=9),
            self.mk(2, h=15, l=10), self.mk(3, h=11, l=9),
            self.mk(4, h=10, l=7), self.mk(5, h=11, l=9),
        ]
        highs, lows = confirmed_pivots(bars, 1, 1)
        self.assertEqual(highs.get(3), 15)
        self.assertEqual(lows.get(5), 7)

    def test_sweep_requires_close_back_inside(self):
        bars = [self.mk(0, h=12, l=8, c=9), self.mk(1, h=12, l=8, c=11)]
        events = liquidity_sweeps(bars, [10])
        self.assertIn((0, "bearish_sweep", 10), events)
        self.assertIn((1, "bullish_sweep", 10), events)


if __name__ == "__main__":
    unittest.main()
