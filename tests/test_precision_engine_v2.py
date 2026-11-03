import unittest
from precision_engine_v2 import evaluate_multitimeframe_setup


def frame(tf, *, side="BUY", age=30, adx=25, rsi=60, macd=0.2,
          close=101, ema20=100, ema50=99, atr=1, **extra):
    return {"timeframe_seconds": tf, "candle_age_seconds": age,
            "adx": adx, "rsi": rsi, "macd_hist": macd,
            "close": close, "ema20": ema20, "ema50": ema50, "atr": atr,
            **extra}


class PrecisionEngineV2Tests(unittest.TestCase):
    def setUp(self):
        self.m30 = frame(1800)
        self.m15 = frame(900, adx=22)
        self.m5 = frame(300, adx=18, close=102, ema20=100,
                        ema50=99, previous_high=101, swing_low=100.8)

    def test_aligned_confirmed_buy_is_candidate(self):
        result = evaluate_multitimeframe_setup(
            symbol="XAU/USD", m30=self.m30, m15=self.m15, m5=self.m5)
        self.assertEqual(result["signal"], "BUY")
        self.assertEqual(result["quality"], "CANDIDATA")
        self.assertGreaterEqual(result["risk_reward"], 2)

    def test_m30_m15_conflict_waits(self):
        m15 = frame(900, ema20=98, ema50=99, rsi=40, macd=-0.2)
        result = evaluate_multitimeframe_setup(
            symbol="EUR/USD", m30=self.m30, m15=m15, m5=self.m5)
        self.assertEqual(result["signal"], "ESPERAR")

    def test_stale_frame_blocks_signal(self):
        m5 = {**self.m5, "candle_age_seconds": 500}
        result = evaluate_multitimeframe_setup(
            symbol="BTC/USD", m30=self.m30, m15=self.m15, m5=m5)
        self.assertEqual(result["signal"], "ESPERAR")
        self.assertIn("antigua", result["reason"])

    def test_no_closed_m5_breakout_waits(self):
        m5 = {**self.m5, "close": 100.5, "previous_high": 101}
        result = evaluate_multitimeframe_setup(
            symbol="XAU/USD", m30=self.m30, m15=self.m15, m5=m5)
        self.assertEqual(result["signal"], "ESPERAR")

    def test_missing_snapshot_waits(self):
        result = evaluate_multitimeframe_setup(
            symbol="XAU/USD", m30=self.m30, m15=self.m15, m5=None)
        self.assertEqual(result["signal"], "ESPERAR")

    def test_sell_alignment_and_breakout(self):
        m30 = frame(1800, ema20=98, ema50=99, rsi=40, macd=-0.2)
        m15 = frame(900, ema20=98, ema50=99, rsi=40, macd=-0.2, adx=22)
        m5 = frame(300, ema20=99, ema50=100, rsi=40, macd=-0.2,
                   close=97, previous_low=98, swing_high=97.2, adx=18)
        result = evaluate_multitimeframe_setup(
            symbol="EUR/USD", m30=m30, m15=m15, m5=m5)
        self.assertEqual(result["signal"], "SELL")
        self.assertGreaterEqual(result["risk_reward"], 2)


if __name__ == "__main__":
    unittest.main()
