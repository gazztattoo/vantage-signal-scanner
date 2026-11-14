import unittest
from datetime import datetime, timezone
from biquote_stream import _extract_tick, publish_tick, snapshot

class BiQuoteStreamTests(unittest.TestCase):
    def test_normalizes_tick(self):
        now=datetime.now(timezone.utc).isoformat()
        tick=_extract_tick([{"symbol":"XAUUSD","bid":2000.0,"ask":2000.5,"mid":2000.25,"timestamp":now,"marketState":"open"}])
        self.assertEqual(tick["symbol"],"XAU/USD")
        self.assertEqual(tick["mid"],2000.25)
        self.assertFalse(tick["is_vantage_quote"])
    def test_rejects_unknown_symbol(self):
        self.assertIsNone(_extract_tick({"symbol":"UNKNOWN","bid":1,"ask":2}))
    def test_rejects_invalid_spread(self):
        self.assertIsNone(_extract_tick({"symbol":"EURUSD","bid":2,"ask":1,"timestamp":datetime.now(timezone.utc).isoformat()}))
    def test_rejects_stale_tick(self):
        self.assertIsNone(_extract_tick({"symbol":"BTCUSD","bid":1,"ask":2,"timestamp":"2020-01-01T00:00:00+00:00"}))
    def test_publish_updates_snapshot(self):
        now=datetime.now(timezone.utc).isoformat()
        self.assertTrue(publish_tick({"symbol":"BTCUSD","bid":100000,"ask":100001,"timestamp":now}))
        self.assertIn("BTC/USD",snapshot()["ticks"])

if __name__=="__main__": unittest.main()
