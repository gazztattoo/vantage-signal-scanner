"""Validate single-broker MT5 candle batches before server-side persistence.

No trading, order placement, external requests, or database access lives here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import re
from typing import Any, Mapping

ALLOWED_SYMBOLS = {"EUR/USD", "XAU/USD", "BTC/USD"}
TIMEFRAME_SECONDS = {"5min": 300, "15min": 900, "30min": 1800}
BROKER_SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
MAX_BATCH_SIZE = 150
MAX_HISTORY_DAYS = 45


def _parse_aware_utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Falta la fecha UTC de la vela.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("La fecha UTC de la vela no es válida.") from exc
    if parsed.tzinfo is None:
        raise ValueError("La fecha de la vela debe incluir zona horaria.")
    return parsed.astimezone(timezone.utc)


def _parse_server_wall_time(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Falta la hora original del servidor Vantage.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("La hora del servidor no es válida.") from exc
    if parsed.tzinfo is not None:
        raise ValueError("La hora original del servidor debe ser local, sin zona adjunta.")
    return parsed


def _finite_price(row: Mapping[str, Any], name: str) -> float:
    try:
        value = float(row[name])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Precio {name} incompleto o no numérico.") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"Precio {name} fuera de rango.")
    return value


def normalize_mt5_batch(
    payload: Any,
    *,
    now: datetime | None = None,
    max_batch_size: int = MAX_BATCH_SIZE,
    max_history_days: int = MAX_HISTORY_DAYS,
) -> list[dict]:
    """Return PostgREST rows after validating identity, timestamps and OHLC."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("El reloj del servidor debe incluir zona horaria.")
    now = now.astimezone(timezone.utc)

    if not isinstance(payload, dict):
        raise ValueError("El cuerpo debe ser un objeto JSON.")
    symbol = str(payload.get("symbol", "")).strip().upper()
    timeframe = str(payload.get("timeframe", "")).strip()
    broker_symbol = str(payload.get("broker_symbol", "")).strip()
    candles = payload.get("candles")

    if symbol not in ALLOWED_SYMBOLS:
        raise ValueError("Símbolo no permitido para el escáner.")
    if timeframe not in TIMEFRAME_SECONDS:
        raise ValueError("Temporalidad no permitida para el escáner.")
    if not BROKER_SYMBOL_PATTERN.fullmatch(broker_symbol):
        raise ValueError("Nombre de símbolo del bróker no válido.")
    if not isinstance(candles, list) or not candles:
        raise ValueError("El lote no contiene velas.")
    if len(candles) > max_batch_size:
        raise ValueError(f"El lote supera el máximo de {max_batch_size} velas.")

    step_seconds = TIMEFRAME_SECONDS[timeframe]
    output: list[dict] = []
    seen: set[datetime] = set()
    previous: datetime | None = None

    for index, candle in enumerate(candles):
        if not isinstance(candle, dict):
            raise ValueError(f"La vela {index} no es un objeto.")
        if candle.get("is_closed") is not True:
            raise ValueError("El lote contiene una vela todavía abierta.")

        server_time = _parse_server_wall_time(candle.get("server_time"))
        try:
            offset_minutes = int(candle.get("server_utc_offset_minutes"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Falta el desfase horario del servidor.") from exc
        if offset_minutes not in (120, 180):
            raise ValueError("Vantage debe enviar desfase GMT+2 o GMT+3.")

        supplied_utc = _parse_aware_utc(candle.get("time"))
        expected_utc = server_time.replace(
            tzinfo=timezone(timedelta(minutes=offset_minutes))
        ).astimezone(timezone.utc)
        if abs((supplied_utc - expected_utc).total_seconds()) > 1:
            raise ValueError("La conversión de hora del servidor a UTC no coincide.")

        timestamp = int(supplied_utc.timestamp())
        if timestamp % step_seconds != 0:
            raise ValueError("La hora de apertura no está alineada con la temporalidad.")
        if supplied_utc in seen:
            raise ValueError("El lote contiene timestamps duplicados.")
        if previous is not None and supplied_utc <= previous:
            raise ValueError("Las velas deben venir en orden cronológico.")
        seen.add(supplied_utc)
        previous = supplied_utc

        age_seconds = (now - supplied_utc).total_seconds()
        if age_seconds < -120:
            raise ValueError("La vela está fechada en el futuro.")
        if age_seconds > max_history_days * 86400:
            raise ValueError("El histórico supera la ventana permitida de 45 días.")

        opening = _finite_price(candle, "open")
        high = _finite_price(candle, "high")
        low = _finite_price(candle, "low")
        close = _finite_price(candle, "close")
        if high < max(opening, low, close) or low > min(opening, high, close):
            raise ValueError("La vela incumple las reglas OHLC.")

        try:
            tick_volume = int(candle.get("tick_volume", 0))
            spread_points = int(candle.get("spread_points", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("Volumen o spread no numérico.") from exc
        if tick_volume < 0 or spread_points < 0:
            raise ValueError("Volumen y spread no pueden ser negativos.")

        output.append({
            "symbol": symbol,
            "timeframe": timeframe,
            "broker_symbol": broker_symbol,
            "open_time": supplied_utc.isoformat(),
            "server_time": server_time.isoformat(timespec="seconds"),
            "server_utc_offset_minutes": offset_minutes,
            "open": opening,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": tick_volume,
            "spread_points": spread_points,
            "source": "VANTAGE_MT5",
        })

    return output
