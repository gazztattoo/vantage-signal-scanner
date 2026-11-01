import unittest
from unittest.mock import Mock, patch

import app as scanner


class PushTestDeviceScopeTests(unittest.TestCase):
    def setUp(self):
        self.client = scanner.app.test_client()
        self.secret_patch = patch.object(scanner, "ACCESS_TOKEN_SECRET", "test-only-secret")
        self.secret_patch.start()
        self.token = scanner.access_serializer().dumps({"access": True})

    def tearDown(self):
        self.secret_patch.stop()

    def auth(self):
        return {"Authorization": "Bearer " + self.token}

    def test_push_test_requires_device_endpoint(self):
        with patch.object(scanner.requests, "get") as get:
            response = self.client.post("/api/push/test", json={}, headers=self.auth())
        self.assertEqual(response.status_code, 400)
        get.assert_not_called()

    def test_push_test_queries_only_requesting_device(self):
        endpoint = "https://push.example.test/device-123"
        subscription = {
            "endpoint": endpoint,
            "keys": {"p256dh": "test", "auth": "test"},
        }
        stored = Mock(status_code=200)
        stored.json.return_value = [{"subscription": subscription}]
        with (
            patch.object(scanner, "supabase_headers", return_value={"apikey": "test"}),
            patch.object(scanner.requests, "get", return_value=stored) as get,
            patch.object(scanner, "send_web_push") as send,
        ):
            response = self.client.post(
                "/api/push/test",
                json={"endpoint": endpoint},
                headers=self.auth(),
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(get.call_args.kwargs["params"]["endpoint"], "eq." + endpoint)
        send.assert_called_once()

    def test_push_test_does_not_send_when_device_is_not_registered(self):
        stored = Mock(status_code=200)
        stored.json.return_value = []
        with (
            patch.object(scanner, "supabase_headers", return_value={"apikey": "test"}),
            patch.object(scanner.requests, "get", return_value=stored),
            patch.object(scanner, "send_web_push") as send,
        ):
            response = self.client.post(
                "/api/push/test",
                json={"endpoint": "https://push.example.test/unknown"},
                headers=self.auth(),
            )
        self.assertEqual(response.status_code, 404)
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
