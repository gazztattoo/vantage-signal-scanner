import unittest
from datetime import datetime, timedelta, timezone
from prealert_engine import evaluate_prealert

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
BASE = dict(symbol="EUR/USD", direction="BUY", current_price=1.1008,
    entry_zone_low=1.1000, entry_zone_high=1.1005, setup_valid=True,
    confirmation_valid=False, invalidated=False, data_timestamp=NOW,
    now=NOW, approach_distance=0.0005)

class PrealertTests(unittest.TestCase):
    def test_buy_approaches_from_above(self):
        r = evaluate_prealert(**BASE)
        self.assertEqual(r["state"], "PREALERTA")
        self.assertGreater(r["distance_to_zone"], 0)

    def test_price_too_far(self):
        self.assertIsNone(evaluate_prealert(**{**BASE, "current_price": 1.1020}))

    def test_buy_already_below_zone_is_not_prealerted(self):
        self.assertIsNone(evaluate_prealert(**{**BASE, "current_price": 1.0997}))

    def test_sell_approaches_from_below(self):
        r = evaluate_prealert(**{**BASE, "direction": "SELL", "current_price": 1.0997})
        self.assertEqual(r["state"], "PREALERTA")

    def test_sell_already_above_zone_is_not_prealerted(self):
        self.assertIsNone(evaluate_prealert(**{**BASE, "direction": "SELL", "current_price": 1.1008}))

    def test_stale_data_never_creates_alert(self):
        self.assertIsNone(evaluate_prealert(**{**BASE, "data_timestamp": NOW-timedelta(seconds=91)}))

    def test_future_data_never_creates_alert(self):
        self.assertIsNone(evaluate_prealert(**{**BASE, "data_timestamp": NOW+timedelta(seconds=1)}))

    def test_confirmation_only_inside_zone(self):
        r = evaluate_prealert(**{**BASE, "current_price": 1.1003, "confirmation_valid": True})
        self.assertEqual(r["state"], "CONFIRMADA")
        outside = evaluate_prealert(**{**BASE, "confirmation_valid": True})
        self.assertEqual(outside["state"], "PREALERTA")

    def test_invalidated_setup_cancels_existing_prealert(self):
        p = evaluate_prealert(**BASE)
        r = evaluate_prealert(**{**BASE, "invalidated": True, "previous": p})
        self.assertEqual(r["state"], "CANCELADA")

    def test_price_moving_away_cancels_existing_prealert(self):
        p = evaluate_prealert(**BASE)
        r = evaluate_prealert(**{**BASE, "current_price": 1.1020, "previous": p})
        self.assertEqual(r["state"], "CANCELADA")

    def test_crossed_buy_zone_cancels_existing_prealert(self):
        p = evaluate_prealert(**BASE)
        r = evaluate_prealert(**{**BASE, "current_price": 1.0997, "previous": p})
        self.assertEqual(r["state"], "CANCELADA")

    def test_expiry(self):
        p = evaluate_prealert(**BASE)
        later = NOW + timedelta(seconds=181)
        r = evaluate_prealert(**{**BASE, "now": later, "data_timestamp": later, "previous": p})
        self.assertEqual(r["state"], "EXPIRADA")

    def test_terminal_state_is_idempotent(self):
        p = {**evaluate_prealert(**BASE), "state": "CANCELADA"}
        self.assertEqual(evaluate_prealert(**{**BASE, "previous": p}), p)

    def test_repeated_evaluation_keeps_same_alert_id(self):
        first = evaluate_prealert(**BASE)
        second = evaluate_prealert(**{**BASE, "previous": first, "current_price": 1.1007})
        self.assertEqual(second["alert_id"], first["alert_id"])
        self.assertEqual(second["created_at"], first["created_at"])

    def test_same_market_snapshot_gets_deterministic_id(self):
        first = evaluate_prealert(**BASE)
        second = evaluate_prealert(**BASE)
        self.assertEqual(first["alert_id"], second["alert_id"])

    def test_all_supported_markets_use_local_engine(self):
        cases = [
            ("EUR/USD", 1.1008, 1.1000, 1.1005, 0.0005),
            ("XAU/USD", 2750.8, 2750.0, 2750.5, 0.5),
            ("BTC/USD", 67000.8, 67000.0, 67000.5, 0.5),
        ]
        for symbol, price, low, high, distance in cases:
            with self.subTest(symbol=symbol):
                r = evaluate_prealert(**{**BASE, "symbol": symbol, "current_price": price,
                    "entry_zone_low": low, "entry_zone_high": high, "approach_distance": distance})
                self.assertEqual(r["state"], "PREALERTA")
                self.assertEqual(r["symbol"], symbol)

    def test_invalid_setup_does_not_create_alert(self):
        self.assertIsNone(evaluate_prealert(**{**BASE, "setup_valid": False}))

    def test_invalid_direction_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_prealert(**{**BASE, "direction": "HOLD"})

if __name__ == "__main__":
    unittest.main()
