#!/usr/bin/env python3
"""Offline comparison of experimental filters on existing backtest trade CSVs.

This is a screening tool, not a full re-simulation: filtered variants are
subsets of baseline trades and skipped entries are not regenerated. No network,
broker, live market data, or provider credits are used.
"""
import argparse
import csv
import json
import math
from pathlib import Path

VARIANTS = {
    "BASELINE": lambda r: True,
    "SELL_SCORE4": lambda r: r["side"] == "SELL" and r["score"] == 4,
    "BUY_SCORE3": lambda r: r["side"] == "BUY" and r["score"] == 3,
    "COMBINED": lambda r: (r["side"] == "SELL" and r["score"] == 4)
    or (r["side"] == "BUY" and r["score"] == 3),
}


def read_trades(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"signal_time", "side", "score"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"{path}: required columns are {sorted(required)}")
        # Prefer corrected mark-to-market R when available; legacy R may leave
        # expired trades at zero. Fall back only for compatible clean exports.
        r_column = "R_adjusted" if "R_adjusted" in reader.fieldnames else ("result_r" if "result_r" in reader.fieldnames else "R")
        if r_column not in reader.fieldnames:
            raise ValueError(f"{path}: expected R_adjusted, result_r, or R")
        rows = []
        for line, raw in enumerate(reader, start=2):
            side = raw["side"].strip().upper()
            if side not in {"BUY", "SELL"}:
                raise ValueError(f"{path}:{line}: invalid side {side!r}")
            score = int(raw["score"])
            result_r = float(raw[r_column])
            if not math.isfinite(result_r):
                raise ValueError(f"{path}:{line}: non-finite result_r")
            rows.append({"signal_time": raw["signal_time"], "side": side,
                         "score": score, "result_r": result_r})
    rows.sort(key=lambda row: row["signal_time"])
    return rows


def metrics(rows, cost_r):
    values = [row["result_r"] for row in rows]
    equity = peak = max_drawdown = 0.0
    for value in values:
        equity += value - cost_r
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    gross = sum(values)
    return {
        "trades": len(values),
        "gross_r": round(gross, 6),
        "net_after_cost_r": round(gross - cost_r * len(values), 6),
        "average_gross_r": round(gross / len(values), 6) if values else None,
        "max_drawdown_after_cost_r": round(max_drawdown, 6),
        "cost_assumption_r_per_trade": cost_r,
    }


def compare(path, costs):
    trades = read_trades(path)
    result = {
        "source_file": str(path),
        "trades_in_baseline_file": len(trades),
        "warning": "Filtered variants are subsets of baseline trades; skipped entries are not regenerated. Prefer R_adjusted for corrected expiry mark-to-market. Costs are hypothetical R/trade, not broker quotes.",
        "variants": {},
    }
    for name, predicate in VARIANTS.items():
        selected = [row for row in trades if predicate(row)]
        result["variants"][name] = {
            "gross": metrics(selected, 0.0),
            "cost_scenarios": [metrics(selected, cost) for cost in costs],
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_files", nargs="+", help="Corrected operation CSV files")
    parser.add_argument("--cost-r", type=float, nargs="+", default=[0.05, 0.10, 0.20],
                        help="Hypothetical transaction cost in R per trade")
    parser.add_argument("--out", default="variant_comparison.json")
    args = parser.parse_args()
    if any(not math.isfinite(cost) or cost < 0 for cost in args.cost_r):
        parser.error("--cost-r values must be finite and non-negative")
    report = {Path(path).stem: compare(path, args.cost_r) for path in args.csv_files}
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
