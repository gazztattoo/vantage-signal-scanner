"""Balanced signal gate using ADX, MACD histogram and RSI."""
from math import isfinite


def evaluate_three_indicator_filter(*, adx14, macd_hist, rsi14):
    """Validate with 2/3 confluence, ADX>=18 and no double momentum opposition."""
    try:
        adx14, macd_hist, rsi14 = (float(v) for v in (adx14, macd_hist, rsi14))
        if not all(isfinite(v) for v in (adx14, macd_hist, rsi14)):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        raise ValueError("ADX, MACD y RSI deben ser valores numéricos válidos.")

    adx_ok = adx14 >= 18
    buy_checks = {
        "ADX >= 18": adx_ok,
        "MACD positivo": macd_hist > 0,
        "RSI 50-70": 50 <= rsi14 <= 70,
    }
    sell_checks = {
        "ADX >= 18": adx_ok,
        "MACD negativo": macd_hist < 0,
        "RSI 30-50": 30 <= rsi14 <= 50,
    }
    buy_score = sum(buy_checks.values())
    sell_score = sum(sell_checks.values())

    # Two confirmations are enough, but MACD and RSI cannot both oppose the trade.
    buy_candidate = adx_ok and buy_score >= 2 and not (macd_hist < 0 and rsi14 < 50)
    sell_candidate = adx_ok and sell_score >= 2 and not (macd_hist > 0 and rsi14 > 50)

    signal = "ESPERAR"
    reason = "Falta fuerza ADX o confluencia direccional suficiente."
    if buy_candidate and not sell_candidate:
        signal = "COMPRA"
        reason = f"Confluencia alcista 2/3 o superior. ADX {adx14:.1f}"
    elif sell_candidate and not buy_candidate:
        signal = "VENTA"
        reason = f"Confluencia bajista 2/3 o superior. ADX {adx14:.1f}"
    elif buy_candidate and sell_candidate:
        reason = "Lecturas mixtas: no hay dirección dominante."

    selected_score = buy_score if signal == "COMPRA" else sell_score if signal == "VENTA" else max(buy_score, sell_score)
    selected_quality = min(100, selected_score * 30 + (10 if adx_ok else 0))
    quality_grade = (
        "A+" if selected_score == 3 and adx14 >= 25
        else "A" if selected_score == 3
        else "B" if selected_score == 2
        else "C"
    )
    if signal == "ESPERAR":
        reason += f" Confluencia: {selected_score}/3."

    return {
        "signal": signal, "reason": reason,
        "buy_checks": buy_checks, "sell_checks": sell_checks,
        "buy_score": buy_score, "sell_score": sell_score,
        "selected_score": selected_score,
        "quality_score": selected_quality, "quality_grade": quality_grade,
    }
