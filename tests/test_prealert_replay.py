import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Import the offline replay module from the repository's scripts directory.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from replay_prealerts import replay_rows

T0 = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


def row(seconds, price, confirm=False, invalid=False):
    stamp = (T0 + timedelta(seconds=seconds)).isoformat()
    return {"timestamp": stamp, "symbol": "EUR/USD", "direction": "BUY",
            "price": str(price), "zone_low": "1.1000", "zone_high": "1.1005",
            "setup_valid": "true", "confirmation_valid": str(confirm).lower(),
            "invalidated": str(invalid).lower(), "approach_distance": "0.0005"}


class ReplayTests(unittest.TestCase):
    def test_replay_counts_confirmation_and_lead_time(self):
        report = replay_rows([
            row(0, 1.1010), row(20, 1.1008), row(40, 1.1004, confirm=True)
        ])
        self.assertEqual(report["alert_lifecycles"], 1)
        self.assertEqual(report["confirmed"], 1)
        self.assertEqual(report["lead_seconds"]["min"], 20)

    def test_confirmed_setup_does_not_duplicate_on_following_rows(self):
        report = replay_rows([
            row(0, 1.1010), row(20, 1.1008), row(40, 1.1004, confirm=True),
            row(60, 1.1003, confirm=True)
        ])
        self.assertEqual(report["alert_lifecycles"], 1)
        self.assertEqual(report["confirmed"], 1)

    def test_replay_counts_cancellation(self):
        report = replay_rows([row(0, 1.1010), row(20, 1.1008), row(30, 1.1004, invalid=True)])
        self.assertEqual(report["cancelled_without_confirmation"], 1)

    def test_no_alert_when_price_moves_away(self):
        report = replay_rows([row(0, 1.1010), row(20, 1.1012)])
        self.assertEqual(report["alert_lifecycles"], 0)

    def test_data_staleness_blocks_alert(self):
        stale = row(0, 1.1010)
        stale["data_timestamp"] = (T0 - timedelta(seconds=120)).isoformat()
        report = replay_rows([stale])
        self.assertEqual(report["alert_lifecycles"], 0)


if __name__ == "__main__":
    unittest.main()
