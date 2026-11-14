import unittest
from three_indicator_filter import evaluate_three_indicator_filter


class ThreeIndicatorFilterTests(unittest.TestCase):
    def test_buy_with_two_of_three_and_adx_18(self):
        result = evaluate_three_indicator_filter(adx14=19, macd_hist=0.3, rsi14=72)
        self.assertEqual(result["signal"], "COMPRA")
        self.assertEqual(result["buy_score"], 2)

    def test_sell_with_two_of_three_and_adx_18(self):
        result = evaluate_three_indicator_filter(adx14=21, macd_hist=-0.2, rsi14=28)
        self.assertEqual(result["signal"], "VENTA")
        self.assertEqual(result["sell_score"], 2)

    def test_full_confluence_keeps_high_grade(self):
        result = evaluate_three_indicator_filter(adx14=26, macd_hist=-0.2, rsi14=39)
        self.assertEqual(result["signal"], "VENTA")
        self.assertEqual(result["quality_grade"], "A+")

    def test_adx_below_18_does_not_validate(self):
        result = evaluate_three_indicator_filter(adx14=17.9, macd_hist=0.2, rsi14=60)
        self.assertEqual(result["signal"], "ESPERAR")

    def test_both_momentum_indicators_opposing_blocks_entry(self):
        result = evaluate_three_indicator_filter(adx14=25, macd_hist=-0.2, rsi14=60)
        self.assertEqual(result["signal"], "ESPERAR")

    def test_invalid_values_fail_closed(self):
        with self.assertRaises(ValueError):
            evaluate_three_indicator_filter(adx14=None, macd_hist=0.2, rsi14=60)


if __name__ == "__main__":
    unittest.main()
