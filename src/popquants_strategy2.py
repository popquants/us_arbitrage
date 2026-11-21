#!/usr/bin/env python3
"""PopQuants Strategy 2: educational small-universe triangulated stat arb.
READ ONLY. By default consumes local uSMART adjusted close CSV from Strategy 1.
This is a transparent FOUR-stock teaching adaptation, NOT replication of the
published 13F embedding / 25-long 25-short / depth>=4 model.
Signals at close t; traded for close-to-close return t+1 using shift(1).
No order or authentication calls are made by this script.
"""
from __future__ import annotations
import argparse, json, math, webbrowser
from pathlib import Path
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs
from popquants_pairs_strategy1 import backtest as strategy1_backtest

DEFAULT_CSV=Path.home()/ 'Documents/usmart/popquants_strategy1/us_stock_adjusted_daily.csv'
DEFAULT_OUT=Path.home()/ 'Documents/usmart/popquants_strategy2'
DEFAULT_UNIVERSE=['AMAT','LRCX','KLAC','TER']
COLORS=['#83e0c4','#9ecbfc','#f3c66d','#f28d91']

def read_prices(path:Path,universe:list[str])->pd.DataFrame:
    x=pd.read_csv(path,parse_dates=['date']).set_index('date').sort_index()
    missing=set(universe)-set(x.columns)
    if missing: raise ValueError('Missing tickers: '+', '.join(sorted(missing)))
    x=x[universe].apply(pd.to_numeric,errors='coerce').replace([np.inf,-np.inf],np.nan).dropna()
    if x.index.duplicated().any() or (x<=0).any().any(): raise ValueError('Duplicate dates or nonpositive prices')
    if len(x)<180: raise ValueError('Need >= 180 aligned daily prices')
    return x

def compute_signals(prices:pd.DataFrame,lookback:int=60):
    """All predeclared within-universe pairs; equal weighting; no hindsight pair selection."""
    names=list(prices.columns); votes={k:[] for k in names}; zz={}
    for i,a in enumerate(names):
        for b in names[i+1:]:
            s=np.log(prices[a])-np.log(prices[b]); m=s.rolling(lookback).mean(); sd=s.rolling(lookback).std(ddof=1)
            z=((s-m)/sd.replace(0,np.nan)).replace([np.inf,-np.inf],np.nan)
            zz[f'{a}/{b}']=z;votes[a].append(z.rename(f'{a}/{b}'));votes[b].append((-z).rename(f'{b}/{a}'))
    agg=pd.DataFrame(index=prices.index);depth=pd.DataFrame(index=prices.index);cons=pd.DataFrame(index=prices.index)
    for ticker in names:
        stack=pd.concat(votes[ticker],axis=1)
        agg[ticker]=stack.mean(axis=1)
        depth[ticker]=stack.notna().sum(axis=1)
        cons[ticker]=stack.apply(lambda row: abs(np.sign(row.dropna()).mean()) if row.notna().any() else np.nan,axis=1)
    return pd.DataFrame(zz),agg,depth,cons

