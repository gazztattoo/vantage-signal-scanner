import unittest
from experimental_gate import evaluate_shadow_gate

class ExperimentalShadowGateTests(unittest.TestCase):
    def test_xau_buy_is_the_only_research_candidate(self):
        result = evaluate_shadow_gate("XAU/USD", "15min", "BUY")
        self.assertEqual(result["decision"], "WOULD_ALLOW")
        self.assertFalse(result["would_block"])
        self.assertFalse(result["enabled"])

    def test_eur_buy_is_not_allowlisted(self):
        result = evaluate_shadow_gate("EUR/USD", "15min", "BUY")
        self.assertEqual(result["decision"], "WOULD_BLOCK")

    def test_btc_signal_would_be_blocked_without_changing_original(self):
        result = evaluate_shadow_gate("BTC/USD", "5min", "BUY")
        self.assertEqual(result["decision"], "WOULD_BLOCK")
        self.assertTrue(result["would_block"])
        self.assertTrue(result["safety"]["original_signal_unchanged"])
        self.assertFalse(result["safety"]["orders_enabled"])

    def test_wrong_direction_would_be_blocked(self):
        result = evaluate_shadow_gate("XAU/USD", "15min", "SELL")
        self.assertEqual(result["decision"], "WOULD_BLOCK")
        self.assertTrue(result["would_block"])

    def test_wait_is_not_counted_as_blocked(self):
        result = evaluate_shadow_gate("BTC/USD", "5min", "WAIT")
        self.assertEqual(result["decision"], "NO_ENTRY_TO_FILTER")
        self.assertFalse(result["would_block"])

    def test_mock_mode_is_reported(self):
        result = evaluate_shadow_gate("EUR/USD", "15min", "BUY", data_mode="mock")
        self.assertEqual(result["data_mode"], "mock")
        self.assertEqual(result["safety"]["provider_calls_by_gate"], 0)

if __name__ == "__main__":
    unittest.main()
