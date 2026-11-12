#!/usr/bin/env python3
"""Backtest the exact TEST strategy_engine.analyze implementation.

Offline only: no broker, API, network, credentials, or production writes.
Input CSV is already aggregated to M5, M15, or M30 and must contain UTC timestamps.
Signals are evaluated at candle close; execution is at the next candle open.
"""
from __future__ import annotations
import argparse, csv, json, math, sys
from datetime import datetime, timezone
from pathlib import Path

# Make direct execution work from repository root or tools/.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from strategy_engine import analyze

TF_MAP = {"M5": "5min", "M15": "15min", "M30": "30min"}

def parse_time(value: str) -> datetime:
    raw = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        raise ValueError("Timestamp sin zona horaria; normaliza a UTC antes del backtest")
    return dt.astimezone(timezone.utc)

def load(path: Path):
    rows=[]
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader=csv.DictReader(f)
        for n,r in enumerate(reader,2):
            t=r.get("time") or r.get("timestamp") or r.get("datetime")
            if not t: raise ValueError(f"Fila {n}: falta time")
            b={"time":parse_time(t).isoformat().replace("+00:00","Z")}
            for k in ("open","high","low","close"):
                b[k]=float(r[k])
                if not math.isfinite(b[k]): raise ValueError(f"Fila {n}: {k} no finito")
            if b["high"] < max(b["open"],b["low"],b["close"]) or b["low"] > min(b["open"],b["high"],b["close"]):
                raise ValueError(f"Fila {n}: OHLC inconsistente")
            rows.append(b)
    rows.sort(key=lambda x:x["time"])
    if len(rows)<150: raise ValueError(f"Solo {len(rows)} velas")
    if len({x["time"] for x in rows}) != len(rows): raise ValueError("Timestamps duplicados")
    return rows

def run(rows, tf, symbol, expiry=24, step=1, max_bars=None):
    # Break into contiguous segments; never let indicators/MTF cross data gaps.
    mins=int(tf[1:])
    segments=[]; cur=[]
    for b in rows:
        if cur:
            a=parse_time(cur[-1]["time"]); z=parse_time(b["time"])
            if (z-a).total_seconds()!=mins*60:
                if cur: segments.append(cur)
                cur=[]
        cur.append(b)
    if cur: segments.append(cur)
    trades=[]; evaluated=0
    for seg in segments:
        if len(seg)<150: continue
        end=len(seg)-1
        if max_bars: end=min(end,max_bars-1)
        i=0
        while i<end:
            if i+1>=len(seg): break
            # Engine only receives candles available at the signal close.
            result=analyze(seg[:i+1],TF_MAP[tf],symbol)
            evaluated+=1
            if result.get("signal") not in ("BUY","SELL") or not result.get("entry"):
                i+=step; continue
            side=result["signal"]; entry=seg[i+1]["open"]
            sl=float(result["stop_loss"]); tp=float(result["take_profit"])
            risk=entry-sl if side=="BUY" else sl-entry
            reward=tp-entry if side=="BUY" else entry-tp
            if risk<=0 or reward<=0:
                i+=step; continue
            outcome="EXPIRED"; exit_price=seg[min(i+expiry,len(seg)-1)]["close"]
            exit_i=min(i+expiry,len(seg)-1)
            for j in range(i+1,min(i+expiry,len(seg)-1)+1):
                bar=seg[j]
                sl_hit=bar["low"]<=sl if side=="BUY" else bar["high"]>=sl
                tp_hit=bar["high"]>=tp if side=="BUY" else bar["low"]<=tp
                if sl_hit:
                    outcome,exit_price,exit_i="SL",sl,j; break
                if tp_hit:
                    outcome,exit_price,exit_i="TP",tp,j; break
            move=(exit_price-entry) if side=="BUY" else (entry-exit_price)
            r_multiple=move/risk
            trades.append({"signal_time":seg[i]["time"],"entry_time":seg[i+1]["time"],
                "exit_time":seg[exit_i]["time"],"side":side,"entry":entry,"sl":sl,"tp":tp,
                "outcome":outcome,"r":round(r_multiple,6),"engine_score":result.get("score"),
                "grade":result.get("quality_grade"),"status_code":result.get("status_code")})
            i=exit_i+1 # one position at a time
    wins=sum(t["outcome"]=="TP" for t in trades); losses=sum(t["outcome"]=="SL" for t in trades)
    gross=sum(t["r"] for t in trades)
    pos=sum(max(0,t["r"]) for t in trades); neg=-sum(min(0,t["r"]) for t in trades)
    eq=peak=dd=0.0
    for t in trades:
        eq+=t["r"]; peak=max(peak,eq); dd=max(dd,peak-eq)
    return {"symbol":symbol,"timeframe":tf,"candles":len(rows),"contiguous_segments":len(segments),
        "evaluations":evaluated,"trades":len(trades),"tp":wins,"sl":losses,
        "expired":sum(t["outcome"]=="EXPIRED" for t in trades),
        "win_rate_resolved_pct":round(100*wins/(wins+losses),2) if wins+losses else None,
        "net_r":round(gross,3),"expectancy_r":round(gross/len(trades),4) if trades else None,
        "profit_factor":round(pos/neg,3) if neg else (None if not pos else "inf"),
        "max_drawdown_r":round(dd,3),
        "limitations":"Gross results. No bid/ask, commissions, swap, slippage, or broker execution simulation."}, trades

def main():
    p=argparse.ArgumentParser()
    p.add_argument("csv"); p.add_argument("--symbol",required=True)
    p.add_argument("--timeframe",choices=TF_MAP,required=True)
    p.add_argument("--expiry",type=int,default=24); p.add_argument("--step",type=int,default=1)
    p.add_argument("--max-bars",type=int); p.add_argument("--out",default="engine_backtest.json")
    a=p.parse_args()
    rows=load(Path(a.csv)); summary,trades=run(rows,a.timeframe,a.symbol,a.expiry,a.step,a.max_bars)
    Path(a.out).write_text(json.dumps({"summary":summary,"trades":trades},indent=2),encoding="utf-8")
    print(json.dumps(summary,indent=2))
if __name__=="__main__": main()
