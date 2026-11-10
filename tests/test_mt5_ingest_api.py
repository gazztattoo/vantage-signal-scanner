import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import app


def valid_payload():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    stamp = now - timedelta(minutes=5)
    server = stamp + timedelta(hours=2)
    return {
        "symbol": "EUR/USD",
        "broker_symbol": "EURUSD.a",
        "timeframe": "5min",
        "candles": [{
            "time": stamp.isoformat().replace("+00:00", "Z"),
            "server_time": server.replace(tzinfo=None).isoformat(timespec="seconds"),
            "server_utc_offset_minutes": 120,
            "open": 1.1, "high": 1.101, "low": 1.099, "close": 1.1005,
            "tick_volume": 10, "spread_points": 8, "is_closed": True,
        }],
    }


class TestMt5IngestEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        self.patches = [
            patch.object(app, "MT5_INGEST_TOKEN", "test-only-mt5-token"),
            patch.object(app, "SUPABASE_URL", app.MT5_TEST_SUPABASE_URL),
            patch.object(app, "SUPABASE_SECRET_KEY", "test-only-supabase-key"),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def test_rejects_missing_or_wrong_token_before_database_call(self):
        with patch.object(app.requests, "post") as db_post:
            missing = self.client.post("/api/mt5/ingest", json=valid_payload())
            wrong = self.client.post(
                "/api/mt5/ingest", json=valid_payload(),
                headers={"X-MT5-Token": "wrong"},
            )
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.status_code, 401)
        db_post.assert_not_called()

    def test_rejects_non_test_supabase_target(self):
        with patch.object(app, "SUPABASE_URL", "https://production.supabase.co"):
            with patch.object(app.requests, "post") as db_post:
                response = self.client.post(
                    "/api/mt5/ingest", json=valid_payload(),
                    headers={"X-MT5-Token": "test-only-mt5-token"},
                )
        self.assertEqual(response.status_code, 503)
        db_post.assert_not_called()

    def test_rejects_unconfigured_token_and_database_credentials(self):
        with patch.object(app, "MT5_INGEST_TOKEN", ""):
            response = self.client.post("/api/mt5/ingest", json=valid_payload())
            self.assertEqual(response.status_code, 503)
        with patch.object(app, "SUPABASE_SECRET_KEY", ""):
            response = self.client.post(
                "/api/mt5/ingest", json=valid_payload(),
                headers={"X-MT5-Token": "test-only-mt5-token"},
            )
            self.assertEqual(response.status_code, 503)

    def test_valid_batch_upserts_to_test_only_and_does_not_activate_signals(self):
        mocked = Mock()
        mocked.status_code = 201
        with patch.object(app.requests, "post", return_value=mocked) as db_post:
            response = self.client.post(
                "/api/mt5/ingest", json=valid_payload(),
                headers={"X-MT5-Token": "test-only-mt5-token"},
            )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["stored"], 1)
        self.assertEqual(data["data_mode"], "mock")
        self.assertFalse(data["signals_activated"])
        args, kwargs = db_post.call_args
        self.assertEqual(args[0], app.MT5_TEST_SUPABASE_URL + "/rest/v1/mt5_market_candles")
        self.assertEqual(kwargs["params"]["on_conflict"], "symbol,timeframe,open_time")
        self.assertEqual(kwargs["json"][0]["source"], "VANTAGE_MT5")

    def test_invalid_candle_batch_is_rejected_before_database_call(self):
        payload = valid_payload()
        payload["candles"][0]["is_closed"] = False
        with patch.object(app.requests, "post") as db_post:
            response = self.client.post(
                "/api/mt5/ingest", json=payload,
                headers={"X-MT5-Token": "test-only-mt5-token"},
            )
        self.assertEqual(response.status_code, 400)
        db_post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
