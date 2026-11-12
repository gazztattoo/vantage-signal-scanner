import unittest
from unittest.mock import patch

import app


class TestMockStrategyAPI(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()

    def test_test_alert_preview_builds_prealert_without_trade_levels_or_push(self):
        response = self.client.post("/api/test/alert-preview", json={"analysis": {
            "symbol": "EUR/USD", "interval": "5min",
            "pre_signal": True, "pre_signal_side": "BUY",
        }})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["dry_run"])
        self.assertFalse(payload["push_sent"])
        self.assertEqual(payload["provider_calls"], 0)
        self.assertEqual(payload["event"]["event_type"], "PREALERTA")
        self.assertIsNone(payload["event"]["entry"])
        self.assertIsNone(payload["event"]["stop_loss"])
        self.assertIsNone(payload["event"]["take_profit"])

    def test_test_alert_preview_builds_confirmed_entry_with_levels(self):
        response = self.client.post("/api/test/alert-preview", json={"analysis": {
            "symbol": "XAU/USD", "interval": "15min", "signal": "BUY",
            "entry": 2350.0, "stop_loss": 2340.0, "take_profit": 2370.0,
        }})
        self.assertEqual(response.status_code, 200)
        event = response.get_json()["event"]
        self.assertEqual(event["event_type"], "ENTRADA_VALIDADA")
        self.assertEqual(event["entry"], 2350.0)
        self.assertEqual(event["stop_loss"], 2340.0)
        self.assertEqual(event["take_profit"], 2370.0)

    def test_test_alert_preview_rejects_unknown_market(self):
        response = self.client.post("/api/test/alert-preview", json={"analysis": {
            "symbol": "US30", "interval": "5min", "pre_signal": True,
            "pre_signal_side": "BUY",
        }})
        self.assertEqual(response.status_code, 400)

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
                    self.assertEqual(
                        response.status_code, 200,
                        response.get_data(as_text=True),
                    )
                    network_get.assert_not_called()
                    payload = response.get_json()
                    self.assertEqual(payload["data_mode"], "mock")
                    self.assertEqual(payload["symbol"], symbol)
                    self.assertEqual(payload["interval"], interval)
                    self.assertEqual(payload["candles"], 1200)
                    self.assertIn(
                        payload["signal"],
                        {"COMPRA", "VENTA", "ESPERAR", "PRESEÑAL"},
                    )
                    for field in (
                        "price", "ema20", "ema50", "rsi14", "atr14",
                        "macd", "bollinger_upper", "bollinger_middle",
                        "bollinger_lower", "mtf", "regime", "structure",
                        "pre_signal", "candle_time", "candle_age_minutes",
                        "recent_candles", "warning",
                    ):
                        self.assertIn(field, payload, f"Falta {field} para {symbol} {interval}")
                    self.assertTrue(payload["recent_candles"])
                    self.assertIn("DATOS SIMULADOS", payload["warning"])


if __name__ == "__main__":
    unittest.main()
