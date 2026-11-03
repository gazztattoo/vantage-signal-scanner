#!/usr/bin/env python3
"""Offline, spread-aware replay of VANTAGE's current signal rules on Dukascopy M1 BID/ASK CSVs.

Signals are calculated only on fully closed M5/M15/M30 bars. Entries occur on the
next M1 open. Long exits are evaluated on BID; short exits on ASK. If SL and TP
are both touched in one M1 candle, SL is assumed first (conservative).
"""
from __future__ import annotations
import argparse, csv, datetime as dt
from pathlib import Path

TIMEFRAMES = (5, 15, 30)
MAX_HOLD_MINUTES = 240

def ema(values, period):
    if len(values) < period: return None
    a=2/(period+1); v=sum(values[:period])/period
    for x in values[period:]: v=x*a+v*(1-a)
    return v

def rsi(values, period=14):
    if len(values) <= period: return None
    ch=[values[i]-values[i-1] for i in range(1,len(values))]
    gains=[max(x,0) for x in ch]; losses=[max(-x,0) for x in ch]
    ag=sum(gains[:period])/period; al=sum(losses[:period])/period
    for i in range(period,len(ch)):
        ag=(ag*(period-1)+gains[i])/period; al=(al*(period-1)+losses[i])/period
    if al == 0: return 100.0 if ag > 0 else 50.0
    return 100-100/(1+ag/al)

def atr(c, period=14):
    if len(c) <= period: return None
    tr=[max(x['high']-x['low'],abs(x['high']-c[i-1]['close']),abs(x['low']-c[i-1]['close']))
        for i,x in enumerate(c) if i]
    v=sum(tr[:period])/period
    for x in tr[period:]: v=(v*(period-1)+x)/period
    return v

def adx(c, period=14):
    if len(c)<period*2+1: return None
    tr=[]; pd=[]; md=[]
    for i in range(1,len(c)):
        x,y=c[i],c[i-1]; up=x['high']-y['high']; down=y['low']-x['low']
        pd.append(up if up>down and up>0 else 0.0); md.append(down if down>up and down>0 else 0.0)
        tr.append(max(x['high']-x['low'],abs(x['high']-y['close']),abs(x['low']-y['close'])))
    st=sum(tr[:period]); sp=sum(pd[:period]); sm=sum(md[:period]); dx=[]
    for i in range(period-1,len(tr)):
        if i>=period:
            st=st-st/period+tr[i]; sp=sp-sp/period+pd[i]; sm=sm-sm/period+md[i]
        if st<=0: continue
        pdi=100*sp/st; mdi=100*sm/st; den=pdi+mdi
        dx.append(100*abs(pdi-mdi)/den if den else 0)
    if len(dx)<period: return None
    v=sum(dx[:period])/period
    for x in dx[period:]: v=(v*(period-1)+x)/period
    return v

def macd(values, fast=12, slow=26, signal_period=9):
    if len(values)<slow+signal_period: return None
    line=[ema(values[:i+1],fast)-ema(values[:i+1],slow) for i in range(slow-1,len(values))]
    sig=ema(line,signal_period)
    return line[-1]-sig

def bb_middle(values, period=20):
    return sum(values[-period:])/period if len(values)>=period else None

def load_m1(path):
    rows=[]
    with open(path,newline='',encoding='utf-8') as f:
        for r in csv.DictReader(f):
            t=dt.datetime.fromisoformat(r['timestamp'].replace('Z','+00:00'))
            if t.tzinfo is None: t=t.replace(tzinfo=dt.timezone.utc)
            x={'time':t.astimezone(dt.timezone.utc)}
            for side in ('bid','ask'):
                for k in ('open','high','low','close'):
                    x[f'{side}_{k}']=float(r[f'{side}_{k}'])
            if any(x[f'ask_{k}'] < x[f'bid_{k}'] for k in ('open','high','low','close')):
                raise ValueError(f'ASK below BID at {t.isoformat()}')
            rows.append(x)
    rows.sort(key=lambda x:x['time'])
    if len({x['time'] for x in rows}) != len(rows): raise ValueError('Duplicate M1 timestamps')
    return rows

def aggregate(m1, minutes):
    groups={}
    for x in m1:
        epoch=int(x['time'].timestamp()); start=epoch-epoch%(minutes*60)
        groups.setdefault(start,[]).append(x)
    out=[]
    for start, xs in sorted(groups.items()):
        if len(xs)!=minutes: continue
        if any((xs[i]['time']-xs[i-1]['time']).total_seconds()!=60 for i in range(1,len(xs))): continue
        b={'time':dt.datetime.fromtimestamp(start,tz=dt.timezone.utc)}
        for side in ('bid','ask'):
            b[f'{side}_open']=xs[0][f'{side}_open']
            b[f'{side}_high']=max(z[f'{side}_high'] for z in xs)
            b[f'{side}_low']=min(z[f'{side}_low'] for z in xs)
            b[f'{side}_close']=xs[-1][f'{side}_close']
        b.update(open=b['bid_open'],high=b['bid_high'],low=b['bid_low'],close=b['bid_close'])
        out.append(b)
    return out

