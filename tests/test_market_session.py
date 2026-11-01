import unittest
from datetime import datetime, timezone

from app import market_session, vantage_server_time


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class VantageMarketSessionTests(unittest.TestCase):
    def test_eurusd_opens_after_sunday_server_rollover_in_winter(self):
        result = market_session("EUR/USD", utc("2026-11-01T22:02:00Z"))
        self.assertTrue(result["open"])
        self.assertEqual(result["server_utc_offset"], "UTC+2")

    def test_xauusd_remains_closed_during_first_hour_after_sunday_rollover(self):
        result = market_session("XAU/USD", utc("2026-11-01T22:02:00Z"))
        self.assertFalse(result["open"])

    def test_xauusd_opens_at_0101_server_time_in_winter(self):
        result = market_session("XAU/USD", utc("2026-11-01T23:02:00Z"))
        self.assertTrue(result["open"])

    def test_xauusd_daily_pause_is_closed(self):
        result = market_session("XAU/USD", utc("2026-11-02T22:30:00Z"))
        self.assertFalse(result["open"])

    def test_eurusd_daily_rollover_pause_is_closed(self):
        result = market_session("EUR/USD", utc("2026-11-02T21:58:00Z"))
        self.assertFalse(result["open"])

    def test_eurusd_is_open_before_daily_rollover_pause(self):
        result = market_session("EUR/USD", utc("2026-11-02T21:57:00Z"))
        self.assertTrue(result["open"])

    def test_friday_close_is_respected(self):
        before_close = market_session("EUR/USD", utc("2026-11-06T21:56:00Z"))
        at_close = market_session("EUR/USD", utc("2026-11-06T21:57:00Z"))
        self.assertTrue(before_close["open"])
        self.assertFalse(at_close["open"])

    def test_summer_time_uses_gmt_plus_three(self):
        server_now, offset = vantage_server_time(utc("2026-07-05T21:02:00Z"))
        self.assertEqual(offset, 3)
        self.assertEqual(server_now.weekday(), 0)
        self.assertTrue(market_session("EUR/USD", utc("2026-07-05T21:02:00Z"))["open"])
        self.assertFalse(market_session("XAU/USD", utc("2026-07-05T21:02:00Z"))["open"])

    def test_xauusd_opens_after_summer_daily_pause(self):
        self.assertTrue(market_session("XAU/USD", utc("2026-07-05T22:02:00Z"))["open"])

    def test_btcusd_remains_open_on_weekend_with_mt5_session_note(self):
        result = market_session("BTC/USD", utc("2026-11-01T12:00:00Z"))
        self.assertTrue(result["open"])
        self.assertIn("no separa una sesión de trading", result["reason"])


if __name__ == "__main__":
    unittest.main()
