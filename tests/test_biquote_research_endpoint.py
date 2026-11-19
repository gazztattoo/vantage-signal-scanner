import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import app


class BiQuoteResearchEndpointTests(unittest.TestCase):
    def setUp(self):
        app.BIQUOTE_RESEARCH_CACHE.clear()
        app.BIQUOTE_TICK_RESEARCH_CACHE.clear()
        self.client = app.app.test_client()

    def _payload(self, symbol="XAUUSD", interval="5m", count=60):
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        step = int(interval[:-1])
        bars = []
        for i in range(count):
            opened = now - timedelta(minutes=step * (count - i))
            price = 2650.0 + i * 0.1
            bars.append({
                "openTime": opened.isoformat().replace("+00:00", "Z"),
                "open": price,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price + 0.2,
                "tickVolume": 25,
                "isOpen": False,
            })
        bars.append({
            "openTime": now.isoformat().replace("+00:00", "Z"),
            "open": 2656.0, "high": 2657.0, "low": 2655.0,
            "close": 2656.5, "tickVolume": 5, "isOpen": True,
        })
        return {"symbol": symbol, "interval": interval, "bars": bars}

    def test_endpoint_returns_research_only_closed_ohlc(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = self._payload()
        with patch.object(app, "BIQUOTE_RESEARCH_ENABLED", True), patch(
            "app.requests.get", return_value=response
        ) as get:
            result = self.client.get(
                "/api/research/biquote/ohlc?symbol=XAU/USD&interval=5m"
            )
        self.assertEqual(result.status_code, 200)
        data = result.get_json()
        self.assertTrue(data["research_only"])
        self.assertFalse(data["signals_activated"])
        self.assertEqual(data["data_mode"], "provider_research")
        self.assertEqual(data["operational_data_mode"], "mock")
        self.assertEqual(data["closed_bars"], 60)
        self.assertTrue(all(not bar.get("isOpen", False) for bar in data["bars"]))
        self.assertIn("/api/XAUUSD/ohlc", get.call_args.args[0])
        self.assertEqual(get.call_args.kwargs["params"]["interval"], "5m")

    def test_endpoint_cache_prevents_repeated_provider_requests(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = self._payload()
        with patch.object(app, "BIQUOTE_RESEARCH_ENABLED", True), patch(
            "app.requests.get", return_value=response
        ) as get:
            first = self.client.get(
                "/api/research/biquote/ohlc?symbol=XAU/USD&interval=5m"
            )
            second = self.client.get(
                "/api/research/biquote/ohlc?symbol=XAU/USD&interval=5m"
            )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.get_json()["cache"]["hit"] is False)
        self.assertEqual(get.call_count, 1)

    def test_endpoint_rejects_unsupported_interval_without_network(self):
        with patch.object(app, "BIQUOTE_RESEARCH_ENABLED", True), patch(
            "app.requests.get"
        ) as get:
            result = self.client.get(
                "/api/research/biquote/ohlc?symbol=XAU/USD&interval=1m"
            )
        self.assertEqual(result.status_code, 400)
        get.assert_not_called()

    def _tick_payload(self, symbol="XAUUSD", market_state="open", stale=False):
        return {
            "symbol": symbol,
            "bid": 2650.10,
            "ask": 2650.35,
            "mid": 2650.225,
            "spread": 0.25,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "marketState": market_state,
            "stale": stale,
        }

    def test_tick_endpoint_returns_fresh_research_quote_only(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = self._tick_payload()
        with patch.object(app, "BIQUOTE_RESEARCH_ENABLED", True), patch(
            "app.requests.get", return_value=response
        ) as get:
            result = self.client.get("/api/research/biquote/tick?symbol=XAU/USD")
        self.assertEqual(result.status_code, 200)
        data = result.get_json()
        self.assertTrue(data["accepted"])
        self.assertTrue(data["research_only"])
        self.assertFalse(data["signals_activated"])
        self.assertFalse(data["execution_price"])
        self.assertEqual(data["symbol"], "XAU/USD")
        self.assertIn("/api/XAUUSD", get.call_args.args[0])
        self.assertEqual(get.call_args.kwargs["params"]["allowStale"], "false")

    def test_tick_endpoint_rejects_closed_or_stale_quote(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = self._tick_payload(market_state="closed", stale=True)
        with patch.object(app, "BIQUOTE_RESEARCH_ENABLED", True), patch(
            "app.requests.get", return_value=response
        ):
            result = self.client.get("/api/research/biquote/tick?symbol=XAU/USD")
        self.assertEqual(result.status_code, 503)
        self.assertFalse(result.get_json()["signals_activated"])

    def test_tick_endpoint_cache_avoids_repeated_provider_requests(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = self._tick_payload()
        with patch.object(app, "BIQUOTE_RESEARCH_ENABLED", True), patch(
            "app.requests.get", return_value=response
        ) as get:
            first = self.client.get("/api/research/biquote/tick?symbol=XAU/USD")
            second = self.client.get("/api/research/biquote/tick?symbol=XAU/USD")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.get_json()["cache"]["hit"])
        self.assertEqual(get.call_count, 1)

    def test_tick_endpoint_rejects_unsupported_symbol_without_network(self):
        with patch.object(app, "BIQUOTE_RESEARCH_ENABLED", True), patch(
            "app.requests.get"
        ) as get:
            result = self.client.get("/api/research/biquote/tick?symbol=US30")
        self.assertEqual(result.status_code, 400)
        get.assert_not_called()

    def _research_bars(self, count, step_minutes):
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        rows = []
        for i in range(count):
            stamp = now - timedelta(minutes=step_minutes * (count - i))
            price = 2650.0 + i * 0.05
            rows.append({
                "time": stamp.isoformat(),
                "open": price,
                "high": price + 0.2,
                "low": price - 0.2,
                "close": price + 0.05,
            })
        return rows

    def test_shadow_analysis_uses_direct_mtf_series_and_never_activates_signals(self):
        datasets = {
            "5m": {"bars": self._research_bars(120, 5), "closed_bars": 120, "last_closed_close_time": "2026-11-19T20:00:00+00:00"},
            "15m": {"bars": self._research_bars(60, 15), "closed_bars": 60, "last_closed_close_time": "2026-11-19T20:00:00+00:00"},
            "30m": {"bars": self._research_bars(60, 30), "closed_bars": 60, "last_closed_close_time": "2026-11-19T20:00:00+00:00"},
        }
        strategy = {
            "signal": "WAIT", "pre_signal": False, "primary_bias": "BULL",
            "mtf": {"timeframe_1": "M15", "bias_1": "BULL", "timeframe_2": "M30", "bias_2": "BULL"},
            "regime": "TREND", "structure": {"type": "NONE"}, "score": 5,
            "score_max": 7, "confluence_grade": "A", "setup_grade": "C",
            "quality_grade": "C", "reason": "research only",
        }
        with patch.object(app, "BIQUOTE_RESEARCH_ENABLED", True), patch(
            "app.cached_biquote_research_ohlc",
            side_effect=lambda symbol, interval: datasets[interval],
        ), patch("app.analyze_strategy", return_value=strategy) as analyze:
            response = self.client.get("/api/research/biquote/analysis?symbol=XAU/USD&interval=5min")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["research_only"])
        self.assertFalse(data["signals_activated"])
        self.assertFalse(data["execution_price"])
        self.assertEqual(data["confluence_grade"], "A")
        self.assertEqual(data["setup_grade"], "C")
        self.assertEqual(data["quality_grade"], "C")
        self.assertEqual(data["candles"], {"5m": 120, "15m": 60, "30m": 60})
        kwargs = analyze.call_args.kwargs
        self.assertEqual(len(kwargs["higher_timeframes"]["15min"]), 60)
        self.assertEqual(len(kwargs["higher_timeframes"]["30min"]), 60)

    def test_shadow_analysis_rejects_insufficient_higher_timeframe_history(self):
        datasets = {
            "5m": {"bars": self._research_bars(120, 5), "closed_bars": 120, "last_closed_close_time": "2026-11-19T20:00:00+00:00"},
            "15m": {"bars": self._research_bars(59, 15), "closed_bars": 59, "last_closed_close_time": "2026-11-19T20:00:00+00:00"},
            "30m": {"bars": self._research_bars(60, 30), "closed_bars": 60, "last_closed_close_time": "2026-11-19T20:00:00+00:00"},
        }
        with patch.object(app, "BIQUOTE_RESEARCH_ENABLED", True), patch(
            "app.cached_biquote_research_ohlc",
            side_effect=lambda symbol, interval: datasets[interval],
        ), patch("app.analyze_strategy") as analyze:
            response = self.client.get("/api/research/biquote/analysis?symbol=XAU/USD&interval=5min")
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.get_json()["signals_activated"])
        analyze.assert_not_called()

    def test_endpoint_can_be_disabled(self):
        with patch.object(app, "BIQUOTE_RESEARCH_ENABLED", False), patch(
            "app.requests.get"
        ) as get:
            result = self.client.get(
                "/api/research/biquote/ohlc?symbol=XAU/USD&interval=5m"
            )
        self.assertEqual(result.status_code, 503)
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
