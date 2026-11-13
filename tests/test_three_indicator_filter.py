import unittest
from three_indicator_filter import evaluate_three_indicator_filter


class ThreeIndicatorFilterTests(unittest.TestCase):
    def test_buy_requires_all_three_indicators(self):
        result = evaluate_three_indicator_filter(adx14=24, macd_hist=0.3, rsi14=61)
        self.assertEqual(result["signal"], "COMPRA")
        self.assertEqual(result["buy_score"], 3)
        self.assertEqual(result["quality_grade"], "A")

    def test_sell_requires_all_three_indicators(self):
        result = evaluate_three_indicator_filter(adx14=26, macd_hist=-0.2, rsi14=39)
        self.assertEqual(result["signal"], "VENTA")
        self.assertEqual(result["quality_grade"], "A+")

    def test_preparar_confluence_when_adx_is_developing(self):
        result = evaluate_three_indicator_filter(adx14=17, macd_hist=0.2, rsi14=60)
        self.assertEqual(result["signal"], "ESPERAR")
        self.assertEqual(result["buy_score"], 2)
        self.assertEqual(result["quality_grade"], "B")

    def test_low_strength_never_validates_entry(self):
        result = evaluate_three_indicator_filter(adx14=14, macd_hist=0.2, rsi14=60)
        self.assertEqual(result["signal"], "ESPERAR")
        self.assertEqual(result["buy_score"], 2)

    def test_mixed_momentum_does_not_validate(self):
        result = evaluate_three_indicator_filter(adx14=25, macd_hist=0.2, rsi14=45)
        self.assertEqual(result["signal"], "ESPERAR")

    def test_invalid_values_fail_closed(self):
        with self.assertRaises(ValueError):
            evaluate_three_indicator_filter(adx14=None, macd_hist=0.2, rsi14=60)


if __name__ == "__main__":
    unittest.main()
