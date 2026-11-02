
import os
import json
import time
import requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, request
from flask_cors import CORS
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import hmac

app = Flask(__name__)
CORS(app, resources={
    r"/api/*": {
        "origins": ["https://gazztattoo.github.io", "https://vantage-signal-scanner.onrender.com"],
        "allow_headers": ["Content-Type", "Authorization"],
        "methods": ["GET", "POST", "OPTIONS"]
    }
})

API_KEY = os.environ.get("TWELVE_DATA_API_KEY")
PANEL_ACCESS_CODE = os.environ.get("PANEL_ACCESS_CODE", "")
ACCESS_TOKEN_SECRET = os.environ.get("ACCESS_TOKEN_SECRET", "")
TOKEN_MAX_AGE_SECONDS = 12 * 60 * 60

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "").strip()

def supabase_headers():
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        raise RuntimeError("El almacenamiento de notificaciones no está configurado.")
    return {
        "apikey": SUPABASE_SECRET_KEY,
        "Content-Type": "application/json",
    }

def send_web_push(subscription, payload):
    if not VAPID_PUBLIC_KEY or not VAPID_PRIVATE_KEY or not VAPID_SUBJECT:
        raise RuntimeError("Falta configurar VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY o VAPID_SUBJECT.")
    from pywebpush import webpush
    from py_vapid import Vapid
    # Render guarda la clave privada como PEM en una variable de entorno.
    # Convertirla a un objeto Vapid evita que pywebpush la interprete como DER.
    if "-----BEGIN" in VAPID_PRIVATE_KEY:
        vapid_key = Vapid.from_pem(VAPID_PRIVATE_KEY.encode("utf-8"))
    else:
        vapid_key = Vapid.from_string(VAPID_PRIVATE_KEY)
    return webpush(
        subscription_info=subscription,
        data=json.dumps(payload, ensure_ascii=False),
        vapid_private_key=vapid_key,
        vapid_claims={"sub": VAPID_SUBJECT},
    )


def access_serializer():
    if not ACCESS_TOKEN_SECRET:
        raise RuntimeError("Falta configurar ACCESS_TOKEN_SECRET en Render")
    return URLSafeTimedSerializer(ACCESS_TOKEN_SECRET, salt="global-signal-panel-access-v1")


@app.route("/api/access", methods=["POST"])
def create_access_token():
    if not PANEL_ACCESS_CODE or not ACCESS_TOKEN_SECRET:
        return jsonify({"error": "El acceso privado aún no está configurado en el servidor."}), 503
    payload = request.get_json(silent=True) or {}
    supplied_code = str(payload.get("code", ""))
    if not hmac.compare_digest(supplied_code, PANEL_ACCESS_CODE):
        return jsonify({"error": "Código de acceso incorrecto."}), 401
    token = access_serializer().dumps({"access": True})
    return jsonify({"token": token, "expires_in": TOKEN_MAX_AGE_SECONDS})


@app.before_request
def protect_signal_endpoints():
    if request.method == "OPTIONS":
        return None
    if request.path not in ("/api/analysis", "/api/price", "/api/history", "/api/performance", "/api/lab-status", "/api/push/subscribe", "/api/push/test", "/api/push/unsubscribe"):
        return None
    if not ACCESS_TOKEN_SECRET:
        return jsonify({"error": "El acceso privado no está configurado."}), 503
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return jsonify({"error": "Acceso requerido. Introduce el código del panel."}), 401
    token = authorization[7:].strip()
    try:
        data = access_serializer().loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
        if data.get("access") is not True:
            raise BadSignature("Token no válido")
    except (BadSignature, SignatureExpired, RuntimeError):
        return jsonify({"error": "Sesión caducada o no válida. Vuelve a introducir el código."}), 401
    return None
TWELVE_DATA_URL = "https://api.twelvedata.com"

ALLOWED_SYMBOLS = {"EUR/USD", "XAU/USD", "BTC/USD"}
ALLOWED_INTERVALS = {"5min", "15min", "30min"}

CANARY_TZ = ZoneInfo("Atlantic/Canary")
NEW_YORK_TZ = ZoneInfo("America/New_York")


