import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import app


class BiQuoteResearchEndpointTests(unittest.TestCase):
    def setUp(self):
        app.BIQUOTE_RESEARCH_CACHE.clear()
        self.client = app.app.test_client()

    def _payload(self, symbol="XAUUSD", interval="5m", count=60):
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        step = int(interval[:-1])
        bars = []
        for i in range(count):
            opened = now - timedelta(minutes=step * (count - i))
            price = 2650.0 + i * 0.1
            bars.append({
                "openTime": opened.isoformat().replace("+00:00", "Z"),
                "open": price,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price + 0.2,
                "tickVolume": 25,
                "isOpen": False,
            })
        bars.append({
            "openTime": now.isoformat().replace("+00:00", "Z"),
            "open": 2656.0, "high": 2657.0, "low": 2655.0,
            "close": 2656.5, "tickVolume": 5, "isOpen": True,
        })
        return {"symbol": symbol, "interval": interval, "bars": bars}

    def test_endpoint_returns_research_only_closed_ohlc(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = self._payload()
        with patch.object(app, "BIQUOTE_RESEARCH_ENABLED", True), patch(
            "app.requests.get", return_value=response
        ) as get:
            result = self.client.get(
                "/api/research/biquote/ohlc?symbol=XAU/USD&interval=5m"
            )
        self.assertEqual(result.status_code, 200)
        data = result.get_json()
        self.assertTrue(data["research_only"])
        self.assertFalse(data["signals_activated"])
        self.assertEqual(data["data_mode"], "mock")
        self.assertEqual(data["closed_bars"], 60)
        self.assertTrue(all(not bar.get("isOpen", False) for bar in data["bars"]))
        self.assertIn("/api/XAUUSD/ohlc", get.call_args.args[0])
        self.assertEqual(get.call_args.kwargs["params"]["interval"], "5m")

    def test_endpoint_cache_prevents_repeated_provider_requests(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = self._payload()
        with patch.object(app, "BIQUOTE_RESEARCH_ENABLED", True), patch(
            "app.requests.get", return_value=response
        ) as get:
            first = self.client.get(
                "/api/research/biquote/ohlc?symbol=XAU/USD&interval=5m"
            )
            second = self.client.get(
                "/api/research/biquote/ohlc?symbol=XAU/USD&interval=5m"
            )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.get_json()["cache"]["hit"] is False)
        self.assertEqual(get.call_count, 1)

    def test_endpoint_rejects_unsupported_interval_without_network(self):
        with patch.object(app, "BIQUOTE_RESEARCH_ENABLED", True), patch(
            "app.requests.get"
        ) as get:
            result = self.client.get(
                "/api/research/biquote/ohlc?symbol=XAU/USD&interval=1m"
            )
        self.assertEqual(result.status_code, 400)
        get.assert_not_called()

    def test_endpoint_can_be_disabled(self):
        with patch.object(app, "BIQUOTE_RESEARCH_ENABLED", False), patch(
            "app.requests.get"
        ) as get:
            result = self.client.get(
                "/api/research/biquote/ohlc?symbol=XAU/USD&interval=5m"
            )
        self.assertEqual(result.status_code, 503)
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
