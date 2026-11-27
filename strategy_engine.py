"""Experimental, deterministic multi-timeframe signal engine for GLOBAL SIGNAL SCANNER.

Pure Python: no network, API keys, broker calls or persistence. Candles must be
chronologically ordered and contain open timestamps and OHLC fields. Higher
timeframes are built only from complete, contiguous lower-timeframe candles.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any

TIMEFRAME_MINUTES = {"5min": 5, "15min": 15, "30min": 30}
# Automatic M5/M15 context avoids the M30 feed; M30 remains manual-only.
MTF_MAP = {"5min": (15, 60), "15min": (60, 240), "30min": (60, 240)}


def _time_seconds(value: Any) -> int:
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.astimezone(timezone.utc).timestamp())


def _normalise(candles: list[dict]) -> list[dict]:
    result = []
    previous = None
    for raw in candles:
        stamp = _time_seconds(raw.get("time", raw.get("timestamp")))
        bar = {"ts": stamp}
        for key in ("open", "high", "low", "close"):
            value = float(raw[key])
            if not isfinite(value):
                raise ValueError(f"Non-finite OHLC {key}")
            bar[key] = value
        if bar["high"] < max(bar["open"], bar["close"], bar["low"]):
            raise ValueError("Invalid candle high")
        if bar["low"] > min(bar["open"], bar["close"], bar["high"]):
            raise ValueError("Invalid candle low")
        if previous is not None and stamp <= previous:
            raise ValueError("Candles must be strictly chronological without duplicates")
        previous = stamp
        result.append(bar)
    return result


def aggregate_candles(candles: list[dict], source_minutes: int, target_minutes: int) -> list[dict]:
    """Aggregate only exact, contiguous, fully completed buckets; never fill gaps."""
    if target_minutes <= source_minutes or target_minutes % source_minutes:
        raise ValueError("Target timeframe must be an integer multiple of source timeframe")
    source_seconds = source_minutes * 60
    target_seconds = target_minutes * 60
    factor = target_minutes // source_minutes
    bars = _normalise(candles)
    grouped: dict[int, list[dict]] = {}
    for bar in bars:
        bucket = (bar["ts"] // target_seconds) * target_seconds
        grouped.setdefault(bucket, []).append(bar)
    output = []
    for bucket in sorted(grouped):
        group = grouped[bucket]
        if len(group) != factor:
            continue
        expected = [bucket + i * source_seconds for i in range(factor)]
        if [x["ts"] for x in group] != expected:
            continue
        output.append({
            "time": datetime.fromtimestamp(bucket, timezone.utc).isoformat(),
            "open": group[0]["open"],
            "high": max(x["high"] for x in group),
            "low": min(x["low"] for x in group),
            "close": group[-1]["close"],
        })
    return output


def ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    alpha = 2.0 / (period + 1.0)
    current = sum(values[:period]) / period
    out[period - 1] = current
    for i in range(period, len(values)):
        current = values[i] * alpha + current * (1.0 - alpha)
        out[i] = current
    return out


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(x, 0.0) for x in changes]
    losses = [max(-x, 0.0) for x in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def value(g, l):
        if l <= 0:
            return 100.0 if g > 0 else 50.0
        rs = g / l
        return 100.0 - 100.0 / (1.0 + rs)

    out[period] = value(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        j = i - 1
        avg_gain = (avg_gain * (period - 1) + gains[j]) / period
        avg_loss = (avg_loss * (period - 1) + losses[j]) / period
        out[i] = value(avg_gain, avg_loss)
    return out


def _true_ranges(candles: list[dict]) -> list[float]:
    values = []
    for i in range(1, len(candles)):
        cur, prev = candles[i], candles[i - 1]
        values.append(max(
            cur["high"] - cur["low"],
            abs(cur["high"] - prev["close"]),
            abs(cur["low"] - prev["close"]),
        ))
    return values


def atr(candles: list[dict], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(candles)
    trs = _true_ranges(candles)
    if len(trs) < period:
        return out
    current = sum(trs[:period]) / period
    out[period] = current
    for i in range(period + 1, len(candles)):
        current = (current * (period - 1) + trs[i - 1]) / period
        out[i] = current
    return out


def adx_di(candles: list[dict], period: int = 14):
    n = len(candles)
    adx_values: list[float | None] = [None] * n
    plus_values: list[float | None] = [None] * n
    minus_values: list[float | None] = [None] * n
    if n < period * 2 + 1:
        return adx_values, plus_values, minus_values
    trs, plus_dm, minus_dm = [], [], []
    for i in range(1, n):
        cur, prev = candles[i], candles[i - 1]
        up = cur["high"] - prev["high"]
        down = prev["low"] - cur["low"]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(
            cur["high"] - cur["low"],
            abs(cur["high"] - prev["close"]),
            abs(cur["low"] - prev["close"]),
        ))
    st, sp, sm = sum(trs[:period]), sum(plus_dm[:period]), sum(minus_dm[:period])
    dx, dx_indices = [], []
    for i in range(period - 1, len(trs)):
        if i >= period:
            st += trs[i] - st / period
            sp += plus_dm[i] - sp / period
            sm += minus_dm[i] - sm / period
        if st <= 0:
            continue
        pdi, mdi = 100.0 * sp / st, 100.0 * sm / st
        den = pdi + mdi
        dx.append(100.0 * abs(pdi - mdi) / den if den else 0.0)
        dx_indices.append(i + 1)
        plus_values[i + 1], minus_values[i + 1] = pdi, mdi
    if len(dx) < period:
        return adx_values, plus_values, minus_values
    current = sum(dx[:period]) / period
    adx_values[dx_indices[period - 1]] = current
    for k in range(period, len(dx)):
        current = (current * (period - 1) + dx[k]) / period
        adx_values[dx_indices[k]] = current
    return adx_values, plus_values, minus_values


def macd_histogram(values: list[float], fast=12, slow=26, signal_period=9):
    fast_line, slow_line = ema(values, fast), ema(values, slow)
    macd_line = [None if a is None or b is None else a - b
                 for a, b in zip(fast_line, slow_line)]
    valid = [x for x in macd_line if x is not None]
    signal_valid = ema(valid, signal_period)
    signal_iter = iter(signal_valid)
    hist: list[float | None] = []
    for item in macd_line:
        if item is None:
            hist.append(None)
            continue
        sig = next(signal_iter)
        hist.append(None if sig is None else item - sig)
    return hist


def _latest_bias(candles: list[dict]) -> str:
    if len(candles) < 60:
        return "INSUFFICIENT"
    bars = _normalise(candles)
    closes = [x["close"] for x in bars]
    e20, e50 = ema(closes, 20)[-1], ema(closes, 50)[-1]
    if e20 is None or e50 is None:
        return "INSUFFICIENT"
    close = closes[-1]
    if e20 > e50 and close > e20:
        return "BULL"
    if e20 < e50 and close < e20:
        return "BEAR"
    return "NEUTRAL"


def _structure(bars: list[dict], atr_now: float, lookback: int = 20):
    if len(bars) < lookback + 2:
        return {"type": "INSUFFICIENT", "direction": "NONE", "level": None,
                "near_direction": "NONE", "distance_atr": None}
    prior = bars[-lookback-1:-1]
    last, previous = bars[-1], bars[-2]
    upper = max(x["high"] for x in prior)
    lower = min(x["low"] for x in prior)
    direction, kind, level = "NONE", "NONE", None
    if last["close"] > upper and previous["close"] <= upper:
        direction, kind, level = "BUY", "BREAKOUT", upper
    elif last["close"] < lower and previous["close"] >= lower:
        direction, kind, level = "SELL", "BREAKOUT", lower
    # Liquidity sweep and reclaim: price runs a prior range extreme intrabar,
    # then the closed candle finishes back inside the range.
    elif last["low"] < lower and last["close"] > lower:
        direction, kind, level = "BUY", "SWEEP_RECLAIM", last["low"]
    elif last["high"] > upper and last["close"] < upper:
        direction, kind, level = "SELL", "SWEEP_RECLAIM", last["high"]
    else:
        start = max(lookback + 1, len(bars) - 6)
        for j in range(len(bars) - 2, start - 1, -1):
            old_range = bars[j-lookback:j]
            old_high = max(x["high"] for x in old_range)
            old_low = min(x["low"] for x in old_range)
            if bars[j]["close"] > old_high and bars[j-1]["close"] <= old_high:
                if last["low"] <= old_high + 0.25 * atr_now and last["close"] > old_high:
                    direction, kind, level = "BUY", "RETEST", old_high
                    break
            if bars[j]["close"] < old_low and bars[j-1]["close"] >= old_low:
                if last["high"] >= old_low - 0.25 * atr_now and last["close"] < old_low:
                    direction, kind, level = "SELL", "RETEST", old_low
                    break
    dist_up = abs(upper - last["close"]) / atr_now if atr_now else float("inf")
    dist_dn = abs(last["close"] - lower) / atr_now if atr_now else float("inf")
    # Wider preparation window: this is a prealert context, not an entry trigger.
    # Final entry still requires a closed-candle breakout/retest and risk validation.
    prealert_distance_atr = 0.75
    near_direction = (
        "BUY" if last["close"] <= upper and dist_up <= prealert_distance_atr
        else "SELL" if last["close"] >= lower and dist_dn <= prealert_distance_atr
        else "NONE"
    )
    near_distance = dist_up if near_direction == "BUY" else dist_dn if near_direction == "SELL" else None
    return {
        "type": kind, "direction": direction, "level": level,
        "range_high": upper, "range_low": lower,
        "near_direction": near_direction, "distance_atr": near_distance,
    }


def _wait(code: str, reason: str) -> dict:
    return {
        "signal": "WAIT", "pre_signal": False, "pre_signal_side": None,
        "entry": None, "stop_loss": None, "take_profit": None,
        "risk_distance": None, "risk_reward": None,
        "reason": reason, "status_code": code, "structure_trigger": "NONE",
    }


def analyze(candles: list[dict], timeframe: str, symbol: str = "", higher_timeframes: dict[str, list[dict]] | None = None) -> dict:
    """Return confirmed signal, pre-signal, market regime and MTF evidence."""
    if timeframe not in TIMEFRAME_MINUTES:
        raise ValueError("Unsupported timeframe")
    bars = _normalise(candles)
    base_minutes = TIMEFRAME_MINUTES[timeframe]
    htf1, htf2 = MTF_MAP[timeframe]
    # Direct provider higher-timeframe series avoid synthetic aggregation limits.
    minimum = 120 if higher_timeframes is not None else max(120, (60 * htf2) // base_minutes + 2)
    if len(bars) < minimum:
        return _wait(
            "HISTORIAL_INSUFICIENTE",
            "No hay suficientes velas completas para confirmar las temporalidades superiores.",
        )

    closes = [x["close"] for x in bars]
    e20s, e50s = ema(closes, 20), ema(closes, 50)
    rsis, atrs = rsi(closes, 14), atr(bars, 14)
    adxs, pdis, mdis = adx_di(bars, 14)
    macds = macd_histogram(closes)
    e20, e50 = e20s[-1], e50s[-1]
    rsi_now, atr_now, adx_now = rsis[-1], atrs[-1], adxs[-1]
    pdi, mdi, macd_now = pdis[-1], mdis[-1], macds[-1]
    if any(v is None for v in (e20, e50, rsi_now, atr_now, adx_now, pdi, mdi, macd_now)) or atr_now <= 0:
        return _wait(
            "INDICADORES_INCOMPLETOS",
            "Faltan datos válidos para calcular indicadores; no se genera señal.",
        )

    if higher_timeframes is not None:
        htf_bars_1 = higher_timeframes.get(f"{htf1}min", [])
        htf_bars_2 = higher_timeframes.get(f"{htf2}min", [])
    else:
        htf_bars_1 = aggregate_candles(candles, base_minutes, htf1)
        htf_bars_2 = aggregate_candles(candles, base_minutes, htf2)
    bias1, bias2 = _latest_bias(htf_bars_1), _latest_bias(htf_bars_2)
    primary = (
        "BULL" if e20 > e50 and closes[-1] > e20
        else "BEAR" if e20 < e50 and closes[-1] < e20
        else "NEUTRAL"
    )
    if adx_now < 18:
        regime = "RANGE"
    elif adx_now < 20:
        regime = "TRANSITION"
    else:
        regime = "TREND"

    structure = _structure(bars, atr_now)
    sides = {}
    for side in ("BUY", "SELL"):
        desired = "BULL" if side == "BUY" else "BEAR"
        checks = {
            "primary_trend": primary == desired,
            "htf_1": bias1 == desired,
            "htf_2": bias2 == desired,
            "rsi_momentum": (50 <= rsi_now <= 70) if side == "BUY" else (30 <= rsi_now <= 50),
            "macd_momentum": macd_now > 0 if side == "BUY" else macd_now < 0,
            "directional_strength": pdi > mdi if side == "BUY" else mdi > pdi,
            "price_vs_ema20": closes[-1] > e20 if side == "BUY" else closes[-1] < e20,
        }
        sides[side] = {
            "score": sum(checks.values()), "checks": checks,
            "mtf_aligned": bias1 == desired and bias2 == desired,
        }

    best_side = max(("BUY", "SELL"), key=lambda s: sides[s]["score"])
    best = sides[best_side]
    score = int(best["score"])
    for side in ("BUY", "SELL"):
        desired = "BULL" if side == "BUY" else "BEAR"
        opposed = "BEAR" if side == "BUY" else "BULL"
        st = structure
        side_structure_ok = st["direction"] == side and st["type"] in ("BREAKOUT", "RETEST", "SWEEP_RECLAIM")
        side_primary_ok = primary in (desired, "NEUTRAL")
        side_htf_conflict = bias1 == opposed and bias2 == opposed
        side_near = st["near_direction"] == side
        blockers = []
        if not side_primary_ok:
            blockers.append("TENDENCIA_PRIMARIA_EN_CONTRA")
        if side_htf_conflict:
            blockers.append("H1_H4_EN_CONTRA")
        if sides[side]["score"] < 3:
            blockers.append("CONFLUENCIA_INSUFICIENTE_PARA_PREALERTA")
        if sides[side]["score"] < 4:
            blockers.append("CONFLUENCIA_INSUFICIENTE_PARA_ENTRADA")
        range_prealert_ok = (
            regime != "RANGE"
            or (
                sides[side]["score"] >= 4
                and side_near
                and st.get("distance_atr") is not None
                and st["distance_atr"] <= 0.25
                and sides[side]["checks"]["directional_strength"]
            )
        )
        if regime == "RANGE" and not range_prealert_ok:
            blockers.append("MERCADO_LATERAL_REQUIERE_CONFLUENCIA_Y_PROXIMIDAD_EXTRA")
        if not side_structure_ok:
            blockers.append("FALTA_RUPTURA_O_RETEST")
        if not side_structure_ok and not side_near:
            blockers.append("ESTRUCTURA_NO_CERCANA")
        sides[side]["diagnostic"] = {
            "side": side, "score": sides[side]["score"], "score_max": 7,
            "checks": sides[side]["checks"], "blockers": blockers,
            "primary_trend_aligned": primary == desired,
            "primary_trend_opposed": primary == opposed,
            "higher_timeframes_both_opposed": side_htf_conflict,
            "structure_confirmed": side_structure_ok,
            "structure_near": side_near,
            "distance_to_structure_atr": st.get("distance_atr") if side_near else None,
            "distance_to_structure_price": (
                abs(float(st["range_high"] if side == "BUY" else st["range_low"]) - closes[-1])
                if st.get("range_high") is not None and st.get("range_low") is not None else None
            ),
            "prealert_eligible_context": (
                range_prealert_ok and side_primary_ok and not side_htf_conflict
                and sides[side]["score"] >= 3 and side_near
            ),
            "entry_eligible_context": (
                side_structure_ok and side_primary_ok and not side_htf_conflict
                and sides[side]["score"] >= 4
            ),
        }
    structure_ok = structure["direction"] == best_side and structure["type"] in ("BREAKOUT", "RETEST", "SWEEP_RECLAIM")
    primary_aligned = primary == ("BULL" if best_side == "BUY" else "BEAR")
    primary_not_opposed = primary in (("BULL", "NEUTRAL") if best_side == "BUY" else ("BEAR", "NEUTRAL"))
    opposed_bias = "BEAR" if best_side == "BUY" else "BULL"
    higher_timeframes_both_opposed = bias1 == opposed_bias and bias2 == opposed_bias
    # ADX/RSI/MACD/MTF aportan contexto y puntuación; no son candados individuales.
    # La estructura, la dirección primaria y el riesgo sí protegen la entrada.
    # Confluence grade describes indicator alignment; setup grade also requires
    # a valid structural trigger. Never label a high-scoring context as a ready setup.
    if score >= 6:
        confluence_grade = "A"
    elif score >= 5:
        confluence_grade = "B"
    else:
        confluence_grade = "C"
    if structure_ok and primary_not_opposed and not higher_timeframes_both_opposed and score >= 6 and adx_now >= 25:
        grade = "A"
    elif structure_ok and primary_not_opposed and not higher_timeframes_both_opposed and score >= 4:
        grade = "B"
    else:
        grade = "C"
    confirmation_missing = []
    if not primary_not_opposed:
        confirmation_missing.append("PRIMARY_TREND_OPPOSED")
    if not structure_ok:
        confirmation_missing.append("BREAKOUT_OR_RETEST")
    if score < 4:
        confirmation_missing.append("MINIMUM_CONFLUENCE_4_OF_7")
    if higher_timeframes_both_opposed:
        confirmation_missing.append("HIGHER_TIMEFRAME_CONFLICT")
    setup_status = "READY" if not confirmation_missing else "WAIT"

    common = {
        "symbol": symbol, "timeframe": timeframe,
        "setup_status": setup_status,
        "confirmation_missing": confirmation_missing,
        "price": closes[-1], "ema20": e20, "ema50": e50, "rsi14": rsi_now,
        "atr14": atr_now, "adx14": adx_now, "plus_di": pdi, "minus_di": mdi,
        "macd_histogram": macd_now, "primary_bias": primary,
        "mtf": {
            "timeframe_1": f"M{htf1}" if htf1 < 60 else f"H{htf1//60}",
            "bias_1": bias1,
            "timeframe_2": f"M{htf2}" if htf2 < 60 else f"H{htf2//60}",
            "bias_2": bias2,
            "aligned_buy": sides["BUY"]["mtf_aligned"],
            "aligned_sell": sides["SELL"]["mtf_aligned"],
        },
        "regime": regime, "structure": structure,
        "score": score, "score_max": 7,
        "confluence_grade": confluence_grade,
        "setup_grade": grade, "quality_grade": grade,
        "quality_score": round(score / 7 * 100), "quality_max": 100,
        "buy_score": sides["BUY"]["score"], "sell_score": sides["SELL"]["score"],
        "score_components": best["checks"],
        "side_diagnostics": {"BUY": sides["BUY"]["diagnostic"], "SELL": sides["SELL"]["diagnostic"]},
        "wait_diagnostics": {
            "best_side": best_side,
            "best_side_blockers": sides[best_side]["diagnostic"]["blockers"],
            "summary": (
                "Contexto favorable para prealerta; falta el disparador estructural."
                if sides[best_side]["diagnostic"]["prealert_eligible_context"] and not structure_ok
                else "Hay ruptura/retest y confluencia suficientes; revisar el riesgo estructural."
                if sides[best_side]["diagnostic"]["entry_eligible_context"]
                else "No se cumplen todavía las condiciones de prealerta o entrada."
            ),
        },
        "candle_time": candles[-1].get("time", candles[-1].get("timestamp")),
        "candles": len(bars),
        "data_warning": "Evaluación técnica; la puntuación no representa probabilidad de acierto.",
    }

    if structure_ok and primary_not_opposed and not higher_timeframes_both_opposed and score >= 4:
        level = float(structure["level"])
        # Stop anchored to the broken/retested structure with an ATR buffer.
        if best_side == "BUY":
            sl = level - 0.5 * atr_now
            risk = closes[-1] - sl
            tp = closes[-1] + 2.0 * risk
        else:
            sl = level + 0.5 * atr_now
            risk = sl - closes[-1]
            tp = closes[-1] - 2.0 * risk
        if risk <= 0 or risk > 2.5 * atr_now or risk < 0.35 * atr_now:
            return {
                **common,
                "setup_status": "WAIT",
                "confirmation_missing": ["ACCEPTABLE_STRUCTURAL_RISK"],
                "setup_grade": "C",
                "quality_grade": "C",
                **_wait(
                    "RIESGO_ESTRUCTURAL",
                    "La estructura exige un stop fuera del rango de riesgo permitido; se descarta la entrada.",
                ),
            }
        return {
            **common,
            "signal": best_side, "pre_signal": False, "pre_signal_side": None,
            "entry": closes[-1], "stop_loss": sl, "take_profit": tp,
            "risk_distance": risk, "risk_reward": 2.0,
            "reason": f"Ruptura/retest confirmado; confluencia {score}/7. ADX y MTF se usan como contexto.",
            "structure_trigger": structure["type"],
        }

    range_prealert_ok = (
        regime != "RANGE"
        or (
            score >= 4
            and structure["near_direction"] == best_side
            and structure.get("distance_atr") is not None
            and structure["distance_atr"] <= 0.25
            and best["checks"]["directional_strength"]
        )
    )
    if range_prealert_ok and primary_not_opposed and not higher_timeframes_both_opposed and score >= 3 and structure["near_direction"] == best_side:
        # Provisional breakout plan for early preparation only. It is NOT a
        # confirmed entry: the stop order is placed beyond the nearby range
        # boundary, with an ATR-buffered invalidation and a 2R target.
        level = float(structure["range_high"] if best_side == "BUY" else structure["range_low"])
        planned_entry = level + 0.05 * atr_now if best_side == "BUY" else level - 0.05 * atr_now
        planned_sl = level - 0.50 * atr_now if best_side == "BUY" else level + 0.50 * atr_now
        planned_risk = abs(planned_entry - planned_sl)
        planned_tp = planned_entry + 2.0 * planned_risk if best_side == "BUY" else planned_entry - 2.0 * planned_risk
        return {
            **common,
            "signal": "WAIT", "pre_signal": True, "pre_signal_side": best_side,
            "entry": None, "stop_loss": None, "take_profit": None,
            "planned_entry": planned_entry, "planned_stop_loss": planned_sl,
            "planned_take_profit": planned_tp, "planned_risk_distance": planned_risk,
            "planned_risk_reward": 2.0,
            "planned_entry_type": "BUY_STOP" if best_side == "BUY" else "SELL_STOP",
            "risk_distance": None, "risk_reward": None,
            "reason": f"Preseñal {best_side}: estructura cercana y confluencia {score}/7. Niveles provisionales; esperar ruptura del nivel antes de validar la entrada.",
            "structure_trigger": "PENDING",
        }

    return {
        **common, **_wait(
            "SIN_CONFIRMACION",
            (
                f"Confluencia {score}/7, pero falta ruptura/retest de estructura. Esperar confirmación."
                if score >= 4 and primary_not_opposed and not structure_ok
                else "Tendencia primaria en contra o confluencia insuficiente. Esperar."
            ),
        ),
    }
