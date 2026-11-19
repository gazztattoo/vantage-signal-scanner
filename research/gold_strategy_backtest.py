"""Deterministic, offline Gedeon 2.0 research backtest for XAUUSD.

Research only. No network, broker, notifications, scanner writes, or orders.
Signal logic: confirmed swing liquidity sweep -> confirmed opposite BOS ->
retest of BOS level -> next-bar execution. Bid/Ask fills and gap-aware exits.
"""
from __future__ import annotations
import argparse, csv, json
from pathlib import Path
from gold_liquidity_research import load_m1, aggregate, ema, audit


def atr(bars, period=14):
    tr, prev = [], None
    for b in bars:
        tr.append(max(b.h-b.l, abs(b.h-prev), abs(b.l-prev)) if prev is not None else b.h-b.l)
        prev = b.c
    out = [None] * len(bars)
    if len(tr) >= period:
        value = sum(tr[:period]) / period
        out[period-1] = value
        for i in range(period, len(tr)):
            value = (value*(period-1) + tr[i]) / period
            out[i] = value
    return out


def backtest(bars, timeframe, pivot_left=2, pivot_right=2, retest_bars=8,
             stop_atr_buffer=0.10, rr=2.0, max_hold=48, spread_cap=None,
             slippage_per_side=0.10):
    """Single-position chronological simulation; conservative stop-first if both touched."""
    if timeframe not in (5, 15):
        raise ValueError("automatic research timeframes are M5/M15")
    n = len(bars)
    closes = [b.c for b in bars]
    e50, e200, av = ema(closes, 50), ema(closes, 200), atr(bars, 14)
    last_high = last_low = pending = position = None
    trades, invalidated_at_gaps = [], 0

    for i, b in enumerate(bars):
        if i and b.ts-bars[i-1].ts != timeframe*60:
            pending = None
            if position:
                invalidated_at_gaps += 1
                position = None

        # Pivots become visible only after their right-hand confirmation bars close.
        p = i-pivot_right
        if p >= pivot_left and i >= pivot_left+pivot_right:
            window = bars[p-pivot_left:p+pivot_right+1]
            if bts := bars[p].h:
                if bts == max(x.h for x in window) and sum(x.h == bts for x in window) == 1:
                    last_high = (bts, i)
            pivot_low = bars[p].l
            if pivot_low == min(x.l for x in window) and sum(x.l == pivot_low for x in window) == 1:
                last_low = (pivot_low, i)

        # Existing position: executable exits use Bid for longs and Ask for shorts.
        if position:
            if position["side"] == "long":
                stop_hit, target_hit = b.l <= position["sl"], b.h >= position["tp"]
                if stop_hit or target_hit or i-position["entry_i"] >= max_hold:
                    if stop_hit: exit_price = min(position["sl"], b.o)-slippage_per_side
                    elif target_hit: exit_price = position["tp"]-slippage_per_side
                    else: exit_price = b.c-slippage_per_side
                    pnl = exit_price-position["entry"]
                    trades.append({**position, "exit_i":i, "exit_ts":b.ts, "exit_price":exit_price,
                                   "pnl":pnl, "r_multiple":pnl/position["risk"],
                                   "exit_reason":"stop" if stop_hit else "target" if target_hit else "timeout"})
                    position = None
            else:
                stop_hit, target_hit = b.ah >= position["sl"], b.al <= position["tp"]
                if stop_hit or target_hit or i-position["entry_i"] >= max_hold:
                    if stop_hit: exit_price = max(position["sl"], b.ao)+slippage_per_side
                    elif target_hit: exit_price = position["tp"]+slippage_per_side
                    else: exit_price = b.ac+slippage_per_side
                    pnl = position["entry"]-exit_price
                    trades.append({**position, "exit_i":i, "exit_ts":b.ts, "exit_price":exit_price,
                                   "pnl":pnl, "r_multiple":pnl/position["risk"],
                                   "exit_reason":"stop" if stop_hit else "target" if target_hit else "timeout"})
                    position = None

        # After BOS, wait for a later retest and directional close.
        if pending and "bos_i" in pending:
            age = i-pending["bos_i"]
            if age > retest_bars:
                pending = None
            elif age >= 1:
                level, direction = pending["level"], pending["side"]
                if direction == "long" and b.l <= level and b.c > level and b.c > b.o:
                    if e50[i] is not None and e200[i] is not None and e50[i] > e200[i] and b.c > e50[i] and av[i]:
                        entry_i = i+1
                        if entry_i < n and bars[entry_i].ts-b.ts == timeframe*60:
                            entry = bars[entry_i].ao+slippage_per_side
                            sl = pending["sweep_extreme"]-stop_atr_buffer*av[i]
                            risk = entry-sl
                            if risk > 0 and (spread_cap is None or bars[entry_i].ao-bars[entry_i].o <= spread_cap):
                                position = {"side":"long", "entry_i":entry_i, "entry_ts":bars[entry_i].ts,
                                            "entry":entry, "sl":sl, "tp":entry+rr*risk, "risk":risk,
                                            "setup_i":pending["sweep_i"], "bos_i":pending["bos_i"]}
                elif direction == "short" and b.h >= level and b.c < level and b.c < b.o:
                    if e50[i] is not None and e200[i] is not None and e50[i] < e200[i] and b.c < e50[i] and av[i]:
                        entry_i = i+1
                        if entry_i < n and bars[entry_i].ts-b.ts == timeframe*60:
                            entry = bars[entry_i].o-slippage_per_side
                            sl = pending["sweep_extreme"]+stop_atr_buffer*av[i]
                            risk = sl-entry
                            if risk > 0 and (spread_cap is None or bars[entry_i].ao-bars[entry_i].o <= spread_cap):
                                position = {"side":"short", "entry_i":entry_i, "entry_ts":bars[entry_i].ts,
                                            "entry":entry, "sl":sl, "tp":entry-rr*risk, "risk":risk,
                                            "setup_i":pending["sweep_i"], "bos_i":pending["bos_i"]}
                if position:
                    pending = None

        # A sweep must take a previously confirmed swing level.
        if not position and not pending and av[i] is not None and e50[i] is not None and e200[i] is not None:
            if last_low and last_low[1] < i and b.l < last_low[0] and b.c > last_low[0]:
                if e50[i] > e200[i]:
                    pending = {"side":"long", "sweep_i":i, "sweep_extreme":b.l, "await_bos":True}
            elif last_high and last_high[1] < i and b.h > last_high[0] and b.c < last_high[0]:
                if e50[i] < e200[i]:
                    pending = {"side":"short", "sweep_i":i, "sweep_extreme":b.h, "await_bos":True}

        # BOS is a completed close beyond a pivot that was confirmed before this candle.
        if pending and pending.get("await_bos") and i > pending["sweep_i"]:
            if i-pending["sweep_i"] > retest_bars*2:
                pending = None
            elif pending["side"] == "long" and last_high and last_high[1] < i and b.c > last_high[0]:
                pending.update({"level":last_high[0], "bos_i":i})
                pending.pop("await_bos")
            elif pending["side"] == "short" and last_low and last_low[1] < i and b.c < last_low[0]:
                pending.update({"level":last_low[0], "bos_i":i})
                pending.pop("await_bos")

    if position:
        b = bars[-1]
        px = b.c-slippage_per_side if position["side"] == "long" else b.ac+slippage_per_side
        pnl = px-position["entry"] if position["side"] == "long" else position["entry"]-px
        trades.append({**position, "exit_i":n-1, "exit_ts":b.ts, "exit_price":px,
                       "pnl":pnl, "r_multiple":pnl/position["risk"], "exit_reason":"end_of_data"})

    winners = [t["pnl"] for t in trades if t["pnl"] > 0]
    losers = [-t["pnl"] for t in trades if t["pnl"] < 0]
    return {"timeframe":f"M{timeframe}", "bars":n, "trades":len(trades),
            "wins":len(winners), "losses":len(losers),
            "win_rate":len(winners)/len(trades) if trades else 0,
            "net_price_units":sum(t["pnl"] for t in trades),
            "profit_factor":sum(winners)/sum(losers) if losers else (None if not winners else float("inf")),
            "expectancy_price_units":sum(t["pnl"] for t in trades)/len(trades) if trades else 0,
            "mean_r":sum(t["r_multiple"] for t in trades)/len(trades) if trades else 0,
            "max_drawdown_price_units":_max_drawdown(trades),
            "invalidated_at_data_gaps":invalidated_at_gaps, "trade_records":trades}


