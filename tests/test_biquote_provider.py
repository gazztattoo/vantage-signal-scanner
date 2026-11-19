import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, Mock

os.environ.setdefault("ACCESS_TOKEN_SECRET", "biquote-test-secret")
os.environ.setdefault("PANEL_ACCESS_CODE", "biquote-test-code")

from app import app, access_serializer, fetch_candles, apply_live_entry_validation, actionable_alert_direction


class BiQuoteProviderTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.token = access_serializer().dumps({"access": True})
        self.headers = {"Authorization": "Bearer " + self.token}
        quota = patch("app.reserve_biquote_request", return_value={
            "allowed": True, "used": 1, "daily_limit": 100,
            "remaining": 99, "reset_at_utc": "2026-11-05T00:00:00+00:00",
        })
        quota.start()
        self.addCleanup(quota.stop)

    def _bars(self, interval_minutes=5, count=60):
        now = datetime.now(timezone.utc)
        start = now.replace(second=0, microsecond=0)
        start = start - timedelta(minutes=(count + 1) * interval_minutes)
        bars = []
        for i in range(count):
            price = 1.08 + i * 0.0001
            opened = start + timedelta(minutes=i * interval_minutes)
            bars.append({
                "openTime": opened.isoformat().replace("+00:00", "Z"),
                "open": price, "high": price + 0.0002,
                "low": price - 0.0002, "close": price + 0.0001,
                "isOpen": False,
            })
        return bars

    def test_fetch_candles_uses_biquote_and_closed_bars(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"bars": self._bars()}
        with patch("app.requests.get", return_value=response) as get:
            candles, candle_time, age = fetch_candles("XAU/USD", "5min")
        self.assertEqual(len(candles), 60)
        parsed_time = datetime.fromisoformat(candle_time)
        self.assertIsNotNone(parsed_time.tzinfo)
        self.assertEqual(parsed_time.utcoffset(), timedelta(0))
        self.assertGreaterEqual(age, 0)
        self.assertIn("biquote.io/api/XAUUSD/ohlc", get.call_args.args[0])
        self.assertEqual(get.call_args.kwargs["params"]["interval"], "5m")

    def test_fetch_candles_rejects_insufficient_or_bad_provider_data(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"bars": [{"isOpen": True}]}
        with patch("app.requests.get", return_value=response):
            with self.assertRaises(RuntimeError):
                fetch_candles("XAU/USD", "15min")

    def test_price_returns_live_bid_ask_and_external_source(self):
        now = datetime.now(timezone.utc).isoformat()
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "bid": 2000.0, "ask": 2000.2, "timestamp": now,
            "marketState": "open", "stale": False,
            "source": "MetaTrader 5 (Broker 1)",
        }
        with patch("app.requests.get", return_value=response):
            result = self.client.get("/api/price?symbol=XAU/USD", headers=self.headers)
        self.assertEqual(result.status_code, 200)
        payload = result.get_json()
        self.assertEqual(payload["bid"], 2000.0)
        self.assertEqual(payload["ask"], 2000.2)
        self.assertFalse(payload["is_vantage_quote"])

    def test_price_rejects_closed_or_stale_quotes(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "bid": 2000.0, "ask": 2000.2,
            "timestamp": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
            "marketState": "closed", "stale": True,
        }
        with patch("app.requests.get", return_value=response):
            result = self.client.get("/api/price?symbol=XAU/USD", headers=self.headers)
        self.assertEqual(result.status_code, 503)

    def test_price_requires_authentication(self):
        result = self.client.get("/api/price?symbol=EUR/USD")
        self.assertEqual(result.status_code, 401)


    def test_alert_direction_requires_explicit_live_readiness(self):
        valid_quote = {"valid": True}
        self.assertEqual(actionable_alert_direction({
            "signal": "COMPRA",
            "entry_readiness": {"ready": True, "status": "READY"},
            "live_quote_validation": valid_quote,
        }), "COMPRA")
        self.assertEqual(actionable_alert_direction({
            "signal": "VENTA",
            "entry_readiness": {"ready": False, "status": "NO_LIVE_TICK"},
            "live_quote_validation": valid_quote,
        }), "ESPERAR")
        self.assertEqual(actionable_alert_direction({
            "signal": "COMPRA",
            "entry_readiness": {"ready": True, "status": "READY"},
            "live_quote_validation": {"valid": False},
        }), "ESPERAR")

    def test_preparation_alert_requires_fresh_quote_but_not_confirmed_entry(self):
        base = {
            "signal": "ESPERAR", "pre_signal": True, "pre_signal_side": "COMPRA",
            "entry_readiness": {"ready": False, "status": "NO_CONFIRMED_SIGNAL"},
            "live_quote_validation": {"valid": True},
        }
        self.assertEqual(actionable_alert_direction(base), "PREPARAR_COMPRA")
        stale = dict(base, live_quote_validation={"valid": False})
        self.assertEqual(actionable_alert_direction(stale), "ESPERAR")
        blocked = dict(base, entry_readiness={"ready": False, "status": "STALE_TICK"})
        self.assertEqual(actionable_alert_direction(blocked), "ESPERAR")

    def test_preparation_is_suppressed_without_live_tick(self):
        payload = {
            "signal": "ESPERAR", "pre_signal": True,
            "pre_signal_side": "COMPRA", "pre_signal_reason": "confluencia parcial",
        }
        with patch("app.biquote_stream_snapshot", return_value={"ticks": {}}):
            result = apply_live_entry_validation(payload, "EUR/USD")
        self.assertFalse(result["pre_signal"])
        self.assertIsNone(result["pre_signal_side"])
        self.assertEqual(result["live_quote_validation"]["status"], "NO_LIVE_TICK")
        self.assertEqual(result["entry_readiness"]["status"], "NO_LIVE_TICK")

    def test_preparation_is_suppressed_when_tick_is_marked_stale_even_with_recent_timestamp(self):
        payload = {
            "signal": "ESPERAR", "pre_signal": True,
            "pre_signal_side": "COMPRA", "price": 1.0800, "atr14": 0.001,
        }
        tick = {
            "bid": 1.08005, "ask": 1.08010,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stale": True, "market_state": "open",
        }
        with patch("app.biquote_stream_snapshot", return_value={"ticks": {"EUR/USD": tick}}):
            result = apply_live_entry_validation(payload, "EUR/USD")
        self.assertFalse(result["pre_signal"])
        self.assertEqual(result["live_quote_validation"]["status"], "STALE_TICK")
        self.assertEqual(result["entry_readiness"]["status"], "STALE_TICK")

    def test_entry_levels_use_executable_live_ask_for_buy(self):
        payload = {
            "signal": "COMPRA", "price": 1.0800, "atr14": 0.001,
        }
        tick = {
            "bid": 1.08005, "ask": 1.08010,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stale": False, "market_state": "open",
        }
        with patch("app.biquote_stream_snapshot", return_value={"ticks": {"EUR/USD": tick}}):
            result = apply_live_entry_validation(payload, "EUR/USD")
        self.assertEqual(result["signal"], "COMPRA")
        self.assertAlmostEqual(result["entry"], 1.08010)
        self.assertAlmostEqual(result["stop_loss"], 1.07860)
        self.assertAlmostEqual(result["take_profit"], 1.08310)
        self.assertTrue(result["entry_readiness"]["ready"])
        self.assertEqual(result["entry_readiness"]["status"], "READY")

    def test_preparation_is_suppressed_when_live_tick_is_stale(self):
        stale_stamp = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        payload = {
            "signal": "ESPERAR", "pre_signal": True,
            "pre_signal_side": "VENTA", "pre_signal_reason": "confluencia parcial",
        }
        tick = {"bid": 1.08, "ask": 1.0802, "timestamp": stale_stamp}
        with patch("app.biquote_stream_snapshot", return_value={"ticks": {"EUR/USD": tick}}):
            result = apply_live_entry_validation(payload, "EUR/USD")
        self.assertFalse(result["pre_signal"])
        self.assertIsNone(result["pre_signal_side"])
        self.assertEqual(result["live_quote_validation"]["status"], "STALE_TICK")

    def test_analysis_refuses_closed_market_before_fetching_or_using_cache(self):
        closed = {
            "open": False,
            "status": "MERCADO CERRADO",
            "reason": "Pausa diaria Vantage.",
            "server_time": "2026-11-03T00:30:00+02:00",
            "server_utc_offset": "UTC+2",
        }
        with patch("app.market_session", return_value=closed), patch("app.fetch_candles") as fetch:
            result = self.client.get(
                "/api/analysis?symbol=XAU/USD&interval=5min",
                headers=self.headers,
            )
        self.assertEqual(result.status_code, 409)
        payload = result.get_json()
        self.assertTrue(payload["market_closed"])
        self.assertEqual(payload["market_state"]["status"], "MERCADO CERRADO")
        fetch.assert_not_called()

    def test_preparation_is_suppressed_when_price_has_moved_too_far(self):
        payload = {
            "signal": "ESPERAR", "pre_signal": True,
            "pre_signal_side": "COMPRA", "price": 1.0800, "atr14": 0.001,
        }
        tick = {
            "bid": 1.0810, "ask": 1.0812,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with patch("app.biquote_stream_snapshot", return_value={"ticks": {"EUR/USD": tick}}):
            result = apply_live_entry_validation(payload, "EUR/USD")
        self.assertFalse(result["pre_signal"])
        self.assertIsNone(result["pre_signal_side"])
        self.assertEqual(result["live_quote_validation"]["status"], "PRICE_MOVED")
        self.assertFalse(result["entry_readiness"]["ready"])
        self.assertEqual(result["entry_readiness"]["status"], "PRICE_MOVED")
        self.assertIn("demasiado", result["pre_signal_reason"])

    def test_preparation_is_kept_when_live_price_is_within_drift_limit(self):
        payload = {
            "signal": "ESPERAR", "pre_signal": True,
            "pre_signal_side": "COMPRA", "price": 1.0800, "atr14": 0.001,
        }
        tick = {
            "bid": 1.08005, "ask": 1.08010,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with patch("app.biquote_stream_snapshot", return_value={"ticks": {"EUR/USD": tick}}):
            result = apply_live_entry_validation(payload, "EUR/USD")
        self.assertTrue(result["pre_signal"])
        self.assertEqual(result["pre_signal_side"], "COMPRA")
        self.assertTrue(result["live_quote_validation"]["valid"])
        self.assertFalse(result["entry_readiness"]["ready"])
        self.assertEqual(result["entry_readiness"]["status"], "NO_CONFIRMED_SIGNAL")


if __name__ == "__main__":
    unittest.main()
