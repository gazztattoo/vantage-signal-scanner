
import os
import json
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, request
from flask_cors import CORS
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import hmac

app = Flask(__name__)
CORS(app, resources={
    r"/api/*": {
        "origins": ["https://gazztattoo.github.io", "https://global-signal-push-test-panel.onrender.com"],
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
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com")

def supabase_headers():
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        raise RuntimeError("El almacenamiento de notificaciones no está configurado.")
    return {
        "apikey": SUPABASE_SECRET_KEY,
        "Content-Type": "application/json",
    }

def send_web_push(subscription, payload):
    if not VAPID_PUBLIC_KEY or not VAPID_PRIVATE_KEY:
        raise RuntimeError("Las claves Web Push aún no están configuradas.")
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
    if request.path not in ("/api/analysis", "/api/price", "/api/push/subscribe", "/api/push/test", "/api/push/unsubscribe"):
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


def market_session(symbol, now=None):
    """Horario semanal orientativo de los mercados spot; BTC opera 24/7."""
    now = now or datetime.now(timezone.utc)
    if symbol == "BTC/USD":
        return {"open": True, "status": "ABIERTO", "reason": "Mercado cripto 24/7"}
    # Forex y oro spot: sesión habitual domingo 22:00 UTC a viernes 22:00 UTC.
    # El horario exacto puede variar según el bróker y los festivos.
    weekday = now.weekday()  # lunes=0, domingo=6
    minute = now.hour * 60 + now.minute
    open_now = (
        (weekday == 6 and minute >= 22 * 60)
        or (0 <= weekday <= 3)
        or (weekday == 4 and minute < 22 * 60)
    )
    return {
        "open": open_now,
        "status": "ABIERTO" if open_now else "MERCADO CERRADO",
        "reason": "Sesión semanal habitual; confirma el horario de tu bróker"
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
            "method": (
                "EMA20/EMA50 + RSI14 + MACD + ADX14 + Bollinger(20,2); "
                "score mínimo 3/4 y ADX>=18; SL=1.5x ATR; TP=3x ATR"
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
    try:
        response = requests.get(
            SUPABASE_URL + "/rest/v1/push_subscriptions?select=subscription",
            headers=supabase_headers(),
            timeout=15,
        )
        if response.status_code != 200:
            return jsonify({"error": "No se pudieron consultar las suscripciones."}), 502
        subscriptions = response.json()
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
                    "url": "https://global-signal-push-test-panel.onrender.com/panel_escaner_vantage.html",
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


SIGNAL_CRON_SECRET = os.environ.get("SIGNAL_CRON_SECRET", "")

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

    # Reutiliza el mismo endpoint de análisis que consume el panel.
    # Pausa entre consultas para no superar de golpe el límite del proveedor.
    import time
    with app.test_client() as client:
        for index, (symbol, interval) in enumerate(
            (pair for interval in intervals for pair in [(s, interval) for s in symbols])
        ):
            session = market_session(symbol)
            if not session["open"]:
                results.append({
                    "symbol": symbol, "interval": interval,
                    "status": "market_closed",
                    "detail": "Mercado cerrado: no se analiza ni se envían alertas."
                })
                if index < 8:
                    time.sleep(8)
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
                direction = data.get("signal") or "ESPERAR"

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
                            "direction": direction
                        })
                    else:
                        candle_time = str(data.get("candle_time") or "")
                        event_stamp = datetime.now(timezone.utc).isoformat()
                        fingerprint = "|".join([
                            symbol, interval, direction, candle_time, event_stamp
                        ])

                        # Registrar el cambio de estado ANTES del push para impedir
                        # que el siguiente ciclo vuelva a enviar la misma alerta.
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
                                "status": "state_save_error"
                            })
                        elif not insert_response.json():
                            results.append({
                                "symbol": symbol, "interval": interval,
                                "status": "duplicate_state"
                            })
                        elif direction == "ESPERAR":
                            results.append({
                                "symbol": symbol, "interval": interval,
                                "status": "state_reset_no_notification"
                            })
                        else:
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

                            subs_response = requests.get(
                                SUPABASE_URL + "/rest/v1/push_subscriptions?select=subscription",
                                headers=supabase_headers(), timeout=15,
                            )
                            if subs_response.status_code != 200:
                                results.append({
                                    "symbol": symbol, "interval": interval,
                                    "status": "subscription_read_error"
                                })
                            else:
                                subscriptions = subs_response.json()
                                delivered = 0
                                expired = []
                                for row in subscriptions:
                                    subscription = row.get("subscription")
                                    if not isinstance(subscription, dict):
                                        continue
                                    try:
                                        send_web_push(subscription, {
                                            "title": f"GLOBAL SIGNAL · {direction}",
                                            "body": alert_body,
                                            "tag": "gss-" + symbol.replace("/", "") + "-" + interval,
                                            "url": "https://vantage-signal-scanner.onrender.com/",
                                            "data": {
                                                "symbol": symbol, "interval": interval,
                                                "direction": direction,
                                                "entry": data.get("entry"),
                                                "stop_loss": data.get("stop_loss"),
                                                "take_profit": data.get("take_profit"),
                                            },
                                        })
                                        delivered += 1
                                    except Exception as exc:
                                        response_obj = getattr(exc, "response", None)
                                        if getattr(response_obj, "status_code", None) in (404, 410):
                                            expired.append(subscription.get("endpoint", ""))

                                for endpoint in expired:
                                    if endpoint:
                                        requests.delete(
                                            SUPABASE_URL + "/rest/v1/push_subscriptions?endpoint=eq." + requests.utils.quote(endpoint, safe=""),
                                            headers=supabase_headers(), timeout=10,
                                        )

                                sent_total += delivered
                                results.append({
                                    "symbol": symbol, "interval": interval,
                                    "status": "new_signal_notified" if delivered else "new_signal_no_delivery",
                                    "devices": delivered
                                })
            if index < 8:
                time.sleep(8)

    return jsonify({"ok": True, "alerts_sent": sent_total, "checks": results})


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
    )