def signal(candles):
    closes=[x['close'] for x in candles]
    e20,e50=ema(closes,20),ema(closes,50); rv=rsi(closes); av=atr(candles); dx=adx(candles)
    mh=macd(closes); mid=bb_middle(closes)
    if any(x is None for x in (e20,e50,rv,av,dx,mh,mid)) or av<=0: return None
    price=closes[-1]
    buy=[e20>e50,52<=rv<=68,mh>0,price>mid]
    sell=[e20<e50,32<=rv<=48,mh<0,price<mid]
    bs,ss=sum(buy),sum(sell)
    if dx>=20 and bs>=3 and bs>ss: return ('BUY',av)
    if dx>=20 and ss>=3 and ss>bs: return ('SELL',av)
    return None

def replay(m1, timeframe):
    bars=aggregate(m1,timeframe); by_time={x['time']:i for i,x in enumerate(m1)}
    trades=[]; blocked_until=None
    for i in range(50,len(bars)-1):
        if blocked_until is not None and bars[i]['time'] < blocked_until: continue
        s=signal(bars[:i+1])
        if not s: continue
        side,av=s; next_time=bars[i]['time']+dt.timedelta(minutes=timeframe)
        entry_idx=by_time.get(next_time)
        if entry_idx is None: continue
        entrybar=m1[entry_idx]
        entry=entrybar['ask_open'] if side=='BUY' else entrybar['bid_open']
        risk=1.5*av; sl=entry-risk if side=='BUY' else entry+risk
        tp=entry+3*av if side=='BUY' else entry-3*av
        end=min(len(m1)-1,entry_idx+MAX_HOLD_MINUTES-1); exit_price=None; reason='TIME'
        for j in range(entry_idx,end+1):
            z=m1[j]
            if side=='BUY':
                hit_sl=z['bid_low']<=sl; hit_tp=z['bid_high']>=tp
            else:
                hit_sl=z['ask_high']>=sl; hit_tp=z['ask_low']<=tp
            if hit_sl: exit_price=sl; reason='SL'; break
            if hit_tp: exit_price=tp; reason='TP'; break
        if exit_price is None:
            z=m1[end]; exit_price=z['bid_close'] if side=='BUY' else z['ask_close']
        pnl=(exit_price-entry) if side=='BUY' else (entry-exit_price)
        trades.append({'time':entrybar['time'].isoformat(),'side':side,'entry':entry,'sl':sl,'tp':tp,
                       'exit':exit_price,'exit_reason':reason,'pnl_price':pnl,'r_multiple':pnl/risk})
        blocked_until=m1[end]['time']+dt.timedelta(minutes=1)
    return trades

def stats(trades):
    if not trades: return {'trades':0,'win_rate_pct':None,'profit_factor':None,'expectancy_r':None,'net_r':0.0,'max_drawdown_r':0.0}
    rs=[x['r_multiple'] for x in trades]; wins=[x for x in rs if x>0]; losses=[x for x in rs if x<0]
    gross_win=sum(wins); gross_loss=abs(sum(losses)); eq=peak=dd=0
    for x in rs:
        eq+=x; peak=max(peak,eq); dd=max(dd,peak-eq)
    return {'trades':len(rs),'win_rate_pct':round(100*len(wins)/len(rs),2),
            'profit_factor':round(gross_win/gross_loss,3) if gross_loss else None,
            'expectancy_r':round(sum(rs)/len(rs),4),'net_r':round(sum(rs),3),
            'max_drawdown_r':round(dd,3)}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input-dir',default='research_data/dukascopy_m1')
    ap.add_argument('--output-dir',default='research_results'); args=ap.parse_args()
    src=Path(args.input_dir); dst=Path(args.output_dir); dst.mkdir(parents=True,exist_ok=True)
    rows=[]
    for symbol in ('EURUSD','XAUUSD','BTCUSD'):
        path=src/f'{symbol}_M1_BID_ASK.csv'
        if not path.exists(): continue
        m1=load_m1(path)
        for tf in TIMEFRAMES:
            trades=replay(m1,tf)
            if not trades: rows.append({'symbol':symbol,'timeframe':f'M{tf}','segment':'ALL',**stats([])}); continue
            split=int(len(trades)*0.7)
            for segment,part in (('TRAIN',trades[:split]),('TEST',trades[split:]),('ALL',trades)):
                rows.append({'symbol':symbol,'timeframe':f'M{tf}','segment':segment,**stats(part)})
            with (dst/f'{symbol}_M{tf}_trades.csv').open('w',newline='',encoding='utf-8') as f:
                w=csv.DictWriter(f,fieldnames=list(trades[0])); w.writeheader(); w.writerows(trades)
    if not rows: raise SystemExit('No valid BID/ASK datasets found; no backtest performed.')
    with (dst/'summary.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print(f'Wrote {len(rows)} summary rows to {dst}/summary.csv')

if __name__=='__main__': main()
