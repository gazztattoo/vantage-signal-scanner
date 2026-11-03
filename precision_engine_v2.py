"""Isolated multi-timeframe signal quality gate for VANTAGE.

Pure function: no market-data calls, persistence, notifications, or order execution.
Inputs are completed-candle snapshots supplied by a separate adapter.
This module is research-only until historical validation and explicit approval.
"""
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass(frozen=True)
class PrecisionConfig:
    min_m30_adx: float = 18.0
    min_m15_adx: float = 16.0
    min_m5_adx: float = 14.0
    max_candle_age_fraction: float = 1.25
    min_reward_risk: float = 2.0
    stop_atr_buffer: float = 0.15
    stop_atr_multiple: float = 1.5
    max_stop_atr_multiple: float = 2.5


def _valid_frame(frame, required):
    if not isinstance(frame, dict):
        return False
    try:
        return all(k in frame and frame[k] is not None for k in required)
    except TypeError:
        return False


def _direction(frame):
    if frame["ema20"] > frame["ema50"]:
        return "BUY"
    if frame["ema20"] < frame["ema50"]:
        return "SELL"
    return "NEUTRAL"


def evaluate_multitimeframe_setup(*, symbol: str, m30: dict, m15: dict, m5: dict,
                                  config: Optional[PrecisionConfig] = None):
    """Return a reasoned BUY/SELL/WAIT decision using closed-candle snapshots.

    Required per frame: ema20, ema50, adx, rsi, macd_hist, close, atr,
    candle_age_seconds, timeframe_seconds. M5 additionally requires
    previous_high and previous_low. All prices must share the same quote basis.
    """
    cfg = config or PrecisionConfig()
    required = ("ema20", "ema50", "adx", "rsi", "macd_hist", "close",
                "atr", "candle_age_seconds", "timeframe_seconds")
    if not symbol or not all(_valid_frame(f, required) for f in (m30, m15, m5)):
        return {"signal": "ESPERAR", "quality": "INSUFICIENTE",
                "reason": "Faltan datos completos de M30, M15 o M5."}

    for label, frame in (("M30", m30), ("M15", m15), ("M5", m5)):
        numeric = required
        try:
            if any(not isinstance(frame[k], (int, float)) for k in numeric):
                raise ValueError()
            if frame["atr"] <= 0 or frame["timeframe_seconds"] <= 0:
                raise ValueError()
            if frame["candle_age_seconds"] < 0:
                raise ValueError()
        except (TypeError, ValueError):
            return {"signal": "ESPERAR", "quality": "INSUFICIENTE",
                    "reason": f"Datos inválidos en {label}."}
        if frame["candle_age_seconds"] > frame["timeframe_seconds"] * cfg.max_candle_age_fraction:
            return {"signal": "ESPERAR", "quality": "INSUFICIENTE",
                    "reason": f"Vela {label} demasiado antigua; se bloquea la señal."}

    bias = _direction(m30)
    confirm = _direction(m15)
    if bias == "NEUTRAL" or confirm != bias:
        return {"signal": "ESPERAR", "quality": "BAJA",
                "reason": "M30 y M15 no están alineados; no se fuerza una entrada.",
                "m30_bias": bias, "m15_direction": confirm}

    side = bias
    if m30["adx"] < cfg.min_m30_adx or m15["adx"] < cfg.min_m15_adx:
        return {"signal": "ESPERAR", "quality": "BAJA",
                "reason": "Falta fuerza direccional en M30 o M15.",
                "m30_bias": bias, "m15_direction": confirm}

    if side == "BUY":
        momentum_ok = m15["macd_hist"] > 0 and 50 <= m15["rsi"] <= 70
        trigger_ok = (m5["close"] > m5["ema20"] and
                      m5["close"] > m5.get("previous_high", float("inf")) and
                      m5["macd_hist"] > 0 and 50 <= m5["rsi"] <= 72)
        invalidation = min(m5.get("swing_low", m5["close"] - cfg.stop_atr_multiple*m5["atr"]),
                           m5["close"] - cfg.stop_atr_multiple*m5["atr"])
        entry = m5["close"]
        stop = invalidation - cfg.stop_atr_buffer*m5["atr"]
        risk = entry - stop
        target = entry + cfg.min_reward_risk*risk
    else:
        momentum_ok = m15["macd_hist"] < 0 and 30 <= m15["rsi"] <= 50
        trigger_ok = (m5["close"] < m5["ema20"] and
                      m5["close"] < m5.get("previous_low", float("-inf")) and
                      m5["macd_hist"] < 0 and 28 <= m5["rsi"] <= 50)
        invalidation = max(m5.get("swing_high", m5["close"] + cfg.stop_atr_multiple*m5["atr"]),
                           m5["close"] + cfg.stop_atr_multiple*m5["atr"])
        entry = m5["close"]
        stop = invalidation + cfg.stop_atr_buffer*m5["atr"]
        risk = stop - entry
        target = entry - cfg.min_reward_risk*risk

    if not momentum_ok:
        return {"signal": "ESPERAR", "quality": "MEDIA",
                "reason": "La dirección coincide, pero M15 no confirma el momentum.",
                "m30_bias": bias, "m15_direction": confirm}
    if m5["adx"] < cfg.min_m5_adx or not trigger_ok:
        return {"signal": "ESPERAR", "quality": "MEDIA",
                "reason": "Esperar ruptura y cierre de confirmación en M5.",
                "m30_bias": bias, "m15_direction": confirm}
    if risk <= 0 or risk > cfg.max_stop_atr_multiple*m5["atr"]:
        return {"signal": "ESPERAR", "quality": "BAJA",
                "reason": "Stop demasiado amplio o inválido respecto al ATR de M5."}

    return {
        "symbol": symbol, "signal": side, "quality": "CANDIDATA",
        "reason": "M30 y M15 alineados; momentum M15 y ruptura confirmada en M5.",
        "entry_reference": entry, "stop_loss_reference": stop,
        "take_profit_reference": target, "risk_reward": round(abs(target-entry)/risk, 2),
        "m30_bias": bias, "m15_direction": confirm,
        "filters": {"m30_strength": True, "m15_strength": True,
                    "m15_momentum": True, "m5_closed_breakout": True,
                    "m5_strength": True},
        "warning": "Candidata de investigación; no es probabilidad de éxito ni orden ejecutable."
    }


