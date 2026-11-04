import unittest
import requests
from unittest.mock import Mock, patch

import biquote_quota


class BiquoteQuotaTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            "os.environ",
            {
                "SUPABASE_URL": "https://example.supabase.co",
                "SUPABASE_SECRET_KEY": "test-service-role",
                "BIQUOTE_DAILY_REQUEST_LIMIT": "100",
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    @patch("biquote_quota.requests.post")
    def test_reservation_calls_atomic_rpc_and_accepts_last_slot(self, post):
        post.return_value = Mock(
            status_code=200,
            json=lambda: {
                "allowed": True,
                "used": 100,
                "daily_limit": 100,
                "remaining": 0,
                "reset_at_utc": "2026-11-05T00:00:00+00:00",
            },
        )
        result = biquote_quota.reserve_biquote_request()
        self.assertEqual(result["used"], 100)
        self.assertEqual(post.call_count, 1)
        self.assertIn("/rpc/consume_biquote_request", post.call_args.args[0])

    @patch("biquote_quota.requests.post")
    def test_rejects_request_after_limit(self, post):
        post.return_value = Mock(
            status_code=200,
            json=lambda: {
                "allowed": False,
                "used": 100,
                "daily_limit": 100,
                "remaining": 0,
                "reset_at_utc": "2026-11-05T00:00:00+00:00",
            },
        )
        with self.assertRaises(biquote_quota.BiquoteQuotaError):
            biquote_quota.reserve_biquote_request()

    @patch("biquote_quota.requests.post")
    def test_fails_closed_when_supabase_is_unavailable(self, post):
        post.side_effect = requests.Timeout("simulated timeout")
        with self.assertRaises(biquote_quota.BiquoteQuotaError):
            biquote_quota.reserve_biquote_request()

    @patch("biquote_quota.requests.post")
    def test_status_read_does_not_reserve_quota(self, post):
        post.return_value = Mock(
            status_code=200,
            json=lambda: {
                "allowed": True,
                "used": 7,
                "daily_limit": 100,
                "remaining": 93,
                "reset_at_utc": "2026-11-05T00:00:00+00:00",
            },
        )
        result = biquote_quota.get_biquote_usage()
        self.assertEqual(result["used"], 7)
        self.assertIn("/rpc/get_biquote_usage", post.call_args.args[0])

    @patch("biquote_quota.requests.post")
    def test_rejects_malformed_rpc_response(self, post):
        post.return_value = Mock(status_code=200, json=lambda: {"used": 1})
        with self.assertRaises(biquote_quota.BiquoteQuotaError):
            biquote_quota.reserve_biquote_request()


if __name__ == "__main__":
    unittest.main()
