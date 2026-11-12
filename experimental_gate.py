"""Shadow-only eligibility gate for GLOBAL SIGNAL SCANNER TEST.

This module never changes the original signal, creates entries, sends notifications,
or submits broker orders. It reports what the experimental allowlist would do.
"""
from __future__ import annotations

POLICY_VERSION = "shadow-allowlist-v0"
ALLOWED_COMBINATIONS = frozenset({
    ("XAUUSD", "15min", "BUY"),
})

def evaluate_shadow_gate(symbol: str, timeframe: str, signal: str, data_mode: str = "mock") -> dict:
    """Describe the hypothetical gate decision without modifying any signal."""
    normalized_symbol = str(symbol or "").upper().replace("/", "").replace("_", "")
    normalized_timeframe = str(timeframe or "").strip().lower()
    normalized_signal = str(signal or "WAIT").strip().upper()
    is_candidate = normalized_signal in {"BUY", "SELL"}
    allowed = (normalized_symbol, normalized_timeframe, normalized_signal) in ALLOWED_COMBINATIONS
    would_block = bool(is_candidate and not allowed)
    if not is_candidate:
        decision = "NO_ENTRY_TO_FILTER"
        reason = "La estrategia original no produjo una entrada confirmada."
    elif allowed:
        decision = "WOULD_ALLOW"
        reason = "La combinación figura en la lista experimental de investigación."
    else:
        decision = "WOULD_BLOCK"
        reason = "La combinación no figura en la lista experimental de investigación."
    return {
        "mode": "shadow", "enabled": False, "policy_version": POLICY_VERSION,
        "original_signal": normalized_signal, "would_block": would_block,
        "decision": decision, "reason": reason, "data_mode": data_mode,
        "allowlist": [{"symbol": sym, "timeframe": tf, "signal": side}
                      for sym, tf, side in sorted(ALLOWED_COMBINATIONS)],
        "safety": {"original_signal_unchanged": True, "orders_enabled": False,
                   "notifications_enabled_by_gate": False, "provider_calls_by_gate": 0},
    }
