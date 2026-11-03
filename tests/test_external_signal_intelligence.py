import unittest
from datetime import datetime, timezone, timedelta

from external_signal_intelligence import (
    normalize_trader_idea, summarize_external_consensus,
)


NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


def idea(**overrides):
    data = {
        "symbol": "XAU/USD", "side": "BUY", "source": "x_api",
        "author": "trader_one", "url": "https://example.com/post/1",
        "published_at": (NOW - timedelta(minutes=5)).isoformat(),
        "entry": 2000, "stop_loss": 1990, "take_profit": 2020,
        "verified_stats": {"closed_ideas": 40, "wins": 24, "losses": 16},
    }
    data.update(overrides)
    return data


class ExternalSignalIntelligenceTests(unittest.TestCase):
    def test_accepts_recent_well_formed_idea(self):
        result = normalize_trader_idea(idea(), now=NOW)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["side"], "BUY")
        self.assertEqual(result["verified_closed_ideas"], 40)
        self.assertFalse(result["weight_is_probability"])

    def test_rejects_stale_idea(self):
        result = normalize_trader_idea(
            idea(published_at=(NOW - timedelta(hours=4)).isoformat()), now=NOW)
        self.assertFalse(result["accepted"])

    def test_rejects_incoherent_buy_levels(self):
        result = normalize_trader_idea(
            idea(entry=2000, stop_loss=2010, take_profit=2020), now=NOW)
        self.assertFalse(result["accepted"])

    def test_rejects_missing_source_provenance(self):
        result = normalize_trader_idea(idea(url=""), now=NOW)
        self.assertFalse(result["accepted"])

    def test_consensus_counts_one_vote_per_author_and_source(self):
        records = [
            idea(),
            idea(url="https://example.com/post/2",
                 published_at=(NOW - timedelta(minutes=2)).isoformat()),
            idea(side="SELL", author="trader_two",
                 url="https://example.com/post/3",
                 entry=2000, stop_loss=2010, take_profit=1990),
        ]
        result = summarize_external_consensus(records, symbol="XAU/USD", now=NOW)
        self.assertEqual(result["independent_authors"], 2)
        self.assertEqual(result["external_bias"], "BUY")
        self.assertEqual(result["decision_role"], "CONTEXT_ONLY_NOT_A_TRADE_SIGNAL")

    def test_no_external_data_never_generates_trade(self):
        result = summarize_external_consensus([], symbol="EUR/USD", now=NOW)
        self.assertEqual(result["external_bias"], "NO_DATA")
        self.assertEqual(result["decision_role"], "CONTEXT_ONLY_NOT_A_TRADE_SIGNAL")


if __name__ == "__main__":
    unittest.main()
