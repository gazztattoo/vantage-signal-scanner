import unittest
from datetime import datetime, timezone
from persistent_candles import MinuteCandleBuilder, aggregate_minutes


class PersistentCandleTests(unittest.TestCase):
    def test_tick_builds_and_closes_minute(self):
        builder = MinuteCandleBuilder()
        self.assertEqual(builder.add_tick({"symbol":"XAU/USD","mid":2000,"timestamp":"2026-11-14T10:00:01Z"}), [])
        closed = builder.add_tick({"symbol":"XAU/USD","mid":2002,"timestamp":"2026-11-14T10:01:00Z"})
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["open"], 2000)
        self.assertEqual(closed[0]["high"], 2000)
        self.assertEqual(closed[0]["close"], 2000)

    def test_ignores_out_of_order_tick(self):
        builder = MinuteCandleBuilder()
        builder.add_tick({"symbol":"BTC/USD","mid":100,"timestamp":"2026-11-14T10:02:10Z"})
        self.assertEqual(builder.add_tick({"symbol":"BTC/USD","mid":90,"timestamp":"2026-11-14T10:01:50Z"}), [])
        closed = builder.add_tick({"symbol":"BTC/USD","mid":101,"timestamp":"2026-11-14T10:03:00Z"})
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["minute_open"], "2026-11-14T10:02:00Z")
        self.assertEqual(closed[0]["close"], 100)

    def test_rejects_stale_and_unknown(self):
        builder = MinuteCandleBuilder()
        self.assertEqual(builder.add_tick({"symbol":"UNKNOWN","mid":10,"timestamp":"2026-11-14T10:00:01Z"}), [])
        self.assertEqual(builder.add_tick({"symbol":"BTC/USD","mid":10,"stale":True,"timestamp":"2026-11-14T10:00:01Z"}), [])

    def test_aggregates_only_complete_five_minute_bar(self):
        rows = []
        for minute in range(5):
            rows.append({"minute_open":f"2026-11-14T10:0{minute}:00Z","open":100+minute,"high":105+minute,"low":99+minute,"close":104+minute})
        result = aggregate_minutes(rows, 5)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["open"], 100)
        self.assertEqual(result[0]["high"], 109)
        self.assertEqual(result[0]["low"], 99)
        self.assertEqual(result[0]["close"], 108)

    def test_drops_incomplete_interval(self):
        rows = [{"minute_open":f"2026-11-14T10:0{minute}:00Z","open":100,"high":101,"low":99,"close":100} for minute in (0,1,2,4)]
        self.assertEqual(aggregate_minutes(rows, 5), [])


if __name__ == "__main__":
    unittest.main()