def build_strategy(prices:pd.DataFrame, window:int=60, min_depth:int=2, min_consistency:float=.5, cost_bps:float=5.):
    zs,agg,depth,cons=compute_signals(prices,window)
    eligible=(depth>=min_depth)&(cons>min_consistency)
    tgt=pd.DataFrame(0.0,index=prices.index,columns=prices.columns)
    event_rows=[]
    for day in prices.index:
        good=agg.loc[day].where(eligible.loc[day]).dropna()
        longs=good[good<0].sort_values(); shorts=good[good>0].sort_values(ascending=False)
        if longs.empty or shorts.empty: continue
        long=longs.index[0];short=shorts.index[0]
        if long==short: continue
        tgt.at[day,long]=.5;tgt.at[day,short]=-.5
        event_rows.append({'date':day.strftime('%Y-%m-%d'),'long':long,'short':short,
          'long_score':float(agg.at[day,long]),'short_score':float(agg.at[day,short]),
          'long_depth':int(depth.at[day,long]),'short_depth':int(depth.at[day,short]),
          'long_consistency':float(cons.at[day,long]),'short_consistency':float(cons.at[day,short])})
    # Daily close t signal influences the return at t+1 only.
    held=tgt.shift(1).fillna(0)
    stock_returns=prices.pct_change().fillna(0)
    gross=(held*stock_returns).sum(axis=1)
    turnover=held.diff().abs().sum(axis=1).fillna(0)
    # Initial position move from flat handled via diff with fillna(0).
    net=gross-turnover*cost_bps/10000
    first=agg.dropna(how='all').index.min()
    if pd.isna(first): raise ValueError('No valid signals (try reducing lookback)')
    df=pd.DataFrame({'gross_return':gross,'net_return':net,'turnover':turnover,'active':(held.abs().sum(axis=1)>0).astype(int)},index=prices.index).loc[first:]
    df['gross_equity']=(1+df.gross_return).cumprod();df['equity']=(1+df.net_return).cumprod();df['drawdown']=df.equity/df.equity.cummax()-1
    benchmark=(prices.loc[df.index]/prices.loc[df.index].iloc[0]).mean(axis=1).pct_change().fillna(0)
    df['benchmark_return']=benchmark
    df['benchmark_equity']=(1+benchmark).cumprod()
    # Trade transitions (actual changes in selected target weights) only.
    # The daily candidates remain available separately as aggregate_signals.csv.
    changes=tgt.ne(tgt.shift(1).fillna(0)).any(axis=1)
    events=pd.DataFrame(event_rows)
    if not events.empty:
        events=events.loc[pd.to_datetime(events['date']).isin(tgt.index[changes]) & (pd.to_datetime(events['date'])>=first)].reset_index(drop=True)
    return df,zs.loc[df.index],agg.loc[df.index],depth.loc[df.index],cons.loc[df.index],tgt.loc[df.index],events

def metrics(r:pd.Series)->dict:
    r=r.fillna(0).astype(float); eq=(1+r).cumprod();dd=eq/eq.cummax()-1
    sd=r.std(ddof=1);down=np.sqrt(np.mean(np.minimum(r,0)**2));cagr=float(eq.iloc[-1]**(252/len(r))-1) if eq.iloc[-1]>0 else float('nan')
    return {'Total Return':float(eq.iloc[-1]-1),'CAGR':cagr,'Sharpe (rf=0)':float(np.sqrt(252)*r.mean()/sd) if sd>0 else None,
      'Sortino (rf=0)':float(np.sqrt(252)*r.mean()/down) if down>0 else None,'Annual Volatility':float(sd*np.sqrt(252)),
      'Max Drawdown':float(dd.min()),'Calmar':float(cagr/abs(dd.min())) if dd.min()<0 else None,'Trading Days':len(r)}

def heatmap(r:pd.Series,monthly:bool=True):
    if monthly:
        monthly_r=(1+r).resample('ME').prod()-1;years=sorted(monthly_r.index.year.unique());mat=np.full((len(years),12),np.nan)
        for d,v in monthly_r.items():mat[years.index(d.year),d.month-1]=100*v
        return go.Figure(go.Heatmap(z=mat,x=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'],y=years,
            colorscale='RdYlGn',zmid=0,hovertemplate='%{x} %{y}: %{z:.2f}%<extra></extra>'))
    # Week rows with Mon-Fri columns, every trading session included; no weekend fabricated.
    isoweek=r.index.isocalendar();keys=list(dict.fromkeys(zip(isoweek.year.to_numpy(),isoweek.week.to_numpy())))
    mat=np.full((len(keys),5),np.nan)
    for dt,val in r.items():mat[keys.index((int(dt.isocalendar().year),int(dt.isocalendar().week))),dt.weekday()]=100*val
    fig=go.Figure(go.Heatmap(z=mat,x=['Mon','Tue','Wed','Thu','Fri'],y=[f'{a}-W{b:02d}' for a,b in keys],colorscale='RdYlGn',zmid=0,
        hovertemplate='%{y} %{x}: %{z:.2f}%<extra></extra>'))
    return fig

def fig_style(fig,title,height=380):
    fig.update_layout(title=title,height=height,template='plotly_dark',paper_bgcolor='#0e1d20',plot_bgcolor='#0e1d20',font={'color':'#e1efe7'},margin=dict(l=55,r=25,t=58,b=55),hovermode='x unified')
    return fig

