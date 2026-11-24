import unittest
from datetime import datetime, timedelta, timezone

from signal_lifecycle import (
    build_m15_prealert_context,
    encode_m15_prealert_context,
    restore_m15_prealert_context,
    evaluate_m15_confirmation,
    prealert_entry_not_crossed,
)


NOW = datetime.now(timezone.utc)
BASE = {
    "pre_signal": True, "pre_signal_side": "SELL",
    "planned_entry": 3613.0, "planned_stop_loss": 3620.0,
    "planned_take_profit": 3599.0, "atr14": 3.0,
    "candle_time": (NOW - timedelta(minutes=15)).isoformat(),
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

    def test_prealert_is_blocked_if_sell_trigger_already_crossed(self):
        crossed_quote = {**QUOTE, "bid": 3608.0, "ask": 3608.2}
        self.assertFalse(prealert_entry_not_crossed(self.context, crossed_quote))

    def test_prealert_is_allowed_while_sell_trigger_is_still_ahead(self):
        ahead_quote = {**QUOTE, "bid": 3614.0, "ask": 3614.2}
        self.assertTrue(prealert_entry_not_crossed(self.context, ahead_quote))

    def test_validation_keeps_original_m15_entry(self):
        quote = {**QUOTE, "bid": 3613.1, "ask": 3613.3}
        result = evaluate_m15_confirmation(self.context, M5, M30, quote, now=NOW)
        self.assertTrue(result["valid"])
        self.assertEqual(result["entry"], 3613.0)

    def test_late_sell_at_3608_is_rejected_instead_of_repriced(self):
        quote = {**QUOTE, "bid": 3608.0, "ask": 3608.2}
        result = evaluate_m15_confirmation(self.context, M5, M30, quote, now=NOW)
        self.assertFalse(result["valid"])
        self.assertIn("cruzó", result["reason"])

    def test_price_that_has_crossed_even_slightly_is_rejected(self):
        crossed_quote = {**QUOTE, "bid": 3612.9, "ask": 3613.1}
        result = evaluate_m15_confirmation(self.context, M5, M30, crossed_quote, now=NOW)
        self.assertFalse(result["valid"])
        self.assertIn("cruzó", result["reason"])

    def test_price_too_far_from_entry_waits(self):
        far_quote = {**QUOTE, "bid": 3615.0, "ask": 3615.2}
        result = evaluate_m15_confirmation(self.context, M5, M30, far_quote, now=NOW)
        self.assertFalse(result["valid"])
        self.assertIn("lejos", result["reason"])

    def test_opposing_m30_blocks_confirmation(self):
        result = evaluate_m15_confirmation(
            self.context, M5, {**M30, "primary_bias": "BULL"}, QUOTE, now=NOW
        )
        self.assertFalse(result["valid"])
        self.assertIn("M30", result["reason"])

    def test_m5_wrong_side_blocks_confirmation(self):
        result = evaluate_m15_confirmation(
            self.context, {**M5, "signal": "COMPRA"}, M30, QUOTE, now=NOW
        )
        self.assertFalse(result["valid"])

    def test_expired_prealert_is_rejected(self):
        stale = {
            **self.context,
            "candle_time": (NOW - timedelta(minutes=30)).isoformat(),
        }
        result = evaluate_m15_confirmation(stale, M5, M30, QUOTE, now=NOW)
        self.assertFalse(result["valid"])

    def test_invalid_stop_target_order_is_rejected(self):
        invalid = {**BASE, "planned_stop_loss": 3590.0, "planned_take_profit": 3620.0}
        self.assertIsNone(build_m15_prealert_context(invalid))


if __name__ == "__main__":
    unittest.main()
