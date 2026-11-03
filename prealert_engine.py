"""Provider-free VANTAGE prealert state machine; accepts local data only."""
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone

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
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

def evaluate_prealert(*, symbol, direction, current_price, entry_zone_low, entry_zone_high,
                      setup_valid, confirmation_valid, invalidated, data_timestamp, now,
                      approach_distance, max_data_age_seconds=90, ttl_seconds=180,
                      previous=None, alert_id="local-test"):
    """Evaluate a strategy snapshot. Does not promise an exact 60-second lead."""
    direction=direction.upper()
    if direction not in {"BUY","SELL"}: raise ValueError("direction must be BUY or SELL")
    if not symbol or current_price<=0 or approach_distance<0 or ttl_seconds<=0: raise ValueError("invalid input")
    if entry_zone_low<=0 or entry_zone_high<entry_zone_low: raise ValueError("invalid entry zone")
    now,data=_utc(now),_utc(data_timestamp)
    created=_utc(datetime.fromisoformat(previous["created_at"])) if previous and previous.get("created_at") else now
    expiry=created+timedelta(seconds=ttl_seconds)
    if (now-data).total_seconds()>max_data_age_seconds or data>now:
        return ({**previous,"state":"CANCELADA","reason":"datos_obsoletos"}
                if previous and previous.get("state") in {"PREALERTA","CONFIRMADA"} else None)
    if previous and previous.get("state") in {"CANCELADA","EXPIRADA","CONFIRMADA"}: return previous
    if previous and now>=_utc(datetime.fromisoformat(previous["expires_at"])):
        return {**previous,"state":"EXPIRADA","reason":"ventana_de_prealerta_vencida"}
    if invalidated or not setup_valid:
        return ({**previous,"state":"CANCELADA","reason":"configuracion_invalidada"}
                if previous and previous.get("state")=="PREALERTA" else None)
    distance=(0.0 if entry_zone_low<=current_price<=entry_zone_high else
              current_price-entry_zone_high if current_price>entry_zone_high else entry_zone_low-current_price)
    if confirmation_valid and distance==0:
        return Prealert("CONFIRMADA",direction,symbol,entry_zone_low,entry_zone_high,current_price,0.0,
                        created.isoformat(),expiry.isoformat(),"condiciones_confirmadas_en_zona",alert_id).to_dict()
    if distance>approach_distance:
        return ({**previous,"state":"CANCELADA","reason":"precio_se_alejo_de_la_zona"}
                if previous and previous.get("state")=="PREALERTA" else None)
    if previous and previous.get("state")=="PREALERTA":
        return {**previous,"current_price":current_price,"distance_to_zone":distance,"reason":"setup_sigue_vigente"}
    return Prealert("PREALERTA",direction,symbol,entry_zone_low,entry_zone_high,current_price,distance,
                    now.isoformat(),(now+timedelta(seconds=ttl_seconds)).isoformat(),
                    "precio_acercandose_a_zona; confirmar_antes_de_entrar",alert_id).to_dict()
