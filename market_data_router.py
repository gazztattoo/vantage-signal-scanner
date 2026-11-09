"""Offline-first market data routing primitives.

This module deliberately contains no HTTP client and no vendor-specific adapter.
Live adapters must be injected explicitly, and live routing fails closed unless
it is explicitly enabled and backed by a shared atomic quota store.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import threading
import time
from typing import Callable, Mapping, Protocol, Sequence


INTERVAL_MINUTES = {"5min": 5, "15min": 15, "30min": 30}


class RouterError(RuntimeError):
    """Base exception for market-data routing errors."""


class LiveDataDisabled(RouterError):
    """Raised when a live provider is accessed outside an approved live mode."""


class SharedQuotaStoreRequired(RouterError):
    """Raised when live routing has no shared atomic quota store."""


class NoUsableProvider(RouterError):
    """Raised when no eligible provider returns a valid, fresh series."""


@dataclass(frozen=True)
class ProviderPolicy:
    name: str
    symbols: tuple[str, ...]
    intervals: tuple[str, ...]
    priority: int
    enabled: bool = False
    calls_per_day: int = 0
    reserve_calls: int = 0
    max_age_intervals: float = 2.0
    max_age_grace_seconds: int = 90


class QuotaStore(Protocol):
    """Production implementations must reserve atomically across all workers."""

    def reserve(
        self, provider: str, calls_per_day: int, reserve_calls: int,
        now: datetime,
    ) -> bool:
        ...


class CandleAdapter(Protocol):
    """Adapters return canonical rows; vendor-specific parsing stays in adapters."""

    def __call__(
        self, symbol: str, interval: str, count: int,
    ) -> Sequence[Mapping[str, object]]:
        ...


class CandleCache:
    """Small process cache. It is an optimization, never the quota authority."""

    def __init__(self, monotonic: Callable[[], float] = time.monotonic):
        self._monotonic = monotonic
        self._entries: dict[tuple, tuple[float, list[dict]]] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple) -> list[dict] | None:
        now = self._monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, candles = entry
            if expires_at <= now:
                self._entries.pop(key, None)
                return None
            return deepcopy(candles)

    def set(self, key: tuple, candles: Sequence[Mapping[str, object]], ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            raise ValueError("El TTL de caché debe ser mayor que cero.")
        with self._lock:
            self._entries[key] = (
                self._monotonic() + ttl_seconds,
                deepcopy([dict(row) for row in candles]),
            )


class InMemoryQuotaStore:
    """Test-only quota store. Never use this as a production global hard cap."""

    def __init__(self):
        self._counts: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def reserve(
        self, provider: str, calls_per_day: int, reserve_calls: int,
        now: datetime,
    ) -> bool:
        if calls_per_day <= 0 or reserve_calls < 0 or reserve_calls >= calls_per_day:
            return False
        day = now.astimezone(timezone.utc).date().isoformat()
        key = (provider, day)
        with self._lock:
            used = self._counts.get(key, 0)
            if used >= calls_per_day - reserve_calls:
                return False
            self._counts[key] = used + 1
            return True


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("La vela no contiene una marca de tiempo.")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("La marca de tiempo debe incluir zona horaria.")
    return parsed.astimezone(timezone.utc)


def normalize_closed_candles(
    rows: Sequence[Mapping[str, object]],
    interval: str,
    now: datetime,
    *,
    max_age_intervals: float = 2.0,
    max_age_grace_seconds: int = 90,
) -> list[dict]:
    """Validate a single provider series and retain only explicitly closed bars."""
    if interval not in INTERVAL_MINUTES:
        raise ValueError("Temporalidad no permitida.")
    if now.tzinfo is None:
        raise ValueError("El reloj de validación debe incluir zona horaria.")

    closed_rows = [row for row in rows if row.get("is_closed") is True]
    if not closed_rows:
        raise ValueError("El proveedor no entregó velas cerradas.")

    normalized: list[dict] = []
    seen: set[datetime] = set()
    previous_time: datetime | None = None

    for row in closed_rows:
        candle_time = _parse_utc(row.get("time"))
        if candle_time in seen:
            raise ValueError("El proveedor devolvió timestamps duplicados.")
        if previous_time is not None and candle_time <= previous_time:
            raise ValueError("Las velas no están en orden cronológico.")
        seen.add(candle_time)
        previous_time = candle_time

        try:
            opening = float(row["open"])
            high = float(row["high"])
            low = float(row["low"])
            close = float(row["close"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("La vela contiene precios no numéricos o incompletos.") from exc

        values = (opening, high, low, close)
        if any(value != value or value in (float("inf"), float("-inf")) for value in values):
            raise ValueError("La vela contiene valores no finitos.")
        if low <= 0 or high < max(opening, close) or low > min(opening, close) or high < low:
            raise ValueError("La vela incumple las reglas OHLC.")

        normalized.append({
            "time": candle_time.isoformat(),
            "open": opening,
            "high": high,
            "low": low,
            "close": close,
            "is_closed": True,
        })
        if row.get("tick_volume") is not None:
            try:
                normalized[-1]["tick_volume"] = float(row["tick_volume"])
            except (TypeError, ValueError) as exc:
                raise ValueError("El tick volume no es numérico.") from exc

    latest_time = datetime.fromisoformat(normalized[-1]["time"])
    age_seconds = (now.astimezone(timezone.utc) - latest_time).total_seconds()
    maximum_age = (
        INTERVAL_MINUTES[interval] * 60 * max_age_intervals
        + max_age_grace_seconds
    )
    if age_seconds < -5:
        raise ValueError("La última vela está fechada en el futuro.")
    if age_seconds > maximum_age:
        raise ValueError("Los datos están desactualizados; no se puede generar señal.")

    return normalized


class MarketDataRouter:
    """Selects one complete series from one provider; never stitches providers."""

    def __init__(
        self,
        *,
        mode: str,
        live_enabled: bool = False,
        policies: Sequence[ProviderPolicy] = (),
        adapters: Mapping[str, CandleAdapter] | None = None,
        quota_store: QuotaStore | None = None,
        cache: CandleCache | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.mode = mode
        self.live_enabled = live_enabled
        self.policies = tuple(sorted(policies, key=lambda item: item.priority))
        self.adapters = dict(adapters or {})
        self.quota_store = quota_store
        self.cache = cache or CandleCache()
        self.now = now

    def get_candles(
        self, symbol: str, interval: str, count: int = 1200,
        cache_ttl_seconds: float = 30,
    ) -> dict:
        # The mock/test mode is a hard stop: not even a cached live feed is returned.
        if self.mode != "live" or not self.live_enabled:
            raise LiveDataDisabled("El acceso a proveedores en vivo está deshabilitado.")
        if self.quota_store is None:
            raise SharedQuotaStoreRequired(
                "La ruta en vivo exige un contador de cuota compartido y atómico."
            )
        if interval not in INTERVAL_MINUTES or count < 1:
            raise ValueError("Símbolo, temporalidad o cantidad no válidos.")

        errors: list[str] = []
        for policy in self.policies:
            if not policy.enabled:
                continue
            if symbol not in policy.symbols or interval not in policy.intervals:
                continue
            adapter = self.adapters.get(policy.name)
            if adapter is None:
                continue

            cache_key = (policy.name, symbol, interval, count)
            cached = self.cache.get(cache_key)
            if cached is not None:
                try:
                    # Recheck freshness when serving cache; a short TTL must not
                    # allow an otherwise stale series to become signal-eligible.
                    cached = normalize_closed_candles(
                        cached, interval, self.now(),
                        max_age_intervals=policy.max_age_intervals,
                        max_age_grace_seconds=policy.max_age_grace_seconds,
                    )
                except ValueError:
                    cached = None
                if cached is not None:
                    return {
                        "symbol": symbol, "interval": interval,
                        "provider": policy.name, "cached": True,
                        "candles": cached,
                    }

            if not self.quota_store.reserve(
                policy.name, policy.calls_per_day, policy.reserve_calls, self.now()
            ):
                errors.append(policy.name + ": cuota agotada o reservada.")
                continue

            try:
                raw = adapter(symbol, interval, count)
                candles = normalize_closed_candles(
                    raw, interval, self.now(),
                    max_age_intervals=policy.max_age_intervals,
                    max_age_grace_seconds=policy.max_age_grace_seconds,
                )
                candles = candles[-count:]
                self.cache.set(cache_key, candles, cache_ttl_seconds)
                return {
                    "symbol": symbol, "interval": interval,
                    "provider": policy.name, "cached": False,
                    "candles": candles,
                }
            except Exception as exc:
                errors.append(policy.name + ": " + str(exc))

        detail = "; ".join(errors) if errors else "No hay proveedores habilitados para esta combinación."
        raise NoUsableProvider(detail)
