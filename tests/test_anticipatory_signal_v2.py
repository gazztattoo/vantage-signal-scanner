import unittest

from anticipatory_signal_v2 import evaluate_preparation, validate_entry


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
        result = self.setup_buy_breakout(approach_speed_per_second=1.0)
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

    def test_price_runaway_is_not_chased(self):
        preparation = self.setup_buy_breakout()
        result = validate_entry(
            preparation=preparation, current_price=2651.5, spread=0.1,
            atr=2.0, trigger_confirmed=True, structure_valid=True)
        self.assertEqual(result["status"], "TOO_LATE")
        self.assertIsNone(result["notification"])


if __name__ == "__main__":
    unittest.main()
