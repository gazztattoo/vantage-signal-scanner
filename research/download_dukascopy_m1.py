#!/usr/bin/env python3
"""Download Dukascopy native M1 BID/ASK candles for offline VANTAGE research.

No Twelve Data, Render, Supabase, broker, or order API calls are made.
Output is research-only and must not be treated as the user's broker feed.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import lzma
import math
import struct
import time
from pathlib import Path

import requests

BASE_URL = "https://datafeed.dukascopy.com/datafeed"
INSTRUMENTS = {"EUR/USD": "EURUSD", "XAU/USD": "XAUUSD", "BTC/USD": "BTCUSD"}
RECORD = struct.Struct(">IIIIIf")  # seconds, open, close, low, high, volume
MAX_HTTP_ATTEMPTS = 5
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
EXPECTED = {
    "EUR/USD": (0.5, 2.0, 1.1),
    "XAU/USD": (200.0, 20000.0, 4000.0),
    "BTC/USD": (1000.0, 200000.0, 60000.0),
}


def _decompress(payload: bytes) -> bytes:
    try:
        return lzma.decompress(payload)
    except lzma.LZMAError as exc:
        raise ValueError("Invalid LZMA/BI5 payload") from exc


def decode_day(payload: bytes, day: dt.date, scale: int) -> list[dict]:
    raw = _decompress(payload)
    if len(raw) % RECORD.size:
        raise ValueError(f"Invalid BI5 length: {len(raw)} bytes")
    start = dt.datetime.combine(day, dt.time(), tzinfo=dt.timezone.utc)
    rows = []
    for offset in range(0, len(raw), RECORD.size):
        seconds, op, close, low, high, volume = RECORD.unpack_from(raw, offset)
        if seconds >= 86400:
            raise ValueError(f"Invalid candle second offset: {seconds}")
        prices = [op / scale, high / scale, low / scale, close / scale]
        o, h, l, c = prices
        if not all(math.isfinite(v) and v > 0 for v in prices):
            continue
        if h < max(o, c, l) or l > min(o, c, h):
            raise ValueError("OHLC integrity check failed")
        timestamp = start + dt.timedelta(seconds=seconds)
        rows.append({
            "timestamp": timestamp.isoformat(),
            "open": o, "high": h, "low": l, "close": c,
            "volume": float(volume),
        })
    return rows


def detect_scale(raw_payload: bytes, symbol: str) -> int:
    raw = _decompress(raw_payload)
    if len(raw) < RECORD.size or len(raw) % RECORD.size:
        raise ValueError("Cannot infer price scale from empty/invalid candle file")
    samples = []
    for offset in range(0, min(len(raw), RECORD.size * 100), RECORD.size):
        _, op, close, low, high, _ = RECORD.unpack_from(raw, offset)
        samples.extend([op, close, low, high])
    median_raw = sorted(samples)[len(samples) // 2]
    low, high, target = EXPECTED[symbol]
    candidates = []
    for power in range(0, 9):
        scale = 10 ** power
        value = median_raw / scale
        if low <= value <= high:
            candidates.append((abs(math.log(value / target)), scale))
    if not candidates:
        raise ValueError(f"No plausible price scale for {symbol}; refusing to guess")
    candidates.sort()
    return candidates[0][1]


def fetch_day(session: requests.Session, instrument: str, day: dt.date, side: str):
    """Fetch one daily file, retrying transient Dukascopy/network failures."""
    url = (f"{BASE_URL}/{instrument}/{day.year}/{day.month - 1:02d}/"
           f"{day.day:02d}/{side}_candles_min_1.bi5")
    for attempt in range(MAX_HTTP_ATTEMPTS):
        try:
            response = session.get(url, timeout=30)
            if response.status_code == 404:
                return None  # No file for this date/side is a valid absence.
            if response.status_code in RETRYABLE_STATUS:
                if attempt == MAX_HTTP_ATTEMPTS - 1:
                    response.raise_for_status()
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = min(15.0, max(0.0, float(retry_after))) if retry_after else min(15.0, 2.0 ** attempt)
                except ValueError:
                    delay = min(15.0, 2.0 ** attempt)
                time.sleep(delay)
                continue
            response.raise_for_status()
            return response.content or None
        except requests.exceptions.RequestException:
            if attempt == MAX_HTTP_ATTEMPTS - 1:
                raise
            time.sleep(min(15.0, 2.0 ** attempt))
    raise RuntimeError(f"Download failed after {MAX_HTTP_ATTEMPTS} attempts: {url}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=28)
    parser.add_argument("--end", default=(dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)).isoformat())
    parser.add_argument("--out", default="research_data/dukascopy_m1")
    args = parser.parse_args()
    if not 14 <= args.days <= 56:
        raise SystemExit("--days must be between 14 and 56")
    end = dt.date.fromisoformat(args.end)
    start = end - dt.timedelta(days=args.days - 1)
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": "VANTAGE-offline-research/1.0"})
    summary = []
    failures = []
    for display_symbol, instrument in INSTRUMENTS.items():
        all_rows = {}
        scale = None
        missing = 0
        for n in range(args.days):
            day = start + dt.timedelta(days=n)
            daily = {}
            for side in ("BID", "ASK"):
                try:
                    payload = fetch_day(session, instrument, day, side)
                except requests.exceptions.RequestException as exc:
                    # Keep valid history already downloaded when the provider has a transient outage.
                    failures.append({"symbol": display_symbol, "date": day.isoformat(), "side": side, "error": str(exc)})
                    daily[side] = []
                    print("WARNING: " + display_symbol + " " + day.isoformat() + " " + side + " failed after retries: " + str(exc), flush=True)
                    continue
                time.sleep(0.20)
                if payload is None:
                    daily[side] = []
                    continue
                if scale is None:
                    scale = detect_scale(payload, display_symbol)
                daily[side] = decode_day(payload, day, scale)
            bid = {r["timestamp"]: r for r in daily["BID"]}
            ask = {r["timestamp"]: r for r in daily["ASK"]}
            for stamp in sorted(set(bid) & set(ask)):
                b, a = bid[stamp], ask[stamp]
                if any(a[k] < b[k] for k in ("open", "high", "low", "close")):
                    raise ValueError(f"ASK below BID for {display_symbol} at {stamp}")
                all_rows[stamp] = {
                    "timestamp": stamp,
                    **{f"bid_{k}": b[k] for k in ("open", "high", "low", "close", "volume")},
                    **{f"ask_{k}": a[k] for k in ("open", "high", "low", "close", "volume")},
                }
            if not bid or not ask:
                missing += 1
        rows = [all_rows[k] for k in sorted(all_rows)]
        target = output / f"{instrument}_M1_BID_ASK.csv"
        if rows:
            with target.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        summary.append({
            "symbol": display_symbol, "instrument": instrument, "scale": scale,
            "days_requested": args.days, "days_with_missing_side": missing,
            "failed_downloads": sum(1 for x in failures if x["symbol"] == display_symbol),
            "matched_bid_ask_bars": len(rows), "file": str(target) if rows else None,
        })
        print(summary[-1], flush=True)
    summary_path = output / "download_summary.txt"
    summary_path.write_text("\n".join(str(x) for x in summary) + "\n", encoding="utf-8")
    if failures:
        failure_path = output / "download_failures.csv"
        with failure_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["symbol", "date", "side", "error"])
            writer.writeheader()
            writer.writerows(failures)
        print("WARNING: " + str(len(failures)) + " requests failed after retries; see " + str(failure_path), flush=True)
    if not any(x["matched_bid_ask_bars"] for x in summary):
        raise SystemExit("No market data downloaded; refusing an empty successful run")


if __name__ == "__main__":
    main()
