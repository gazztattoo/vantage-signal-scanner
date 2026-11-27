import unittest
from unittest.mock import patch

import app


class TestOperationalSafetyAPI(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()

    def test_disabled_analysis_fails_closed_without_levels_or_network(self):
        with patch.object(app, "DATA_MODE", "disabled"), patch.object(
            app, "BIQUOTE_RESEARCH_ENABLED", False
        ), patch.object(app.requests, "get") as network_get:
            response = self.client.get("/api/analysis?symbol=XAU/USD&interval=15min")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        network_get.assert_not_called()
        payload = response.get_json()
        self.assertEqual(payload["signal"], "ESPERAR")
        self.assertEqual(payload["data_mode"], "disabled")
        self.assertIsNone(payload["entry"])
        self.assertIsNone(payload["stop_loss"])
        self.assertIsNone(payload["take_profit"])
        self.assertIsNone(payload["planned_entry"])
        self.assertFalse(payload["pre_signal"])
        self.assertFalse(payload["signals_activated"])
        self.assertFalse(payload["orders_sent"])

    def test_legacy_alert_preview_endpoint_is_not_exposed(self):
        response = self.client.post("/api/test/alert-preview", json={"analysis": {
            "symbol": "XAU/USD", "interval": "15min", "signal": "BUY",
            "entry": 2350.0, "stop_loss": 2340.0, "take_profit": 2370.0,
        }})
        self.assertEqual(response.status_code, 404)

    def test_market_status_is_read_only_and_returns_market_map(self):
        response = self.client.get("/api/market-status")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIsInstance(payload["markets"], dict)
        self.assertIn("XAU/USD", payload["markets"])

    def test_all_supported_intervals_fail_closed_when_provider_is_disabled(self):
        for interval in ("5min", "15min", "30min"):
            with self.subTest(interval=interval), patch.object(
                app, "DATA_MODE", "disabled"
            ), patch.object(app, "BIQUOTE_RESEARCH_ENABLED", False):
                response = self.client.get(
                    f"/api/analysis?symbol=XAU/USD&interval={interval}"
                )
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["signal"], "ESPERAR")
            self.assertIsNone(payload["entry"])
            self.assertIsNone(payload["stop_loss"])
            self.assertIsNone(payload["take_profit"])
            self.assertFalse(payload["signals_activated"])


if __name__ == "__main__":
    unittest.main()
