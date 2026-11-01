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


class PushDeliveryOutboxTests(unittest.TestCase):
    def pending_row(self, attempts=0, endpoint="https://push.example.test/device-a"):
        return {
            "id": 41,
            "endpoint": endpoint,
            "subscription": {"endpoint": endpoint, "keys": {"p256dh": "x", "auth": "y"}},
            "payload": {"title": "GLOBAL SIGNAL", "body": "test"},
            "attempts": attempts,
        }

    def test_success_marks_only_that_device_as_sent(self):
        due = Mock(status_code=200)
        due.json.return_value = [self.pending_row()]
        saved = Mock(status_code=204)
        with (
            patch.object(scanner, "supabase_headers", return_value={"apikey": "test"}),
            patch.object(scanner.requests, "get", return_value=due),
            patch.object(scanner.requests, "patch", return_value=saved) as patch_request,
            patch.object(scanner, "send_web_push") as send,
        ):
            result = scanner.process_pending_push_deliveries()
        self.assertEqual(result["sent"], 1)
        self.assertEqual(result["retrying"], 0)
        send.assert_called_once()
        update = patch_request.call_args.kwargs["json"]
        self.assertEqual(update["status"], "sent")
        self.assertEqual(update["attempts"], 1)

    def test_transient_failure_stays_pending_for_retry(self):
        due = Mock(status_code=200)
        due.json.return_value = [self.pending_row(attempts=1)]
        saved = Mock(status_code=204)
        with (
            patch.object(scanner, "supabase_headers", return_value={"apikey": "test"}),
            patch.object(scanner.requests, "get", return_value=due),
            patch.object(scanner.requests, "patch", return_value=saved) as patch_request,
            patch.object(scanner, "send_web_push", side_effect=RuntimeError("temporary outage")),
        ):
            result = scanner.process_pending_push_deliveries()
        self.assertEqual(result["retrying"], 1)
        update = patch_request.call_args.kwargs["json"]
        self.assertEqual(update["status"], "pending")
        self.assertEqual(update["attempts"], 2)
        self.assertIn("next_attempt_at", update)

    def test_expired_subscription_is_marked_dead_and_removed(self):
        due = Mock(status_code=200)
        endpoint = "https://push.example.test/expired"
        due.json.return_value = [self.pending_row(endpoint=endpoint)]
        saved = Mock(status_code=204)
        expired_error = RuntimeError("gone")
        expired_error.response = Mock(status_code=410)
        with (
            patch.object(scanner, "supabase_headers", return_value={"apikey": "test"}),
            patch.object(scanner.requests, "get", return_value=due),
            patch.object(scanner.requests, "patch", return_value=saved) as patch_request,
            patch.object(scanner.requests, "delete", return_value=Mock(status_code=204)) as delete_request,
            patch.object(scanner, "send_web_push", side_effect=expired_error),
        ):
            result = scanner.process_pending_push_deliveries()
        self.assertEqual(result["dead"], 1)
        self.assertEqual(patch_request.call_args.kwargs["json"]["status"], "dead")
        delete_request.assert_called_once()
        self.assertEqual(delete_request.call_args.kwargs["params"]["endpoint"], "eq." + endpoint)

    def test_outbox_read_error_does_not_attempt_push(self):
        due = Mock(status_code=503)
        with (
            patch.object(scanner, "supabase_headers", return_value={"apikey": "test"}),
            patch.object(scanner.requests, "get", return_value=due),
            patch.object(scanner, "send_web_push") as send,
        ):
            result = scanner.process_pending_push_deliveries()
        self.assertEqual(result["errors"], 1)
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
