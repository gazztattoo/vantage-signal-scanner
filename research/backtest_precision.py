#!/usr/bin/env python3
"""Provider-free VANTAGE baseline backtest on Dukascopy M1 BID/ASK CSVs.

Signals reproduce app.py's current EMA/RSI/ATR/ADX/MACD/Bollinger rules.
Execution starts on the next M1 bar, uses ASK to buy/BID to sell, and exits
against the opposite side. If SL and TP are both touched in one M1 bar, SL
is assumed first (conservative). This is research, not broker execution.
"""
from __future__ import annotations
import argparse, csv, json, math
from collections import defaultdict
from bisect import bisect_left
from datetime import datetime, timezone
from pathlib import Path

def ema(v, p):
    if len(v)<p: return None
    a=2/(p+1); x=sum(v[:p])/p
    for z in v[p:]: x=z*a+x*(1-a)
    return x

def rsi(v,p=14):
    if len(v)<=p:return None
    d=[v[i]-v[i-1] for i in range(1,len(v))]
    g=[max(x,0) for x in d]; l=[max(-x,0) for x in d]
    ag=sum(g[:p])/p; al=sum(l[:p])/p
    for i in range(p,len(g)):
        ag=(ag*(p-1)+g[i])/p; al=(al*(p-1)+l[i])/p
    return 100.0 if al==0 and ag>0 else 50.0 if al==0 else 100-100/(1+ag/al)

def atr(c,p=14):
    if len(c)<=p:return None
    tr=[max(c[i]["high"]-c[i]["low"],abs(c[i]["high"]-c[i-1]["close"]),abs(c[i]["low"]-c[i-1]["close"])) for i in range(1,len(c))]
    x=sum(tr[:p])/p
    for z in tr[p:]:x=(x*(p-1)+z)/p
    return x

def adx(c,p=14):
    if len(c)<2*p+1:return None
    tr=[]; pd=[]; md=[]
    for i in range(1,len(c)):
        u=c[i]["high"]-c[i-1]["high"]; d=c[i-1]["low"]-c[i]["low"]
        pd.append(u if u>d and u>0 else 0.0); md.append(d if d>u and d>0 else 0.0)
        tr.append(max(c[i]["high"]-c[i]["low"],abs(c[i]["high"]-c[i-1]["close"]),abs(c[i]["low"]-c[i-1]["close"])))
    st,sp,sm=sum(tr[:p]),sum(pd[:p]),sum(md[:p]); dx=[]
    for i in range(p-1,len(tr)):
        if i>=p: st+=tr[i]-st/p; sp+=pd[i]-sp/p; sm+=md[i]-sm/p
        if st<=0:continue
        a,b=100*sp/st,100*sm/st; den=a+b
        dx.append(100*abs(a-b)/den if den else 0.0)
    if len(dx)<p:return None
    x=sum(dx[:p])/p
    for z in dx[p:]:x=(x*(p-1)+z)/p
    return x

def macd(v):
    if len(v)<35:return None
    line=[ema(v[:i+1],12)-ema(v[:i+1],26) for i in range(25,len(v))]
    sig=ema(line,9)
    return line[-1]-sig if sig is not None else None

def signal(c, adx_floor=20, score_floor=3):
    v=[x["close"] for x in c]
    e20,e50,r,a,x,m=ema(v,20),ema(v,50),rsi(v),atr(c),adx(c),macd(v)
    if None in (e20,e50,r,a,x,m) or a<=0:return None
    w=v[-20:]; mid=sum(w)/20
    buy=sum((e20>e50,52<=r<=68,m>0,v[-1]>mid))
    sell=sum((e20<e50,32<=r<=48,m<0,v[-1]<mid))
    if x<adx_floor:return None
    if buy>=score_floor and buy>sell:return ("BUY",a)
    if sell>=score_floor and sell>buy:return ("SELL",a)
    return None