def evaluate_m15_m5_setup(*, symbol: str, m15: dict, m5: dict,
                          m30: Optional[dict] = None,
                          config: Optional[PrecisionConfig] = None):
    """Evaluate M15 direction + momentum with a completed M5 breakout.

    M30 is optional context only. This pure research gate never fetches data,
    places orders, or treats a candle close as a live Bid/Ask execution quote.
    """
    cfg = config or PrecisionConfig()
    required = ("ema20", "ema50", "adx", "rsi", "macd_hist", "close",
                "atr", "candle_age_seconds", "timeframe_seconds")
    frames = (("M15", m15, cfg.min_m15_adx), ("M5", m5, cfg.min_m5_adx))
    if not symbol or any(not _valid_frame(frame, required) for _, frame, _ in frames):
        return {"signal": "ESPERAR", "quality": "INSUFICIENTE",
                "reason": "Faltan datos completos de M15 o M5."}

    for label, frame, _ in frames:
        try:
            values = [float(frame[key]) for key in required]
            if not all(__import__("math").isfinite(value) for value in values):
                raise ValueError()
            if frame["atr"] <= 0 or frame["timeframe_seconds"] <= 0:
                raise ValueError()
            if frame["candle_age_seconds"] < 0:
                raise ValueError()
        except (TypeError, ValueError, OverflowError):
            return {"signal": "ESPERAR", "quality": "INSUFICIENTE",
                    "reason": f"Datos inválidos en {label}."}
        if frame["candle_age_seconds"] > frame["timeframe_seconds"] * cfg.max_candle_age_fraction:
            return {"signal": "ESPERAR", "quality": "INSUFICIENTE",
                    "reason": f"Vela {label} demasiado antigua; se bloquea la señal."}

    bias = _direction(m15)
    if bias == "NEUTRAL" or m15["adx"] < cfg.min_m15_adx:
        return {"signal": "ESPERAR", "quality": "BAJA",
                "reason": "M15 no muestra dirección y fuerza suficientes."}

    if bias == "BUY":
        momentum_ok = m15["macd_hist"] > 0 and 50 <= m15["rsi"] <= 70
        breakout_key, swing_key = "previous_high", "swing_low"
        trigger_ok = (
            _direction(m5) == "BUY" and m5["close"] > m5["ema20"] and
            m5["macd_hist"] > 0 and 50 <= m5["rsi"] <= 72 and
            _valid_frame(m5, (breakout_key,)) and
            m5["close"] > m5[breakout_key]
        )
        entry = float(m5["close"])
        swing = float(m5[swing_key]) if swing_key in m5 else entry - cfg.stop_atr_multiple * m5["atr"]
        stop = min(swing, entry - cfg.stop_atr_multiple * m5["atr"]) - cfg.stop_atr_buffer * m5["atr"]
        risk = entry - stop
        target = entry + cfg.min_reward_risk * risk
    else:
        momentum_ok = m15["macd_hist"] < 0 and 30 <= m15["rsi"] <= 50
        breakout_key, swing_key = "previous_low", "swing_high"
        trigger_ok = (
            _direction(m5) == "SELL" and m5["close"] < m5["ema20"] and
            m5["macd_hist"] < 0 and 28 <= m5["rsi"] <= 50 and
            _valid_frame(m5, (breakout_key,)) and
            m5["close"] < m5[breakout_key]
        )
        entry = float(m5["close"])
        swing = float(m5[swing_key]) if swing_key in m5 else entry + cfg.stop_atr_multiple * m5["atr"]
        stop = max(swing, entry + cfg.stop_atr_multiple * m5["atr"]) + cfg.stop_atr_buffer * m5["atr"]
        risk = stop - entry
        target = entry - cfg.min_reward_risk * risk

    if not momentum_ok:
        return {"signal": "ESPERAR", "quality": "MEDIA",
                "reason": "M15 apunta una dirección, pero falta confirmación de momentum."}
    if m5["adx"] < cfg.min_m5_adx or not trigger_ok:
        return {"signal": "ESPERAR", "quality": "MEDIA",
                "reason": "Esperar ruptura y cierre confirmado en M5."}
    if risk <= 0 or risk > cfg.max_stop_atr_multiple * m5["atr"]:
        return {"signal": "ESPERAR", "quality": "BAJA",
                "reason": "Stop demasiado amplio o inválido respecto al ATR de M5."}

    m30_context = _direction(m30) if _valid_frame(m30, ("ema20", "ema50")) else None
    context_note = ("M30 acompaña." if m30_context == bias else
                    "M30 está en contra; revisar contexto manual." if m30_context else
                    "M30 no se exige para esta señal.")
    return {
        "symbol": symbol, "signal": bias, "quality": "CANDIDATA",
        "reason": f"M15 confirma dirección y momentum; M5 cerró ruptura. {context_note}",
        "entry_reference": entry, "stop_loss_reference": stop,
        "take_profit_reference": target,
        "risk_reward": round(abs(target - entry) / risk, 2),
        "m15_direction": bias, "m30_context": m30_context,
        "filters": {"m15_strength": True, "m15_momentum": True,
                    "m5_closed_breakout": True, "m5_strength": True},
        "warning": "Referencia basada en cierre M5; no es cotización Bid/Ask ni orden ejecutable."
    }


def precision_frame_from_payload(payload, interval):
    """Build a closed-candle engine frame from an already cached analysis."""
    candles = payload.get("recent_candles") or []
    if len(candles) < 3:
        return None
    previous = candles[-4:-1]
    latest = candles[-1]
    if len(previous) < 2:
        return None
    try:
        if interval == "5min":
            previous_high = max(float(item["high"]) for item in previous)
            previous_low = min(float(item["low"]) for item in previous)
        else:
            previous_high = max(float(item["high"]) for item in previous)
            previous_low = min(float(item["low"]) for item in previous)
        age_minutes = float(payload.get("candle_age_minutes"))
        frame = {
            "ema20": float(payload["ema20"]),
            "ema50": float(payload["ema50"]),
            "adx": float(payload["adx14"]),
            "rsi": float(payload["rsi14"]),
            "macd_hist": float(payload["macd_histogram"]),
            "close": float(latest["close"]),
            "atr": float(payload["atr14"]),
            "candle_age_seconds": age_minutes * 60,
            "timeframe_seconds": {"5min": 300, "15min": 900}[interval],
            "previous_high": previous_high,
            "previous_low": previous_low,
        }
        if not all(__import__("math").isfinite(value) for value in frame.values()):
            return None
        return frame
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


