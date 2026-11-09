import unittest
from datetime import datetime, timedelta, timezone

from market_data_router import (
    CandleCache,
    InMemoryQuotaStore,
    LiveDataDisabled,
    MarketDataRouter,
    NoUsableProvider,
    ProviderPolicy,
    SharedQuotaStoreRequired,
    normalize_closed_candles,
)


class FakeClock:
    def __init__(self, value):
        self.value = value
    def __call__(self):
        return self.value


def sample_rows(now, count=3):
    latest = now - timedelta(minutes=5)
    rows = []
    for index in range(count):
        stamp = latest - timedelta(minutes=5 * (count - 1 - index))
        rows.append({
            "time": stamp.isoformat(), "open": 100 + index,
            "high": 102 + index, "low": 99 + index,
            "close": 101 + index, "is_closed": True,
        })
    return rows


class RouterSafetyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
        self.clock = FakeClock(self.now)
        self.policy = ProviderPolicy(
            name="provider-a", symbols=("EUR/USD",),
            intervals=("5min",), priority=1, enabled=True,
            calls_per_day=100, reserve_calls=10,
        )

    def test_mock_mode_never_calls_adapter_or_quota_store(self):
        calls = []
        router = MarketDataRouter(
            mode="mock", live_enabled=True, policies=(self.policy,),
            adapters={"provider-a": lambda *args: calls.append(args)},
            quota_store=InMemoryQuotaStore(), now=self.clock,
        )
        with self.assertRaises(LiveDataDisabled):
            router.get_candles("EUR/USD", "5min")
        self.assertEqual(calls, [])

    def test_live_mode_fails_closed_without_shared_quota_store(self):
        router = MarketDataRouter(
            mode="live", live_enabled=True, policies=(self.policy,),
            adapters={"provider-a": lambda *args: sample_rows(self.now)},
            now=self.clock,
        )
        with self.assertRaises(SharedQuotaStoreRequired):
            router.get_candles("EUR/USD", "5min")

    def test_cache_hit_does_not_consume_another_provider_call(self):
        calls = []
        quota = InMemoryQuotaStore()
        router = MarketDataRouter(
            mode="live", live_enabled=True, policies=(self.policy,),
            adapters={"provider-a": lambda *args: (calls.append(args) or sample_rows(self.now))},
            quota_store=quota, now=self.clock,
            cache=CandleCache(monotonic=lambda: 10.0),
        )
        first = router.get_candles("EUR/USD", "5min")
        second = router.get_candles("EUR/USD", "5min")
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(len(calls), 1)

    def test_incomplete_and_invalid_bars_are_rejected(self):
        rows = sample_rows(self.now)
        rows[-1]["is_closed"] = False
        with self.assertRaises(ValueError):
            normalize_closed_candles(rows, "5min", self.now)

        rows = sample_rows(self.now)
        rows[-1]["high"] = rows[-1]["low"] - 1
        with self.assertRaises(ValueError):
            normalize_closed_candles(rows, "5min", self.now)

    def test_duplicate_timestamps_are_rejected(self):
        rows = sample_rows(self.now)
        rows[-1]["time"] = rows[-2]["time"]
        with self.assertRaises(ValueError):
            normalize_closed_candles(rows, "5min", self.now)

    def test_stale_series_never_returns_a_signal_eligible_result(self):
        rows = sample_rows(self.now)
        rows[-1]["time"] = (self.now - timedelta(hours=2)).isoformat()
        with self.assertRaises(ValueError):
            normalize_closed_candles(rows, "5min", self.now)

    def test_quota_exhaustion_does_not_call_provider(self):
        calls = []
        policy = ProviderPolicy(
            name="provider-a", symbols=("EUR/USD",),
            intervals=("5min",), priority=1, enabled=True,
            calls_per_day=1, reserve_calls=0,
        )
        quota = InMemoryQuotaStore()
        quota.reserve("provider-a", 1, 0, self.now)
        router = MarketDataRouter(
            mode="live", live_enabled=True, policies=(policy,),
            adapters={"provider-a": lambda *args: calls.append(args)},
            quota_store=quota, now=self.clock,
        )
        with self.assertRaises(NoUsableProvider):
            router.get_candles("EUR/USD", "5min")
        self.assertEqual(calls, [])

    def test_fallback_uses_one_complete_provider_series(self):
        calls = []
        bad_policy = self.policy
        good_policy = ProviderPolicy(
            name="provider-b", symbols=("EUR/USD",),
            intervals=("5min",), priority=2, enabled=True,
            calls_per_day=100, reserve_calls=10,
        )
        def bad_adapter(*args):
            calls.append("a")
            rows = sample_rows(self.now)
            rows[-1]["high"] = 1
            return rows
        def good_adapter(*args):
            calls.append("b")
            return sample_rows(self.now)

        router = MarketDataRouter(
            mode="live", live_enabled=True,
            policies=(bad_policy, good_policy),
            adapters={"provider-a": bad_adapter, "provider-b": good_adapter},
            quota_store=InMemoryQuotaStore(), now=self.clock,
        )
        result = router.get_candles("EUR/USD", "5min")
        self.assertEqual(result["provider"], "provider-b")
        self.assertEqual(calls, ["a", "b"])
        self.assertEqual(len(result["candles"]), 3)


if __name__ == "__main__":
    unittest.main()
