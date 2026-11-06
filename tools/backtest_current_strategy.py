#!/usr/bin/env python3
"""Offline backtest for the production Vantage signal rules.

No network calls, API keys, broker connection, or Twelve Data credits.
CSV columns: time (or datetime), open, high, low, close.
Rows may be newest-first (as Twelve Data exports); they are sorted chronologically.
Signals are evaluated at candle close and outcomes only on later candles.
One position at a time; overlapping signals are skipped while a trade is open.
If SL and TP are both touched in one candle, SL is counted first.
"""
import argparse
import csv
import json
import math
from pathlib import Path


def ema(values, period):
    if len(values) < period:
        return None
    alpha = 2 / (period + 1)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = value * alpha + result * (1 - alpha)
    return result


def rsi(values, period=14):
    if len(values) <= period:
        return None
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def atr(candles, period=14):
    if len(candles) <= period:
        return None
    trs = []
    for i in range(1, len(candles)):
        cur, prev = candles[i], candles[i - 1]
        trs.append(max(cur["high"] - cur["low"],
                       abs(cur["high"] - prev["close"]),
                       abs(cur["low"] - prev["close"])))
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


def macd(values, fast=12, slow=26, signal_period=9):
    if len(values) < slow + signal_period:
        return None, None, None
    line = [ema(values[:i + 1], fast) - ema(values[:i + 1], slow)
            for i in range(slow - 1, len(values))]
    signal_line = ema(line, signal_period)
    return line[-1], signal_line, line[-1] - signal_line


def bollinger(values, period=20, deviations=2.0):
    if len(values) < period:
        return None, None, None
    window = values[-period:]
    middle = sum(window) / period
    variance = sum((v - middle) ** 2 for v in window) / period
    width = deviations * math.sqrt(variance)
    return middle + width, middle, middle - width


def adx(candles, period=14):
    if len(candles) < (period * 2 + 1):
        return None
    trs, plus_dm, minus_dm = [], [], []
    for i in range(1, len(candles)):
        cur, prev = candles[i], candles[i - 1]
        up_move = cur["high"] - prev["high"]
        down_move = prev["low"] - cur["low"]
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
        trs.append(max(cur["high"] - cur["low"],
                       abs(cur["high"] - prev["close"]),
                       abs(cur["low"] - prev["close"])))
    smoothed_tr = sum(trs[:period])
    smoothed_plus = sum(plus_dm[:period])
    smoothed_minus = sum(minus_dm[:period])
    dx_values = []
    for i in range(period - 1, len(trs)):
        if i >= period:
            smoothed_tr = smoothed_tr - smoothed_tr / period + trs[i]
            smoothed_plus = smoothed_plus - smoothed_plus / period + plus_dm[i]
            smoothed_minus = smoothed_minus - smoothed_minus / period + minus_dm[i]
        if smoothed_tr <= 0:
            continue
        plus_di = 100.0 * smoothed_plus / smoothed_tr
        minus_di = 100.0 * smoothed_minus / smoothed_tr
        denominator = plus_di + minus_di
        dx_values.append(100.0 * abs(plus_di - minus_di) / denominator
                         if denominator > 0 else 0.0)
    if len(dx_values) < period:
        return None
    value = sum(dx_values[:period]) / period
    for dx in dx_values[period:]:
        value = (value * (period - 1) + dx) / period
    return value


def load_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("El CSV está vacío o no tiene encabezados.")
        fields = {name.strip().lower(): name for name in reader.fieldnames}
        time_key = fields.get("time") or fields.get("datetime") or fields.get("date")
        required = ["open", "high", "low", "close"]
        missing = [name for name in required if name not in fields]
        if not time_key or missing:
            raise ValueError("Columnas requeridas: time/datetime, open, high, low, close. Faltan: " + ", ".join(missing))
        candles = []
        for row_num, row in enumerate(reader, start=2):
            try:
                candle = {"time": row[time_key].strip()}
                for name in required:
                    candle[name] = float(row[fields[name]])
                if not all(math.isfinite(candle[k]) for k in required):
                    raise ValueError("valor no finito")
                if candle["high"] < candle["low"]:
                    raise ValueError("high menor que low")
                candles.append(candle)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Fila {row_num} inválida: {exc}") from exc
    candles.sort(key=lambda item: item["time"])
    if len(candles) < 100:
        raise ValueError(f"Solo hay {len(candles)} velas; se recomiendan al menos 100.")
    if any(candles[i]["time"] == candles[i - 1]["time"] for i in range(1, len(candles))):
        raise ValueError("Hay fechas duplicadas; limpia el CSV antes de probar.")
    return candles


def build_signal(window):
    closes = [c["close"] for c in window]
    e20, e50 = ema(closes, 20), ema(closes, 50)
    r14, a14, x14 = rsi(closes, 14), atr(window, 14), adx(window, 14)
    m_line, m_signal, m_hist = macd(closes)
    _, bb_mid, _ = bollinger(closes, 20, 2.0)
    if any(v is None for v in (e20, e50, r14, a14, x14, m_line, m_signal, m_hist, bb_mid)) or a14 <= 0:
        return None
    entry = closes[-1]
    buy = sum((e20 > e50, 52 <= r14 <= 68, m_hist > 0, entry > bb_mid))
    sell = sum((e20 < e50, 32 <= r14 <= 48, m_hist < 0, entry < bb_mid))
    if x14 >= 18 and buy >= 3 and buy > sell:
        return {"side": "BUY", "entry": entry, "sl": entry - 1.5 * a14,
                "tp": entry + 3.0 * a14, "adx": x14, "score": buy}
    if x14 >= 18 and sell >= 3 and sell > buy:
        return {"side": "SELL", "entry": entry, "sl": entry + 1.5 * a14,
                "tp": entry - 3.0 * a14, "adx": x14, "score": sell}
    return None


