"""Offline CSV replay for VANTAGE prealerts. No network or provider access."""
import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from prealert_engine import evaluate_prealert


def _parse_bool(value):
    return str(value).strip().lower() in {"1", "true", "yes", "si", "sí"}


def _parse_time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def replay_rows(rows, *, approach_distances=None, max_data_age_seconds=90, ttl_seconds=180):
    """Replay chronologically ordered snapshots with precomputed strategy context.

    Required CSV columns:
      timestamp,symbol,direction,price,zone_low,zone_high,setup_valid,
      confirmation_valid,invalidated
    Optional: data_timestamp (defaults to timestamp).
    """
    approach_distances = approach_distances or {}
    rows = sorted(rows, key=lambda row: _parse_time(row["timestamp"]))
    previous_by_key = {}
    previous_price_by_key = {}
    events = []
    terminal_keys = set()
    for row in rows:
        symbol = row["symbol"].strip()
        direction = row["direction"].strip().upper()
        key = (symbol, direction)
        now = _parse_time(row["timestamp"])
        data_time = _parse_time(row.get("data_timestamp") or row["timestamp"])
        price = float(row["price"])
        zone_low, zone_high = float(row["zone_low"]), float(row["zone_high"])
        default_distance = max((zone_high - zone_low) * 2, abs(price) * 0.0002)
        result = evaluate_prealert(
            symbol=symbol,
            direction=direction,
            current_price=price,
            entry_zone_low=zone_low,
            entry_zone_high=zone_high,
            setup_valid=_parse_bool(row["setup_valid"]),
            confirmation_valid=_parse_bool(row["confirmation_valid"]),
            invalidated=_parse_bool(row["invalidated"]),
            data_timestamp=data_time,
            now=now,
            approach_distance=float(approach_distances.get(symbol, default_distance)),
            max_data_age_seconds=max_data_age_seconds,
            ttl_seconds=ttl_seconds,
            previous_price=previous_price_by_key.get(key),
            previous=previous_by_key.get(key),
        )
        if not _parse_bool(row["setup_valid"]) or _parse_bool(row["invalidated"]):
            terminal_keys.discard(key)
        if key in terminal_keys:
            previous_price_by_key[key] = price
            continue
        prior = previous_by_key.get(key)
        if result and (not prior or result.get("alert_id") != prior.get("alert_id") or result.get("state") != prior.get("state")):
            events.append({
                "timestamp": now.isoformat(),
                "symbol": symbol,
                "direction": direction,
                "state": result["state"],
                "alert_id": result["alert_id"],
                "price": price,
                "distance_to_zone": result["distance_to_zone"],
                "reason": result["reason"],
            })
        if result and result["state"] == "PREALERTA":
            previous_by_key[key] = result
        else:
            previous_by_key.pop(key, None)
            if result and result["state"] in {"CONFIRMADA", "CANCELADA", "EXPIRADA"}:
                terminal_keys.add(key)
        previous_price_by_key[key] = price

    by_id = {}
    for event in events:
        by_id.setdefault(event["alert_id"], []).append(event)
    confirmed_leads = []
    totals = Counter()
    for lifecycle in by_id.values():
        states = [item["state"] for item in lifecycle]
        totals["prealerts"] += states.count("PREALERTA")
        if "CONFIRMADA" in states:
            totals["confirmed"] += 1
            first = next(item for item in lifecycle if item["state"] == "PREALERTA")
            confirmed = next(item for item in lifecycle if item["state"] == "CONFIRMADA")
            lead = (_parse_time(confirmed["timestamp"]) - _parse_time(first["timestamp"])).total_seconds()
            confirmed_leads.append(lead)
        elif "CANCELADA" in states:
            totals["cancelled_without_confirmation"] += 1
        elif "EXPIRADA" in states:
            totals["expired_without_confirmation"] += 1

    total_lifecycles = len(by_id)
    unconfirmed = totals["cancelled_without_confirmation"] + totals["expired_without_confirmation"]
    return {
        "input_rows": len(rows),
        "alert_lifecycles": total_lifecycles,
        "active_at_end": sum(1 for item in previous_by_key.values() if item.get("state") == "PREALERTA"),
        "confirmed": totals["confirmed"],
        "cancelled_without_confirmation": totals["cancelled_without_confirmation"],
        "expired_without_confirmation": totals["expired_without_confirmation"],
        "unconfirmed_rate": round(unconfirmed / total_lifecycles, 4) if total_lifecycles else None,
        "lead_seconds": {
            "count": len(confirmed_leads),
            "min": min(confirmed_leads) if confirmed_leads else None,
            "median": sorted(confirmed_leads)[len(confirmed_leads)//2] if confirmed_leads else None,
            "max": max(confirmed_leads) if confirmed_leads else None,
            "within_60_seconds": sum(0 <= value <= 60 for value in confirmed_leads),
        },
        "events": events,
        "data_note": "Offline replay of supplied snapshots; not a prediction or profitability test.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_file", type=Path)
    parser.add_argument("--max-data-age", type=int, default=90)
    parser.add_argument("--ttl", type=int, default=180)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with args.csv_file.open(newline="", encoding="utf-8-sig") as handle:
        report = replay_rows(list(csv.DictReader(handle)),
                             max_data_age_seconds=args.max_data_age,
                             ttl_seconds=args.ttl)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
