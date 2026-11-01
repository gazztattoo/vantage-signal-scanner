"""Flask middleware tests for the read-only API boundary.

No external services are called. The verifier is stubbed to isolate routing rules.
"""
import unittest
from unittest.mock import patch

import app as scanner_app


class ReadonlyRouteBoundaryTests(unittest.TestCase):
    def setUp(self):
        scanner_app.ACCESS_TOKEN_SECRET = "unit-test-only-secret"
        self.client = scanner_app.app.test_client()

    def test_readonly_token_is_considered_only_on_allowlisted_get(self):
        with patch.object(scanner_app, "verify_readonly_access_token", return_value=True):
            response = self.client.get(
                "/api/analysis?symbol=NOT-ALLOWED",
                headers={"Authorization": "Bearer mocked-readonly-token"},
            )
        # 400 is returned by the endpoint after auth succeeds; 401 would mean
        # the read-only credential was not accepted by the middleware.
        self.assertEqual(response.status_code, 400)

    def test_readonly_token_cannot_authorize_push_post(self):
        with patch.object(scanner_app, "verify_readonly_access_token", return_value=True):
            response = self.client.post(
                "/api/push/test",
                headers={"Authorization": "Bearer mocked-readonly-token"},
                json={},
            )
        self.assertEqual(response.status_code, 401)

    def test_readonly_token_cannot_authorize_access_token_minting(self):
        with patch.object(scanner_app, "verify_readonly_access_token", return_value=True):
            response = self.client.post(
                "/api/access",
                headers={"Authorization": "Bearer mocked-readonly-token"},
                json={"code": "not-the-panel-code"},
            )
        # /api/access remains outside the panel GET guard and still requires
        # the panel code; the mocked read-only verifier is never consulted.
        self.assertIn(response.status_code, (401, 503))


if __name__ == "__main__":
    unittest.main()