def vantage_server_time(now):
    """Convierte UTC a la hora del servidor Vantage (GMT+2/GMT+3 por DST de EE. UU.)."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    utc_now = now.astimezone(timezone.utc)
    new_york_dst = utc_now.astimezone(NEW_YORK_TZ).dst()
    server_offset = 3 if new_york_dst and new_york_dst.total_seconds() else 2
    server_tz = timezone(timedelta(hours=server_offset))
    return utc_now.astimezone(server_tz), server_offset


def market_session(symbol, now=None):
    """Comprueba las sesiones MT5 observadas para los símbolos Vantage del panel."""
    now = now or datetime.now(timezone.utc)
    server_now, server_offset = vantage_server_time(now)
    server_time_label = server_now.isoformat()

    if symbol == "BTC/USD":
        return {
            "open": True,
            "status": "ABIERTO",
            "reason": (
                "MT5 muestra sesiones de cotización 00:00-24:00 todos los días; "
                "la captura no separa una sesión de trading."
            ),
            "server_time": server_time_label,
            "server_utc_offset": f"UTC+{server_offset}",
        }

    schedules = {
        # (hora de apertura, cierre lun-jue, cierre viernes), en minutos del servidor.
        "EUR/USD": (1, 23 * 60 + 58, 23 * 60 + 57),
        "XAU/USD": (61, 23 * 60 + 58, 23 * 60 + 57),
    }
    if symbol not in schedules:
        return {
            "open": False,
            "status": "MERCADO CERRADO",
            "reason": "Símbolo sin horario Vantage configurado.",
            "server_time": server_time_label,
            "server_utc_offset": f"UTC+{server_offset}",
        }

    weekday = server_now.weekday()  # lunes=0, domingo=6
    if weekday >= 5:
        return {
            "open": False,
            "status": "MERCADO CERRADO",
            "reason": "Fin de semana según la sesión MT5 de Vantage.",
            "server_time": server_time_label,
            "server_utc_offset": f"UTC+{server_offset}",
        }

    start_minute, weekday_close, friday_close = schedules[symbol]
    close_minute = friday_close if weekday == 4 else weekday_close
    seconds_since_midnight = (
        server_now.hour * 3600 + server_now.minute * 60 + server_now.second
    )
    is_open = (
        start_minute * 60 <= seconds_since_midnight < close_minute * 60
    )

    if is_open:
        reason = "Dentro de la sesión de trading mostrada en MT5 Vantage."
    elif seconds_since_midnight < start_minute * 60:
        reason = "Pausa diaria: todavía no ha comenzado la sesión MT5."
    else:
        reason = "Pausa diaria o cierre semanal: la sesión MT5 ya terminó."

    return {
        "open": is_open,
        "status": "ABIERTO" if is_open else "MERCADO CERRADO",
        "reason": reason,
        "server_time": server_time_label,
        "server_utc_offset": f"UTC+{server_offset}",
    }


@app.route("/api/market-status", methods=["GET"])
def market_status():
    now = datetime.now(timezone.utc)
    return jsonify({
        "checked_at": now.isoformat(),
        "markets": {symbol: market_session(symbol, now) for symbol in sorted(ALLOWED_SYMBOLS)}
    })


INTERVAL_MINUTES = {
    "5min": 5,
    "15min": 15,
    "30min": 30,
}

# Caché en memoria compartida por las peticiones del proceso API.
# Los TTL dejan margen antes del siguiente ciclo programado.
ANALYSIS_CACHE_TTL_SECONDS = {
    "5min": 18 * 60,
    "15min": 28 * 60,
    "30min": 55 * 60,
}
ANALYSIS_CACHE = {}


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


def macd(values, fast=12, slow=26, signal_period=9):
    if len(values) < slow + signal_period:
        return None, None, None
    line = [ema(values[:i + 1], fast) - ema(values[:i + 1], slow)
            for i in range(slow - 1, len(values))]
    signal_line = ema(line, signal_period)
    return line[-1], signal_line, line[-1] - signal_line

def bollinger(values, period=20, deviations=2.0):
    if len(values) < period: return None, None, None
    window = values[-period:]
    middle = sum(window) / period
    variance = sum((v-middle)**2 for v in window) / period
    width = deviations * variance ** 0.5
    return middle + width, middle, middle - width

def adx(candles, period=14):
    """ADX con suavizado de Wilder para TR, +DM, -DM y DX."""
    if len(candles) < (period * 2 + 1):
        return None

    trs, plus_dm, minus_dm = [], [], []
    for i in range(1, len(candles)):
        cur, prev = candles[i], candles[i - 1]
        up_move = cur["high"] - prev["high"]
        down_move = prev["low"] - cur["low"]
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
        trs.append(max(
            cur["high"] - cur["low"],
            abs(cur["high"] - prev["close"]),
            abs(cur["low"] - prev["close"]),
        ))

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
        dx_values.append(
            100.0 * abs(plus_di - minus_di) / denominator
            if denominator > 0 else 0.0
        )

    if len(dx_values) < period:
        return None

    adx_value = sum(dx_values[:period]) / period
    for dx_value in dx_values[period:]:
        adx_value = ((adx_value * (period - 1)) + dx_value) / period
    return adx_value

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

        candle["time"] = candle_time.astimezone(CANARY_TZ).isoformat()
        candles.append(candle)
        valid_times.append(candle_time)

    # Excluir siempre la vela que todavía está formándose.
    # Twelve Data entrega datetime como hora de apertura de cada vela.
    now = datetime.now(timezone.utc)
    interval_seconds = INTERVAL_MINUTES[interval] * 60
    closed = [
        (candle, candle_time)
        for candle, candle_time in zip(candles, valid_times)
        if candle_time.astimezone(timezone.utc).timestamp() + interval_seconds <= now.timestamp()
    ]
    if len(closed) < 55:
        raise RuntimeError(
            "Datos insuficientes después de excluir la vela en curso "
            f"({len(closed)} velas cerradas)"
        )
    candles = [item[0] for item in closed]
    valid_times = [item[1] for item in closed]

    latest_time = valid_times[-1]

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

    cache_key = (symbol, interval)
    cached = ANALYSIS_CACHE.get(cache_key)
    cache_age = time.monotonic() - cached["stored_at"] if cached else None
    if cached and cache_age < ANALYSIS_CACHE_TTL_SECONDS[interval]:
        payload = dict(cached["payload"])
        payload["cache"] = {"hit": True, "age_seconds": int(cache_age)}
        return jsonify(payload)

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
        adx14 = adx(candles, 14)
        macd_line, macd_signal, macd_hist = macd(closes)
        bb_upper, bb_middle, bb_lower = bollinger(closes, 20, 2.0)

        entry = closes[-1]

        if (
            None in (ema20, ema50, rsi14, atr14, adx14, macd_line, macd_signal, macd_hist, bb_upper, bb_middle, bb_lower)
            or atr14 <= 0
        ):
            raise RuntimeError(
                "No hay suficientes datos válidos "
                "para calcular los indicadores"
            )

        # Motor multicriterio experimental: tendencia, fuerza y momentum.
        buy_checks = {'EMA20 > EMA50': ema20 > ema50, 'RSI 52-68': 52 <= rsi14 <= 68, 'MACD positivo': macd_hist > 0, 'Precio sobre media Bollinger': entry > bb_middle}
        sell_checks = {'EMA20 < EMA50': ema20 < ema50, 'RSI 32-48': 32 <= rsi14 <= 48, 'MACD negativo': macd_hist < 0, 'Precio bajo media Bollinger': entry < bb_middle}
        buy_score = sum(buy_checks.values())
        sell_score = sum(sell_checks.values())
        signal = 'ESPERAR'
        reason = 'Confluencia insuficiente o tendencia débil; se espera.'
        if adx14 >= 18 and buy_score >= 3 and buy_score > sell_score:
            signal = 'COMPRA'
            reason = 'Confluencia alcista: ' + ', '.join(k for k,v in buy_checks.items() if v) + '. ADX ' + str(round(adx14,1))
        elif adx14 >= 18 and sell_score >= 3 and sell_score > buy_score:
            signal = 'VENTA'
            reason = 'Confluencia bajista: ' + ', '.join(k for k,v in sell_checks.items() if v) + '. ADX ' + str(round(adx14,1))
        stop_loss = None
        take_profit = None

        if signal == "COMPRA":
            stop_loss = entry - 1.5 * atr14
            take_profit = entry + 3.0 * atr14

        elif signal == "VENTA":
            stop_loss = entry + 1.5 * atr14
            take_profit = entry - 3.0 * atr14

        payload = {
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
            "adx14": adx14,
            "macd": macd_line,
            "macd_signal": macd_signal,
            "macd_histogram": macd_hist,
            "bollinger_upper": bb_upper,
            "bollinger_middle": bb_middle,
            "bollinger_lower": bb_lower,
            "buy_score": buy_score,
            "sell_score": sell_score,
            "score_max": 4,
            "candle_time": candle_time,
            "candle_age_minutes": candle_age_minutes,
            "candles": len(candles),
            "recent_candles": [
                {"time": candle["time"], "high": candle["high"], "low": candle["low"], "close": candle["close"]}
                for candle in candles
            ],
            "method": (
                "EMA20/EMA50 + RSI14 + MACD + ADX14 + Bollinger(20,2); "
                "score mínimo 3/4 y ADX>=18; SL=1.5x ATR; TP=3x ATR"
            ),
            "warning": (
                "Señal técnica experimental. Verifica datos, "
                "spread y riesgo antes de operar. "
                "No ejecuta órdenes."
            ),
            "cache": {"hit": False, "age_seconds": 0},
        }
        ANALYSIS_CACHE[cache_key] = {
            "stored_at": time.monotonic(),
            "payload": payload,
        }
        return jsonify(payload)

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



@app.route("/api/history", methods=["GET"])
def signal_history():
    """Devuelve el historial real de señales registradas por el escáner."""
    try:
        limit = min(max(int(request.args.get("limit", "50")), 1), 200)
    except ValueError:
        return jsonify({"error": "El límite debe ser un número."}), 400

    params = {
        "select": "id,symbol,timeframe,direction,entry_price,stop_loss,take_profit,signal_at,candle_time,status,exit_price,closed_at,result_r,result_source,indicators",
        "order": "signal_at.desc",
        "limit": str(limit),
    }
    for query_name, column in (("symbol", "symbol"), ("timeframe", "timeframe"), ("status", "status")):
        value = (request.args.get(query_name) or "").strip()
        if value:
            allowed = {
                "symbol": ALLOWED_SYMBOLS,
                "timeframe": ALLOWED_INTERVALS,
                "status": {"PENDIENTE", "TP", "SL", "CADUCADA", "CANCELADA"},
            }[query_name]
            if value not in allowed:
                return jsonify({"error": "Filtro no permitido: " + query_name}), 400
            params[column] = "eq." + value

    candle_time = (request.args.get("candle_time") or "").strip()
    if candle_time:
        if len(candle_time) > 64:
            return jsonify({"error": "Fecha de vela no válida."}), 400
        params["candle_time"] = "eq." + candle_time

    try:
        response = requests.get(
            SUPABASE_URL + "/rest/v1/signal_history",
            params=params,
            headers=supabase_headers(),
            timeout=15,
        )
        if response.status_code != 200:
            return jsonify({"error": "No se pudo consultar el historial."}), 502
        rows = response.json()
        return jsonify({"count": len(rows), "signals": rows})
    except (RuntimeError, requests.RequestException):
        return jsonify({"error": "No se pudo conectar con el historial."}), 502


@app.route("/api/performance", methods=["GET"])
def signal_performance():
    """Estadísticas de señales simuladas, basadas exclusivamente en resultados guardados."""
    try:
        rows = []
        page_size = 1000
        offset = 0
        # Paginar para que el crecimiento del historial no corte silenciosamente
        # la muestra en el límite por defecto de PostgREST.
        while offset < 20000:
            response = requests.get(
                SUPABASE_URL + "/rest/v1/signal_history",
                params={
                    "select": "symbol,timeframe,direction,status,result_r,closed_at",
                    "order": "signal_at.asc",
                    "limit": str(page_size),
                    "offset": str(offset),
                },
                headers=supabase_headers(),
                timeout=20,
            )
            if response.status_code != 200:
                return jsonify({"error": "No se pudo consultar el historial de señales."}), 502
            batch = response.json()
            rows.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size

        wins = sum(1 for row in rows if row.get("status") == "TP")
        losses = sum(1 for row in rows if row.get("status") == "SL")
        pending = sum(1 for row in rows if row.get("status") == "PENDIENTE")
        expired = sum(1 for row in rows if row.get("status") == "CADUCADA")
        cancelled = sum(1 for row in rows if row.get("status") == "CANCELADA")
        closed = wins + losses
        r_values = [
            float(row["result_r"]) for row in rows
            if row.get("status") in ("TP", "SL") and row.get("result_r") is not None
        ]
        positive_r = sum(value for value in r_values if value > 0)
        negative_r = abs(sum(value for value in r_values if value < 0))
        average_r = (sum(r_values) / len(r_values)) if r_values else None
        breakdown = {}
        direction_breakdown = {}
        for row in rows:
            status = row.get("status")
            key = row["symbol"] + " · " + row["timeframe"]
            item = breakdown.setdefault(key, {
                "total": 0, "pending": 0, "wins": 0, "losses": 0,
                "expired": 0, "cancelled": 0, "closed": 0
            })
            item["total"] += 1
            if status == "PENDIENTE":
                item["pending"] += 1
            elif status == "TP":
                item["wins"] += 1
                item["closed"] += 1
            elif status == "SL":
                item["losses"] += 1
                item["closed"] += 1
            elif status == "CADUCADA":
                item["expired"] += 1
            elif status == "CANCELADA":
                item["cancelled"] += 1

            direction = row.get("direction") or "SIN DIRECCIÓN"
            side = direction_breakdown.setdefault(direction, {
                "total": 0, "wins": 0, "losses": 0, "closed": 0
            })
            side["total"] += 1
            if status == "TP":
                side["wins"] += 1
                side["closed"] += 1
            elif status == "SL":
                side["losses"] += 1
                side["closed"] += 1

        sample_size = len(r_values)
        sample_note = (
            "Muestra inicial: todavía hacen falta más resultados cerrados para valorar la estrategia."
            if sample_size < 30 else
            "Muestra en desarrollo: conviene acumular al menos 100 resultados y comprobar estabilidad por mercado."
            if sample_size < 100 else
            "Muestra de 100 o más resultados; sigue siendo necesario validar por mercado y en distintos periodos."
        )
        return jsonify({
            "total_signals": len(rows),
            "closed_trades": closed,
            "pending": pending,
            "expired": expired,
            "cancelled": cancelled,
            "wins": wins,
            "losses": losses,
            "win_rate": round((wins / closed) * 100, 2) if closed else None,
            "average_r": round(average_r, 4) if average_r is not None else None,
            "expectancy_r": round(average_r, 4) if average_r is not None else None,
            "profit_factor": round(positive_r / negative_r, 4) if negative_r > 0 else (None if positive_r == 0 else "inf"),
            "sample_size": sample_size,
            "sample_note": sample_note,
            "breakdown": breakdown,
            "direction_breakdown": direction_breakdown,
            "note": "Estadísticas de señales simuladas del scanner. TP/SL se evalúan con velas OHLC cerradas; no son operaciones reales ni garantizan rentabilidad.",
            "truncated": len(rows) >= 20000,
        })
    except (RuntimeError, requests.RequestException, ValueError, TypeError, KeyError):
        return jsonify({"error": "No se pudieron calcular las estadísticas."}), 502


@app.route("/api/lab-status", methods=["GET"])
def lab_status():
    """Diagnóstico real del motor y contadores persistidos en Supabase."""
    try:
        def exact_count(path, params=None):
            response = requests.get(
                SUPABASE_URL + path,
                params=params or {"select": "id"},
                headers={**supabase_headers(), "Prefer": "count=exact", "Range": "0-0"},
                timeout=15,
            )
            if response.status_code not in (200, 206):
                raise RuntimeError("No se pudo consultar el estado del laboratorio.")
            content_range = response.headers.get("Content-Range", "")
            total = content_range.rsplit("/", 1)[-1]
            return int(total) if total.isdigit() else 0

        total_signals = exact_count("/rest/v1/signal_history")
        pending_signals = exact_count(
            "/rest/v1/signal_history",
            {"select": "id", "status": "eq.PENDIENTE"},
        )
        closed_signals = exact_count(
            "/rest/v1/signal_history",
            {"select": "id", "status": "in.(TP,SL)"},
        )
        subscriptions = exact_count("/rest/v1/push_subscriptions")
        return jsonify({
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "data_provider": "Twelve Data",
            "provider_configured": bool(API_KEY),
            "storage_configured": bool(SUPABASE_URL and SUPABASE_SECRET_KEY),
            "engine": {
                "trend": "EMA 20 / EMA 50",
                "momentum": "RSI 14 + MACD 12/26/9",
                "volatility": "ATR 14 + Bollinger 20,2",
                "strength": "ADX 14 (Wilder)",
                "minimum_confluence": "3/4",
                "minimum_adx": 18,
                "risk_model": "SL 1.5 × ATR; TP 3 × ATR",
                "closed_candles_only": True,
                "automatic_execution": False,
            },
            "records": {
                "total_signals": total_signals,
                "pending_signals": pending_signals,
                "closed_signals": closed_signals,
                "push_devices": subscriptions,
            },
            "note": "Contadores exactos del almacenamiento. El laboratorio no inventa resultados ni ejecuta órdenes.",
        })
    except (RuntimeError, requests.RequestException, ValueError, TypeError):
        return jsonify({"error": "No se pudo obtener el diagnóstico real del laboratorio."}), 502


@app.route("/api/push/public-key", methods=["GET"])
def push_public_key():
    if not VAPID_PUBLIC_KEY:
        return jsonify({"error": "Las notificaciones aún no están configuradas."}), 503
    return jsonify({"public_key": VAPID_PUBLIC_KEY})


@app.route("/api/push/subscribe", methods=["POST"])
def push_subscribe():
    payload = request.get_json(silent=True) or {}
    subscription = payload.get("subscription")
    if not isinstance(subscription, dict) or not subscription.get("endpoint"):
        return jsonify({"error": "Suscripción no válida."}), 400
    try:
        response = requests.post(
            SUPABASE_URL + "/rest/v1/push_subscriptions?on_conflict=endpoint",
            headers={**supabase_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={"endpoint": subscription["endpoint"], "subscription": subscription},
            timeout=15,
        )
        if response.status_code not in (200, 201, 204):
            return jsonify({"error": "No se pudo guardar la suscripción."}), 502
        return jsonify({"ok": True})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except requests.RequestException:
        return jsonify({"error": "No se pudo conectar con el almacenamiento."}), 502


@app.route("/api/push/unsubscribe", methods=["POST"])
def push_unsubscribe():
    payload = request.get_json(silent=True) or {}
    endpoint = payload.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint:
        return jsonify({"error": "Endpoint no válido."}), 400
    try:
        response = requests.delete(
            SUPABASE_URL + "/rest/v1/push_subscriptions?endpoint=eq." + requests.utils.quote(endpoint, safe=""),
            headers=supabase_headers(),
            timeout=15,
        )
        if response.status_code not in (200, 204):
            return jsonify({"error": "No se pudo eliminar la suscripción."}), 502
        return jsonify({"ok": True})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except requests.RequestException:
        return jsonify({"error": "No se pudo conectar con el almacenamiento."}), 502


@app.route("/api/push/test", methods=["POST"])
def push_test():
    payload = request.get_json(silent=True) or {}
    endpoint = payload.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint.strip():
        return jsonify({"error": "No se indicó el dispositivo que solicita la prueba."}), 400
    try:
        response = requests.get(
            SUPABASE_URL + "/rest/v1/push_subscriptions",
            params={"select": "subscription", "endpoint": "eq." + endpoint},
            headers=supabase_headers(),
            timeout=15,
        )
        if response.status_code != 200:
            return jsonify({"error": "No se pudieron consultar las suscripciones."}), 502
        subscriptions = response.json()
        if not subscriptions:
            return jsonify({"error": "Este dispositivo no tiene una suscripción push guardada."}), 404
        sent = 0
        expired = []
        failures = []
        for row in subscriptions:
            subscription = row.get("subscription")
            if not isinstance(subscription, dict):
                continue
            try:
                send_web_push(subscription, {
                    "title": "GLOBAL SIGNAL SCANNER",
                    "body": "¡Perfecto! Las notificaciones push están funcionando en este dispositivo.",
                    "tag": "gss-push-test",
                    "url": "https://vantage-signal-scanner.onrender.com/panel_escaner_vantage.html",
                })
                sent += 1
            except Exception as exc:
                response_obj = getattr(exc, "response", None)
                status = getattr(response_obj, "status_code", None)
                detail = getattr(response_obj, "text", "") if response_obj is not None else ""
                failures.append({"status": status, "detail": str(detail)[:300] or str(exc)[:300]})
                if status in (404, 410):
                    expired.append(subscription.get("endpoint", ""))
        for endpoint in expired:
            if endpoint:
                requests.delete(
                    SUPABASE_URL + "/rest/v1/push_subscriptions?endpoint=eq." + requests.utils.quote(endpoint, safe=""),
                    headers=supabase_headers(),
                    timeout=10,
                )
        if sent == 0:
            return jsonify({
                "error": "La suscripción está guardada, pero el proveedor rechazó el envío push.",
                "subscriptions_found": len(subscriptions),
                "failures": failures,
            }), 502
        return jsonify({"ok": True, "sent": sent, "subscriptions_found": len(subscriptions), "failures": failures})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except requests.RequestException:
        return jsonify({"error": "No se pudo conectar con el almacenamiento."}), 502



@app.route("/api/push/admin-test", methods=["POST"])
def push_admin_test():
    """Prueba push administrativa, protegida por el secreto del escáner."""
    supplied = request.headers.get("X-Scanner-Cron-Secret", "")
    if not SIGNAL_CRON_SECRET or not hmac.compare_digest(supplied, SIGNAL_CRON_SECRET):
        return jsonify({"error": "No autorizado."}), 401

    try:
        response = requests.get(
            SUPABASE_URL + "/rest/v1/push_subscriptions",
            params={"select": "subscription", "limit": "100"},
            headers=supabase_headers(),
            timeout=15,
        )
        if response.status_code != 200:
            return jsonify({"error": "No se pudieron consultar las suscripciones."}), 502

        subscriptions = response.json()
        if not subscriptions:
            return jsonify({"ok": False, "subscriptions_found": 0, "sent": 0,
                            "failed": 0, "message": "No hay dispositivos suscritos."}), 404

        sent = 0
        failures = []
        for row in subscriptions:
            subscription = row.get("subscription")
            if not isinstance(subscription, dict) or not subscription.get("endpoint"):
                failures.append({"reason": "Suscripción no válida."})
                continue
            try:
                send_web_push(subscription, {
                    "title": "VANTAGE SIGNAL SCANNER",
                    "body": "Prueba de producción: las notificaciones de Vantage están llegando a este dispositivo.",
                    "tag": "vantage-production-push-test",
                    "url": "https://vantage-signal-scanner.onrender.com/panel_escaner_vantage.html",
                })
                sent += 1
            except Exception as exc:
                response_obj = getattr(exc, "response", None)
                failures.append({
                    "status": getattr(response_obj, "status_code", None),
                    "reason": str(exc)[:300],
                })

        return jsonify({
            "ok": sent > 0,
            "subscriptions_found": len(subscriptions),
            "sent": sent,
            "failed": len(failures),
            "failures": failures,
        }), (200 if sent > 0 else 502)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except requests.RequestException:
        return jsonify({"error": "No se pudo conectar con el almacenamiento."}), 502


def resolve_pending_signal_outcomes(symbol, interval, recent_candles):
    """Cierra TP/SL o caduca señales tras 24 velas cerradas posteriores."""
    if not recent_candles:
        return {"resolved": 0, "expired": 0, "errors": 0}

    response = requests.get(
        SUPABASE_URL + "/rest/v1/signal_history",
        params={
            "select": "id,direction,candle_time,stop_loss,take_profit,status",
            "symbol": "eq." + symbol,
            "timeframe": "eq." + interval,
            "status": "eq.PENDIENTE",
            "order": "candle_time.asc",
            "limit": "200",
        },
        headers=supabase_headers(), timeout=15,
    )
    if response.status_code != 200:
        return {"resolved": 0, "expired": 0, "errors": 1}

    pending = response.json()
    resolved = 0
    expired = 0
    errors = 0
    interval_seconds = INTERVAL_MINUTES[interval] * 60
    max_lifetime_candles = 24

    for signal in pending:
        try:
            signal_time = datetime.fromisoformat(str(signal["candle_time"]).replace("Z", "+00:00"))
            stop = float(signal["stop_loss"])
            target = float(signal["take_profit"])
            direction = signal["direction"]
            outcome = None
            closing_candle = None
            candles_elapsed = 0

            for candle in recent_candles:
                candle_time = datetime.fromisoformat(str(candle["time"]).replace("Z", "+00:00"))
                if candle_time <= signal_time:
                    continue

                candles_elapsed += 1
                high = float(candle["high"])
                low = float(candle["low"])
                if direction == "COMPRA":
                    hit_sl = low <= stop
                    hit_tp = high >= target
                else:
                    hit_sl = high >= stop
                    hit_tp = low <= target

                # Si ambos niveles aparecen tocados en la misma vela OHLC,
                # se aplica el criterio conservador: SL primero.
                if hit_sl:
                    outcome, closing_candle = "SL", candle
                    break
                if hit_tp:
                    outcome, closing_candle = "TP", candle
                    break
                if candles_elapsed >= max_lifetime_candles:
                    closing_candle = candle
                    outcome = "CADUCADA"
                    break

            if not outcome:
                continue

            close_time = datetime.fromisoformat(str(closing_candle["time"]).replace("Z", "+00:00"))
            closed_at = datetime.fromtimestamp(
                close_time.timestamp() + interval_seconds, timezone.utc
            ).isoformat()
            close_high = float(closing_candle["high"])
            close_low = float(closing_candle["low"])
            close_price = float(closing_candle["close"])
            is_expired = outcome == "CADUCADA"

            patch_data = {
                "status": outcome,
                "exit_price": None if is_expired else (stop if outcome == "SL" else target),
                "closed_at": closed_at,
                "result_r": None if is_expired else (2.0 if outcome == "TP" else -1.0),
                "result_source": (
                    "CADUCADA tras 24 velas cerradas posteriores a la señal; "
                    "sin tocar TP ni SL."
                    if is_expired else
                    "Velas OHLC cerradas posteriores a la señal; "
                    "si TP y SL coinciden en una vela, se cuenta SL primero."
                ),
                "closing_candle_time": close_time.astimezone(timezone.utc).isoformat(),
                "closing_candle_high": close_high,
                "closing_candle_low": close_low,
                "closing_candle_close": close_price,
                "candles_to_resolution": candles_elapsed,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            patch = requests.patch(
                SUPABASE_URL + "/rest/v1/signal_history",
                params={"id": "eq." + str(signal["id"]), "status": "eq.PENDIENTE"},
                headers={**supabase_headers(), "Prefer": "return=minimal"},
                json=patch_data,
                timeout=15,
            )
            if patch.status_code in (200, 204):
                if is_expired:
                    expired += 1
                else:
                    resolved += 1
            else:
                errors += 1
        except (KeyError, TypeError, ValueError, requests.RequestException):
            errors += 1

    return {"resolved": resolved, "expired": expired, "errors": errors}

SIGNAL_CRON_SECRET = os.environ.get("SIGNAL_CRON_SECRET", "")

def process_pending_push_deliveries(limit=100):
    """Envía la cola pendiente por dispositivo sin repetir entregas confirmadas."""
    summary = {"sent": 0, "retrying": 0, "dead": 0, "errors": 0}
    try:
        response = requests.get(
            SUPABASE_URL + "/rest/v1/push_delivery_outbox",
            params={
                "select": "id,endpoint,subscription,payload,attempts",
                "status": "eq.pending",
                "next_attempt_at": "lte." + datetime.now(timezone.utc).isoformat(),
                "order": "created_at.asc",
                "limit": str(limit),
            },
            headers=supabase_headers(),
            timeout=15,
        )
        if response.status_code != 200:
            summary["errors"] += 1
            return summary
        for item in response.json():
            row_id = item.get("id")
            endpoint = item.get("endpoint")
            attempts = int(item.get("attempts") or 0) + 1
            try:
                send_web_push(item.get("subscription") or {}, item.get("payload") or {})
                update = {
                    "status": "sent",
                    "attempts": attempts,
                    "delivered_at": datetime.now(timezone.utc).isoformat(),
                    "last_error": None,
                }
                summary["sent"] += 1
            except Exception as exc:
                response_obj = getattr(exc, "response", None)
                status_code = getattr(response_obj, "status_code", None)
                if status_code in (404, 410):
                    update = {
                        "status": "dead", "attempts": attempts,
                        "last_error": "Suscripción caducada (HTTP %s)." % status_code,
                    }
                    summary["dead"] += 1
                    if endpoint:
                        requests.delete(
                            SUPABASE_URL + "/rest/v1/push_subscriptions",
                            params={"endpoint": "eq." + endpoint},
                            headers=supabase_headers(), timeout=10,
                        )
                elif attempts >= 8:
                    update = {
                        "status": "dead", "attempts": attempts,
                        "last_error": "Máximo de 8 intentos alcanzado: " + str(exc)[:500],
                    }
                    summary["dead"] += 1
                else:
                    delay_minutes = min(5 * (2 ** (attempts - 1)), 360)
                    update = {
                        "status": "pending", "attempts": attempts,
                        "next_attempt_at": (
                            datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)
                        ).isoformat(),
                        "last_error": str(exc)[:500],
                    }
                    summary["retrying"] += 1
            saved = requests.patch(
                SUPABASE_URL + "/rest/v1/push_delivery_outbox",
                params={"id": "eq." + str(row_id)},
                headers={**supabase_headers(), "Prefer": "return=minimal"},
                json=update,
                timeout=15,
            )
            if saved.status_code not in (200, 204):
                summary["errors"] += 1
        return summary
    except (RuntimeError, requests.RequestException, ValueError, TypeError):
        summary["errors"] += 1
        return summary


@app.route("/api/push/scan", methods=["POST"])
def push_scan():
    supplied = request.headers.get("X-Scanner-Cron-Secret", "")
    if not SIGNAL_CRON_SECRET or not hmac.compare_digest(supplied, SIGNAL_CRON_SECRET):
        return jsonify({"error": "No autorizado."}), 401

    results = []
    sent_total = 0
    token = access_serializer().dumps({"access": True})
    intervals = ["5min", "15min", "30min"]
    symbols = ["EUR/USD", "XAU/USD", "BTC/USD"]

    # El escáner programado procesa una combinación por petición para respetar
    # el límite HTTP de la plataforma. El runner invoca las 9 combinaciones
    # secuencialmente; cada petición queda aislada y puede reintentarse.
    requested_symbol = request.args.get("symbol")
    requested_interval = request.args.get("interval")
    if requested_symbol and requested_symbol not in symbols:
        return jsonify({"error": "Símbolo no permitido."}), 400
    if requested_interval and requested_interval not in intervals:
        return jsonify({"error": "Temporalidad no permitida."}), 400
    selected_symbols = [requested_symbol] if requested_symbol else symbols
    selected_intervals = [requested_interval] if requested_interval else intervals

    with app.test_client() as client:
        for symbol, interval in (
            (s, tf) for tf in selected_intervals for s in selected_symbols
        ):
            session = market_session(symbol)
            if not session["open"]:
                results.append({
                    "symbol": symbol, "interval": interval,
                    "status": "market_closed",
                    "detail": "Mercado cerrado: no se analiza ni se envían alertas."
                })
                continue

            response = client.get(
                "/api/analysis",
                query_string={"symbol": symbol, "interval": interval},
                headers={"Authorization": "Bearer " + token},
            )
            if response.status_code != 200:
                results.append({
                    "symbol": symbol, "interval": interval,
                    "status": "analysis_error",
                    "detail": (response.get_json(silent=True) or {}).get("error", "Error de análisis"),
                })
            else:
                data = response.get_json() or {}
                outcome_result = resolve_pending_signal_outcomes(
                    symbol, interval, data.get("recent_candles", [])
                )
                direction = data.get("signal") or "ESPERAR"

                # Asegurar el historial en cada ciclo para señales activas.
                # La clave única hace esta escritura idempotente: si ya existe,
                # Supabase ignora el duplicado; si faltaba por un fallo previo,
                # queda recuperada aunque el estado ya estuviera registrado.
                candle_time = str(data.get("candle_time") or "")
                if direction in ("COMPRA", "VENTA"):
                    signal_key = "|".join([symbol, interval, direction, candle_time])
                    history_payload = {
                        "signal_key": signal_key,
                        "symbol": symbol,
                        "timeframe": interval,
                        "direction": direction,
                        "entry_price": data.get("entry"),
                        "stop_loss": data.get("stop_loss"),
                        "take_profit": data.get("take_profit"),
                        "signal_at": datetime.now(timezone.utc).isoformat(),
                        "candle_time": data.get("candle_time"),
                        "status": "PENDIENTE",
                        "indicators": {
                            "ema20": data.get("ema20"),
                            "ema50": data.get("ema50"),
                            "rsi14": data.get("rsi14"),
                            "adx14": data.get("adx14"),
                            "atr14": data.get("atr14"),
                            "macd": data.get("macd"),
                            "macd_signal": data.get("macd_signal"),
                            "macd_histogram": data.get("macd_histogram"),
                            "bollinger_upper": data.get("bollinger_upper"),
                            "bollinger_middle": data.get("bollinger_middle"),
                            "bollinger_lower": data.get("bollinger_lower"),
                            "buy_score": data.get("buy_score"),
                            "sell_score": data.get("sell_score"),
                        },
                    }
                    history_response = requests.post(
                        SUPABASE_URL + "/rest/v1/signal_history?on_conflict=signal_key",
                        headers={
                            **supabase_headers(),
                            "Prefer": "resolution=ignore-duplicates,return=minimal",
                        },
                        json=history_payload,
                        timeout=15,
                    )
                    if history_response.status_code not in (200, 201, 204):
                        results.append({
                            "symbol": symbol, "interval": interval,
                            "status": "history_save_error",
                            "http_status": history_response.status_code,
                        })
                        continue

                # Consultar el último estado registrado para este mercado y temporalidad.
                # Así no se repite una alerta mientras COMPRA/VENTA siga vigente.
                state_response = requests.get(
                    SUPABASE_URL + "/rest/v1/signal_alerts",
                    params={
                        "select": "direction,created_at",
                        "market": "eq." + symbol,
                        "timeframe": "eq." + interval,
                        "order": "created_at.desc",
                        "limit": "1",
                    },
                    headers=supabase_headers(),
                    timeout=15,
                )
                if state_response.status_code != 200:
                    results.append({
                        "symbol": symbol, "interval": interval,
                        "status": "state_read_error"
                    })
                else:
                    previous_rows = state_response.json()
                    previous_direction = (
                        previous_rows[0].get("direction") if previous_rows else None
                    )

                    if previous_direction == direction:
                        results.append({
                            "symbol": symbol, "interval": interval,
                            "status": "same_signal_no_notification",
                            "direction": direction,
                            "outcomes_resolved": outcome_result["resolved"],
                            "outcome_errors": outcome_result["errors"]
                        })
                    else:
                        # El historial ya quedó asegurado arriba antes de leer/guardar el estado.
                        # El estado se guarda solo después de asegurar el historial.
                        # Si falla este paso, la inserción idempotente del historial
                        # permitirá reintentar el siguiente ciclo sin duplicar señales.
                        # Huella determinista: dos Cron simultáneos no deben
                        # crear dos eventos para la misma transición/vela.
                        fingerprint = "|".join([
                            symbol, interval, direction, candle_time
                        ])
                        insert_response = requests.post(
                            SUPABASE_URL + "/rest/v1/signal_alerts?on_conflict=market,timeframe,fingerprint",
                            headers={
                                **supabase_headers(),
                                "Prefer": "resolution=ignore-duplicates,return=representation",
                            },
                            json={
                                "market": symbol,
                                "timeframe": interval,
                                "direction": direction,
                                "fingerprint": fingerprint,
                            },
                            timeout=15,
                        )
                        if insert_response.status_code not in (200, 201):
                            results.append({
                                "symbol": symbol, "interval": interval,
                                "status": "state_save_error",
                                "http_status": insert_response.status_code,
                            })
                            continue
                        if not insert_response.json():
                            results.append({
                                "symbol": symbol, "interval": interval,
                                "status": "duplicate_state"
                            })
                            continue
                        if direction == "ESPERAR":
                            results.append({
                                "symbol": symbol, "interval": interval,
                                "status": "state_reset_no_notification"
                            })
                            continue

                        # Solo se notifica una transición nueva a COMPRA o VENTA.
                        price_digits = 5 if symbol == "EUR/USD" else 2

                        def display_price(value):
                            return f"{float(value):,.{price_digits}f}" if value is not None else "—"

                        short_interval = {
                            "5min": "M5", "15min": "M15", "30min": "M30"
                        }.get(interval, interval)
                        alert_body = (
                            f"{symbol} · {short_interval} | "
                            f"Entrada {display_price(data.get('entry'))} · "
                            f"SL {display_price(data.get('stop_loss'))} · "
                            f"TP {display_price(data.get('take_profit'))}"
                        )

                        # Crear una entrega independiente por dispositivo. La clave única
                        # (event_fingerprint, endpoint) hace idempotente la cola.
                        subs_response = requests.get(
                            SUPABASE_URL + "/rest/v1/push_subscriptions?select=endpoint,subscription",
                            headers=supabase_headers(), timeout=15,
                        )
                        if subs_response.status_code != 200:
                            results.append({
                                "symbol": symbol, "interval": interval,
                                "status": "subscription_read_error"
                            })
                        else:
                            subscriptions = subs_response.json()
                            price_digits = 5 if symbol == "EUR/USD" else 2

                            def display_price(value):
                                return f"{float(value):,.{price_digits}f}" if value is not None else "—"

                            short_interval = {
                                "5min": "M5", "15min": "M15", "30min": "M30"
                            }.get(interval, interval)
                            alert_body = (
                                f"{symbol} · {short_interval} | "
                                f"Entrada {display_price(data.get('entry'))} · "
                                f"SL {display_price(data.get('stop_loss'))} · "
                                f"TP {display_price(data.get('take_profit'))}"
                            )
                            queued = 0
                            queue_errors = 0
                            for row in subscriptions:
                                endpoint = row.get("endpoint")
                                subscription = row.get("subscription")
                                if not endpoint or not isinstance(subscription, dict):
                                    continue
                                payload = {
                                    "title": f"GLOBAL SIGNAL · {direction}",
                                    "body": alert_body,
                                    "tag": "gss-" + symbol.replace("/", "") + "-" + interval + "-" + requests.utils.quote(candle_time, safe=""),
                                    "url": ("https://vantage-signal-scanner.onrender.com/panel_escaner_vantage.html?view=scanner&symbol=" + requests.utils.quote(symbol, safe="") + "&timeframe=" + requests.utils.quote(interval, safe="")),
                                    "data": {
                                        "symbol": symbol, "interval": interval,
                                        "direction": direction,
                                        "entry": data.get("entry"),
                                        "stop_loss": data.get("stop_loss"),
                                        "take_profit": data.get("take_profit"),
                                    },
                                }
                                queue_response = requests.post(
                                    SUPABASE_URL + "/rest/v1/push_delivery_outbox?on_conflict=event_fingerprint,endpoint",
                                    headers={
                                        **supabase_headers(),
                                        "Prefer": "resolution=ignore-duplicates,return=minimal",
                                    },
                                    json={
                                        "event_fingerprint": fingerprint,
                                        "endpoint": endpoint,
                                        "subscription": subscription,
                                        "payload": payload,
                                        "status": "pending",
                                    },
                                    timeout=15,
                                )
                                if queue_response.status_code in (200, 201, 204):
                                    queued += 1
                                else:
                                    queue_errors += 1
                            if queue_errors:
                                # Permitir que el siguiente ciclo reconstruya las filas que faltaron.
                                # La unicidad del outbox evita duplicar las que ya quedaron guardadas.
                                requests.delete(
                                    SUPABASE_URL + "/rest/v1/signal_alerts",
                                    params={"market": "eq." + symbol, "timeframe": "eq." + interval,
                                            "fingerprint": "eq." + fingerprint},
                                    headers=supabase_headers(), timeout=10,
                                )
                            results.append({
                                "symbol": symbol, "interval": interval,
                                "status": "delivery_queued" if queued else "new_signal_no_subscribers",
                                "devices_queued": queued,
                                "queue_errors": queue_errors
                            })

    delivery_summary = process_pending_push_deliveries()
    sent_total += delivery_summary["sent"]
    return jsonify({
        "ok": True,
        "alerts_sent": sent_total,
        "delivery_queue": delivery_summary,
        "checks": results
    })


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
    )
