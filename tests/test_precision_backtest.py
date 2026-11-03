import unittest
import csv
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta
from research.backtest_precision import ema, rsi, aggregate, metrics, load_m1

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

    def _write_m1(self, rows):
        fields=["timestamp"]+[f"{s}_{k}" for s in ("bid","ask") for k in ("open","high","low","close")]
        tmp=tempfile.NamedTemporaryFile(mode="w",newline="",encoding="utf-8",delete=False)
        with tmp:
            writer=csv.DictWriter(tmp,fieldnames=fields);writer.writeheader();writer.writerows(rows)
        return Path(tmp.name)

    def test_loader_rejects_duplicate_timestamps(self):
        row={"timestamp":"2026-01-01T00:00:00Z"}
        for side in ("bid","ask"):
            row.update({f"{side}_open":100,f"{side}_high":101,f"{side}_low":99,f"{side}_close":100})
        path=self._write_m1([row,row.copy()])
        try:
            with self.assertRaisesRegex(ValueError,"strictly increasing"):load_m1(path)
        finally:path.unlink(missing_ok=True)

    def test_loader_rejects_ask_below_bid(self):
        row={"timestamp":"2026-01-01T00:00:00Z"}
        row.update({"bid_open":100,"bid_high":101,"bid_low":99,"bid_close":100,
                    "ask_open":99.9,"ask_high":101,"ask_low":99,"ask_close":100})
        path=self._write_m1([row])
        try:
            with self.assertRaisesRegex(ValueError,"ASK below BID"):load_m1(path)
        finally:path.unlink(missing_ok=True)

if __name__=="__main__": unittest.main()
