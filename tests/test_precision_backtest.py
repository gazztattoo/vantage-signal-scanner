import unittest
from datetime import datetime, timezone, timedelta
from research.backtest_precision import ema, rsi, aggregate, metrics

class PrecisionBacktestTests(unittest.TestCase):
    def test_ema_seed_matches_production_simple_average(self):
        self.assertAlmostEqual(ema([1,2,3,4],3),3.0)
    def test_rsi_rising_series_is_100(self):
        self.assertEqual(rsi(list(range(20))),100.0)
    def test_aggregate_requires_complete_contiguous_minutes(self):
        rows=[]
        start=datetime(2026,1,1,tzinfo=timezone.utc)
        for i in range(5):
            x={"time":start+timedelta(minutes=i)}
            for side in ("bid","ask"):
                x.update({f"{side}_open":100+i, f"{side}_high":101+i,
                          f"{side}_low":99+i, f"{side}_close":100.5+i})
            rows.append(x)
        self.assertEqual(len(aggregate(rows,5)),1)
        self.assertEqual(len(aggregate(rows[:-1],5)),0)
    def test_metrics_exclude_open_trade_from_win_rate(self):
        report=metrics([{"outcome":"TP","r_multiple":2.0},
                        {"outcome":"SL","r_multiple":-1.0},
                        {"outcome":"OPEN_AT_END","r_multiple":0.2}])
        self.assertEqual(report["closed_trades"],2)
        self.assertEqual(report["win_rate"],0.5)
        self.assertEqual(report["open_at_end"],1)

if __name__=="__main__": unittest.main()
