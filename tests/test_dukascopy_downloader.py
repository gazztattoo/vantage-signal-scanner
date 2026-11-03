import datetime as dt
import lzma
import struct
import unittest

from research.download_dukascopy_m1 import decode_day, detect_scale

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


if __name__ == "__main__":
    unittest.main()
