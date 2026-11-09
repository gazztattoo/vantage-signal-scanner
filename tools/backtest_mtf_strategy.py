"""Walk-forward MTF backtest. Offline only; no broker or provider calls."""
from __future__ import annotations
import argparse, csv, gzip, json, math
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from strategy_engine import analyze, TIMEFRAME_MINUTES

LOOKBACK = {"5min": 800, "15min": 300, "30min": 550}

def run_backtest(candles, timeframe, symbol="", expiry=24, costs=(0.0, 0.05, 0.10), max_bars=None):
    if timeframe not in TIMEFRAME_MINUTES or expiry < 1:
        raise ValueError("Invalid timeframe or expiry")
    if any(not math.isfinite(float(c)) or c < 0 for c in costs):
        raise ValueError("Costs must be finite and non-negative")
    bars = candles[:max_bars] if max_bars else candles
    trades, counts = [], Counter()
    i, lookback, n = LOOKBACK[timeframe] - 1, LOOKBACK[timeframe], len(bars)
    while i < n - 1:
        result = analyze(bars[max(0, i-lookback+1):i+1], timeframe, symbol)
        if result.get("pre_signal"): counts["pre_signals"] += 1
        side = result.get("signal")
        if side not in ("BUY", "SELL"):
            i += 1
            continue
        counts["confirmed_setups"] += 1
        entry_i = i + 1
        entry = float(bars[entry_i]["open"])
        stop = float(result["stop_loss"])
        atr = float(result["atr14"])
        risk = abs(entry-stop)
        valid = ((stop < entry) if side == "BUY" else (stop > entry)) and 0.35*atr <= risk <= 2.5*atr
        if not valid or risk <= 0:
            counts["rejected_gap_or_risk"] += 1
            i += 1
            continue
        end_i = entry_i + expiry - 1
        if end_i >= n:
            counts["incomplete_horizon"] += 1
            break
        target = entry + (2*risk if side == "BUY" else -2*risk)
        outcome, exit_i, exit_price = "EXPIRED", end_i, float(bars[end_i]["close"])
        for j in range(entry_i, end_i+1):
            bar = bars[j]
            sl_hit = float(bar["low"]) <= stop if side == "BUY" else float(bar["high"]) >= stop
            tp_hit = float(bar["high"]) >= target if side == "BUY" else float(bar["low"]) <= target
            if sl_hit:  # Conservative tie break
                outcome, exit_i, exit_price = "SL", j, stop
                break
            if tp_hit:
                outcome, exit_i, exit_price = "TP", j, target
                break
        move = exit_price-entry if side == "BUY" else entry-exit_price
        r = move/risk
        if outcome == "TP": r = 2.0; counts["tp"] += 1
        elif outcome == "SL": r = -1.0; counts["sl"] += 1
        else: counts["expired_mtm"] += 1
        trades.append({
            "signal_time": bars[i]["time"], "entry_time": bars[entry_i]["time"],
            "exit_time": bars[exit_i]["time"], "side": side,
            "signal_price": result.get("price"), "entry": entry,
            "stop_loss": stop, "take_profit": target, "exit_price": exit_price,
            "outcome": outcome, "result_r": round(r, 6), "score": result.get("score"),
            "grade": result.get("quality_grade"), "regime": result.get("regime"),
            "structure_trigger": result.get("structure_trigger"), "mtf": result.get("mtf")
        })
        i = exit_i + 1  # One open trade at a time.

    def cost_stats(cost):
        eq=peak=dd=profit=loss=net=0.0
        for trade in trades:
            r=float(trade["result_r"])-cost
            net+=r; eq+=r; peak=max(peak,eq); dd=max(dd,peak-eq)
            if r>0: profit+=r
            elif r<0: loss-=r
        return {"net_after_cost_r":round(net,4),
                "profit_factor_after_cost":round(profit/loss,4) if loss else ("inf" if profit else None),
                "max_drawdown_after_cost_r":round(dd,4)}
    gross=cost_stats(0.0)
    wins, losses = counts["tp"], counts["sl"]
    resolved=wins+losses
    summary={
        "symbol":symbol,"timeframe":timeframe,"candles":len(bars),
        "pre_signals":counts["pre_signals"],"confirmed_setups":counts["confirmed_setups"],
        "trades":len(trades),"rejected_gap_or_risk":counts["rejected_gap_or_risk"],
        "incomplete_horizon":counts["incomplete_horizon"],"tp":wins,"sl":losses,
        "expired_mtm":counts["expired_mtm"],
        "win_rate_resolved_pct":round(100*wins/resolved,2) if resolved else None,
        "gross_net_r":round(sum(t["result_r"] for t in trades),4),
        "gross_profit_factor":gross["profit_factor_after_cost"],
        "gross_max_drawdown_r":gross["max_drawdown_after_cost_r"],
        "cost_scenarios":{str(c):cost_stats(c) for c in costs},
        "methodology":"Signal at close; entry next candle open; structural SL; TP 2R from actual entry; SL-first tie break; expiry mark-to-market; no overlap. Gross excludes real spread/commission/slippage."
    }
    return {"summary":summary,"trades":trades}

