import unittest
from datetime import datetime, timedelta, timezone

from mt5_ingest import normalize_mt5_batch


class Mt5IngestValidationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)

    def make_payload(self, count=2, timeframe="5min", offset=120):
        seconds = {"5min": 300, "15min": 900, "30min": 1800}[timeframe]
        latest_utc = self.now - timedelta(seconds=seconds)
        rows = []
        for index in range(count):
            utc = latest_utc - timedelta(seconds=seconds * (count - 1 - index))
            server = utc + timedelta(minutes=offset)
            rows.append({
                "time": utc.isoformat().replace("+00:00", "Z"),
                "server_time": server.replace(tzinfo=None).isoformat(timespec="seconds"),
                "server_utc_offset_minutes": offset,
                "open": 100.0 + index,
                "high": 102.0 + index,
                "low": 99.0 + index,
                "close": 101.0 + index,
                "tick_volume": 15,
                "spread_points": 12,
                "is_closed": True,
            })
        return {
            "symbol": "EUR/USD", "broker_symbol": "EURUSD.a",
            "timeframe": timeframe, "candles": rows,
        }

    def test_valid_vantage_batch_converts_to_utc_and_normalizes(self):
        payload = self.make_payload()
        rows = normalize_mt5_batch(payload, now=self.now)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["symbol"], "EUR/USD")
        self.assertEqual(rows[0]["source"], "VANTAGE_MT5")
        self.assertEqual(rows[0]["server_utc_offset_minutes"], 120)
        self.assertEqual(rows[0]["tick_volume"], 15)

    def test_accepts_summer_gmt_plus_three(self):
        payload = self.make_payload(offset=180)
        rows = normalize_mt5_batch(payload, now=self.now)
        self.assertEqual(rows[0]["server_utc_offset_minutes"], 180)

    def test_rejects_bad_server_to_utc_conversion(self):
        payload = self.make_payload()
        payload["candles"][0]["time"] = (
            datetime.fromisoformat(payload["candles"][0]["time"].replace("Z", "+00:00"))
            + timedelta(hours=1)
        ).isoformat()
        with self.assertRaisesRegex(ValueError, "conversión"):
            normalize_mt5_batch(payload, now=self.now)

    def test_rejects_open_candle(self):
        payload = self.make_payload()
        payload["candles"][0]["is_closed"] = False
        with self.assertRaisesRegex(ValueError, "abierta"):
            normalize_mt5_batch(payload, now=self.now)

    def test_rejects_unapproved_symbol_and_timeframe(self):
        payload = self.make_payload()
        payload["symbol"] = "USD/JPY"
        with self.assertRaisesRegex(ValueError, "Símbolo"):
            normalize_mt5_batch(payload, now=self.now)
        payload = self.make_payload()
        payload["timeframe"] = "1min"
        with self.assertRaisesRegex(ValueError, "Temporalidad"):
            normalize_mt5_batch(payload, now=self.now)

    def test_rejects_duplicate_and_unsorted_timestamps(self):
        payload = self.make_payload()
        payload["candles"][1]["time"] = payload["candles"][0]["time"]
        payload["candles"][1]["server_time"] = payload["candles"][0]["server_time"]
        with self.assertRaisesRegex(ValueError, "duplicados"):
            normalize_mt5_batch(payload, now=self.now)

        payload = self.make_payload()
        payload["candles"].reverse()
        with self.assertRaisesRegex(ValueError, "cronológico"):
            normalize_mt5_batch(payload, now=self.now)

    def test_rejects_invalid_ohlc_and_negative_spread(self):
        payload = self.make_payload()
        payload["candles"][0]["high"] = payload["candles"][0]["low"] - 1
        with self.assertRaisesRegex(ValueError, "OHLC"):
            normalize_mt5_batch(payload, now=self.now)

        payload = self.make_payload()
        payload["candles"][0]["spread_points"] = -1
        with self.assertRaisesRegex(ValueError, "negativos"):
            normalize_mt5_batch(payload, now=self.now)

    def test_rejects_future_and_too_old_data(self):
        payload = self.make_payload()
        payload["candles"][-1]["time"] = (self.now + timedelta(hours=1)).isoformat()
        payload["candles"][-1]["server_time"] = (
            self.now + timedelta(hours=1, minutes=120)
        ).replace(tzinfo=None).isoformat(timespec="seconds")
        with self.assertRaisesRegex(ValueError, "futuro"):
            normalize_mt5_batch(payload, now=self.now)

        payload = self.make_payload(count=1)
        old = self.now - timedelta(days=46)
        payload["candles"][0]["time"] = old.isoformat()
        payload["candles"][0]["server_time"] = (
            old + timedelta(minutes=120)
        ).replace(tzinfo=None).isoformat(timespec="seconds")
        with self.assertRaisesRegex(ValueError, "45 días"):
            normalize_mt5_batch(payload, now=self.now)

    def test_rejects_too_large_batch(self):
        payload = self.make_payload(count=151)
        with self.assertRaisesRegex(ValueError, "máximo"):
            normalize_mt5_batch(payload, now=self.now)

    def test_rejects_timezone_naive_utc(self):
        payload = self.make_payload()
        payload["candles"][0]["time"] = payload["candles"][0]["time"].replace("Z", "")
        with self.assertRaisesRegex(ValueError, "zona horaria"):
            normalize_mt5_batch(payload, now=self.now)


if __name__ == "__main__":
    unittest.main()
