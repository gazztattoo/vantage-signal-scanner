import unittest

from chronological_replay_v2 import replay_chronological_sequence


def setup():
    return {
        "symbol": "XAU/USD", "side": "BUY", "setup_type": "BREAKOUT",
        "current_price": 100.2, "entry": 101.0, "stop_loss": 100.0,
        "take_profit": 103.0, "atr": 1.0,
        "approach_speed_per_second": 0.8 / 240.0, "spread": 0.02,
    }


def valid_trigger():
    return {
        "current_price": 101.0, "spread": 0.02, "atr": 1.0,
        "trigger_confirmed": True, "structure_valid": True,
    }


class ChronologicalReplayTests(unittest.TestCase):
    def test_prepare_then_later_validation_measures_actual_elapsed_time(self):
        result = replay_chronological_sequence([
            {"timestamp": 0, "setup": setup()},
            {"timestamp": 180, "validation": valid_trigger()},
        ])
        self.assertEqual([x["event"] for x in result["timeline"]],
                         ["PREPARAR MT5", "ENTRADA VALIDADA"])
        self.assertEqual(result["timeline"][0]["estimated_lead_seconds"], 240)
        self.assertEqual(result["timeline"][1]["elapsed_seconds"], 180)
        self.assertEqual(result["stats"]["prepare_notices"], 1)
        self.assertEqual(result["stats"]["validated"], 1)

    def test_no_validation_means_unresolved_not_assumed_success(self):
        result = replay_chronological_sequence([
            {"timestamp": 0, "setup": setup()},
            {"timestamp": 120},
        ])
        self.assertEqual(result["timeline"][-1]["event"], "UNRESOLVED_PREPARATION")
        self.assertEqual(result["stats"]["validated"], 0)

    def test_expired_preparation_is_cancelled(self):
        result = replay_chronological_sequence([
            {"timestamp": 0, "setup": setup()},
            {"timestamp": 301, "validation": valid_trigger()},
        ])
        self.assertEqual(result["timeline"][-1]["event"], "CANCELAR PREPARACIÓN")
        self.assertEqual(result["stats"]["invalidated_or_cancelled"], 1)
        self.assertEqual(result["stats"]["validated"], 0)

    def test_invalidated_structure_does_not_validate(self):
        invalid = {**valid_trigger(), "structure_valid": False}
        result = replay_chronological_sequence([
            {"timestamp": 0, "setup": setup()},
            {"timestamp": 60, "validation": invalid},
        ])
        self.assertEqual(result["timeline"][-1]["event"], "CANCELAR PREPARACIÓN")
        self.assertEqual(result["stats"]["validated"], 0)

    def test_late_price_does_not_chase(self):
        late = {**valid_trigger(), "current_price": 101.5}
        result = replay_chronological_sequence([
            {"timestamp": 0, "setup": setup()},
            {"timestamp": 30, "validation": late},
        ])
        self.assertEqual(result["timeline"][-1]["event"], "TOO_LATE")
        self.assertEqual(result["stats"]["validated"], 0)

    def test_timestamps_must_be_strictly_increasing(self):
        with self.assertRaises(ValueError):
            replay_chronological_sequence([
                {"timestamp": 10}, {"timestamp": 10},
            ])

    def test_later_setup_is_ignored_while_preparation_active(self):
        result = replay_chronological_sequence([
            {"timestamp": 0, "setup": setup()},
            {"timestamp": 30, "setup": setup()},
            {"timestamp": 180, "validation": valid_trigger()},
        ])
        self.assertEqual(result["stats"]["prepare_notices"], 1)
        self.assertEqual(result["stats"]["validated"], 1)


if __name__ == "__main__":
    unittest.main()
