"""Provider-free VANTAGE prealert state machine.

Consumes only a caller-supplied market snapshot. This module never fetches data,
places orders, sends notifications, or calls an external service.
"""
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
import hashlib


@dataclass(frozen=True)
class Prealert:
    state: str
    direction: str
    symbol: str
    entry_zone_low: float
    entry_zone_high: float
    current_price: float
    distance_to_zone: float
    created_at: str
    expires_at: str
    reason: str
    alert_id: str

    def to_dict(self):
        return asdict(self)


def _utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _alert_id(symbol, direction, low, high, created_at):
    identity = f"{symbol}|{direction}|{low:.12g}|{high:.12g}|{created_at.isoformat()}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def evaluate_prealert(*, symbol, direction, current_price, entry_zone_low, entry_zone_high,
                      setup_valid, confirmation_valid, invalidated, data_timestamp, now,
                      approach_distance, max_data_age_seconds=90, ttl_seconds=180,
                      previous=None):
    """Evaluate one snapshot; a prealert is not a promise of entry in 60 seconds."""
    direction = direction.upper()
    if direction not in {"BUY", "SELL"}:
        raise ValueError("direction must be BUY or SELL")
    if not symbol or current_price <= 0 or approach_distance < 0 or ttl_seconds <= 0:
        raise ValueError("invalid symbol, price, distance, or TTL")
    if entry_zone_low <= 0 or entry_zone_high < entry_zone_low:
        raise ValueError("invalid entry zone")
    if max_data_age_seconds < 0:
        raise ValueError("max_data_age_seconds cannot be negative")

    now, data = _utc(now), _utc(data_timestamp)
    created = (_utc(datetime.fromisoformat(previous["created_at"]))
               if previous and previous.get("created_at") else now)
    expires = created + timedelta(seconds=ttl_seconds)

    # Stale/future data must never create or preserve an actionable warning.
    if (now - data).total_seconds() > max_data_age_seconds or data > now:
        if previous and previous.get("state") in {"PREALERTA", "CONFIRMADA"}:
            return {**previous, "state": "CANCELADA", "reason": "datos_obsoletos"}
        return None

    if previous and previous.get("state") in {"CANCELADA", "EXPIRADA", "CONFIRMADA"}:
        return previous
    if previous and now >= _utc(datetime.fromisoformat(previous["expires_at"])):
        return {**previous, "state": "EXPIRADA", "reason": "ventana_de_prealerta_vencida"}

    if invalidated or not setup_valid:
        if previous and previous.get("state") == "PREALERTA":
            return {**previous, "state": "CANCELADA", "reason": "configuracion_invalidada"}
        return None

    # BUY approaches the zone from above; SELL approaches from below.
    # A price that has already crossed the zone must not create a late warning.
    if direction == "BUY" and current_price < entry_zone_low:
        if previous and previous.get("state") == "PREALERTA":
            return {**previous, "state": "CANCELADA", "reason": "zona_buy_ya_fue_cruzada"}
        return None
    if direction == "SELL" and current_price > entry_zone_high:
        if previous and previous.get("state") == "PREALERTA":
            return {**previous, "state": "CANCELADA", "reason": "zona_sell_ya_fue_cruzada"}
        return None

    if entry_zone_low <= current_price <= entry_zone_high:
        distance = 0.0
    elif direction == "BUY":
        distance = current_price - entry_zone_high
    else:
        distance = entry_zone_low - current_price

    if confirmation_valid and distance == 0:
        return Prealert(
            "CONFIRMADA", direction, symbol, entry_zone_low, entry_zone_high,
            current_price, 0.0, created.isoformat(), expires.isoformat(),
            "condiciones_confirmadas_en_zona",
            previous["alert_id"] if previous else _alert_id(symbol, direction, entry_zone_low, entry_zone_high, created),
        ).to_dict()

    if distance > approach_distance:
        if previous and previous.get("state") == "PREALERTA":
            return {**previous, "state": "CANCELADA", "reason": "precio_se_alejo_de_la_zona"}
        return None

    if previous and previous.get("state") == "PREALERTA":
        return {**previous, "current_price": current_price,
                "distance_to_zone": distance, "reason": "setup_sigue_vigente"}

    return Prealert(
        "PREALERTA", direction, symbol, entry_zone_low, entry_zone_high,
        current_price, distance, now.isoformat(),
        (now + timedelta(seconds=ttl_seconds)).isoformat(),
        "precio_acercandose_a_zona; confirmar_antes_de_entrar",
        _alert_id(symbol, direction, entry_zone_low, entry_zone_high, now),
    ).to_dict()
