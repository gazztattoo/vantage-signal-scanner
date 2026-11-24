import unittest
from unittest.mock import Mock, patch

import app


class PerformanceSourceSeparationTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.multiple(
            app,
            SUPABASE_URL="https://example.supabase.co",
            SUPABASE_SECRET_KEY="test-secret",
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = app.app.test_client()

    def test_primary_performance_excludes_unknown_legacy_provider_rows(self):
        response = Mock(status_code=200)
        response.json.return_value = [
            {
                "symbol": "XAU/USD", "timeframe": "5min", "direction": "COMPRA",
                "status": "TP", "result_r": 2.0,
                "indicators": {"data_provider": "biquote_reference_only"},
            },
            {
                "symbol": "XAU/USD", "timeframe": "5min", "direction": "VENTA",
                "status": "SL", "result_r": -1.0,
                "indicators": {},
            },
        ]
        with patch.object(app.requests, "get", return_value=response):
            result = self.client.get("/api/performance")
        self.assertEqual(result.status_code, 200)
        data = result.get_json()
        self.assertEqual(data["wins"], 1)
        self.assertEqual(data["losses"], 0)
        self.assertEqual(data["closed_trades"], 1)
        self.assertEqual(data["data_quality"]["unknown_legacy_count"], 1)
        self.assertEqual(data["source_breakdown"]["unknown_legacy"]["losses"], 1)
        self.assertEqual(data["source_breakdown"]["biquote_reference_only"]["wins"], 1)


if __name__ == "__main__":
    unittest.main()
