import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from tools.backtest_mtf_strategy import run_backtest

def bars(count=20, opens=None, highs=None, lows=None, closes=None):
    start=datetime(2026,10,1,tzinfo=timezone.utc); out=[]
    for i in range(count):
        out.append({"time":(start+timedelta(minutes=5*i)).isoformat(),
                    "open":opens[i] if opens else 100.0,
                    "high":highs[i] if highs else 101.0,
                    "low":lows[i] if lows else 99.0,
                    "close":closes[i] if closes else 100.0})
    return out

class MtfBacktestTests(unittest.TestCase):
    def setUp(self):
        p=patch("tools.backtest_mtf_strategy.LOOKBACK",{"5min":2})
        p.start(); self.addCleanup(p.stop)

    @staticmethod
    def signal():
        return {"signal":"BUY","pre_signal":False,"price":100.0,"stop_loss":97.0,
                "atr14":2.0,"score":6,"quality_grade":"A","regime":"TREND",
                "structure_trigger":"BREAKOUT","mtf":{"aligned_buy":True}}

    def _once(self):
        return patch("tools.backtest_mtf_strategy.analyze",
                     side_effect=[self.signal()]+[{"signal":"WAIT","pre_signal":False}]*100)

    def test_entry_next_open_and_rebased_2r_target(self):
        hi=[101.0]*20; hi[3]=112.1
        data=bars(opens=[100,100,102]+[102]*17,highs=hi)
        with self._once(): result=run_backtest(data,"5min",expiry=5)
        trade=result["trades"][0]
        self.assertEqual(trade["entry"],102.0)
        self.assertEqual(trade["take_profit"],112.0)
        self.assertEqual(trade["result_r"],2.0)

    def test_sl_wins_if_both_levels_touch_same_candle(self):
        hi=[101.0]*20; lo=[99.0]*20; hi[3],lo[3]=113.0,96.0
        with self._once(): result=run_backtest(bars(highs=hi,lows=lo),"5min",expiry=5)
        self.assertEqual(result["trades"][0]["outcome"],"SL")
        self.assertEqual(result["trades"][0]["result_r"],-1.0)

    def test_expiry_marks_to_market(self):
        close=[100.0]*20; close[6]=101.0
        with self._once(): result=run_backtest(bars(closes=close),"5min",expiry=5)
        self.assertEqual(result["trades"][0]["outcome"],"EXPIRED")
        self.assertAlmostEqual(result["trades"][0]["result_r"],1/3,places=6)

    def test_incomplete_horizon_is_not_opened(self):
        with self._once(): result=run_backtest(bars(count=6),"5min",expiry=10)
        self.assertEqual(result["summary"]["trades"],0)
        self.assertEqual(result["summary"]["incomplete_horizon"],1)

    def test_cost_is_deducted_per_trade(self):
        hi=[101.0]*20; hi[3]=112.1
        with self._once(): result=run_backtest(bars(highs=hi),"5min",expiry=5,costs=(0,.1))
        self.assertEqual(result["summary"]["cost_scenarios"]["0.1"]["net_after_cost_r"],1.9)

if __name__=="__main__": unittest.main()