def save_dashboard(out:Path,bt:pd.DataFrame,agg:pd.DataFrame,depth:pd.DataFrame,cons:pd.DataFrame,events:pd.DataFrame,compare:dict):
    cols={'Strategy 1 (AMAT/LRCX)':COLORS[1],'Strategy 2 (network)':COLORS[0],'Equal-weight buy/hold':COLORS[2]}
    eq=go.Figure()
    for name,r in compare.items():eq.add_trace(go.Scatter(x=r.index,y=(1+r).cumprod(),name=name,line=dict(color=cols.get(name,'#ffffff'))))
    eq.add_trace(go.Scatter(x=bt.index,y=bt.gross_equity,name='Strategy 2 gross',line=dict(color='#b4d4a9',dash='dot')))
    dd=go.Figure()
    for name,r in compare.items():
        e=(1+r).cumprod();dd.add_trace(go.Scatter(x=r.index,y=100*(e/e.cummax()-1),name=name,line=dict(color=cols.get(name,'#fff'))))
    ag=go.Figure()
    for ticker in agg.columns:ag.add_trace(go.Scatter(x=agg.index,y=agg[ticker],name=ticker))
    sig=go.Figure()
    if not events.empty:
        ev=events.copy();ev['date']=pd.to_datetime(ev['date'])
        for label,column,color,symbol in [('Long','long','#84dfcc','triangle-up'),('Short','short','#f18a92','triangle-down')]:
            for ticker in agg.columns:
                sub=ev[ev[column]==ticker]
                if not sub.empty:
                    sig.add_trace(go.Scatter(x=sub['date'],y=agg[ticker].reindex(sub['date']),mode='markers',name=f'{label} {ticker}',marker={'color':color,'size':9,'symbol':symbol}))
    scores=[]
    for name,r in compare.items():scores.append((name,metrics(r)))
    format_percent={'Total Return','CAGR','Annual Volatility','Max Drawdown'}
    headers='<tr><th>Metric</th>'+''.join('<th>'+n+'</th>' for n,_ in scores)+'</tr>'
    score_html=''
    for key in ['Total Return','CAGR','Sharpe (rf=0)','Sortino (rf=0)','Annual Volatility','Max Drawdown','Calmar','Trading Days']:
        cells=[]
        for _,m in scores:
            v=m[key];cells.append('<td>'+(f'{v*100:+.2f}%' if key in format_percent and v is not None else f'{v:.3f}' if isinstance(v,float) and np.isfinite(v) else str(v) if v is not None else 'N/A')+'</td>')
        score_html+='<tr><th>'+key+'</th>'+''.join(cells)+'</tr>'
    price_note='Local adjusted prices previously downloaded from uSMART; four stocks only. No 13F embeddings, Sharpe weighting or original 50-stock portfolio. Signal at close t, positions held at t+1 close-to-close proxy. 5bp per one-way turnover. No borrow, financing, dividends, fill costs.'
    graphs=[fig_style(eq,'Cumulative equity comparison',460),fig_style(dd,'Underwater / Drawdown'),fig_style(ag,'Aggregated signal by ticker'),fig_style(sig,'Signals: selected long/short candidates'),fig_style(heatmap(bt.net_return),'Monthly net return heatmap',350),fig_style(heatmap(bt.net_return,False),'Daily return by ISO week',max(420,15*len(bt)//5))]
    js=get_plotlyjs();blocks=''.join(f'<section class="graph">{fig.to_html(full_html=False,include_plotlyjs=False)}</section>' for fig in graphs)
    events_html=events.tail(80).to_html(index=False,float_format=lambda x:f'{x:.3f}',classes='tbl') if not events.empty else '<p>No eligible paired signals at current filters.</p>'
    document=f'''<!doctype html><html><head><meta charset="utf-8"><title>PopQuants Strategy 2</title><style>body{{background:#071718;color:#e4f2eb;font:15px Arial;margin:30px auto;max-width:1280px}}h1{{color:#f4d17e}}.graph{{margin:18px 0;padding:10px;border:1px solid #284343;border-radius:12px}}table{{border-collapse:collapse;width:100%;margin:20px 0}}th,td{{padding:10px;border-bottom:1px solid #35504b;text-align:right}}th:first-child{{text-align:left}}.small{{color:#a8c5c0}}.graph div{{width:100%}}</style><script>{js}</script></head><body><h1>POPQUANTS | STRATEGY 2</h1><p>Triangulated Statistical Arbitrage — classroom GICS baseline / four-stock prototype</p><p class="small">{price_note}</p><h2>Performance Comparison</h2><table>{headers}{score_html}</table>{blocks}<h2>Decision log (signal date, execution next bar)</h2>{events_html}<p class="small">Strategy 3 not yet implemented; it is intentionally omitted rather than fabricated.</p></body></html>'''
    path=out/'interactive_tearsheet.html';path.write_text(document,encoding='utf-8');return path

def run(path:Path,out:Path,window=60,depth=2,consistency=.5,cost_bps=5.,open_browser=True):
    out.mkdir(parents=True,exist_ok=True);prices=read_prices(path,DEFAULT_UNIVERSE)
    df,zs,agg,dep,con,pos,ev=build_strategy(prices,window,depth,consistency,cost_bps)
    # Comparable dates and same Strategy 1 parameters as original default.
    try:
        _,s1,_,_=strategy1_backtest(prices,window=window,entry=2.,exit_z=.5,cost_bps=cost_bps)
        common=df.index.intersection(s1.index)
        df=df.loc[common];agg=agg.loc[common];dep=dep.loc[common];con=con.loc[common];zs=zs.loc[common];pos=pos.loc[common]
        ev=ev.loc[pd.to_datetime(ev.date).isin(common)] if not ev.empty else ev
        s1_ret=s1.loc[common,'net_return']
    except Exception as e:
        print('WARNING Strategy 1 comparison unavailable:',type(e).__name__,str(e));s1_ret=None
    benchmark=(prices.loc[df.index]/prices.loc[df.index].iloc[0]).mean(axis=1).pct_change().fillna(0)
    df['benchmark_return']=benchmark
    compare={'Strategy 2 (network)':df.net_return,'Equal-weight buy/hold':benchmark}
    if s1_ret is not None: compare={'Strategy 1 (AMAT/LRCX)':s1_ret,**compare}
    df.to_csv(out/'daily_backtest.csv');agg.to_csv(out/'aggregate_signals.csv');dep.to_csv(out/'depth.csv');con.to_csv(out/'consistency.csv');zs.to_csv(out/'pair_zscores.csv');pos.to_csv(out/'target_weights.csv');ev.to_csv(out/'trade_signals.csv',index=False)
    pd.DataFrame(compare).to_csv(out/'strategy_comparison_daily.csv',index_label='date')
    json.dump({name:metrics(r) for name,r in compare.items()},(out/'metrics.json').open('w'),indent=2)
    html=save_dashboard(out,df,agg,dep,con,ev,compare)
    print('Strategy 2 daily bars:',len(df),'| new/changed long-short selections:',len(ev))
    print('Net result:',metrics(df.net_return))
    print('Tearsheet:',html)
    if open_browser:webbrowser.open(html.resolve().as_uri())
    return df,agg,dep,con,ev

def main():
    p=argparse.ArgumentParser(description='PopQuants Strategy 2 | read-only offline research from local uSMART CSV')
    p.add_argument('--csv',type=Path,default=DEFAULT_CSV);p.add_argument('--outdir',type=Path,default=DEFAULT_OUT)
    p.add_argument('--window',type=int,default=60);p.add_argument('--min-depth',type=int,default=2)
    p.add_argument('--consistency',type=float,default=.5);p.add_argument('--cost-bps',type=float,default=5.)
    p.add_argument('--no-browser',action='store_true')
    a=p.parse_args()
    if not 20<=a.window<=250 or not 1<=a.min_depth<=3 or not 0<=a.consistency<1 or a.cost_bps<0:p.error('Invalid parameter')
    run(a.csv,a.outdir,a.window,a.min_depth,a.consistency,a.cost_bps,not a.no_browser)
if __name__=='__main__':main()