def _rows_to_bars(rows, tz):
    counts=Counter(x[0] for x in rows)
    clean=sorted((x for x in rows if counts[x[0]]==1),key=lambda x:x[0])
    return [{"time":datetime.fromtimestamp(x[0],timezone.utc).isoformat(),
             "open":x[1],"high":x[2],"low":x[3],"close":x[4]} for x in clean]

def load_eurusd_histdata(path):
    tz=timezone(timedelta(hours=-5)); rows=[]
    with Path(path).open(encoding="utf-8-sig",newline="") as f:
        for row in csv.reader(f,delimiter=";"):
            if len(row)<5: continue
            try:
                ts=int(datetime.strptime(row[0].strip(),"%Y%m%d %H%M%S").replace(tzinfo=tz).timestamp())
                rows.append((ts,*map(float,row[1:5])))
            except (ValueError,IndexError): continue
    return _rows_to_bars(rows,tz)

def load_xau_histdata(path):
    tz=timezone(timedelta(hours=-5)); rows=[]
    with Path(path).open(encoding="utf-8-sig",newline="") as f:
        for row in csv.reader(f):
            if len(row)<6: continue
            try:
                dt=datetime.strptime(row[0].strip()+","+row[1].strip(),"%Y.%m.%d,%H:%M").replace(tzinfo=tz)
                rows.append((int(dt.timestamp()),*map(float,row[2:6])))
            except (ValueError,IndexError): continue
    return _rows_to_bars(rows,tz)

def load_btc_binance_gz(path):
    rows=[]
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        for row in csv.DictReader(f):
            try:
                ts=int(datetime.fromisoformat(row["datetime_utc"].replace("Z","+00:00")).timestamp())
                rows.append((ts,*(float(row[k]) for k in ("open","high","low","close"))))
            except (ValueError,KeyError,TypeError): continue
    return _rows_to_bars(rows,timezone.utc)

def aggregate_m1(m1, minutes):
    width=minutes*60; groups=defaultdict(list)
    for bar in m1:
        ts=int(datetime.fromisoformat(bar["time"]).timestamp())
        groups[(ts//width)*width].append(bar)
    out=[]
    for start,g in sorted(groups.items()):
        g.sort(key=lambda x:x["time"])
        actual=[int(datetime.fromisoformat(x["time"]).timestamp()) for x in g]
        if actual != [start+i*60 for i in range(minutes)]: continue
        out.append({"time":datetime.fromtimestamp(start,timezone.utc).isoformat(),
                    "open":g[0]["open"],"high":max(x["high"] for x in g),
                    "low":min(x["low"] for x in g),"close":g[-1]["close"]})
    return out

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol",choices=("EURUSD","XAUUSD","BTCUSDT"),required=True)
    p.add_argument("--timeframe",choices=tuple(TIMEFRAME_MINUTES),required=True)
    p.add_argument("--input",required=True,type=Path)
    p.add_argument("--expiry",type=int,default=24)
    p.add_argument("--cost-r",type=float,nargs="*",default=[0,.05,.10])
    p.add_argument("--max-bars",type=int,default=None)
    p.add_argument("--out",type=Path,default=Path("mtf_backtest_result.json"))
    a=p.parse_args()
    m1=(load_eurusd_histdata(a.input) if a.symbol=="EURUSD" else
        load_xau_histdata(a.input) if a.symbol=="XAUUSD" else load_btc_binance_gz(a.input))
    bars=aggregate_m1(m1,TIMEFRAME_MINUTES[a.timeframe])
    result=run_backtest(bars,a.timeframe,a.symbol,a.expiry,tuple(a.cost_r),a.max_bars)
    result["summary"]["source_m1_rows_after_duplicate_rejection"]=len(m1)
    result["summary"]["aggregated_bars"]=len(bars)
    a.out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result["summary"],ensure_ascii=False,indent=2))

if __name__=="__main__": main()
