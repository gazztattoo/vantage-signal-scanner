"""Corrected offline XAUUSD simulator. Research only; never routes orders."""
from __future__ import annotations
from dataclasses import asdict
from gold_liquidity_research import ema


def atr(bars, period=14):
    if period < 1:
        raise ValueError("period must be positive")
    out = [None] * len(bars)
    if not bars:
        return out
    trs = []
    for i, b in enumerate(bars):
        prev = bars[i-1].c if i else None
        trs.append(b.h-b.l if prev is None else max(b.h-b.l, abs(b.h-prev), abs(b.l-prev)))
    if len(trs) >= period:
        value = sum(trs[:period])/period
        out[period-1] = value
        for i in range(period, len(trs)):
            value = (value*(period-1)+trs[i])/period
            out[i] = value
    return out


def _metrics(trades):
    wins = [t["net_pnl"] for t in trades if t["net_pnl"] > 0]
    losses = [-t["net_pnl"] for t in trades if t["net_pnl"] < 0]
    gross_win, gross_loss = sum(wins), sum(losses)
    eq = peak = dd = 0.0
    for t in trades:
        eq += t["net_pnl"]
        peak = max(peak, eq)
        dd = max(dd, peak-eq)
    return {
        "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate": len(wins)/len(trades) if trades else 0.0,
        "gross_profit": gross_win, "gross_loss": gross_loss,
        "net_pnl_price_units": sum(t["net_pnl"] for t in trades),
        "profit_factor": gross_win/gross_loss if gross_loss else (None if not gross_win else float("inf")),
        "expectancy_price_units": sum(t["net_pnl"] for t in trades)/len(trades) if trades else 0.0,
        "max_drawdown_price_units": dd,
    }


