import importlib.util
import os
import pathlib
import unittest
from unittest.mock import Mock, patch

SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "run_signal_scan.py"
SPEC = importlib.util.spec_from_file_location("run_signal_scan", SCRIPT_PATH)
scanner_job = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scanner_job)


class ScheduledScannerJobTests(unittest.TestCase):
    def _response(self, status=200, payload=None):
        response = Mock(status_code=status)
        response.json.return_value = payload or {
            "checks": [{"status": "market_closed"}],
            "delivery_queue": {"errors": 0},
        }
        return response

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_configuration_exits_without_http_request(self):
        with patch.object(scanner_job.requests, "Session") as session:
            self.assertEqual(scanner_job.main(), 2)
        session.assert_not_called()

    @patch.dict(os.environ, {
        "SCANNER_API_URL": "https://scanner.example.com/",
        "SIGNAL_CRON_SECRET": "test-only-secret",
    }, clear=True)
    def test_successful_scan_sends_secret_header_and_returns_zero(self):
        session = Mock()
        session.post.return_value = self._response()
        with patch.object(scanner_job.requests, "Session", return_value=session), \
             patch.object(scanner_job.time, "sleep"):
            self.assertEqual(scanner_job.main(), 0)
        self.assertEqual(session.post.call_count, 6)
        args, kwargs = session.post.call_args
        self.assertEqual(args[0], "https://scanner.example.com/api/push/scan")
        self.assertEqual(kwargs["headers"]["X-Scanner-Cron-Secret"], "test-only-secret")
        self.assertEqual(kwargs["timeout"], (10, 45))

    @patch.dict(os.environ, {
        "SCANNER_API_URL": "https://scanner.example.com",
        "SIGNAL_CRON_SECRET": "test-only-secret",
    }, clear=True)
    def test_non_200_response_returns_failure(self):
        session = Mock()
        session.post.return_value = self._response(401, {"error": "No autorizado."})
        with patch.object(scanner_job.requests, "Session", return_value=session), \
             patch.object(scanner_job.time, "sleep"):
            self.assertEqual(scanner_job.main(), 1)
        self.assertEqual(session.post.call_count, 6)

    @patch.dict(os.environ, {
        "SCANNER_API_URL": "https://scanner.example.com",
        "SIGNAL_CRON_SECRET": "test-only-secret",
    }, clear=True)
    def test_network_error_returns_failure(self):
        session = Mock()
        session.post.side_effect = scanner_job.requests.RequestException("network down")
        with patch.object(scanner_job.requests, "Session", return_value=session), \
             patch.object(scanner_job.time, "sleep"):
            self.assertEqual(scanner_job.main(), 1)
        self.assertEqual(session.post.call_count, 6)


if __name__ == "__main__":
    unittest.main()
