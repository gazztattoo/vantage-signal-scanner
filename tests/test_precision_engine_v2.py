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


class M15M5PrecisionGateTests(unittest.TestCase):
    def setUp(self):
        self.m15 = frame(900, adx=22, close=102, ema20=101, ema50=100,
                         rsi=60, macd=0.3)
        self.m5 = frame(300, adx=18, close=103, ema20=102, ema50=101,
                        rsi=62, macd=0.2, previous_high=102.5, swing_low=102)

    def test_buy_candidate_does_not_require_m30(self):
        from precision_engine_v2 import evaluate_m15_m5_setup
        result = evaluate_m15_m5_setup(symbol="XAU/USD", m15=self.m15, m5=self.m5)
        self.assertEqual(result["signal"], "BUY")
        self.assertEqual(result["quality"], "CANDIDATA")
        self.assertIsNone(result["m30_context"])
        self.assertGreaterEqual(result["risk_reward"], 2)

    def test_missing_m5_breakout_level_waits(self):
        from precision_engine_v2 import evaluate_m15_m5_setup
        m5 = {k: v for k, v in self.m5.items() if k != "previous_high"}
        result = evaluate_m15_m5_setup(symbol="EUR/USD", m15=self.m15, m5=m5)
        self.assertEqual(result["signal"], "ESPERAR")

    def test_stale_m15_blocks_candidate(self):
        from precision_engine_v2 import evaluate_m15_m5_setup
        m15 = {**self.m15, "candle_age_seconds": 1200}
        result = evaluate_m15_m5_setup(symbol="BTC/USD", m15=m15, m5=self.m5)
        self.assertEqual(result["signal"], "ESPERAR")
        self.assertIn("antigua", result["reason"])

    def test_m30_is_context_not_required_or_veto(self):
        from precision_engine_v2 import evaluate_m15_m5_setup
        m30_against = frame(1800, ema20=98, ema50=99)
        result = evaluate_m15_m5_setup(
            symbol="EUR/USD", m15=self.m15, m5=self.m5, m30=m30_against)
        self.assertEqual(result["signal"], "BUY")
        self.assertEqual(result["m30_context"], "SELL")
        self.assertIn("M30 está en contra", result["reason"])

    def test_sell_candidate(self):
        from precision_engine_v2 import evaluate_m15_m5_setup
        m15 = frame(900, adx=22, close=98, ema20=99, ema50=100,
                    rsi=40, macd=-0.3)
        m5 = frame(300, adx=18, close=97, ema20=98, ema50=99,
                   rsi=40, macd=-0.2, previous_low=97.5, swing_high=98)
        result = evaluate_m15_m5_setup(symbol="EUR/USD", m15=m15, m5=m5)
        self.assertEqual(result["signal"], "SELL")
        self.assertGreaterEqual(result["risk_reward"], 2)


class CachedPrecisionFrameTests(unittest.TestCase):
    def test_frame_builder_uses_only_closed_candle_payload(self):
        from app import _precision_frame_from_payload
        payload = {
            "ema20": 100, "ema50": 99, "adx14": 22, "rsi14": 60,
            "macd_histogram": 0.3, "atr14": 1, "candle_age_minutes": 1,
            "recent_candles": [
                {"high": 100.5, "low": 99.5, "close": 100},
                {"high": 101, "low": 99.8, "close": 100.5},
                {"high": 101.5, "low": 100, "close": 101},
                {"high": 102, "low": 100.5, "close": 101.5},
            ],
        }
        frame = _precision_frame_from_payload(payload, "5min")
        self.assertEqual(frame["close"], 101.5)
        self.assertEqual(frame["previous_high"], 101.5)
        self.assertEqual(frame["previous_low"], 99.5)
        self.assertEqual(frame["timeframe_seconds"], 300)

    def test_frame_builder_fails_closed_when_data_missing(self):
        from app import _precision_frame_from_payload
        self.assertIsNone(_precision_frame_from_payload({}, "5min"))


if __name__ == "__main__":
    unittest.main()