def run_backtest(candles, expiry=24, variant='BASELINE', cost_per_trade_r=0.0):
    trades = []
    i = 99
    while i < len(candles) - 1:
        # Do not label a trade EXPIRED unless the full expiry horizon exists.
        # A dataset ending early leaves that would-be trade unobserved.
        if i + expiry >= len(candles):
            break
        signal = build_signal(candles[:i + 1])
        if not signal:
            i += 1
            continue
        # Experimental TEST-only filter: rejected signals do not block later entries.
        if variant == "XAU_BUY_SCORE3" and not (
                signal["side"] == "BUY" and signal["score"] == 3):
            i += 1
            continue
        outcome = "EXPIRED"
        exit_index = i + expiry
        exit_price = candles[exit_index]["close"]
        for j in range(i + 1, i + expiry + 1):
            bar = candles[j]
            if signal["side"] == "BUY":
                sl_hit, tp_hit = bar["low"] <= signal["sl"], bar["high"] >= signal["tp"]
            else:
                sl_hit, tp_hit = bar["high"] >= signal["sl"], bar["low"] <= signal["tp"]
            if sl_hit:   # conservative tie-break when both levels touch
                outcome, exit_price, exit_index = "SL", signal["sl"], j
                break
            if tp_hit:
                outcome, exit_price, exit_index = "TP", signal["tp"], j
                break
        if outcome == "TP":
            result_r = 2.0
        elif outcome == "SL":
            result_r = -1.0
        else:
            initial_risk = abs(signal["entry"] - signal["sl"])
            if not math.isfinite(initial_risk) or initial_risk <= 0:
                raise ValueError("Riesgo inicial inválido: entry y stop_loss deben ser distintos.")
            directional_move = (exit_price - signal["entry"] if signal["side"] == "BUY"
                                else signal["entry"] - exit_price)
            result_r = directional_move / initial_risk
        trades.append({
            "signal_time": candles[i]["time"], "exit_time": candles[exit_index]["time"],
            "side": signal["side"], "entry": round(signal["entry"], 8),
            "stop_loss": round(signal["sl"], 8), "take_profit": round(signal["tp"], 8),
            "exit_price": round(exit_price, 8),
            "outcome": outcome, "result_r": round(result_r, 6), "adx": round(signal["adx"], 3),
            "score": signal["score"]
        })
        i = exit_index + 1  # single open position; no overlapping trades

    if not math.isfinite(cost_per_trade_r) or cost_per_trade_r < 0:
        raise ValueError("El coste por operación debe ser un número finito >= 0 R.")
    net_after_cost = sum(t["result_r"] - cost_per_trade_r for t in trades)
    cost_equity = cost_peak = cost_max_dd = 0.0
    for trade in trades:
        cost_equity += trade["result_r"] - cost_per_trade_r
        cost_peak = max(cost_peak, cost_equity)
        cost_max_dd = max(cost_max_dd, cost_peak - cost_equity)
    wins = sum(t["outcome"] == "TP" for t in trades)
    losses = sum(t["outcome"] == "SL" for t in trades)
    expired = sum(t["outcome"] == "EXPIRED" for t in trades)
    resolved = wins + losses
    equity = peak = max_dd = 0.0
    for trade in trades:
        equity += trade["result_r"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    summary = {
        "variant": variant, "candles": len(candles), "signals_traded": len(trades),
        "wins_tp": wins, "losses_sl": losses, "expired": expired,
        "win_rate_resolved_pct": round(100 * wins / resolved, 2) if resolved else None,
        "net_result_r": round(sum(t["result_r"] for t in trades), 3),
        "average_r_per_trade": round(sum(t["result_r"] for t in trades) / len(trades), 3) if trades else None,
        "max_drawdown_r": round(max_dd, 3),
        "cost_per_trade_r": round(cost_per_trade_r, 6),
        "net_after_cost_r": round(net_after_cost, 3),
        "max_drawdown_after_cost_r": round(cost_max_dd, 3),
        "note": "Prueba histórica offline; caducadas valoradas al cierre de la vela 24 (mark-to-market). No incluye spread, comisión, slippage ni ejecución real. Señales sin horizonte completo se omiten. SL se prioriza si SL y TP tocan en la misma vela."
    }
    return {"summary": summary, "trades": trades}


def main():
    parser = argparse.ArgumentParser(description="Backtest offline del motor actual Vantage.")
    parser.add_argument("csv_file", help="CSV OHLC exportado; no se realizan llamadas externas.")
    parser.add_argument("--expiry", type=int, default=24, help="Velas máximas para alcanzar SL/TP (default: 24).")
    parser.add_argument("--variant", choices=["BASELINE", "XAU_BUY_SCORE3"], default="BASELINE", help="Filtro experimental; solo TEST.")
    parser.add_argument("--cost-r", type=float, default=0.0, help="Coste hipotético por operación expresado en R; no representa costes reales del broker.")
    parser.add_argument("--out", default="backtest_result.json", help="Archivo JSON de salida.")
    args = parser.parse_args()
    if args.expiry < 1:
        parser.error("--expiry debe ser mayor que cero.")
    candles = load_csv(Path(args.csv_file))
    result = run_backtest(candles, args.expiry, args.variant, args.cost_r)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(f"Detalle de operaciones guardado en: {args.out}")


if __name__ == "__main__":
    main()