def backtest_v2(bars, timeframe, pivot_left=2, pivot_right=2, retest_bars=8,
                stop_atr_buffer=0.10, rr=2.0, max_hold=48,
                spread_cap=None, slippage_per_side=0.10,
                commission_price_units_per_side=0.0):
    """Causal sweep/BOS/retest model with executable Bid/Ask prices.

    Commission is an explicit price-unit equivalent per side because account
    currency/contract size are not available in the historical export. Keep it
    at zero until calibrated; never interpret price units as account P/L.
    """
    if timeframe not in (5, 15):
        raise ValueError("automatic research timeframes are M5/M15")
    if min(pivot_left, pivot_right, retest_bars, max_hold) < 1 or rr <= 0:
        raise ValueError("invalid strategy parameters")
    if slippage_per_side < 0 or commission_price_units_per_side < 0:
        raise ValueError("costs cannot be negative")
    n = len(bars)
    if not n:
        return {"timeframe": f"M{timeframe}", **_metrics([]), "trade_records": [],
                "gap_exits": 0, "invalidated_setups": 0}
    closes = [b.c for b in bars]
    e50, e200, av = ema(closes, 50), ema(closes, 200), atr(bars, 14)
    piv_hi = piv_lo = None
    pending = position = None
    trades, gap_exits, invalidated_setups = [], 0, 0
    segment_start = 0

    def close_trade(pos, i, ts, px, reason):
        gross = (px-pos["entry"]) if pos["side"] == "long" else (pos["entry"]-px)
        costs = 2*commission_price_units_per_side
        net = gross-costs
        return {**pos, "exit_i": i, "exit_ts": ts, "exit_price": px,
                "gross_pnl": gross, "commission_equivalent": costs,
                "net_pnl": net, "r_multiple": net/pos["risk"],
                "exit_reason": reason}

    for i, b in enumerate(bars):
        gap = i > 0 and b.ts-bars[i-1].ts != timeframe*60
        if gap:
            pending = None
            piv_hi = piv_lo = None
            segment_start = i
            if position:
                # Close at first observed executable quote after the unobserved gap.
                px = (b.o-slippage_per_side if position["side"] == "long"
                      else b.ao+slippage_per_side)
                trades.append(close_trade(position, i, b.ts, px, "data_gap_open"))
                position = None
                gap_exits += 1

        # A pivot can only be confirmed using bars in the same uninterrupted segment.
        p = i-pivot_right
        if p-pivot_left >= segment_start and p >= 0:
            window = bars[p-pivot_left:p+pivot_right+1]
            if len(window) == pivot_left+pivot_right+1:
                ph, pl = bars[p].h, bars[p].l
                if ph == max(x.h for x in window) and sum(x.h == ph for x in window) == 1:
                    piv_hi = (ph, i)
                if pl == min(x.l for x in window) and sum(x.l == pl for x in window) == 1:
                    piv_lo = (pl, i)

        # Existing positions are always marked against the executable side.
        if position:
            if position["side"] == "long":
                stop_hit, target_hit = b.l <= position["sl"], b.h >= position["tp"]
                if stop_hit or target_hit or i-position["entry_i"] >= max_hold:
                    if stop_hit:
                        px = min(position["sl"], b.o)-slippage_per_side
                        reason = "stop"
                    elif target_hit:
                        px, reason = position["tp"]-slippage_per_side, "target"
                    else:
                        px, reason = b.c-slippage_per_side, "timeout"
                    trades.append(close_trade(position, i, b.ts, px, reason))
                    position = None
            else:
                stop_hit, target_hit = b.ah >= position["sl"], b.al <= position["tp"]
                if stop_hit or target_hit or i-position["entry_i"] >= max_hold:
                    if stop_hit:
                        px = max(position["sl"], b.ao)+slippage_per_side
                        reason = "stop"
                    elif target_hit:
                        px, reason = position["tp"]+slippage_per_side, "target"
                    else:
                        px, reason = b.ac+slippage_per_side, "timeout"
                    trades.append(close_trade(position, i, b.ts, px, reason))
                    position = None

        # Retest must happen strictly after BOS; entry occurs next bar open.
        if pending and "bos_i" in pending:
            age = i-pending["bos_i"]
            if age > retest_bars:
                pending = None
            elif age >= 1 and not gap:
                level, side = pending["level"], pending["side"]
                valid = (b.l <= level and b.c > level and b.c > b.o) if side == "long" else (b.h >= level and b.c < level and b.c < b.o)
                trend = (e50[i] is not None and e200[i] is not None and
                         ((e50[i] > e200[i] and b.c > e50[i]) if side == "long"
                          else (e50[i] < e200[i] and b.c < e50[i])))
                entry_i = i+1
                if valid and trend and av[i] is not None and entry_i < n and bars[entry_i].ts-b.ts == timeframe*60:
                    eb = bars[entry_i]
                    spread = eb.ao-eb.o
                    if spread_cap is None or spread <= spread_cap:
                        if side == "long":
                            entry = eb.ao+slippage_per_side
                            sl = pending["sweep_extreme"]-stop_atr_buffer*av[i]
                            risk = entry-sl
                            tp = entry+rr*risk
                        else:
                            entry = eb.o-slippage_per_side
                            sl = pending["sweep_extreme"]+stop_atr_buffer*av[i]
                            risk = sl-entry
                            tp = entry-rr*risk
                        if risk > 0:
                            position = {"side":side, "entry_i":entry_i, "entry_ts":eb.ts,
                                        "entry":entry, "sl":sl, "tp":tp, "risk":risk,
                                        "setup_i":pending["sweep_i"], "bos_i":pending["bos_i"],
                                        "spread_at_entry":spread}
                if valid:
                    pending = None

        # Sweep a previously confirmed swing; the pivot must predate the sweep.
        if not position and not pending and av[i] is not None and e50[i] is not None and e200[i] is not None:
            if piv_lo and piv_lo[1] < i and b.l < piv_lo[0] and b.c > piv_lo[0] and e50[i] > e200[i]:
                pending = {"side":"long", "sweep_i":i, "sweep_extreme":b.l, "await_bos":True}
            elif piv_hi and piv_hi[1] < i and b.h > piv_hi[0] and b.c < piv_hi[0] and e50[i] < e200[i]:
                pending = {"side":"short", "sweep_i":i, "sweep_extreme":b.ah, "await_bos":True}

        # BOS: close beyond a pivot confirmed before this candle and after the sweep.
        if pending and pending.get("await_bos") and i > pending["sweep_i"]:
            if i-pending["sweep_i"] > retest_bars*2:
                pending = None
                invalidated_setups += 1
            elif pending["side"] == "long" and piv_hi and piv_hi[1] < i and b.c > piv_hi[0]:
                pending.update({"level":piv_hi[0], "bos_i":i})
                pending.pop("await_bos")
            elif pending["side"] == "short" and piv_lo and piv_lo[1] < i and b.c < piv_lo[0]:
                pending.update({"level":piv_lo[0], "bos_i":i})
                pending.pop("await_bos")

    if position:
        b = bars[-1]
        px = b.c-slippage_per_side if position["side"] == "long" else b.ac+slippage_per_side
        trades.append(close_trade(position, n-1, b.ts, px, "end_of_data"))
    return {"timeframe":f"M{timeframe}", **_metrics(trades), "trade_records":trades,
            "gap_exits":gap_exits, "invalidated_setups":invalidated_setups,
            "slippage_per_side_price_units":slippage_per_side,
            "commission_price_units_per_side":commission_price_units_per_side,
            "commission_calibrated":commission_price_units_per_side > 0}


def chronological_split(trades, bars):
    if not bars:
        return []
    start, end = bars[0].ts, bars[-1].ts
    cuts = (start+(end-start)*0.6, start+(end-start)*0.8)
    names = ("train_60", "validation_20", "holdout_20")
    groups = [[], [], []]
    for t in trades:
        ts = t["entry_ts"]
        groups[0 if ts < cuts[0] else 1 if ts < cuts[1] else 2].append(t)
    return [{"period":name, **_metrics(group)} for name, group in zip(names, groups)]