def load_m1(path):
    """Load validated, strictly chronological BID/ASK M1 bars."""
    out=[]
    with open(path,newline="",encoding="utf-8-sig") as f:
        reader=csv.DictReader(f)
        required={"timestamp"} | {f"{side}_{k}" for side in ("bid","ask") for k in ("open","high","low","close")}
        missing=required-set(reader.fieldnames or [])
        if missing:raise ValueError(f"Missing required CSV columns: {sorted(missing)}")
        previous=None
        for line,r in enumerate(reader,start=2):
            t=datetime.fromisoformat(r["timestamp"].replace("Z","+00:00"))
            if t.tzinfo is None:t=t.replace(tzinfo=timezone.utc)
            t=t.astimezone(timezone.utc)
            if previous is not None and t<=previous:
                raise ValueError(f"Timestamps must be strictly increasing; duplicate/out-of-order row {line}")
            previous=t
            x={"time":t}
            for side in ("bid","ask"):
                for k in ("open","high","low","close"):
                    value=float(r[f"{side}_{k}"])
                    if not math.isfinite(value) or value<=0:raise ValueError(f"Invalid {side.upper()} {k} at row {line}")
                    x[f"{side}_{k}"]=value
                o,h,l,cl=(x[f"{side}_{k}"] for k in ("open","high","low","close"))
                if h<max(o,cl,l) or l>min(o,cl,h):raise ValueError(f"Invalid {side.upper()} OHLC at row {line}")
            for k in ("open","high","low","close"):
                if x[f"ask_{k}"]<x[f"bid_{k}"]:raise ValueError(f"ASK below BID ({k}) at row {line}")
            out.append(x)
    return out

def aggregate(m1, minutes):
    groups=defaultdict(list)
    for x in m1:
        epoch=int(x["time"].timestamp()); start=epoch-epoch%(minutes*60)
        groups[start].append(x)
    bars=[]
    for start,rows in sorted(groups.items()):
        if len(rows)!=minutes:continue
        if any((rows[i]["time"]-rows[i-1]["time"]).total_seconds()!=60 for i in range(1,len(rows))):continue
        bar={"time":datetime.fromtimestamp(start,timezone.utc)}
        for side in ("bid","ask"):
            bar[f"{side}_open"]=rows[0][f"{side}_open"]
            bar[f"{side}_high"]=max(z[f"{side}_high"] for z in rows)
            bar[f"{side}_low"]=min(z[f"{side}_low"] for z in rows)
            bar[f"{side}_close"]=rows[-1][f"{side}_close"]
        bar["open"]= (bar["bid_open"]+bar["ask_open"])/2
        bar["high"]= (bar["bid_high"]+bar["ask_high"])/2
        bar["low"]= (bar["bid_low"]+bar["ask_low"])/2
        bar["close"]= (bar["bid_close"]+bar["ask_close"])/2
        bars.append(bar)
    return bars