def _max_drawdown(trades):
    equity = peak = drawdown = 0.0
    for trade in trades:
        equity += trade["pnl"]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak-equity)
    return drawdown


def chronological_split(trades, bars):
    if not bars: return []
    start, span = bars[0].ts, bars[-1].ts-bars[0].ts
    cut1, cut2 = start+span*.6, start+span*.8
    groups = [[], [], []]
    for trade in trades:
        ts = trade["entry_ts"]
        groups[0 if ts < cut1 else 1 if ts < cut2 else 2].append(trade)
    result = []
    for name, group in zip(("train_60", "validation_20", "holdout_20"), groups):
        wins = [x["pnl"] for x in group if x["pnl"] > 0]
        losses = [-x["pnl"] for x in group if x["pnl"] < 0]
        result.append({"period":name, "trades":len(group), "net":sum(x["pnl"] for x in group),
                       "profit_factor":sum(wins)/sum(losses) if losses else None,
                       "expectancy":sum(x["pnl"] for x in group)/len(group) if group else None,
                       "win_rate":len(wins)/len(group) if group else None})
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("--out", default="gedeon_backtest")
    args = parser.parse_args()
    m1 = load_m1(args.file)
    report = {"data_audit":audit(m1), "slippage_per_side_price_units":0.10, "timeframes":{}}
    for tf in (5, 15):
        bars = aggregate(m1, tf)
        result = backtest(bars, tf)
        trades = result.pop("trade_records")
        result["chronological_split"] = chronological_split(trades, bars)
        report["timeframes"][f"M{tf}"] = result
        with open(f"{args.out}_M{tf}_trades.csv", "w", newline="") as f:
            if trades:
                writer = csv.DictWriter(f, fieldnames=list(trades[0]))
                writer.writeheader()
                writer.writerows(trades)
    Path(f"{args.out}_summary.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
