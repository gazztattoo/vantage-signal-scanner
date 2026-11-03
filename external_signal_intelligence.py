"""Normalize and score externally published trader ideas.

Research-only module. It consumes structured records obtained through authorized
provider APIs/feeds. It does not scrape websites, fetch data, place orders, or
turn crowd sentiment into a trade instruction.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


SUPPORTED_SYMBOLS = {"XAU/USD", "EUR/USD", "BTC/USD"}
SIDE_MAP = {
    "BUY": "BUY", "LONG": "BUY", "BULLISH": "BUY", "COMPRA": "BUY",
    "SELL": "SELL", "SHORT": "SELL", "BEARISH": "SELL", "VENTA": "SELL",
}


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def normalize_trader_idea(record: dict, *, now: datetime | None = None,
                          max_age_minutes: int = 180) -> dict:
    """Validate one provider-normalized public idea and return a safe record."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not isinstance(record, dict):
        return {"accepted": False, "reason": "Registro inválido."}

    symbol = str(record.get("symbol", "")).upper().replace(" ", "")
    side = SIDE_MAP.get(str(record.get("side", "")).upper())
    published = _parse_time(record.get("published_at"))
    source = str(record.get("source", "")).strip()
    author = str(record.get("author", "")).strip()
    url = str(record.get("url", "")).strip()

    if symbol not in SUPPORTED_SYMBOLS:
        return {"accepted": False, "reason": "Activo no admitido."}
    if side is None:
        return {"accepted": False, "reason": "Dirección no identificable."}
    if not source or not author or not url.startswith(("https://", "http://")):
        return {"accepted": False, "reason": "Falta procedencia verificable."}
    if published is None or published > now:
        return {"accepted": False, "reason": "Fecha ausente o inválida."}

    age_minutes = (now - published).total_seconds() / 60
    if age_minutes > max_age_minutes:
        return {"accepted": False, "reason": "Publicación demasiado antigua.",
                "age_minutes": round(age_minutes, 1)}

    def optional_price(key):
        value = record.get(key)
        if value in (None, ""):
            return None
        try:
            number = float(value)
            return number if number > 0 else None
        except (TypeError, ValueError):
            return None

    entry = optional_price("entry")
    stop = optional_price("stop_loss")
    target = optional_price("take_profit")
    if stop is not None and target is not None and entry is not None:
        if side == "BUY" and not (stop < entry < target):
            return {"accepted": False, "reason": "Niveles BUY incoherentes."}
        if side == "SELL" and not (target < entry < stop):
            return {"accepted": False, "reason": "Niveles SELL incoherentes."}

    # Followers, likes and views are deliberately excluded from quality scoring.
    author_stats = record.get("verified_stats") or {}
    closed = max(0, int(author_stats.get("closed_ideas", 0) or 0))
    wins = max(0, int(author_stats.get("wins", 0) or 0))
    losses = max(0, int(author_stats.get("losses", 0) or 0))
    valid_closed = wins + losses
    hit_rate = wins / valid_closed if valid_closed else None
    sample_factor = min(valid_closed / 30, 1.0)
    age_factor = max(0.0, 1.0 - age_minutes / max_age_minutes)
    # This is a ranking weight, never a win probability.
    author_factor = (0.5 + 0.5 * sample_factor * (hit_rate or 0.0))
    evidence_weight = round(age_factor * author_factor, 4)

    return {
        "accepted": True,
        "symbol": symbol,
        "side": side,
        "source": source,
        "author": author,
        "url": url,
        "published_at": published.isoformat(),
        "age_minutes": round(age_minutes, 1),
        "entry": entry,
        "stop_loss": stop,
        "take_profit": target,
        "verified_closed_ideas": valid_closed,
        "verified_hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
        "evidence_weight": evidence_weight,
        "weight_is_probability": False,
    }


def summarize_external_consensus(records: list[dict], *, symbol: str,
                                 now: datetime | None = None,
                                 max_age_minutes: int = 180) -> dict:
    """Summarize independent author direction without issuing a trade signal."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    normalized = [
        normalize_trader_idea(item, now=now, max_age_minutes=max_age_minutes)
        for item in records
    ]
    accepted = [item for item in normalized if item.get("accepted")
                and item["symbol"] == symbol]
    # One vote per author and source to limit repeated-post amplification.
    unique = {}
    for item in sorted(accepted, key=lambda x: x["published_at"], reverse=True):
        unique.setdefault((item["source"].lower(), item["author"].lower()), item)
    votes = list(unique.values())
    buy = sum(item["evidence_weight"] for item in votes if item["side"] == "BUY")
    sell = sum(item["evidence_weight"] for item in votes if item["side"] == "SELL")
    total = buy + sell
    if not votes or total <= 0:
        direction, agreement = "NO_DATA", 0.0
    elif buy == sell:
        direction, agreement = "MIXED", 0.5
    else:
        direction = "BUY" if buy > sell else "SELL"
        agreement = max(buy, sell) / total

    return {
        "symbol": symbol,
        "external_bias": direction,
        "agreement": round(agreement, 4),
        "independent_authors": len(votes),
        "buy_weight": round(buy, 4),
        "sell_weight": round(sell, 4),
        "ideas": votes,
        "rejected_count": len(normalized) - len(accepted),
        "decision_role": "CONTEXT_ONLY_NOT_A_TRADE_SIGNAL",
        "warning": "El consenso externo no sustituye la confirmación del precio ni del motor técnico.",
    }