def run(m1, minutes, adx_floor=20, score_floor=3):
    bars=aggregate(m1,minutes); trades=[]; i=0; warmup=100
    m1_times=[z["time"] for z in m1]
    while i < len(bars)-1:
        s=signal(bars[:i+1],adx_floor,score_floor) if i>=warmup else None
        if not s:i+=1;continue
        side,vol=s; entrybar=bars[i+1]
        entry=entrybar["ask_open"] if side=="BUY" else entrybar["bid_open"]
        sl=entry-(1.5*vol) if side=="BUY" else entry+(1.5*vol)
        tp=entry+(3*vol) if side=="BUY" else entry-(3*vol)
        exit_price=None; outcome=None; exit_i=None
        entry_time=entrybar["time"]
        entry_m1_index=bisect_left(m1_times,entry_time)
        if entry_m1_index>=len(m1):
            break
        for j in range(entry_m1_index,len(m1)):
            z=m1[j]
            # Use exit-side quotes: long closes at BID, short closes at ASK.
            hi=z["bid_high"] if side=="BUY" else z["ask_high"]
            lo=z["bid_low"] if side=="BUY" else z["ask_low"]
            hit_sl=lo<=sl if side=="BUY" else hi>=sl
            hit_tp=hi>=tp if side=="BUY" else lo<=tp
            if hit_sl and hit_tp:exit_price=sl;outcome="SL_ambiguous_conservative";exit_i=j;break
            if hit_sl:exit_price=sl;outcome="SL";exit_i=j;break
            if hit_tp:exit_price=tp;outcome="TP";exit_i=j;break
        if exit_price is None:
            z=m1[-1];exit_price=z["bid_close"] if side=="BUY" else z["ask_close"];outcome="OPEN_AT_END";exit_i=len(m1)-1
        risk=abs(entry-sl); pnl=(exit_price-entry) if side=="BUY" else (entry-exit_price)
        trades.append({"signal_time":bars[i]["time"].isoformat(),"entry_time":entrybar["time"].isoformat(),
                       "exit_time":m1[exit_i]["time"].isoformat(),"side":side,"entry":entry,"sl":sl,"tp":tp,
                       "exit":exit_price,"outcome":outcome,"r_multiple":pnl/risk if risk else 0})
        # No overlapping positions for the same symbol/timeframe.
        closed_time=m1[exit_i]["time"]
        while i+1<len(bars) and bars[i+1]["time"]<=closed_time:i+=1
        i+=1
    return bars,trades

def metrics(trades):
    closed=[t for t in trades if t["outcome"]!="OPEN_AT_END"]
    wins=[t for t in closed if t["r_multiple"]>0]
    losses=[t for t in closed if t["r_multiple"]<=0]
    gross_win=sum(t["r_multiple"] for t in wins); gross_loss=abs(sum(t["r_multiple"] for t in losses))
    eq=peak=dd=0.0
    for t in closed:
        eq+=t["r_multiple"];peak=max(peak,eq);dd=max(dd,peak-eq)
    return {"trades":len(trades),"closed_trades":len(closed),"wins":len(wins),"losses":len(losses),
      "win_rate":round(len(wins)/len(closed),4) if closed else None,
      "expectancy_r":round(sum(t["r_multiple"] for t in closed)/len(closed),4) if closed else None,
      "profit_factor":round(gross_win/gross_loss,4) if gross_loss else None,
      "max_drawdown_r":round(dd,4),"open_at_end":len(trades)-len(closed)}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("csv",type=Path);p.add_argument("--symbol",default="unknown")
    p.add_argument("--timeframes",default="5,15,30");p.add_argument("--adx",default="18,20,25,30")
    p.add_argument("--scores",default="3,4");p.add_argument("--out",type=Path)
    a=p.parse_args();m1=load_m1(a.csv);results=[]
    for tf in map(int,a.timeframes.split(",")):
      for floor in map(float,a.adx.split(",")):
       for score in map(int,a.scores.split(",")):
        bars,trades=run(m1,tf,floor,score)
        cut=int(len(trades)*0.7); train=trades[:cut]; test=trades[cut:]
        results.append({"symbol":a.symbol,"timeframe_min":tf,"adx_floor":floor,"score_floor":score,
          "bars":len(bars),"train":metrics(train),"out_of_sample":metrics(test),
          "warning":"Very small samples are descriptive only; no statistical edge claim.",
          "trades":trades})
    report={"source":str(a.csv),"m1_bars":len(m1),"experiments":results,
            "method":"closed bars only; next M1 open entry; BID/ASK side-aware execution; SL-first on same-bar ambiguity; 1.5 ATR SL / 3 ATR TP"}
    rendered=json.dumps(report,indent=2,ensure_ascii=False)
    if a.out:a.out.write_text(rendered+"\n",encoding="utf-8")
    print(rendered)
if __name__=="__main__":main()
