import os
import unittest
import time
from unittest.mock import patch

os.environ.setdefault("ACCESS_TOKEN_SECRET", "precision-test-secret")
os.environ.setdefault("PANEL_ACCESS_CODE", "precision-test-code")

from app import app, ANALYSIS_CACHE, access_serializer


class PrecisionCandidateEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.cache_before = dict(ANALYSIS_CACHE)
        ANALYSIS_CACHE.clear()
        self.token = access_serializer().dumps({"access": True})

    def tearDown(self):
        ANALYSIS_CACHE.clear()
        ANALYSIS_CACHE.update(self.cache_before)

    def test_missing_cache_returns_wait_without_provider_fetch(self):
        with patch("app.fetch_candles", side_effect=AssertionError("provider must not be called")):
            response = self.client.get(
                "/api/precision-candidate?symbol=EUR/USD",
                headers={"Authorization": "Bearer " + self.token},
            )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["signal"], "ESPERAR")
        self.assertTrue(payload["cache_only"])

    def test_endpoint_requires_panel_auth(self):
        response = self.client.get("/api/precision-candidate?symbol=EUR/USD")
        self.assertEqual(response.status_code, 401)

    def test_invalid_symbol_is_rejected(self):
        response = self.client.get(
            "/api/precision-candidate?symbol=USD/JPY",
            headers={"Authorization": "Bearer " + self.token},
        )
        self.assertEqual(response.status_code, 400)

    def _cache_payload(self, interval, close, ema20, ema50, rsi, macd):
        candles = [
            {"high": 100.5, "low": 99.5, "close": 100.0},
            {"high": 101.0, "low": 99.8, "close": 100.5},
            {"high": 102.0, "low": 100.0, "close": 101.0},
            {"high": close + 0.2, "low": close - 0.2, "close": close},
        ]
        return {"ema20": ema20, "ema50": ema50, "adx14": 23, "rsi14": rsi,
                "macd_histogram": macd, "atr14": 1.0, "candle_age_minutes": 1.0,
                "recent_candles": candles, "interval": interval}

    def test_fresh_cached_m15_m5_returns_candidate_without_fetch(self):
        ANALYSIS_CACHE[("EUR/USD", "15min")] = {"stored_at": time.monotonic(),
            "payload": self._cache_payload("15min", 102, 101, 100, 60, 0.3)}
        ANALYSIS_CACHE[("EUR/USD", "5min")] = {"stored_at": time.monotonic(),
            "payload": self._cache_payload("5min", 103, 102, 101, 62, 0.2)}
        with patch("app.fetch_candles", side_effect=AssertionError("must remain cache-only")):
            response = self.client.get("/api/precision-candidate?symbol=EUR/USD",
                headers={"Authorization": "Bearer " + self.token})
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(result["signal"], "BUY")
        self.assertTrue(result["cache_only"])
        self.assertFalse(result["execution_ready"])

    def test_expired_cache_returns_wait_without_fetch(self):
        ANALYSIS_CACHE[("EUR/USD", "15min")] = {"stored_at": time.monotonic() - 4000,
            "payload": self._cache_payload("15min", 102, 101, 100, 60, 0.3)}
        ANALYSIS_CACHE[("EUR/USD", "5min")] = {"stored_at": time.monotonic(),
            "payload": self._cache_payload("5min", 103, 102, 101, 62, 0.2)}
        with patch("app.fetch_candles", side_effect=AssertionError("must remain cache-only")):
            response = self.client.get("/api/precision-candidate?symbol=EUR/USD",
                headers={"Authorization": "Bearer " + self.token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["signal"], "ESPERAR")

if __name__ == "__main__":
    unittest.main()
