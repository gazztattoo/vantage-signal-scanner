"""Compact signal gate using only ADX, MACD histogram and RSI."""
from math import isfinite


def evaluate_three_indicator_filter(*, adx14, macd_hist, rsi14):
    """Return signal and confluence scores; no EMA/Bollinger dependencies."""
    values = (adx14, macd_hist, rsi14)
    try:
        adx14, macd_hist, rsi14 = (float(value) for value in values)
        if not all(isfinite(value) for value in (adx14, macd_hist, rsi14)):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        raise ValueError("ADX, MACD y RSI deben ser valores numéricos válidos.")

    buy_checks = {
        "ADX >= 20": adx14 >= 20,
        "MACD positivo": macd_hist > 0,
        "RSI 50-70": 50 <= rsi14 <= 70,
    }
    sell_checks = {
        "ADX >= 20": adx14 >= 20,
        "MACD negativo": macd_hist < 0,
        "RSI 30-50": 30 <= rsi14 <= 50,
    }
    buy_score = sum(buy_checks.values())
    sell_score = sum(sell_checks.values())

    signal = "ESPERAR"
    reason = "Filtro de 3 indicadores: falta fuerza o confirmación de momentum."
    if buy_score == 3 and buy_score > sell_score:
        signal = "COMPRA"
        reason = f"Confirmación alcista ADX + MACD + RSI. ADX {adx14:.1f}"
    elif sell_score == 3 and sell_score > buy_score:
        signal = "VENTA"
        reason = f"Confirmación bajista ADX + MACD + RSI. ADX {adx14:.1f}"

    selected_score = buy_score if signal == "COMPRA" else sell_score if signal == "VENTA" else max(buy_score, sell_score)
    selected_quality = selected_score * 30 + (10 if adx14 >= 20 else 0)
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
