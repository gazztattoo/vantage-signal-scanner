import unittest
from unittest.mock import patch

import app as scanner


class ProductionApiSafetyTests(unittest.TestCase):
    def setUp(self):
        self.client = scanner.app.test_client()
        self.patches = [
            patch.object(scanner, "ACCESS_TOKEN_SECRET", "test-only-secret"),
            patch.object(scanner, "PANEL_ACCESS_CODE", "test-only-code"),
            patch.object(scanner, "SIGNAL_CRON_SECRET", "test-only-cron-secret"),
            patch.object(scanner, "API_KEY", "test-only-provider-key"),
        ]
        for item in self.patches:
            item.start()
        self.token = scanner.access_serializer().dumps({"access": True})

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def auth(self):
        return {"Authorization": "Bearer " + self.token}

    def test_health_endpoint_is_available(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)

    def test_market_status_returns_all_supported_markets(self):
        response = self.client.get("/api/market-status")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(set(payload["markets"]), scanner.ALLOWED_SYMBOLS)

    def test_analysis_requires_access_token(self):
        response = self.client.get("/api/analysis?symbol=EUR/USD&interval=15min")
        self.assertEqual(response.status_code, 401)

    def test_analysis_rejects_unknown_symbol_after_authentication(self):
        response = self.client.get(
            "/api/analysis?symbol=INVALID&interval=15min",
            headers=self.auth(),
        )
        self.assertEqual(response.status_code, 400)

    def test_analysis_rejects_unknown_interval_after_authentication(self):
        response = self.client.get(
            "/api/analysis?symbol=EUR/USD&interval=1min",
            headers=self.auth(),
        )
        self.assertEqual(response.status_code, 400)

    def test_push_scan_rejects_missing_cron_secret_without_running_scan(self):
        with patch.object(scanner, "resolve_pending_signal_outcomes") as resolver:
            response = self.client.post("/api/push/scan")
        self.assertEqual(response.status_code, 401)
        resolver.assert_not_called()

    def test_push_scan_rejects_wrong_cron_secret_without_running_scan(self):
        with patch.object(scanner, "resolve_pending_signal_outcomes") as resolver:
            response = self.client.post(
                "/api/push/scan",
                headers={"X-Scanner-Cron-Secret": "wrong-secret"},
            )
        self.assertEqual(response.status_code, 401)
        resolver.assert_not_called()

    def test_access_endpoint_issues_token_for_correct_code(self):
        response = self.client.post("/api/access", json={"code": "test-only-code"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("token", response.get_json())

    def test_access_endpoint_rejects_wrong_code(self):
        response = self.client.post("/api/access", json={"code": "wrong-code"})
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
