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


class SignalAlertStateRecoveryTests(unittest.TestCase):
    def test_failed_state_reset_is_reported(self):
        failed = Mock(status_code=503)
        with (
            patch.object(scanner, "supabase_headers", return_value={"apikey": "test"}),
            patch.object(scanner.requests, "delete", return_value=failed) as delete_request,
        ):
            result = scanner.reset_signal_alert_state("EUR/USD", "5min", "event-1")
        self.assertFalse(result)
        self.assertEqual(delete_request.call_args.kwargs["params"], {
            "market": "eq.EUR/USD",
            "timeframe": "eq.5min",
            "fingerprint": "eq.event-1",
        })

    def test_successful_state_reset_allows_event_retry(self):
        saved = Mock(status_code=204)
        with (
            patch.object(scanner, "supabase_headers", return_value={"apikey": "test"}),
            patch.object(scanner.requests, "delete", return_value=saved),
        ):
            self.assertTrue(scanner.reset_signal_alert_state("XAU/USD", "15min", "event-2"))

    def test_state_reset_fails_closed_on_storage_exception(self):
        with patch.object(scanner, "supabase_headers", side_effect=RuntimeError("storage unavailable")):
            self.assertFalse(scanner.reset_signal_alert_state("EUR/USD", "5min", "event-3"))


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


class PushScanDeliveryIntegrationTests(unittest.TestCase):
    class FakeAnalysisResponse:
        status_code = 200

        @staticmethod
        def get_json():
            return {
                "signal": "COMPRA",
                "pre_signal": False,
                "candle_time": "2026-11-16T12:00:00Z",
                "entry": 1.085,
                "stop_loss": 1.082,
                "take_profit": 1.091,
                "recent_candles": [],
            }

    class FakeInternalClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, *args, **kwargs):
            return PushScanDeliveryIntegrationTests.FakeAnalysisResponse()

    def setUp(self):
        self.client = scanner.app.test_client()
        self.patches = [
            patch.object(scanner, "SIGNAL_CRON_SECRET", "cron-test-secret"),
            patch.object(scanner, "ACCESS_TOKEN_SECRET", "test-only-secret"),
            patch.object(scanner, "market_session", return_value={"open": True}),
            patch.object(scanner, "resolve_pending_signal_outcomes", return_value={"resolved": 0, "errors": 0}),
            patch.object(scanner.app, "test_client", return_value=self.FakeInternalClient()),
            patch.object(scanner, "supabase_headers", return_value={"apikey": "test"}),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    @staticmethod
    def response(status, payload=None):
        result = Mock(status_code=status)
        result.json.return_value = payload if payload is not None else []
        return result

    def request(self, subscriptions, outbox_statuses=None, subscription_status=200):
        outbox_statuses = list(outbox_statuses or [])
        queued_rows = []

        def get(url, **kwargs):
            if url.endswith("/signal_alerts"):
                return self.response(200, [])
            if "/push_subscriptions" in url:
                return self.response(subscription_status, subscriptions)
            if url.endswith("/push_delivery_outbox"):
                return self.response(200, list(queued_rows))
            raise AssertionError("Unexpected GET " + url)

        def post(url, **kwargs):
            if "/signal_history?" in url:
                return self.response(201)
            if "/signal_alerts?" in url:
                return self.response(201, [{"id": 1}])
            if "/push_delivery_outbox?" in url:
                status = outbox_statuses.pop(0) if outbox_statuses else 201
                if status in (200, 201, 204):
                    queued_rows.append({
                        "id": len(queued_rows) + 1,
                        "endpoint": kwargs["json"]["endpoint"],
                        "subscription": kwargs["json"]["subscription"],
                        "payload": kwargs["json"]["payload"],
                        "attempts": 0,
                    })
                return self.response(status)
            raise AssertionError("Unexpected POST " + url)

        with (
            patch.object(scanner.requests, "get", side_effect=get),
            patch.object(scanner.requests, "post", side_effect=post),
            patch.object(scanner.requests, "patch", return_value=self.response(204)),
            patch.object(scanner.requests, "delete", return_value=self.response(204)) as delete,
            patch.object(scanner, "send_web_push") as sender,
        ):
            response = self.client.post(
                "/api/push/scan?symbol=EUR%2FUSD&interval=15min",
                headers={"X-Scanner-Cron-Secret": "cron-test-secret"},
            )
        return response, delete, sender

    def test_signal_is_queued_per_device_and_reported(self):
        devices = [
            {"endpoint": "https://push.example.test/a", "subscription": {"endpoint": "https://push.example.test/a", "keys": {"p256dh": "a", "auth": "a"}}},
            {"endpoint": "https://push.example.test/b", "subscription": {"endpoint": "https://push.example.test/b", "keys": {"p256dh": "b", "auth": "b"}}},
        ]
        response, delete, sender = self.request(devices, [201, 201])
        self.assertEqual(response.status_code, 200)
        check = response.get_json()["checks"][0]
        self.assertEqual(check["status"], "delivery_queued")
        self.assertEqual(check["devices_queued"], 2)
        self.assertEqual(check["queue_errors"], 0)
        self.assertEqual(response.get_json()["alerts_sent"], 2)
        self.assertEqual(sender.call_count, 2)
        delete.assert_not_called()

    def test_partial_outbox_failure_resets_transition_for_retry(self):
        devices = [
            {"endpoint": "https://push.example.test/a", "subscription": {"endpoint": "https://push.example.test/a", "keys": {"p256dh": "a", "auth": "a"}}},
            {"endpoint": "https://push.example.test/b", "subscription": {"endpoint": "https://push.example.test/b", "keys": {"p256dh": "b", "auth": "b"}}},
        ]
        response, delete, sender = self.request(devices, [201, 503])
        self.assertEqual(response.status_code, 200)
        check = response.get_json()["checks"][0]
        self.assertEqual(check["status"], "delivery_queue_error")
        self.assertEqual(check["devices_queued"], 1)
        self.assertEqual(check["queue_errors"], 1)
        self.assertTrue(check["state_reset"])
        self.assertEqual(response.get_json()["alerts_sent"], 1)
        self.assertEqual(sender.call_count, 1)
        delete.assert_called_once()

    def test_subscription_read_failure_resets_transition_for_retry(self):
        response, delete, sender = self.request([], [], subscription_status=503)
        self.assertEqual(response.status_code, 200)
        check = response.get_json()["checks"][0]
        self.assertEqual(check["status"], "subscription_read_error")
        self.assertTrue(check["state_reset"])
        sender.assert_not_called()
        delete.assert_called_once()


if __name__ == "__main__":
    unittest.main()
