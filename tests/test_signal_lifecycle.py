import unittest
from datetime import datetime, timezone

from signal_lifecycle import (
    build_m15_prealert_context,
    encode_m15_prealert_context,
    restore_m15_prealert_context,
    evaluate_m15_confirmation,
)


NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
BASE = {
    "pre_signal": True, "pre_signal_side": "SELL",
    "planned_entry": 3613.0, "planned_stop_loss": 3620.0,
    "planned_take_profit": 3599.0, "atr14": 3.0,
    "candle_time": "2026-11-24T11:45:00+00:00",
}
M5 = {"signal": "VENTA", "structure_trigger": "BREAKOUT", "candle_age_minutes": 1}
M30 = {"primary_bias": "BEAR", "candle_age_minutes": 5}
QUOTE = {"accepted": True, "quote_age_seconds": 5, "bid": 3612.9, "ask": 3613.1}


class M15LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.context = build_m15_prealert_context(BASE)

    def test_prealert_is_persistable_and_restorable(self):
        fingerprint = encode_m15_prealert_context(self.context)
        restored = restore_m15_prealert_context(
            fingerprint, "PREPARAR_VENTA", now=NOW
        )
        self.assertEqual(restored["entry"], 3613.0)
        self.assertEqual(restored["stop_loss"], 3620.0)

    def test_validation_keeps_original_m15_entry(self):
        quote = {**QUOTE, "bid": 3612.9, "ask": 3613.0}
        result = evaluate_m15_confirmation(self.context, M5, M30, quote, now=NOW)
        self.assertTrue(result["valid"])
        self.assertEqual(result["entry"], 3613.0)

    def test_late_sell_is_rejected_instead_of_repriced(self):
        quote = {**QUOTE, "bid": 3608.0, "ask": 3608.2}
        result = evaluate_m15_confirmation(self.context, M5, M30, quote, now=NOW)
        self.assertFalse(result["valid"])
        self.assertIn("alejó", result["reason"])

    def test_opposing_m30_blocks_confirmation(self):
        result = evaluate_m15_confirmation(self.context, M5, {**M30, "primary_bias": "BULL"}, QUOTE, now=NOW)
        self.assertFalse(result["valid"])
        self.assertIn("M30", result["reason"])

    def test_m5_wrong_side_blocks_confirmation(self):
        result = evaluate_m15_confirmation(self.context, {**M5, "signal": "COMPRA"}, M30, QUOTE, now=NOW)
        self.assertFalse(result["valid"])

    def test_expired_prealert_is_rejected(self):
        stale = {**self.context, "candle_time": "2026-11-24T11:30:00+00:00"}
        result = evaluate_m15_confirmation(stale, M5, M30, QUOTE, now=NOW)
        self.assertFalse(result["valid"])


if __name__ == "__main__":
    unittest.main()
