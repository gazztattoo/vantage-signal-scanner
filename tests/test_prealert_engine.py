import unittest

from prealert_engine import evaluate_prealert


def snap(**overrides):
    data = {
        "instrument": "EUR/USD",
        "timeframe": "5min",
        "timestamp": 1000,
        "price": 1.1020,
        "direction": "BUY",
        "zone_low": 1.1000,
        "zone_high": 1.1010,
        "trend_aligned": True,
        "momentum_aligned": True,
        "structure_valid": True,
        "approach_distance": 0.0020,
        "max_data_age_seconds": 90,
        "prealert_ttl_seconds": 180,
    }
    data.update(overrides)
    return data


class PrealertEngineTests(unittest.TestCase):
    def test_buy_approaching_zone_creates_prealert(self):
        result = evaluate_prealert(snap(), now_ts=1010)
        self.assertEqual(result["state"], "PREALERTA")
        self.assertEqual(result["event"], "PREALERTA")

    def test_sell_approaching_zone_creates_prealert(self):
        data = snap(direction="SELL", price=1.0990,
                    zone_low=1.1000, zone_high=1.1010)
        result = evaluate_prealert(data, now_ts=1010)
        self.assertEqual(result["state"], "PREALERTA")

    def test_entering_zone_confirms(self):
        first = evaluate_prealert(snap(), now_ts=1010)
        second = evaluate_prealert(
            snap(timestamp=1020, price=1.1005),
            previous=first,
            now_ts=1020,
        )
        self.assertEqual(second["state"], "CONFIRMADA")
        self.assertEqual(second["event"], "CONFIRMADA")

    def test_stale_data_never_creates_prealert(self):
        result = evaluate_prealert(snap(timestamp=800), now_ts=1010)
        self.assertEqual(result["state"], "CANCELADA")

    def test_invalid_confluence_cancels(self):
        result = evaluate_prealert(
            snap(momentum_aligned=False), now_ts=1010
        )
        self.assertEqual(result["state"], "CANCELADA")

    def test_price_crossing_zone_without_confirmation_cancels(self):
        result = evaluate_prealert(snap(price=1.0990), now_ts=1010)
        self.assertEqual(result["state"], "CANCELADA")

    def test_prealert_expires(self):
        first = evaluate_prealert(snap(), now_ts=1010)
        later = evaluate_prealert(
            snap(timestamp=1200, price=1.1020),
            previous=first,
            now_ts=1200,
        )
        self.assertEqual(later["state"], "EXPIRADA")

    def test_repeated_prealert_does_not_duplicate_event(self):
        first = evaluate_prealert(snap(), now_ts=1010)
        second = evaluate_prealert(
            snap(timestamp=1015), previous=first, now_ts=1015
        )
        self.assertEqual(second["state"], "PREALERTA")
        self.assertIsNone(second["event"])

    def test_no_alert_when_price_is_too_far(self):
        result = evaluate_prealert(
            snap(price=1.1050), now_ts=1010
        )
        self.assertEqual(result["state"], "SIN_ALERTA")

    def test_terminal_state_is_sticky(self):
        cancelled = evaluate_prealert(
            snap(momentum_aligned=False), now_ts=1010
        )
        later = evaluate_prealert(
            snap(timestamp=1020, price=1.1005),
            previous=cancelled,
            now_ts=1020,
        )
        self.assertEqual(later["state"], "CANCELADA")


if __name__ == "__main__":
    unittest.main()
