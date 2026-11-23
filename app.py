
import os
import json
import requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, request
from flask_cors import CORS
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import hmac
import math
import time
from strategy_engine import analyze as analyze_strategy
from mt5_ingest import normalize_mt5_batch
from experimental_gate import evaluate_shadow_gate

app = Flask(__name__)
CORS(app, resources={
    r"/api/*": {
        "origins": ["https://gazztattoo.github.io", "https://global-signal-push-test-panel.onrender.com"],
        "allow_headers": ["Content-Type", "Authorization", "X-MT5-Token"],
        "methods": ["GET", "POST", "OPTIONS"]
    }
})

# TEST only: mock remains the safe default; BiQuote activation is explicit per TEST service.
DATA_MODE = os.environ.get("TEST_DATA_MODE", "mock").strip().lower()
if DATA_MODE not in ("mock", "biquote"):
    DATA_MODE = "mock"
# MT5 ingestion is a separate shadow feed; it never changes DATA_MODE or signal inputs.
MT5_INGEST_TOKEN = os.environ.get("MT5_INGEST_TOKEN", "")
MT5_TEST_SUPABASE_URL = "https://viiraygjdqwiuawnzyrc.supabase.co"
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


@app.after_request
def disable_api_caching(response):
    # Evita reutilizar respuestas de autenticación o datos protegidos en PWA/navegadores.
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


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


@app.route("/api/access/verify", methods=["GET"])
def verify_access_token():
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return jsonify({"valid": False, "stage": "missing_bearer"}), 401
    try:
        data = access_serializer().loads(
            authorization[7:].strip(),
            max_age=TOKEN_MAX_AGE_SECONDS
        )
        if data.get("access") is not True:
            raise BadSignature("Token no válido")
        return jsonify({"valid": True, "service": "global-signal-push-test"})
    except SignatureExpired:
        return jsonify({"valid": False, "stage": "expired"}), 401
    except (BadSignature, RuntimeError):
        return jsonify({"valid": False, "stage": "invalid_signature"}), 401


@app.route("/api/access/status", methods=["GET"])
def access_configuration_status():
    # Diagnóstico no sensible: nunca devuelve códigos, secretos ni tokens.
    return jsonify({
        "service": "global-signal-push-test",
        "data_mode": DATA_MODE,
        "market_api_enabled": False,
        "data_mode": DATA_MODE,
        "panel_code_configured": bool(PANEL_ACCESS_CODE),
        "token_secret_configured": bool(ACCESS_TOKEN_SECRET),
        "token_ttl_seconds": TOKEN_MAX_AGE_SECONDS,
    })


# TEST ACCESS DISABLED TEMPORARILY: this branch is intentionally open for testing only.

ALLOWED_SYMBOLS = {"XAU/USD"} if DATA_MODE == "biquote" else {"EUR/USD", "XAU/USD", "BTC/USD"}
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


NEWS_CALENDAR_CACHE = {"stored_at": 0, "payload": None}
NEWS_CALENDAR_TTL_SECONDS = 900
NEWS_CALENDAR_URL = "https://showmytrades.com/api/public/calendar/v1"


def economic_news_context():
    """Free USD macro calendar. Context only: never blocks or changes a signal."""
    now_mono = time.monotonic()
    cached = NEWS_CALENDAR_CACHE.get("payload")
    if cached and now_mono - NEWS_CALENDAR_CACHE["stored_at"] < NEWS_CALENDAR_TTL_SECONDS:
        return {**cached, "cache_hit": True}
    try:
        response = requests.get(
            NEWS_CALENDAR_URL,
            params={"currency": "USD", "impact": "high,medium", "days": 7, "format": "json"},
            headers={"User-Agent": "Gedeon2-TEST/1.0"},
            timeout=8,
        )
        response.raise_for_status()
        body = response.json()
        rows = body.get("events", []) if isinstance(body, dict) else body
        if not isinstance(rows, list):
            raise ValueError("Formato inesperado del calendario económico")
        # Prefer the calendar provider clock so event windows do not inherit VPS clock drift.
        server_time = body.get("server_time") if isinstance(body, dict) else None
        now = datetime.fromtimestamp(float(server_time), timezone.utc) if server_time is not None else datetime.now(timezone.utc)
        events = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            stamp = row.get("ts")
            if stamp is None and row.get("time"):
                stamp = datetime.fromisoformat(str(row["time"]).replace("Z", "+00:00")).timestamp()
            if stamp is None:
                continue
            event_time = datetime.fromtimestamp(float(stamp), timezone.utc)
            minutes = round((event_time - now).total_seconds() / 60)
            impact = str(row.get("impact", "")).lower()
            currency = str(row.get("currency", "")).upper()
            if currency not in ("USD", "ALL"):
                continue
            events.append({
                "id": str(row.get("id", "")),
                "time": event_time.isoformat(),
                "minutes_until": minutes,
                "currency": currency,
                "impact": impact,
                "title": str(row.get("title", row.get("name", "Evento económico")))[:180],
                "forecast": row.get("forecast"),
                "previous": row.get("previous"),
                "actual": row.get("actual"),
                "status": str(row.get("status", "scheduled")),
                "confidence": row.get("confidence"),
            })
        events.sort(key=lambda x: x["minutes_until"])
        high_window = [
            e for e in events
            if e["impact"] == "high" and -30 <= e["minutes_until"] <= 30
        ]
        next_high = next((e for e in events if e["impact"] == "high" and e["minutes_until"] >= 0), None)
        payload = {
            "available": True,
            "provider": "ShowMyTrades economic calendar",
            "fetched_at": now.isoformat(),
            "server_time": now.isoformat(),
            "generated_at": body.get("generated_at") if isinstance(body, dict) else None,
            "attribution": "https://showmytrades.com/tools/economic-calendar",
            "cache_ttl_seconds": NEWS_CALENDAR_TTL_SECONDS,
            "events": events[:30],
            "high_impact_window": bool(high_window),
            "active_high_impact_events": high_window,
            "next_high_impact": next_high,
            "signal_policy": "WARNING_ONLY_NO_AUTOMATIC_BLOCK",
        }
        NEWS_CALENDAR_CACHE.update({"stored_at": now_mono, "payload": payload})
        return {**payload, "cache_hit": False}
    except (requests.RequestException, ValueError, TypeError, KeyError, OverflowError) as exc:
        app.logger.warning("Economic calendar unavailable: %s", str(exc)[:160])
        if cached:
            return {**cached, "stale": True, "cache_hit": True, "warning": "Calendario sin actualizar; se muestra la última copia disponible."}
        return {
            "available": False, "provider": "ShowMyTrades economic calendar",
            "events": [], "high_impact_window": None, "stale": True,
            "warning": "No se pudo verificar el calendario. No asumir que no hay noticias.",
        }


