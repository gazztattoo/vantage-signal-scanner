import unittest

from offline_sequence_metrics_v2 import summarize_outcomes


class OfflineSequenceMetricsTests(unittest.TestCase):
    def test_counts_and_conservative_r_metrics(self):
        result = summarize_outcomes(
            ["TP", "SL", "SL_AMBIGUOUS", "UNRESOLVED", "TP"],
            reward_risk=2,
        )
        self.assertEqual(result["total"], 5)
        self.assertEqual(result["resolved"], 4)
        self.assertEqual(result["win_rate_percent"], 50.0)
        self.assertEqual(result["net_r"], 2.0)
        self.assertEqual(result["expectancy_r_per_resolved_trade"], 0.5)
        self.assertEqual(result["unresolved"], 1)
        self.assertTrue(result["ambiguous_counted_as_loss"])

    def test_all_unresolved_has_no_fake_win_rate_or_expectancy(self):
        result = summarize_outcomes(["UNRESOLVED", "UNRESOLVED"])
        self.assertIsNone(result["win_rate_percent"])
        self.assertIsNone(result["expectancy_r_per_resolved_trade"])
        self.assertEqual(result["net_r"], 0.0)

    def test_ambiguous_bar_is_counted_as_loss(self):
        result = summarize_outcomes(["TP", "SL_AMBIGUOUS"])
        self.assertEqual(result["win_rate_percent"], 50.0)
        self.assertEqual(result["net_r"], 1.0)

    def test_unknown_outcome_rejected(self):
        with self.assertRaises(ValueError):
            summarize_outcomes(["MAYBE"])

    def test_invalid_reward_risk_rejected(self):
        for value in (0, -1, float("inf"), "bad"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    summarize_outcomes(["TP"], reward_risk=value)


if __name__ == "__main__":
    unittest.main()
