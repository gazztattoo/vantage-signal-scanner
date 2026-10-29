
import os
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={
    r"/api/*": {
        "origins": ["https://gazztattoo.github.io"]
    }
})

API_KEY = os.environ.get("TWELVE_DATA_API_KEY")
TWELVE_DATA_URL = "https://api.twelvedata.com"

ALLOWED_SYMBOLS = {"EUR/USD", "XAU/USD", "BTC/USD"}
ALLOWED_INTERVALS = {"5min", "15min", "30min"}

CANARY_TZ = ZoneInfo("Atlantic/Canary")

INTERVAL_MINUTES = {
    "5min": 5,
    "15min": 15,
    "30min": 30,
}


def get_symbol(raw):
    symbol = (raw or "").strip().upper()
    return symbol if symbol in ALLOWED_SYMBOLS else None


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

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (
            avg_gain * (period - 1) + gains[i]
        ) / period
        avg_loss = (
            avg_loss * (period - 1) + losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def atr(candles, period=14):
    if len(candles) <= period:
        return None

    trs = []

    for i in range(1, len(candles)):
        current = candles[i]
        previous_close = float(candles[i - 1]["close"])

        high = float(current["high"])
        low = float(current["low"])

        trs.append(max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        ))

    value = sum(trs[:period]) / period

    for tr in trs[period:]:
        value = (
            value * (period - 1) + tr
        ) / period

    return value


def parse_candle_time(raw_time):
    """Convierte la fecha de la vela a hora consciente de zona horaria."""
    if not raw_time:
        raise RuntimeError(
            "El proveedor no devolvió la fecha de la última vela"
        )

    try:
        parsed = datetime.fromisoformat(
            str(raw_time).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise RuntimeError(
            "No se pudo interpretar la fecha de la vela"
        ) from exc

    # Si no incluye zona horaria, interpretarla en la zona
    # solicitada al proveedor.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CANARY_TZ)

    return parsed


def fetch_candles(symbol, interval):
    if not API_KEY:
        raise RuntimeError(
            "Falta configurar TWELVE_DATA_API_KEY en Render"
        )

    response = requests.get(
        f"{TWELVE_DATA_URL}/time_series",
        params={
            "symbol": symbol,
            "interval": interval,
            "outputsize": 100,
            "apikey": API_KEY,
            "order": "ASC",
            "timezone": "Atlantic/Canary",
        },
        timeout=20,
    )

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "Twelve Data devolvió una respuesta no válida"
        ) from exc

    if response.status_code != 200 or "values" not in data:
        message = (
            data.get("message")
            or data.get("code")
            or "No se pudieron obtener velas históricas"
        )
        raise RuntimeError(str(message))

    values = data["values"]

    if not values:
        raise RuntimeError(
            "Twelve Data no devolvió velas"
        )

    # Orden cronológico por fecha de apertura.
    values = sorted(
        values,
        key=lambda item: item.get("datetime", "")
    )

    candles = []
    valid_times = []

    for item in values:
        try:
            candle = {
                key: float(item[key])
                for key in ("open", "high", "low", "close")
            }
            candle_time = parse_candle_time(item["datetime"])
        except (KeyError, TypeError, ValueError):
            continue

        candles.append(candle)
        valid_times.append(candle_time)

    if len(candles) < 55:
        raise RuntimeError(
            "Datos insuficientes para calcular EMA 50 "
            f"({len(candles)} velas válidas)"
        )

    latest_time = valid_times[-1]
    now = datetime.now(timezone.utc)

    age_seconds = (
        now - latest_time.astimezone(timezone.utc)
    ).total_seconds()

    if age_seconds < -120:
        raise RuntimeError(
            "La fecha de la última vela está en el futuro. "
            "Comprueba la zona horaria del proveedor."
        )

    # Tolerancia para la vela en curso y retrasos del proveedor.
    # Si la vela supera este límite, no se genera una señal.
    max_age_seconds = (
        INTERVAL_MINUTES[interval] * 60 * 2 + 90
    )

    if age_seconds > max_age_seconds:
        age_minutes = int(age_seconds // 60)
        raise RuntimeError(
            f"Datos desactualizados: la última vela comenzó "
            f"hace aproximadamente {age_minutes} minutos. "
            "No se genera una señal con estos datos. "
            "Inténtalo más tarde."
        )

    return (
        candles,
        latest_time.astimezone(CANARY_TZ).isoformat(),
        round(max(age_seconds, 0) / 60, 2),
    )


@app.route("/")
def home():
    return jsonify({
        "app": "Vantage Signal Scanner API",
        "status": "online",
        "message": "Servidor preparado",
    })


@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "analysis": True,
    })


