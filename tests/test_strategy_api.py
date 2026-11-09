import unittest
from unittest.mock import patch

import app


class TestMockStrategyAPI(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()

    def test_mock_market_status_keeps_test_cards_visible(self):
        response = self.client.get("/api/market-status")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["data_mode"], "mock")
        self.assertTrue(all(item["open"] for item in payload["markets"].values()))
        self.assertEqual(set(payload["markets"]), {"EUR/USD", "XAU/USD", "BTC/USD"})

    def test_analysis_uses_local_mock_candles_without_network_calls(self):
        with patch.object(app.requests, "get") as network_get:
            response = self.client.get("/api/analysis?symbol=EUR/USD&interval=5min")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        network_get.assert_not_called()
        payload = response.get_json()
        self.assertEqual(payload["data_mode"], "mock")
        self.assertEqual(payload["candles"], 1200)
        self.assertIn("mtf", payload)
        self.assertIn("regime", payload)
        self.assertIn("structure", payload)
        self.assertIn("pre_signal", payload)
        self.assertIn("DATOS SIMULADOS", payload["warning"])

    def test_all_allowed_symbols_and_timeframes_are_supported_offline(self):
        for symbol in ("EUR/USD", "XAU/USD", "BTC/USD"):
            for interval in ("5min", "15min", "30min"):
                with self.subTest(symbol=symbol, interval=interval):
                    with patch.object(app.requests, "get") as network_get:
                        response = self.client.get(
                            f"/api/analysis?symbol={symbol}&interval={interval}"
                        )
                    self.assertEqual(response.status_code, 200)
                    network_get.assert_not_called()
                    payload = response.get_json()
                    self.assertEqual(payload["data_mode"], "mock")
                    self.assertEqual(payload["interval"], interval)
                    self.assertEqual(payload["candles"], 1200)


if __name__ == "__main__":
    unittest.main()
