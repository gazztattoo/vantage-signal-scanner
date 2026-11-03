import os
import unittest
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


if __name__ == "__main__":
    unittest.main()
