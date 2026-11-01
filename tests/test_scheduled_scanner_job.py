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
    @patch.dict(os.environ, {}, clear=True)
    def test_missing_configuration_exits_without_http_request(self):
        with patch.object(scanner_job.requests, "post") as post:
            self.assertEqual(scanner_job.main(), 2)
        post.assert_not_called()

    @patch.dict(os.environ, {
        "SCANNER_API_URL": "https://scanner.example.com/",
        "SIGNAL_CRON_SECRET": "test-only-secret",
    }, clear=True)
    def test_successful_scan_sends_secret_header_and_returns_zero(self):
        response = Mock(status_code=200)
        response.json.return_value = {"ok": True}
        with patch.object(scanner_job.requests, "post", return_value=response) as post:
            self.assertEqual(scanner_job.main(), 0)
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://scanner.example.com/api/push/scan")
        self.assertEqual(kwargs["headers"]["X-Scanner-Cron-Secret"], "test-only-secret")
        self.assertEqual(kwargs["timeout"], (10, 180))

    @patch.dict(os.environ, {
        "SCANNER_API_URL": "https://scanner.example.com",
        "SIGNAL_CRON_SECRET": "test-only-secret",
    }, clear=True)
    def test_non_200_response_returns_failure(self):
        response = Mock(status_code=401)
        response.json.return_value = {"error": "No autorizado."}
        with patch.object(scanner_job.requests, "post", return_value=response):
            self.assertEqual(scanner_job.main(), 1)

    @patch.dict(os.environ, {
        "SCANNER_API_URL": "https://scanner.example.com",
        "SIGNAL_CRON_SECRET": "test-only-secret",
    }, clear=True)
    def test_network_error_returns_failure(self):
        with patch.object(
            scanner_job.requests, "post",
            side_effect=scanner_job.requests.RequestException("network down"),
        ):
            self.assertEqual(scanner_job.main(), 1)


if __name__ == "__main__":
    unittest.main()
