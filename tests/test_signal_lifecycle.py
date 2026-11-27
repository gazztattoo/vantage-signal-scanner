import unittest
from datetime import datetime, timedelta, timezone

from signal_lifecycle import (
    build_m15_prealert_context,
    encode_m15_prealert_context,
    restore_m15_prealert_context,
    evaluate_m15_confirmation,
    prealert_entry_not_crossed,
    same_m15_plan,
)


NOW = datetime.now(timezone.utc)
BASE = {
    "pre_signal": True, "pre_signal_side": "SELL",
    "planned_entry": 3613.0, "planned_stop_loss": 3620.0,
    "planned_take_profit": 3599.0, "atr14": 3.0,
    "candle_time": (NOW - timedelta(minutes=15)).isoformat(),
}
M5 = {"signal": "VENTA", "structure_trigger": "BREAKOUT", "primary_bias": "BEAR", "candle_age_minutes": 1}
M5_EARLY = {
    "signal": "PRESEÑAL", "pre_signal": True, "pre_signal_side": "SELL",
    "primary_bias": "BEAR", "score": 5, "candle_age_minutes": 1,
    "structure": {"near_direction": "SELL", "distance_atr": 0.18},
}
M30 = {"primary_bias": "BEAR", "candle_age_minutes": 5}
QUOTE = {"accepted": True, "quote_age_seconds": 5, "bid": 3612.9, "ask": 3613.1}


class M15LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.context = build_m15_prealert_context(BASE)

    def test_same_m15_plan_requires_exact_original_levels(self):
        self.assertTrue(same_m15_plan(self.context, dict(self.context)))
        self.assertEqual(self.context["entry_type"], "SELL_STOP")
        changed_entry = {**self.context, "entry": 3608.0}
        changed_stop = {**self.context, "stop_loss": 3621.0}
        changed_target = {**self.context, "take_profit": 3600.0}
        changed_atr = {**self.context, "atr": 3.1}
        changed_side = {**self.context, "side": "BUY"}
        changed_candle = {**self.context, "candle_time": (NOW - timedelta(minutes=10)).isoformat()}
        for changed in (changed_entry, changed_stop, changed_target, changed_atr, changed_side, changed_candle):
            self.assertFalse(same_m15_plan(self.context, changed))

    def test_prealert_is_persistable_and_restorable(self):
        fingerprint = encode_m15_prealert_context(self.context)
        restored = restore_m15_prealert_context(
            fingerprint, "PREPARAR_VENTA", now=NOW
        )
        self.assertEqual(restored["entry"], 3613.0)
        self.assertEqual(restored["entry_type"], "SELL_STOP")
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
        self.assertEqual(result["entry_type"], "SELL_STOP")

    def test_early_m5_structure_can_validate_before_entry_crosses(self):
        quote = {**QUOTE, "bid": 3613.1, "ask": 3613.3}
        result = evaluate_m15_confirmation(self.context, M5_EARLY, M30, quote, now=NOW)
        self.assertTrue(result["valid"])
        self.assertEqual(result["entry"], 3613.0)

    def test_early_m5_structure_requires_strong_confluence(self):
        weak = {**M5_EARLY, "score": 3}
        quote = {**QUOTE, "bid": 3613.1, "ask": 3613.3}
        result = evaluate_m15_confirmation(self.context, weak, M30, quote, now=NOW)
        self.assertFalse(result["valid"])

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

    def test_prealert_has_full_twenty_minutes_after_m15_close(self):
        plan = {**self.context, "candle_time": (NOW - timedelta(minutes=35)).isoformat()}
        fingerprint = encode_m15_prealert_context(plan)
        restored_at_limit = restore_m15_prealert_context(
            fingerprint, "PREPARAR_VENTA", now=NOW, max_age_minutes=20
        )
        self.assertIsNotNone(restored_at_limit)
        plan_after_limit = {**plan, "candle_time": (NOW - timedelta(minutes=36)).isoformat()}
        expired = encode_m15_prealert_context(plan_after_limit)
        self.assertIsNone(restore_m15_prealert_context(
            expired, "PREPARAR_VENTA", now=NOW, max_age_minutes=20
        ))

    def test_expired_prealert_is_rejected(self):
        stale = {
            **self.context,
            "candle_time": (NOW - timedelta(minutes=36)).isoformat(),
        }
        result = evaluate_m15_confirmation(stale, M5, M30, QUOTE, now=NOW)
        self.assertFalse(result["valid"])

    def test_buy_confirmation_uses_same_m15_level_from_below(self):
        buy_plan = {
            "side": "BUY", "entry": 3613.0, "stop_loss": 3606.0,
            "take_profit": 3627.0, "atr": 3.0,
            "candle_time": (NOW - timedelta(minutes=15)).isoformat(),
        }
        buy_m5 = {
            "signal": "PRESEÑAL", "pre_signal": True, "pre_signal_side": "BUY",
            "primary_bias": "BULL", "score": 5, "candle_age_minutes": 1,
            "structure": {"near_direction": "BUY", "distance_atr": 0.18},
        }
        buy_m30 = {"primary_bias": "BULL", "candle_age_minutes": 5}
        quote = {"accepted": True, "quote_age_seconds": 5, "bid": 3612.7, "ask": 3612.9}
        result = evaluate_m15_confirmation(buy_plan, buy_m5, buy_m30, quote, now=NOW)
        self.assertTrue(result["valid"])
        self.assertEqual(result["entry"], 3613.0)
        self.assertEqual(result["entry_type"], "BUY_STOP")

    def test_buy_price_already_above_entry_is_rejected(self):
        buy_plan = {
            "side": "BUY", "entry": 3613.0, "stop_loss": 3606.0,
            "take_profit": 3627.0, "atr": 3.0,
            "candle_time": (NOW - timedelta(minutes=15)).isoformat(),
        }
        buy_m5 = {
            "signal": "PRESEÑAL", "pre_signal": True, "pre_signal_side": "BUY",
            "primary_bias": "BULL", "score": 5, "candle_age_minutes": 1,
            "structure": {"near_direction": "BUY", "distance_atr": 0.18},
        }
        buy_m30 = {"primary_bias": "BULL", "candle_age_minutes": 5}
        quote = {"accepted": True, "quote_age_seconds": 5, "bid": 3613.1, "ask": 3613.3}
        result = evaluate_m15_confirmation(buy_plan, buy_m5, buy_m30, quote, now=NOW)
        self.assertFalse(result["valid"])
        self.assertIn("cruzó", result["reason"])

    def test_stale_reference_quote_blocks_validation(self):
        stale_quote = {**QUOTE, "bid": 3613.1, "ask": 3613.3, "quote_age_seconds": 90}
        result = evaluate_m15_confirmation(self.context, M5, M30, stale_quote, now=NOW)
        self.assertFalse(result["valid"])
        self.assertIn("antigua", result["reason"])

    def test_stale_m30_context_blocks_validation(self):
        stale_context = {**M30, "candle_age_minutes": 40}
        result = evaluate_m15_confirmation(
            self.context, M5, stale_context,
            {**QUOTE, "bid": 3613.1, "ask": 3613.3}, now=NOW
        )
        self.assertFalse(result["valid"])
        self.assertIn("desactualizado", result["reason"])

    def test_invalid_stop_target_order_is_rejected(self):
        invalid = {**BASE, "planned_stop_loss": 3590.0, "planned_take_profit": 3620.0}
        self.assertIsNone(build_m15_prealert_context(invalid))


if __name__ == "__main__":
    unittest.main()
