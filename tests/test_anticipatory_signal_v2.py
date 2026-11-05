import unittest

from anticipatory_signal_v2 import evaluate_preparation, validate_entry, cancel_preparation


class AnticipatorySignalTests(unittest.TestCase):
    def setup_buy_breakout(self, **changes):
        values = dict(
            symbol="XAU/USD", side="BUY", setup_type="BREAKOUT",
            current_price=2650.0, entry=2651.0, stop_loss=2649.0,
            take_profit=2655.0, atr=2.0,
            approach_speed_per_second=(1.0 / 240.0), spread=0.1,
        )
        values.update(changes)
        return evaluate_preparation(**values)

    def test_prepare_notice_when_lead_time_is_three_to_five_minutes(self):
        result = self.setup_buy_breakout()  # Default speed estimates a 4-minute lead.
        self.assertEqual(result["status"], "PREPARE")
        self.assertEqual(result["notification"], "PREPARAR MT5")
        self.assertEqual(result["order_type"], "BUY_STOP")

    def test_too_late_does_not_send_prepare_notice(self):
        result = self.setup_buy_breakout(approach_speed_per_second=0.001)
        self.assertEqual(result["status"], "WAIT")  # Entry too far for 5-minute window.
        result = self.setup_buy_breakout(
            current_price=2650.9, approach_speed_per_second=1.0)
        self.assertEqual(result["status"], "TOO_LATE")
        self.assertIsNone(result["notification"])

    def test_pullback_maps_to_limit_order(self):
        result = self.setup_buy_breakout(
            setup_type="PULLBACK", current_price=2652.0, entry=2651.0,
            stop_loss=2649.0, take_profit=2655.0,
        )
        self.assertEqual(result["order_type"], "BUY_LIMIT")

    def test_invalid_levels_and_high_spread_wait(self):
        self.assertEqual(
            self.setup_buy_breakout(stop_loss=2652.0)["status"], "WAIT")
        self.assertEqual(
            self.setup_buy_breakout(spread=0.5)["status"], "WAIT")

    def test_entry_requires_trigger_and_valid_structure(self):
        preparation = self.setup_buy_breakout()
        waiting = validate_entry(
            preparation=preparation, current_price=2651.0, spread=0.1,
            atr=2.0, trigger_confirmed=False, structure_valid=True)
        self.assertEqual(waiting["status"], "WAIT")
        invalid = validate_entry(
            preparation=preparation, current_price=2651.0, spread=0.1,
            atr=2.0, trigger_confirmed=True, structure_valid=False)
        self.assertEqual(invalid["status"], "INVALIDATED")

    def test_validated_notice_only_after_trigger_and_near_entry(self):
        preparation = self.setup_buy_breakout()
        result = validate_entry(
            preparation=preparation, current_price=2651.05, spread=0.1,
            atr=2.0, trigger_confirmed=True, structure_valid=True)
        self.assertEqual(result["status"], "VALIDATED")
        self.assertEqual(result["notification"], "ENTRADA VALIDADA")

    def test_invalidated_preparation_emits_manual_cancel_notice(self):
        preparation = self.setup_buy_breakout()
        cancelled = cancel_preparation(
            preparation=preparation, reason="Ruptura fallida y cierre dentro del rango.")
        self.assertEqual(cancelled["status"], "CANCELLED")
        self.assertEqual(cancelled["notification"], "CANCELAR PREPARACIÓN")
        self.assertTrue(cancelled["manual_action_required"])
        self.assertIn("manualmente", cancelled["warning"])

    def test_expired_preparation_emits_cancel_notice(self):
        preparation = self.setup_buy_breakout()
        cancelled = cancel_preparation(preparation=preparation, reason="", expired=True)
        self.assertEqual(cancelled["status"], "CANCELLED")
        self.assertIn("venció", cancelled["reason"])

    def test_cannot_cancel_non_active_preparation(self):
        cancelled = cancel_preparation(
            preparation={"status": "VALIDATED"}, reason="Already validated")
        self.assertEqual(cancelled["status"], "NO_ACTIVE_PREPARATION")
        self.assertIsNone(cancelled["notification"])


    def test_false_breakout_invalidates_and_cancels_preparation(self):
        preparation = self.setup_buy_breakout()
        # A later closed candle closes back inside the prior range.
        invalid_entry = validate_entry(
            preparation=preparation, current_price=2650.7, spread=0.1,
            atr=2.0, trigger_confirmed=False, structure_valid=False)
        self.assertEqual(invalid_entry["status"], "INVALIDATED")
        cancelled = cancel_preparation(
            preparation=preparation,
            reason="Falsa ruptura: cierre de vela nuevamente dentro del rango.")
        self.assertEqual(cancelled["status"], "CANCELLED")
        self.assertTrue(cancelled["manual_action_required"])

    def test_rapid_price_approach_is_rejected_without_chasing(self):
        result = self.setup_buy_breakout(
            current_price=2650.8, approach_speed_per_second=0.02)
        self.assertEqual(result["status"], "TOO_LATE")
        self.assertIsNone(result["notification"])

    def test_sell_breakout_and_pullback_order_types(self):
        sell_stop = evaluate_preparation(
            symbol="EUR/USD", side="SELL", setup_type="BREAKOUT",
            current_price=1.0850, entry=1.0840, stop_loss=1.0860,
            take_profit=1.0800, atr=0.0020,
            approach_speed_per_second=(0.001 / 240.0), spread=0.0001)
        self.assertEqual(sell_stop["order_type"], "SELL_STOP")
        sell_limit = evaluate_preparation(
            symbol="EUR/USD", side="SELL", setup_type="PULLBACK",
            current_price=1.0830, entry=1.0840, stop_loss=1.0860,
            take_profit=1.0800, atr=0.0020,
            approach_speed_per_second=(0.001 / 240.0), spread=0.0001)
        self.assertEqual(sell_limit["order_type"], "SELL_LIMIT")

    def test_spread_expansion_blocks_second_stage(self):
        preparation = self.setup_buy_breakout()
        result = validate_entry(
            preparation=preparation, current_price=2651.0, spread=0.5,
            atr=2.0, trigger_confirmed=True, structure_valid=True)
        self.assertEqual(result["status"], "WAIT")
        self.assertIsNone(result["notification"])

    def test_expired_preparation_cannot_be_validated_or_cancelled_twice(self):
        preparation = self.setup_buy_breakout()
        expired = cancel_preparation(preparation=preparation, reason="", expired=True)
        self.assertEqual(expired["status"], "CANCELLED")
        second_cancel = cancel_preparation(preparation=expired, reason="duplicate")
        self.assertEqual(second_cancel["status"], "NO_ACTIVE_PREPARATION")
        validated = validate_entry(
            preparation=expired, current_price=2651.0, spread=0.1,
            atr=2.0, trigger_confirmed=True, structure_valid=True)
        self.assertEqual(validated["status"], "WAIT")
        self.assertIsNone(validated["notification"])

    def test_price_runaway_is_not_chased(self):
        preparation = self.setup_buy_breakout()
        result = validate_entry(
            preparation=preparation, current_price=2651.5, spread=0.1,
            atr=2.0, trigger_confirmed=True, structure_valid=True)
        self.assertEqual(result["status"], "TOO_LATE")
        self.assertIsNone(result["notification"])


if __name__ == "__main__":
    unittest.main()
