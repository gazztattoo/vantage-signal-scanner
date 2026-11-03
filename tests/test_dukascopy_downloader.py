import datetime as dt
import lzma
import struct
import unittest
from unittest.mock import patch

import requests
from research.download_dukascopy_m1 import decode_day, detect_scale, fetch_day

RECORD = struct.Struct(">IIIIIf")


class DukascopyDecoderTests(unittest.TestCase):
    def payload(self, open_, close, low, high):
        return lzma.compress(RECORD.pack(0, open_, close, low, high, 1.25), format=lzma.FORMAT_ALONE)

    def test_gold_scale_is_detected_and_ohlc_decoded(self):
        payload = self.payload(4_000_000, 4_001_000, 3_999_000, 4_002_000)
        scale = detect_scale(payload, "XAU/USD")
        self.assertEqual(scale, 1000)
        rows = decode_day(payload, dt.date(2026, 10, 1), scale)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["open"], 4000.0)
        self.assertEqual(rows[0]["close"], 4001.0)

    def test_eurusd_scale_is_detected(self):
        payload = self.payload(110_000, 110_010, 109_990, 110_020)
        self.assertEqual(detect_scale(payload, "EUR/USD"), 100_000)

    def test_rejects_corrupt_record_length(self):
        with self.assertRaises(ValueError):
            decode_day(b"not-a-valid-record", dt.date(2026, 10, 1), 1000)

    def test_rejects_implausible_scale_instead_of_guessing(self):
        payload = self.payload(1, 2, 1, 2)
        with self.assertRaises(ValueError):
            detect_scale(payload, "BTC/USD")

    def test_retries_temporary_503_then_succeeds(self):
        class Response:
            def __init__(self, status, content=b"payload"):
                self.status_code = status
                self.content = content
                self.headers = {}

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise requests.HTTPError(f"HTTP {self.status_code}")

        class Session:
            def __init__(self):
                self.responses = [Response(503), Response(200)]

            def get(self, url, timeout):
                return self.responses.pop(0)

        with patch("research.download_dukascopy_m1.time.sleep") as sleep:
            payload = fetch_day(Session(), "EURUSD", dt.date(2026, 10, 1), "BID")
        self.assertEqual(payload, b"payload")
        sleep.assert_called_once()

    def test_404_is_treated_as_missing_data_without_retry(self):
        class Response:
            status_code = 404
            content = b""
            headers = {}

            def raise_for_status(self):
                raise requests.HTTPError("404")

        class Session:
            def get(self, url, timeout):
                return Response()

        with patch("research.download_dukascopy_m1.time.sleep") as sleep:
            self.assertIsNone(fetch_day(Session(), "EURUSD", dt.date(2026, 10, 1), "ASK"))
        sleep.assert_not_called()


    def test_persistent_503_raises_after_bounded_retries(self):
        class Response:
            status_code = 503
            content = b""
            headers = {}
            def raise_for_status(self):
                raise requests.HTTPError("HTTP 503")
        class Session:
            calls = 0
            def get(self, url, timeout):
                self.calls += 1
                return Response()
        session=Session()
        with patch("research.download_dukascopy_m1.time.sleep"):
            with self.assertRaises(requests.HTTPError):
                fetch_day(session,"EURUSD",dt.date(2026,10,1),"ASK")
        self.assertEqual(session.calls,5)
if __name__ == "__main__":
    unittest.main()