@app.route("/api/price")
def price():
    symbol = get_symbol(request.args.get("symbol"))

    if not symbol:
        return jsonify({
            "error": "Símbolo no permitido",
            "allowed_symbols": sorted(ALLOWED_SYMBOLS),
        }), 400

    if not API_KEY:
        return jsonify({
            "error": "Falta configurar la clave de Twelve Data"
        }), 503

    try:
        response = requests.get(
            f"{TWELVE_DATA_URL}/price",
            params={
                "symbol": symbol,
                "apikey": API_KEY,
            },
            timeout=15,
        )

        data = response.json()

        if response.status_code != 200 or "price" not in data:
            return jsonify({
                "error": data.get(
                    "message",
                    "No se pudo obtener el precio",
                )
            }), 502

        return jsonify({
            "symbol": symbol,
            "price": float(data["price"]),
        })

    except (requests.RequestException, ValueError):
        return jsonify({
            "error": "No se pudo consultar el proveedor de precios"
        }), 502


@app.route("/api/analysis")
def analysis():
    symbol = get_symbol(request.args.get("symbol"))
    interval = (
        request.args.get("interval") or "15min"
    ).strip()

    if not symbol:
        return jsonify({
            "error": "Símbolo no permitido",
            "allowed_symbols": sorted(ALLOWED_SYMBOLS),
        }), 400

    if interval not in ALLOWED_INTERVALS:
        return jsonify({
            "error": "Intervalo no permitido",
            "allowed_intervals": sorted(ALLOWED_INTERVALS),
        }), 400

    try:
        candles, candle_time, candle_age_minutes = fetch_candles(
            symbol,
            interval,
        )

        closes = [candle["close"] for candle in candles]

        ema20 = ema(closes, 20)
        ema50 = ema(closes, 50)
        rsi14 = rsi(closes, 14)
        atr14 = atr(candles, 14)

        entry = closes[-1]

        if (
            None in (ema20, ema50, rsi14, atr14)
            or atr14 <= 0
        ):
            raise RuntimeError(
                "No hay suficientes datos válidos "
                "para calcular los indicadores"
            )

        # Reglas técnicas experimentales.
        signal = "ESPERAR"
        reason = (
            "Los indicadores no muestran una combinación clara."
        )

        if ema20 > ema50 and 50 <= rsi14 < 70:
            signal = "COMPRA"
            reason = (
                "EMA 20 por encima de EMA 50 y RSI "
                "en zona de impulso alcista."
            )

        elif ema20 < ema50 and 30 < rsi14 <= 50:
            signal = "VENTA"
            reason = (
                "EMA 20 por debajo de EMA 50 y RSI "
                "en zona de impulso bajista."
            )

        stop_loss = None
        take_profit = None

        if signal == "COMPRA":
            stop_loss = entry - 1.5 * atr14
            take_profit = entry + 3.0 * atr14

        elif signal == "VENTA":
            stop_loss = entry + 1.5 * atr14
            take_profit = entry - 3.0 * atr14

        return jsonify({
            "symbol": symbol,
            "interval": interval,
            "signal": signal,
            "reason": reason,
            "price": entry,
            "entry": entry if signal != "ESPERAR" else None,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "ema20": ema20,
            "ema50": ema50,
            "rsi14": rsi14,
            "atr14": atr14,
            "candle_time": candle_time,
            "candle_age_minutes": candle_age_minutes,
            "candles": len(candles),
            "method": (
                "EMA20/EMA50 + RSI14; "
                "SL=1.5x ATR; TP=3x ATR"
            ),
            "warning": (
                "Señal técnica experimental. Verifica datos, "
                "spread y riesgo antes de operar. "
                "No ejecuta órdenes."
            ),
        })

    except requests.RequestException:
        return jsonify({
            "error": (
                "No se pudo conectar con Twelve Data. "
                "Inténtalo más tarde."
            )
        }), 502

    except RuntimeError as exc:
        return jsonify({
            "error": str(exc)
        }), 502

    except (ValueError, KeyError, TypeError):
        return jsonify({
            "error": "No se pudieron procesar los datos recibidos"
        }), 502


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
    )
