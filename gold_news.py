"""Gold-only calendar: third-party schedule, never predicts news direction."""
import re
import threading
import time
from datetime import datetime, timezone, timedelta
import requests

URL = "https://www.xauusdglobal.com/calendar/gold-high-impact.ics"
_CACHE = {"at": 0.0, "events": [], "verified": False, "error": "not_loaded"}
_LOCK = threading.Lock()
TTL_SECONDS = 1800

def _parse(text):
    events = []
    for block in text.replace("\r\n", "\n").split("BEGIN:VEVENT")[1:]:
        lines = re.sub(r"\n[ \t]", "", block.split("END:VEVENT", 1)[0]).splitlines()
        fields = {}
        for line in lines:
            if ":" in line:
                key, value = line.split(":", 1)
                fields[key.split(";", 1)[0]] = value
        raw = fields.get("DTSTART", "")
        if not re.fullmatch(r"\d{8}T\d{6}Z", raw) or not fields.get("SUMMARY"):
            continue
        stamp = datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        events.append({"at_utc": stamp.isoformat(), "title": fields["SUMMARY"][:180],
                       "impact": "high", "source": URL})
    return sorted(events, key=lambda x: x["at_utc"])

def gold_news_context(now=None):
    now = now or datetime.now(timezone.utc)
    with _LOCK:
        if time.monotonic() - _CACHE["at"] >= TTL_SECONDS:
            try:
                response = requests.get(URL, timeout=5, headers={"User-Agent": "GoldScanner/1.0"})
                response.raise_for_status()
                parsed = _parse(response.text)
                if not parsed:
                    raise ValueError("No timezone-verified events")
                _CACHE.update(at=time.monotonic(), events=parsed, verified=True, error=None)
            except (requests.RequestException, ValueError) as exc:
                _CACHE.update(at=time.monotonic(), events=[], verified=False, error=type(exc).__name__)
        verified = _CACHE["verified"]
        events = list(_CACHE["events"])
        error = _CACHE["error"]
    near = [event for event in events
            if -1800 <= (datetime.fromisoformat(event["at_utc"]) - now).total_seconds() <= 1200]
    upcoming = [event for event in events if datetime.fromisoformat(event["at_utc"]) >= now][:8]
    return {"verified": verified, "source": URL, "source_type": "third_party_calendar",
            "impact_interpretation": "schedule_only_no_direction_prediction",
            "error": error, "high_impact_window": bool(verified and near),
            "block_new_entries": bool(verified and near),
            "block_reason": ("CALENDAR_UNAVAILABLE" if not verified else
                             "HIGH_IMPACT_NEWS_WINDOW" if near else None),
            "near_events": near, "upcoming_events": upcoming,
            "pre_event_minutes": 30, "post_event_minutes": 20}
