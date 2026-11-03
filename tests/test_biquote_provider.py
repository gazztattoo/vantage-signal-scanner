import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, Mock

os.environ.setdefault("ACCESS_TOKEN_SECRET", "biquote-test-secret")
os.environ.setdefault("PANEL_ACCESS_CODE", "biquote-test-code")

from app import app, access_serializer, fetch_candles


class BiQuoteProviderTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.token = access_serializer().dumps({"access": True})
        self.headers = {"Authorization": "Bearer " + self.token}

    def _bars(self, interval_minutes=5, count=60):
        now = datetime.now(timezone.utc)
        start = now.replace(second=0, microsecond=0)
        start = start - timedelta(minutes=(count + 1) * interval_minutes)
        bars = []
        for i in range(count):
            price = 1.08 + i * 0.0001
            opened = start + timedelta(minutes=i * interval_minutes)
            bars.append({
                "openTime": opened.isoformat().replace("+00:00", "Z"),
                "open": price, "high": price + 0.0002,
                "low": price - 0.0002, "close": price + 0.0001,
                "isOpen": False,
            })
        return bars

    def test_fetch_candles_uses_biquote_and_closed_bars(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"bars": self._bars()}
        with patch("app.requests.get", return_value=response) as get:
            candles, candle_time, age = fetch_candles("EUR/USD", "5min")
        self.assertEqual(len(candles), 60)
        parsed_time = datetime.fromisoformat(candle_time)
        self.assertIsNotNone(parsed_time.tzinfo)
        self.assertEqual(parsed_time.utcoffset(), timedelta(0))
        self.assertGreaterEqual(age, 0)
        self.assertIn("biquote.io/api/EURUSD/ohlc", get.call_args.args[0])
        self.assertEqual(get.call_args.kwargs["params"]["interval"], "5m")

    def test_fetch_candles_rejects_insufficient_or_bad_provider_data(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"bars": [{"isOpen": True}]}
        with patch("app.requests.get", return_value=response):
            with self.assertRaises(RuntimeError):
                fetch_candles("XAU/USD", "15min")

    def test_price_returns_live_bid_ask_and_external_source(self):
        now = datetime.now(timezone.utc).isoformat()
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "bid": 2000.0, "ask": 2000.2, "timestamp": now,
            "marketState": "open", "stale": False,
            "source": "MetaTrader 5 (Broker 1)",
        }
        with patch("app.requests.get", return_value=response):
            result = self.client.get("/api/price?symbol=XAU/USD", headers=self.headers)
        self.assertEqual(result.status_code, 200)
        payload = result.get_json()
        self.assertEqual(payload["bid"], 2000.0)
        self.assertEqual(payload["ask"], 2000.2)
        self.assertFalse(payload["is_vantage_quote"])

    def test_price_rejects_closed_or_stale_quotes(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "bid": 2000.0, "ask": 2000.2,
            "timestamp": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
            "marketState": "closed", "stale": True,
        }
        with patch("app.requests.get", return_value=response):
            result = self.client.get("/api/price?symbol=XAU/USD", headers=self.headers)
        self.assertEqual(result.status_code, 503)

    def test_price_requires_authentication(self):
        result = self.client.get("/api/price?symbol=EUR/USD")
        self.assertEqual(result.status_code, 401)


if __name__ == "__main__":
    unittest.main()