@app.route("/api/news/calendar", methods=["GET"])
def economic_news_calendar():
    return jsonify(economic_news_context())


@app.route("/api/market-status", methods=["GET"])
def market_status():
    now = datetime.now(timezone.utc)
    if DATA_MODE == "mock":
        # TEST uses synthetic candles, so real-world exchange hours must not hide its cards.
        return jsonify({
            "checked_at": now.isoformat(),
            "data_mode": "mock",
            "markets": {
                symbol: {
                    "open": True,
                    "status": "SIMULADO",
                    "reason": "Datos sintéticos de TEST; no representa una sesión real.",
                    "server_time": now.astimezone(CANARY_TZ).isoformat(),
                    "server_utc_offset": "SIMULADO",
                }
                for symbol in sorted(ALLOWED_SYMBOLS)
            },
        })
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


def generate_mock_candles(symbol, interval, count=1200):
    """Velas sintéticas deterministas para probar la interfaz sin datos de mercado."""
    now = datetime.now(timezone.utc)
    step = INTERVAL_MINUTES[interval] * 60
    latest_open = int(now.timestamp() // step) * step - step
    bases = {"EUR/USD": 1.0850, "XAU/USD": 2350.0, "BTC/USD": 68000.0}
    amplitudes = {"EUR/USD": 0.00045, "XAU/USD": 1.8, "BTC/USD": 42.0}
    trends = {"EUR/USD": 0.000002, "XAU/USD": 0.012, "BTC/USD": 0.28}
    base, amplitude, trend = bases[symbol], amplitudes[symbol], trends[symbol]
    candles = []
    previous = base
    for i in range(count):
        close = base + trend * i + amplitude * math.sin(i / 4.7) + amplitude * 0.35 * math.sin(i / 1.9)
        open_price = previous
        spread = amplitude * (0.35 + abs(math.sin(i * 1.31)) * 0.35)
        candle_time = datetime.fromtimestamp(latest_open - (count - 1 - i) * step, timezone.utc).astimezone(CANARY_TZ)
        candles.append({
            "time": candle_time.isoformat(),
            "open": open_price,
            "high": max(open_price, close) + spread,
            "low": min(open_price, close) - spread,
            "close": close,
        })
        previous = close
    latest_time = datetime.fromtimestamp(latest_open, timezone.utc).astimezone(CANARY_TZ)
    age_seconds = max((now - latest_time.astimezone(timezone.utc)).total_seconds(), 0)
    return candles, latest_time.isoformat(), round(age_seconds / 60, 2)


# BiQuote research feed is isolated from the strategy engine and disabled unless
# explicitly enabled in the TEST service environment. It never changes DATA_MODE.
BIQUOTE_RESEARCH_ENABLED = os.environ.get("BIQUOTE_RESEARCH_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
BIQUOTE_RESEARCH_CACHE = {}
BIQUOTE_RESEARCH_CACHE_TTL = {"5m": 20, "15m": 45, "30m": 90, "1h": 180, "4h": 360}
BIQUOTE_INTERVAL_MINUTES = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}
BIQUOTE_INTERVAL_LIMITS = {"5m": 288, "15m": 192, "30m": 192, "1h": 168, "4h": 180}
BIQUOTE_TICK_RESEARCH_CACHE = {}
BIQUOTE_TICK_RESEARCH_TTL_SECONDS = 8


def fetch_biquote_research_tick(symbol):
    """Fetch a fresh BiQuote quote for display/research only; never for signals."""
    from external_biquote_adapter import normalize_biquote_tick

    provider_symbol = {"EUR/USD": "EURUSD", "XAU/USD": "XAUUSD", "BTC/USD": "BTCUSD"}[symbol]
    response = requests.get(
        f"https://biquote.io/api/{provider_symbol}",
        params={"allowStale": "false"},
        timeout=8,
    )
    response.raise_for_status()
    payload = response.json()
    result = normalize_biquote_tick(
        payload,
        symbol=symbol,
        max_age_seconds=60,
    )
    if not result.get("accepted"):
        raise RuntimeError(result.get("reason", "Cotización BiQuote rechazada."))
    result.update({
        "research_only": True,
        "signals_activated": False,
        "execution_price": False,
    })
    return result


def fetch_biquote_research_ohlc(symbol, interval):
    """Fetch and normalize external BiQuote bars for research display only."""
    from external_biquote_adapter import normalize_biquote_ohlc

    provider_symbol = {"EUR/USD": "EURUSD", "XAU/USD": "XAUUSD", "BTC/USD": "BTCUSD"}[symbol]
    response = requests.get(
        f"https://biquote.io/api/{provider_symbol}/ohlc",
        params={"interval": interval, "limit": BIQUOTE_INTERVAL_LIMITS[interval]},
        timeout=12,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("BiQuote devolvió una respuesta OHLC no válida.")
    # The endpoint path and requested interval are authoritative when the
    # provider omits those metadata fields; conflicting values are rejected.
    payload = dict(payload)
    if payload.get("symbol") not in (None, provider_symbol):
        raise RuntimeError("BiQuote devolvió otro símbolo; respuesta rechazada.")
    if payload.get("interval") not in (None, interval):
        raise RuntimeError("BiQuote devolvió otra temporalidad; respuesta rechazada.")
    payload["symbol"] = provider_symbol
    payload["interval"] = interval
    normalized = normalize_biquote_ohlc(
        payload,
        symbol=symbol,
        interval=interval,
        max_last_closed_age_minutes=(BIQUOTE_INTERVAL_MINUTES[interval] * 2 + 2),
    )
    if not normalized.get("accepted"):
        raise RuntimeError(normalized.get("reason", "Datos BiQuote rechazados."))
    if normalized["closed_bars"] < 55:
        raise RuntimeError("BiQuote devolvió menos de 55 velas cerradas; no se publican datos.")
    return normalized


@app.route("/api/research/biquote/tick", methods=["GET"])
def biquote_research_tick():
    """On-demand fresh Bid/Ask snapshot; isolated from signal generation."""
    if not BIQUOTE_RESEARCH_ENABLED:
        return jsonify({"enabled": False, "research_only": True, "signals_activated": False}), 503
    symbol = get_symbol(request.args.get("symbol"))
    if not symbol:
        return jsonify({"error": "Símbolo no permitido.", "allowed_symbols": sorted(ALLOWED_SYMBOLS)}), 400

    now_mono = time.monotonic()
    cached = BIQUOTE_TICK_RESEARCH_CACHE.get(symbol)
    if cached and now_mono - cached["stored_at"] < BIQUOTE_TICK_RESEARCH_TTL_SECONDS:
        result = dict(cached["payload"])
        result["cache"] = {"hit": True, "age_seconds": round(now_mono - cached["stored_at"], 1)}
        return jsonify(result)

    try:
        result = fetch_biquote_research_tick(symbol)
    except (requests.RequestException, ValueError, TypeError, KeyError, RuntimeError) as exc:
        app.logger.warning("BiQuote research tick rejected symbol=%s reason=%s", symbol, str(exc)[:160])
        return jsonify({
            "error": "No hay cotización BiQuote válida y reciente.",
            "research_only": True,
            "signals_activated": False,
            "execution_price": False,
        }), 503

    result["cache"] = {"hit": False, "ttl_seconds": BIQUOTE_TICK_RESEARCH_TTL_SECONDS}
    BIQUOTE_TICK_RESEARCH_CACHE[symbol] = {"stored_at": now_mono, "payload": result}
    return jsonify(result)


def cached_biquote_research_ohlc(symbol, interval):
    key = (symbol, interval)
    now_mono = time.monotonic()
    cached = BIQUOTE_RESEARCH_CACHE.get(key)
    if cached and now_mono - cached["stored_at"] < BIQUOTE_RESEARCH_CACHE_TTL[interval]:
        return dict(cached["payload"])
    result = fetch_biquote_research_ohlc(symbol, interval)
    result.update({
        "research_only": True,
        "signals_activated": False,
        "data_mode": "provider_research",
        "operational_data_mode": DATA_MODE,
        "cache": {"hit": False, "ttl_seconds": BIQUOTE_RESEARCH_CACHE_TTL[interval]},
    })
    BIQUOTE_RESEARCH_CACHE[key] = {"stored_at": now_mono, "payload": result}
    return result


@app.route("/api/research/biquote/analysis", methods=["GET"])
def biquote_research_analysis():
    """Shadow-only MTF analysis using direct BiQuote candles; never feeds live signals."""
    if not BIQUOTE_RESEARCH_ENABLED:
        return jsonify({"enabled": False, "research_only": True, "signals_activated": False}), 503
    symbol = get_symbol(request.args.get("symbol"))
    interval = (request.args.get("interval") or "5min").strip()
    frames = {
        "5min": ("5m", "15m", "30m"),
        "15min": ("15m", "30m", "1h"),
        "30min": ("30m", "1h", "4h"),
    }
    if not symbol:
        return jsonify({"error": "Símbolo no permitido."}), 400
    if interval not in frames:
        return jsonify({"error": "Temporalidad no permitida.", "allowed_intervals": list(frames)}), 400
    try:
        base_interval, htf_interval_1, htf_interval_2 = frames[interval]
        base = cached_biquote_research_ohlc(symbol, base_interval)
        higher_1 = cached_biquote_research_ohlc(symbol, htf_interval_1)
        higher_2 = cached_biquote_research_ohlc(symbol, htf_interval_2)
        if base["closed_bars"] < 120 or higher_1["closed_bars"] < 60 or higher_2["closed_bars"] < 60:
            raise RuntimeError("Historial insuficiente en una o más temporalidades; análisis bloqueado.")
        htf_minutes_1 = BIQUOTE_INTERVAL_MINUTES[htf_interval_1]
        htf_minutes_2 = BIQUOTE_INTERVAL_MINUTES[htf_interval_2]
        strategy = analyze_strategy(
            base["bars"],
            interval,
            symbol,
            higher_timeframes={
                f"{htf_minutes_1}min": higher_1["bars"],
                f"{htf_minutes_2}min": higher_2["bars"],
            },
        )
        return jsonify({
            "symbol": symbol,
            "interval": interval,
            "research_only": True,
            "signals_activated": False,
            "execution_price": False,
            "data_mode": "provider_research",
            "operational_data_mode": DATA_MODE,
            "provider": "biquote",
            "price_basis": "provider_feed_not_verified_as_VANTAGE",
            "signal_research": strategy.get("signal", "WAIT"),
            "pre_signal": strategy.get("pre_signal", False),
            "pre_signal_side": strategy.get("pre_signal_side"),
            "primary_bias": strategy.get("primary_bias"),
            "mtf": strategy.get("mtf"),
            "regime": strategy.get("regime"),
            "structure": strategy.get("structure"),
            "score": strategy.get("score"),
            "score_max": strategy.get("score_max"),
            "confluence_grade": strategy.get("confluence_grade"),
            "setup_grade": strategy.get("setup_grade", strategy.get("quality_grade")),
            "quality_grade": strategy.get("quality_grade"),
            "setup_status": strategy.get("setup_status", "WAIT"),
            "confirmation_missing": strategy.get("confirmation_missing", []),
            "reason": strategy.get("reason"),
            "entry": None,
            "stop_loss": None,
            "take_profit": None,
            "candles": {
                base_interval: base["closed_bars"],
                htf_interval_1: higher_1["closed_bars"],
                htf_interval_2: higher_2["closed_bars"],
            },
            "last_closed": {
                base_interval: base["last_closed_close_time"],
                htf_interval_1: higher_1["last_closed_close_time"],
                htf_interval_2: higher_2["last_closed_close_time"],
            },
            "warning": "Análisis sombra con velas BiQuote. No es señal operativa ni precio de ejecución Vantage.",
        })
    except (requests.RequestException, ValueError, TypeError, KeyError, RuntimeError) as exc:
        app.logger.warning("BiQuote shadow analysis rejected symbol=%s interval=%s reason=%s", symbol, interval, str(exc)[:160])
        return jsonify({
            "error": "No se pudo completar el análisis multitemporal BiQuote.",
            "research_only": True,
            "signals_activated": False,
            "detail": str(exc)[:180],
        }), 503


@app.route("/api/research/biquote/ohlc", methods=["GET"])
def biquote_research_ohlc():
    """On-demand BiQuote OHLC endpoint; never feeds signal generation."""
    if not BIQUOTE_RESEARCH_ENABLED:
        return jsonify({"enabled": False, "research_only": True, "signals_activated": False}), 503
    symbol = get_symbol(request.args.get("symbol"))
    interval = (request.args.get("interval") or "5m").strip()
    if not symbol:
        return jsonify({"error": "Símbolo no permitido.", "allowed_symbols": sorted(ALLOWED_SYMBOLS)}), 400
    if interval not in BIQUOTE_INTERVAL_MINUTES:
        return jsonify({"error": "Temporalidad no permitida.", "allowed_intervals": sorted(BIQUOTE_INTERVAL_MINUTES)}), 400

    key = (symbol, interval)
    now_mono = time.monotonic()
    cached = BIQUOTE_RESEARCH_CACHE.get(key)
    if cached and now_mono - cached["stored_at"] < BIQUOTE_RESEARCH_CACHE_TTL[interval]:
        result = dict(cached["payload"])
        result["cache"] = {"hit": True, "age_seconds": round(now_mono - cached["stored_at"], 1)}
        return jsonify(result)

    try:
        result = fetch_biquote_research_ohlc(symbol, interval)
    except (requests.RequestException, ValueError, TypeError, KeyError, RuntimeError) as exc:
        app.logger.warning("BiQuote research OHLC rejected symbol=%s interval=%s reason=%s", symbol, interval, str(exc)[:160])
        return jsonify({
            "error": "No hay velas BiQuote válidas y recientes para investigación.",
            "research_only": True,
            "signals_activated": False,
        }), 503

    result.update({
        "research_only": True,
        "signals_activated": False,
        "data_mode": "provider_research",
        "operational_data_mode": DATA_MODE,
        "cache": {"hit": False, "ttl_seconds": BIQUOTE_RESEARCH_CACHE_TTL[interval]},
    })
    BIQUOTE_RESEARCH_CACHE[key] = {"stored_at": now_mono, "payload": result}
    return jsonify(result)


def fetch_candles(symbol, interval):
    """TEST fail-closed: synthetic candles only; never call external market providers."""
    if DATA_MODE != "mock":
        raise RuntimeError(
            "TEST está bloqueado para proveedores externos; solo se permiten datos sintéticos."
        )
    return generate_mock_candles(symbol, interval)


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


@app.route("/api/mt5/ingest", methods=["POST"])
def ingest_mt5_candles():
    """Accept closed Vantage MT5 candles into TEST-only shadow storage."""
    if DATA_MODE != "mock":
        return jsonify({"error": "La ingestión MT5 está limitada al entorno TEST."}), 403
    if not MT5_INGEST_TOKEN:
        return jsonify({"error": "La recepción MT5 no está habilitada en TEST."}), 503

    supplied_token = request.headers.get("X-MT5-Token", "")
    if not hmac.compare_digest(supplied_token, MT5_INGEST_TOKEN):
        return jsonify({"error": "Token de ingestión no válido."}), 401

    if request.content_length is not None and request.content_length > 256_000:
        return jsonify({"error": "El lote supera el tamaño máximo permitido."}), 413
    if not request.is_json:
        return jsonify({"error": "El contenido debe ser JSON."}), 415

    payload = request.get_json(silent=True)
    try:
        rows = normalize_mt5_batch(payload)
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    # Hard boundary: even a misconfigured Render variable cannot write to production.
    if SUPABASE_URL.rstrip("/") != MT5_TEST_SUPABASE_URL:
        return jsonify({
            "error": "Destino de base de datos no autorizado. Se exige el proyecto Supabase TEST."
        }), 503
    if not SUPABASE_SECRET_KEY:
        return jsonify({"error": "Falta la clave de servidor de Supabase TEST."}), 503

    try:
        headers = {
            **supabase_headers(),
            "Prefer": "resolution=merge-duplicates,return=minimal",
        }
        response = requests.post(
            SUPABASE_URL + "/rest/v1/mt5_market_candles",
            params={"on_conflict": "symbol,timeframe,open_time"},
            json=rows,
            headers=headers,
            timeout=15,
        )
    except (requests.RequestException, RuntimeError):
        app.logger.error("Falló el transporte de ingestión MT5 hacia el almacenamiento TEST.")
        return jsonify({"error": "No se pudo guardar el lote MT5 en TEST."}), 502

    if response.status_code not in (200, 201, 204):
        app.logger.error(
            "Supabase TEST rechazó un lote MT5 con HTTP %s.",
            response.status_code,
        )
        return jsonify({"error": "Supabase TEST rechazó el lote MT5."}), 502

    return jsonify({
        "ok": True,
        "stored": len(rows),
        "symbol": rows[0]["symbol"],
        "timeframe": rows[0]["timeframe"],
        "source": "VANTAGE_MT5",
        "data_mode": DATA_MODE,
        "signals_activated": False,
    }), 200



# TEST-only live tick relay. In-memory shadow cache; never feeds signals or orders.
MT5_LIVE_TICK = {}
MT5_LIVE_TICK_LOCK = __import__("threading").Lock()

@app.route("/api/mt5/tick", methods=["POST", "GET"])
def mt5_live_tick():
    # This route exists only on the dedicated TEST branch/service. Permit both
    # mock and BiQuote TEST modes; the Vantage tick remains shadow-only.
    if DATA_MODE not in ("mock", "biquote"):
        return jsonify({"error": "El puente MT5 está limitado al entorno TEST."}), 403
    if not MT5_INGEST_TOKEN:
        return jsonify({"error": "La recepción MT5 no está habilitada en TEST."}), 503
    supplied_token = request.headers.get("X-MT5-Token", "")
    if not hmac.compare_digest(supplied_token, MT5_INGEST_TOKEN):
        return jsonify({"error": "Token de ingestión no válido."}), 401

    if request.method == "POST":
        if not request.is_json:
            return jsonify({"error": "El contenido debe ser JSON."}), 415
        payload = request.get_json(silent=True) or {}
        symbol = str(payload.get("symbol", "")).strip().upper()
        broker_symbol = str(payload.get("broker_symbol", "")).strip()
        try:
            bid, ask = float(payload.get("bid")), float(payload.get("ask"))
            stamp = datetime.fromisoformat(str(payload.get("timestamp_utc", "")).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("timestamp sin zona")
        except (TypeError, ValueError):
            return jsonify({"error": "Bid/Ask o timestamp no válidos."}), 400
        if symbol != "XAU/USD" or not broker_symbol or not (math.isfinite(bid) and math.isfinite(ask)) or bid <= 0 or ask < bid:
            return jsonify({"error": "Solo se acepta XAU/USD con Bid/Ask válidos."}), 400
        now = datetime.now(timezone.utc)
        age = (now - stamp.astimezone(timezone.utc)).total_seconds()
        if age < -10 or age > 30:
            return jsonify({"error": "Tick fuera de ventana temporal.", "age_seconds": round(age, 3)}), 400
        row = {
            "symbol": symbol, "broker_symbol": broker_symbol,
            "bid": bid, "ask": ask, "mid": (bid + ask) / 2,
            "spread": ask - bid, "timestamp_utc": stamp.astimezone(timezone.utc).isoformat(),
            "received_at": now.isoformat(), "age_seconds": round(max(age, 0), 3),
            "source": "VANTAGE_MT5_TEST", "reference_only": True,
            "signals_activated": False, "orders_sent": False,
        }
        with MT5_LIVE_TICK_LOCK:
            MT5_LIVE_TICK.clear()
            MT5_LIVE_TICK.update(row)
        return jsonify({"ok": True, "accepted": True, "source": row["source"],
                        "signals_activated": False, "orders_sent": False}), 200

    with MT5_LIVE_TICK_LOCK:
        row = dict(MT5_LIVE_TICK)
    if not row:
        return jsonify({"available": False, "reason": "Todavía no se ha recibido ningún tick MT5."}), 404
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["received_at"])).total_seconds()
    row["server_age_seconds"] = round(age, 3)
    row["fresh"] = age <= 15
    if age > 60:
        return jsonify({"available": False, "reason": "El último tick MT5 está obsoleto."}), 404
    return jsonify({"available": True, **row}), 200


@app.route("/api/price")
def price():
    symbol = get_symbol(request.args.get("symbol"))
    if not symbol:
        return jsonify({
            "error": "Símbolo no permitido",
            "allowed_symbols": sorted(ALLOWED_SYMBOLS),
        }), 400
    if DATA_MODE != "mock":
        return jsonify({
            "error": "TEST está bloqueado para proveedores externos.",
            "data_mode": DATA_MODE,
        }), 503
    candles, _, _ = generate_mock_candles(symbol, "5min")
    return jsonify({
        "symbol": symbol,
        "price": candles[-1]["close"],
        "data_mode": "mock",
        "warning": "PRECIO SIMULADO: solo para pruebas, no operar.",
    })


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
        live_bundle = None
        if DATA_MODE == "biquote":
            if symbol != "XAU/USD":
                raise RuntimeError("El motor BiQuote TEST está limitado a XAU/USD.")
            frames = {
                "5min": ("5m", "15m", "30m"),
                "15min": ("15m", "30m", "1h"),
                "30min": ("30m", "1h", "4h"),
            }
            base_interval, htf_1, htf_2 = frames[interval]
            base = cached_biquote_research_ohlc(symbol, base_interval)
            higher_1 = cached_biquote_research_ohlc(symbol, htf_1)
            higher_2 = cached_biquote_research_ohlc(symbol, htf_2)
            if base["closed_bars"] < 120 or higher_1["closed_bars"] < 60 or higher_2["closed_bars"] < 60:
                raise RuntimeError("Historial BiQuote insuficiente; análisis bloqueado sin datos sintéticos.")
            candles = base["bars"]
            candle_time = base["last_closed_open_time"]
            candle_age_minutes = base["last_closed_age_minutes"]
            strategy = analyze_strategy(
                candles, interval, symbol,
                higher_timeframes={
                    str(BIQUOTE_INTERVAL_MINUTES[htf_1]) + "min": higher_1["bars"],
                    str(BIQUOTE_INTERVAL_MINUTES[htf_2]) + "min": higher_2["bars"],
                },
            )
            live_bundle = {
                "provider": "biquote",
                "price_basis": "provider_feed_not_verified_as_VANTAGE",
                "reference_only": True,
                "execution_price": False,
                "signals_activated": False,
                "last_closed": {
                    base_interval: base["last_closed_close_time"],
                    htf_1: higher_1["last_closed_close_time"],
                    htf_2: higher_2["last_closed_close_time"],
                },
                "candles_by_timeframe": {
                    base_interval: base["closed_bars"],
                    htf_1: higher_1["closed_bars"],
                    htf_2: higher_2["closed_bars"],
                },
            }
        else:
            candles, candle_time, candle_age_minutes = fetch_candles(symbol, interval)

        closes = [candle["close"] for candle in candles]
        ema20 = ema(closes, 20)
        ema50 = ema(closes, 50)
        rsi14 = rsi(closes, 14)
        atr14 = atr(candles, 14)
        macd_line, macd_signal, macd_hist = macd(closes)
        bb_upper, bb_middle, bb_lower = bollinger(closes, 20, 2.0)

        if (
            None in (ema20, ema50, rsi14, atr14, macd_line, macd_signal, macd_hist,
                     bb_upper, bb_middle, bb_lower)
            or atr14 <= 0
        ):
            raise RuntimeError(
                "No hay suficientes datos válidos para calcular los indicadores."
            )

        if DATA_MODE != "biquote":
            strategy = analyze_strategy(candles, interval, symbol)
        raw_signal = strategy.get("signal", "WAIT")
        pre_signal = bool(strategy.get("pre_signal"))
        if pre_signal:
            signal = "PRESEÑAL"
        elif raw_signal == "BUY":
            signal = "COMPRA"
        elif raw_signal == "SELL":
            signal = "VENTA"
        else:
            signal = "ESPERAR"

        quality_grade = strategy.get("quality_grade", "C")
        quality_score = strategy.get("quality_score", 0)
        quality_label = {
            "A+": "Excelente confluencia",
            "A": "Buena confluencia",
            "B": "Preseñal / vigilancia",
            "C": "Confluencia insuficiente",
        }.get(quality_grade, "Sin clasificar")
        warning = (
            "DATOS SIMULADOS: entorno TEST. No usar para operar ni enviar órdenes."
            if DATA_MODE == "mock"
            else "Señal técnica experimental. Verifica feed, costes y ejecución; no envía órdenes."
        )

        response_payload = {
            "symbol": symbol,
            "interval": interval,
            "signal": signal,
            "reason": strategy.get("reason", "Esperando confirmación."),
            "price": strategy.get("price", closes[-1]),
            "entry": strategy.get("entry"),
            "stop_loss": strategy.get("stop_loss"),
            "take_profit": strategy.get("take_profit"),
            "planned_entry": strategy.get("planned_entry"),
            "planned_stop_loss": strategy.get("planned_stop_loss"),
            "planned_take_profit": strategy.get("planned_take_profit"),
            "planned_risk_distance": strategy.get("planned_risk_distance"),
            "planned_risk_reward": strategy.get("planned_risk_reward"),
            "planned_entry_type": strategy.get("planned_entry_type"),
            "risk_distance": strategy.get("risk_distance"),
            "risk_reward": strategy.get("risk_reward"),
            "ema20": strategy.get("ema20", ema20),
            "ema50": strategy.get("ema50", ema50),
            "rsi14": strategy.get("rsi14", rsi14),
            "atr14": strategy.get("atr14", atr14),
            "adx14": strategy.get("adx14"),
            "plus_di": strategy.get("plus_di"),
            "minus_di": strategy.get("minus_di"),
            "macd": macd_line,
            "macd_signal": macd_signal,
            "macd_histogram": strategy.get("macd_histogram", macd_hist),
            "bollinger_upper": bb_upper,
            "bollinger_middle": bb_middle,
            "bollinger_lower": bb_lower,
            "buy_score": strategy.get("buy_score", 0),
            "sell_score": strategy.get("sell_score", 0),
            "score": strategy.get("score", 0),
            "score_max": 7,
            "quality_grade": quality_grade,
            "quality_score": quality_score,
            "quality_max": 100,
            "quality_label": quality_label,
            "quality_rule": (
                "A/A+: 6–7 de 7, MTF alineado y ruptura/retest; "
                "B: vigilancia/preseñal sin entrada; C: esperar. "
                "La puntuación no es una probabilidad de acierto."
            ),
            "pre_signal": pre_signal,
            "pre_signal_side": strategy.get("pre_signal_side"),
            "primary_bias": strategy.get("primary_bias"),
            "mtf": strategy.get("mtf"),
            "regime": strategy.get("regime"),
            "structure": strategy.get("structure"),
            "structure_trigger": strategy.get("structure_trigger"),
            "score_components": strategy.get("score_components"),
            "status_code": strategy.get("status_code"),
            "candle_time": candle_time,
            "candle_age_minutes": candle_age_minutes,
            "candles": len(candles),
            "recent_candles": [
                {"time": candle["time"], "high": candle["high"],
                 "low": candle["low"], "close": candle["close"]}
                for candle in candles[-120:]
            ],
            "method": (
                "MTF EMA20/50 (dos marcos superiores) + RSI14 + MACD + DI/ADX; "
                "filtro de régimen; ruptura/retest de rango 20; SL estructural "
                "con buffer ATR; TP objetivo 2R."
            ),
            "data_mode": DATA_MODE,
            "provider": "biquote" if DATA_MODE == "biquote" else "synthetic_mock",
            "price_basis": "provider_feed_not_verified_as_VANTAGE" if DATA_MODE == "biquote" else "synthetic",
            "reference_only": DATA_MODE == "biquote",
            "execution_price": False,
            "signals_activated": False,
            "live_feed": live_bundle,
            "warning": (
                "DATOS REALES BiQuote · niveles de referencia, NO son precios de ejecución Vantage. "
                "Se analiza únicamente con velas cerradas; sin feed fresco no hay análisis."
                if DATA_MODE == "biquote" else warning
            ),
            "indicator_diagnostics": {
                "adx": {
                    "value": round(float(strategy.get("adx14")), 2) if strategy.get("adx14") is not None else None,
                    "required": "contexto >=18",
                    "passed": strategy.get("adx14") is not None and float(strategy.get("adx14")) >= 18,
                },
                "macd": {
                    "value": round(float(strategy.get("macd_histogram")), 6) if strategy.get("macd_histogram") is not None else None,
                    "buy_passed": strategy.get("macd_histogram") is not None and float(strategy.get("macd_histogram")) > 0,
                    "sell_passed": strategy.get("macd_histogram") is not None and float(strategy.get("macd_histogram")) < 0,
                },
                "rsi": {
                    "value": round(float(strategy.get("rsi14")), 2) if strategy.get("rsi14") is not None else None,
                    "buy_passed": strategy.get("rsi14") is not None and 50 <= float(strategy.get("rsi14")) <= 70,
                    "sell_passed": strategy.get("rsi14") is not None and 30 <= float(strategy.get("rsi14")) <= 50,
                },
            },
        }
        response_payload["news_context"] = economic_news_context()
        # Shadow-only comparison: the original signal is never modified or activated.
        response_payload["experimental_shadow"] = evaluate_shadow_gate(
            symbol=symbol, timeframe=interval, signal=raw_signal, data_mode=DATA_MODE
        )
        return jsonify(response_payload)
    except requests.RequestException:
        return jsonify({
            "error": "La solicitud de datos de prueba no pudo completarse.",
            "data_mode": DATA_MODE,
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
            "data_provider": "biquote" if DATA_MODE == "biquote" else "synthetic_mock",
            "provider_configured": DATA_MODE == "biquote" and BIQUOTE_RESEARCH_ENABLED,
            "data_mode": DATA_MODE,
            "signals_activated": False,
            "automatic_push_scan_enabled": DATA_MODE == "biquote" and BIQUOTE_RESEARCH_ENABLED and bool(SIGNAL_CRON_SECRET),
            "automatic_push_timeframes": ["5min", "15min"] if DATA_MODE == "biquote" else [],
            "automatic_push_symbols": ["XAU/USD"] if DATA_MODE == "biquote" else [],
            "automatic_execution": False,
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
                # A VAPID key rotation makes old subscriptions permanently unusable too.
                if status in (404, 410) or (status == 400 and "VapidPkHashMismatch" in str(detail)):
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


def resolve_pending_signal_outcomes(symbol, interval, recent_candles):
    """Cierra TP/SL o caduca señales tras 24 velas cerradas posteriores."""
    if not recent_candles:
        return {"resolved": 0, "expired": 0, "errors": 0}
    response = requests.get(        SUPABASE_URL + "/rest/v1/signal_history",
        params={
            "select": "id,direction,candle_time,entry_price,stop_loss,take_profit,status",
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
            entry_price = float(signal["entry_price"])
            initial_risk = abs(entry_price - stop)
            if initial_risk <= 0:
                raise ValueError("Riesgo inicial inválido para calcular el resultado.")
            expired_result_r = (
                (close_price - entry_price) if direction == "COMPRA"
                else (entry_price - close_price)
            ) / initial_risk

            patch_data = {
                "status": outcome,
                "exit_price": close_price if is_expired else (stop if outcome == "SL" else target),
                "closed_at": closed_at,
                "result_r": round(expired_result_r, 6) if is_expired else (2.0 if outcome == "TP" else -1.0),
                "result_source": (
                    "CADUCADA tras 24 velas cerradas; resultado R calculado al cierre "
                    "de la vela 24 usando el riesgo inicial y el precio de entrada."
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


def build_test_alert_event(analysis):
    """Build a TEST-only alert preview; never sends a push or creates an order."""
    if not isinstance(analysis, dict):
        raise ValueError("analysis must be an object")
    symbol = get_symbol(analysis.get("symbol"))
    interval = analysis.get("interval")
    if not symbol or interval not in ALLOWED_INTERVALS:
        raise ValueError("unsupported symbol or timeframe")
    if analysis.get("pre_signal") is True:
        side = analysis.get("pre_signal_side")
        if side not in ("BUY", "SELL"):
            return None
        return {
            "event_type": "PREALERTA",
            "symbol": symbol, "interval": interval, "side": side,
            "entry": None, "stop_loss": None, "take_profit": None,
            "message": f"{symbol} · {interval}: posible {side}. Prepara MT5; todavía no hay entrada confirmada.",
        }
    direction = analysis.get("signal")
    if direction in ("BUY", "SELL"):
        return {
            "event_type": "ENTRADA_VALIDADA",
            "symbol": symbol, "interval": interval, "side": direction,
            "entry": analysis.get("entry"), "stop_loss": analysis.get("stop_loss"),
            "take_profit": analysis.get("take_profit"),
            "message": f"{symbol} · {interval}: {direction} confirmada.",
        }
    return None


@app.route("/api/test/alert-preview", methods=["POST"])
def test_alert_preview():
    """Offline preview of the two-stage alert contract. No push, provider or broker."""
    if DATA_MODE != "mock":
        return jsonify({"error": "Este endpoint es exclusivo de TEST."}), 403
    payload = request.get_json(silent=True) or {}
    try:
        event = build_test_alert_event(payload.get("analysis"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({
        "ok": True, "dry_run": True, "push_sent": False,
        "provider_calls": 0, "orders_sent": False,
        "event": event,
    })


@app.route("/api/push/scan", methods=["POST"])
def push_scan():
    # This scanner is enabled only in the dedicated BiQuote TEST service.
    # BiQuote remains reference-only: no Vantage execution price and no orders.
    if DATA_MODE != "biquote" or not BIQUOTE_RESEARCH_ENABLED:
        return jsonify({
            "error": "El escáner automático requiere BiQuote en TEST.",
            "data_mode": DATA_MODE,
            "signals_activated": False,
            "orders_sent": False,
        }), 503
    supplied = request.headers.get("X-Scanner-Cron-Secret", "")
    if not SIGNAL_CRON_SECRET or not hmac.compare_digest(supplied, SIGNAL_CRON_SECRET):
        return jsonify({"error": "No autorizado."}), 401

    results = []
    sent_total = 0
    token = access_serializer().dumps({"access": True})
    intervals = ["5min", "15min"]
    symbols = ["XAU/USD"]

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
                outcome_result = resolve_pending_signal_outcomes(
                    symbol, interval, data.get("recent_candles", [])
                )
                trade_direction = data.get("signal") or "ESPERAR"
                if data.get("pre_signal") is True and data.get("pre_signal_side") in ("BUY", "SELL"):
                    direction = "PREPARAR_" + ("COMPRA" if data["pre_signal_side"] == "BUY" else "VENTA")
                elif trade_direction in ("COMPRA", "VENTA"):
                    # Stage 2 requires a fresh BiQuote quote and a confirmed structure trigger.
                    # It is still reference-only and must never be presented as Vantage execution.
                    try:
                        quote_check = fetch_biquote_research_tick(symbol)
                        quote_fresh = (
                            quote_check.get("accepted") is True
                            and quote_check.get("quote_age_seconds") is not None
                            and float(quote_check["quote_age_seconds"]) <= 60
                        )
                    except Exception:
                        quote_fresh = False
                    structure_confirmed = data.get("structure_trigger") not in (None, "", "NONE", "PENDING")
                    direction = (
                        "ENTRADA_VALIDADA_" + trade_direction
                        if quote_fresh and structure_confirmed else "ESPERAR"
                    )
                else:
                    direction = "ESPERAR"

                # Asegurar el historial en cada ciclo para señales activas.
                # La clave única hace esta escritura idempotente: si ya existe,
                # Supabase ignora el duplicado; si faltaba por un fallo previo,
                # queda recuperada aunque el estado ya estuviera registrado.
                candle_time = str(data.get("candle_time") or "")
                if direction.startswith("ENTRADA_VALIDADA_"):
                    signal_key = "|".join([symbol, interval, trade_direction, candle_time])
                    history_payload = {
                        "signal_key": signal_key,
                        "symbol": symbol,
                        "timeframe": interval,
                        "direction": trade_direction,
                        "entry_price": data.get("entry"),
                        "stop_loss": data.get("stop_loss"),
                        "take_profit": data.get("take_profit"),
                        "signal_at": datetime.now(timezone.utc).isoformat(),
                        "candle_time": data.get("candle_time"),
                        "status": "PENDIENTE",
                        "indicators": {
                            "data_provider": "biquote_reference_only",
                            "execution_price": False,
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
                        # Stage 2 must follow a recent Stage 1 for the SAME symbol,
                        # timeframe and side. A validated M5 setup must never be
                        # presented as the continuation of an M15 prealert.
                        if direction.startswith("ENTRADA_VALIDADA_"):
                            expected_prealert = "PREPARAR_" + direction.removeprefix("ENTRADA_VALIDADA_")
                            previous_created = previous_rows[0].get("created_at") if previous_rows else None
                            prealert_is_recent = False
                            if previous_direction == expected_prealert and previous_created:
                                try:
                                    created_dt = datetime.fromisoformat(previous_created.replace("Z", "+00:00"))
                                    if created_dt.tzinfo is None:
                                        created_dt = created_dt.replace(tzinfo=timezone.utc)
                                    max_age_minutes = 45 if interval == "5min" else 90
                                    prealert_is_recent = (
                                        0 <= (datetime.now(timezone.utc) - created_dt).total_seconds()
                                        <= max_age_minutes * 60
                                    )
                                except (TypeError, ValueError):
                                    prealert_is_recent = False
                            if not prealert_is_recent:
                                results.append({
                                    "symbol": symbol, "interval": interval,
                                    "status": "validated_without_matching_recent_prealert_no_notification",
                                    "direction": direction,
                                    "previous_direction": previous_direction,
                                    "required_previous_direction": expected_prealert,
                                })
                                continue

                        # El historial ya quedó asegurado arriba antes de leer/guardar el estado.
                        # El estado se guarda solo después de asegurar el historial.
                        # Si falla este paso, la inserción idempotente del historial
                        # permitirá reintentar el siguiente ciclo sin duplicar señales.
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
                        price_digits = 2

                        def display_price(value):
                            return f"{float(value):,.{price_digits}f}" if value is not None else "—"

                        short_interval = {"5min": "M5", "15min": "M15"}.get(interval, interval)
                        is_prealert = direction.startswith("PREPARAR_")
                        signal_side = "COMPRA" if direction.endswith("COMPRA") else "VENTA"
                        entry_value = data.get("planned_entry") if is_prealert else data.get("entry")
                        stop_value = data.get("planned_stop_loss") if is_prealert else data.get("stop_loss")
                        target_value = data.get("planned_take_profit") if is_prealert else data.get("take_profit")
                        if is_prealert:
                            alert_title = "PREPARAR MT5 · XAU/USD"
                            alert_body = (
                                f"PREALERTA {signal_side} · {short_interval}. "
                                f"Nivel provisional {display_price(entry_value)} · "
                                f"SL {display_price(stop_value)} · TP {display_price(target_value)}. "
                                "Aún NO es entrada. Datos BiQuote de referencia; compara con Vantage."
                            )
                        else:
                            alert_title = "ENTRADA VALIDADA · REFERENCIA"
                            alert_body = (
                                f"XAU/USD · {short_interval} · {signal_side}. "
                                f"Nivel técnico {display_price(entry_value)} · "
                                f"SL {display_price(stop_value)} · TP {display_price(target_value)}. "
                                "Confirmación técnica BiQuote, NO precio Vantage. Verifica cotización y spread antes de decidir."
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
                                        "title": alert_title,
                                        "body": alert_body,
                                        "tag": "gss-" + symbol.replace("/", "") + "-" + interval + "-" + direction,
                                        "url": (
                                            "https://global-signal-push-test-panel.onrender.com/panel_escaner_vantage.html?"
                                            + "view=scanner"
                                            + "&symbol=" + requests.utils.quote(symbol, safe="")
                                            + "&timeframe=" + requests.utils.quote(interval, safe="")
                                            + "&alert_stage=" + requests.utils.quote("PREALERTA" if is_prealert else "ENTRADA_VALIDADA", safe="")
                                            + "&alert_side=" + requests.utils.quote(signal_side, safe="")
                                            + "&alert_entry=" + requests.utils.quote("" if entry_value is None else str(entry_value), safe="")
                                            + "&alert_stop_loss=" + requests.utils.quote("" if stop_value is None else str(stop_value), safe="")
                                            + "&alert_take_profit=" + requests.utils.quote("" if target_value is None else str(target_value), safe="")
                                            + "&alert_candle_time=" + requests.utils.quote(candle_time, safe="")
                                        ),
                                        "data": {
                                            "symbol": symbol, "interval": interval,
                                            "event_type": "PREALERTA" if is_prealert else "ENTRADA_VALIDADA",
                                            "direction": signal_side,
                                            "reference_only": True,
                                            "execution_price": False,
                                            "entry": entry_value,
                                            "stop_loss": stop_value,
                                            "take_profit": target_value,
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
