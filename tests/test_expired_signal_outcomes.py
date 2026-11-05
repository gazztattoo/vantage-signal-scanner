import unittest
from unittest.mock import Mock, patch

import app


class ExpiredSignalOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.multiple(
            app,
            SUPABASE_URL="https://example.supabase.co",
            SUPABASE_SECRET_KEY="test-secret",
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def make_candles(self, final_close):
        candles = []
        # Signal candle opens at 00:00 UTC. The following 24 candles
        # are five-minute candles and none touches SL=90 or TP=120.
        for index in range(1, 25):
            minute = index * 5
            close = final_close if index == 24 else 100.0
            candles.append({
                "time": f"2026-10-01T{minute // 60:02d}:{minute % 60:02d}:00+00:00",
                "open": 100.0,
                "high": max(100.0, close) + 1.0,
                "low": min(100.0, close) - 1.0,
                "close": close,
            })
        return candles

    def run_case(self, direction, final_close):
        pending_response = Mock(status_code=200)
        pending_response.json.return_value = [{
            "id": 7,
            "direction": direction,
            "candle_time": "2026-10-01T00:00:00+00:00",
            "entry_price": 100.0,
            "stop_loss": 90.0 if direction == "COMPRA" else 110.0,
            "take_profit": 120.0 if direction == "COMPRA" else 80.0,
            "status": "PENDIENTE",
        }]
        patch_response = Mock(status_code=204)
        with patch.object(app.requests, "get", return_value=pending_response):
            with patch.object(app.requests, "patch", return_value=patch_response) as patch_call:
                result = app.resolve_pending_signal_outcomes(
                    "EUR/USD", "5min", self.make_candles(final_close)
                )
        return result, patch_call.call_args.kwargs["json"]

    def test_expired_buy_is_marked_to_market(self):
        result, payload = self.run_case("COMPRA", 105.0)
        self.assertEqual(result, {"resolved": 0, "expired": 1, "errors": 0})
        self.assertEqual(payload["status"], "CADUCADA")
        self.assertEqual(payload["exit_price"], 105.0)
        self.assertEqual(payload["result_r"], 0.5)

    def test_expired_sell_is_marked_to_market(self):
        result, payload = self.run_case("VENTA", 95.0)
        self.assertEqual(result, {"resolved": 0, "expired": 1, "errors": 0})
        self.assertEqual(payload["exit_price"], 95.0)
        self.assertEqual(payload["result_r"], 0.5)

    def test_invalid_initial_risk_is_reported_not_written(self):
        pending_response = Mock(status_code=200)
        pending_response.json.return_value = [{
            "id": 8,
            "direction": "COMPRA",
            "candle_time": "2026-10-01T00:00:00+00:00",
            "entry_price": 100.0,
            "stop_loss": 100.0,
            "take_profit": 120.0,
            "status": "PENDIENTE",
        }]
        with patch.object(app.requests, "get", return_value=pending_response):
            with patch.object(app.requests, "patch") as patch_call:
                result = app.resolve_pending_signal_outcomes(
                    "EUR/USD", "5min", self.make_candles(105.0)
                )
        self.assertEqual(result["errors"], 1)
        patch_call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
