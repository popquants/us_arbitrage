#!/usr/bin/env python3
"""PopQuants interactive tear sheet viewer V3.

Read-only visualization from EXISTING Strategy 1 / Strategy 2 output CSV files.
No API calls, credentials, trades, or changes to the strategy scripts.

Usage: python3 popquants_tearsheet_viewer_v3.py
Optional: --root ~/Documents/usmart --no-open
"""
from __future__ import annotations
import argparse
import html
import json
import sys
import webbrowser
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from plotly.offline import get_plotlyjs

BASE = Path(__file__).resolve().parents[1]
COL = {'s1': '#8DB8E3', 's2': '#68D5BD', 'gross': '#A9DAB0', 'benchmark': '#E5BD61', 's3': '#C59DFA',
       'rich': '#E58490', 'cheap': '#68D5BD', 'muted': '#A8B8BA'}
LAYOUT = dict(template='plotly_dark', paper_bgcolor='#0B1B1E', plot_bgcolor='#0B1B1E',
              font=dict(color='#D9E8E3', size=12), margin=dict(l=62, r=36, t=70, b=50),
              hovermode='x unified', legend=dict(orientation='h', y=1.13, x=0, bgcolor='rgba(0,0,0,0)'))
# Disable the Plotly logo/link in every chart's modebar. Still keep legend click toggling.
PLOT_CONFIG = {'responsive': True, 'displaylogo': False,
               'modeBarButtonsToRemove': ['sendDataToCloud'], 'scrollZoom': False}


def read_csv(root: Path, name: str, required: list[str]) -> pd.DataFrame:
    path = root / name
    if not path.is_file():
        raise FileNotFoundError(f'Missing {path}\nRun Strategy 1 and Strategy 2 first.')
    x = pd.read_csv(path)
    if 'date' not in x.columns and x.columns[0].startswith('Unnamed'):
        x = x.rename(columns={x.columns[0]: 'date'})
    absent = sorted(set(required + ['date']) - set(x.columns))
    if absent:
        raise ValueError(f'{path.name}: missing columns {absent}')
    x['date'] = pd.to_datetime(x['date'], errors='raise')
    if x['date'].duplicated().any():
        raise ValueError(f'{path.name}: duplicate dates')
    return x.set_index('date').sort_index()


def get_data(root: Path):
    # Same calendar window for all series. Only existing strategy files are read.
    d1,d2,d3 = [root/'outputs'/f'strategy{i}' for i in (1,2,3)]
    prices = read_csv(root/'data'/'prices','us_stock_adjusted_daily.csv',['AMAT','LRCX','KLAC','TER'])
    s1 = read_csv(d1,'daily_backtest.csv',['net_return'])
    s2 = read_csv(d2,'daily_backtest.csv',['net_return','gross_return'])
    agg = read_csv(d2,'aggregate_signals.csv',list(prices.columns))
    depth = read_csv(d2,'depth.csv',list(prices.columns))
    cons = read_csv(d2,'consistency.csv',list(prices.columns))
    tgt = read_csv(d2,'target_weights.csv',list(prices.columns))
    s3 = read_csv(d3,'daily_backtest.csv',['net_return']) if (d3/'daily_backtest.csv').is_file() else None
    ix = s1.index.intersection(s2.index).intersection(prices.index).intersection(agg.index).intersection(tgt.index)
    if s3 is not None: ix = ix.intersection(s3.index)
    ix = ix[(ix >= pd.Timestamp('2026-01-02')) & (ix <= pd.Timestamp('2026-11-20'))]
    if len(ix) < 60:
        raise ValueError('Need at least 60 overlapping 2026 trading days in strategy exports.')
    s1,s2,prices,agg,depth,cons,tgt = [x.loc[ix].copy() for x in (s1,s2,prices,agg,depth,cons,tgt)]
    bench_equity = (prices / prices.iloc[0]).mean(axis=1)
    returns = {'Strategy 1 (net)':s1['net_return'].astype(float),
               'Strategy 2 (net)':s2['net_return'].astype(float)}
    if s3 is not None:
        returns['Strategy 3 (net)'] = s3.loc[ix,'net_return'].astype(float)
    returns['Equal-weight buy & hold'] = bench_equity.pct_change().fillna(0.)
    if not np.isfinite(pd.concat(returns.values(),axis=1).to_numpy()).all():
        raise ValueError('Nonfinite daily returns found.')
    return prices,s1,s2,agg,depth,cons,tgt,returns


def metrics(r):
    eq=(1+r).cumprod();dd=eq/eq.cummax()-1
    vol=r.std(ddof=1)*np.sqrt(252)
    sd=r.std(ddof=1)
    negative=np.sqrt(np.square(r.clip(upper=0)).mean())
    cagr=eq.iloc[-1]**(252/len(r))-1 if eq.iloc[-1]>0 else np.nan
    return {'Total return': 100*(eq.iloc[-1]-1), 'CAGR':100*cagr,
            'Sharpe (rf=0)':np.sqrt(252)*r.mean()/sd if sd>0 else np.nan,
            'Sortino (rf=0)':np.sqrt(252)*r.mean()/negative if negative>0 else np.nan,
            'Max drawdown':100*dd.min(), 'Annual vol':100*vol,
            'Calmar':cagr/abs(dd.min()) if dd.min()<0 else np.nan}


def equity_fig(returns, gross):
    f=go.Figure()
    for k,r in returns.items():
        c=COL['s1'] if k.startswith('Strategy 1') else COL['s2'] if k.startswith('Strategy 2') else COL['s3'] if k.startswith('Strategy 3') else COL['benchmark']
        f.add_trace(go.Scatter(x=r.index,y=(1+r).cumprod(),mode='lines',name=k,line=dict(color=c,width=2)))
    f.add_trace(go.Scatter(x=gross.index,y=(1+gross).cumprod(),name='Strategy 2 (gross)',
                           line=dict(color=COL['gross'],dash='dot',width=1.4)))
    f.update_layout(**LAYOUT,title='Cumulative equity comparison',height=450)
    f.update_yaxes(title='NAV (start = 1.0)')
    return f


def drawdown_fig(returns):
    f=go.Figure()
    for k,r in returns.items():
        e=(1+r).cumprod(); c=COL['s1'] if k.startswith('Strategy 1') else COL['s2'] if k.startswith('Strategy 2') else COL['s3'] if k.startswith('Strategy 3') else COL['benchmark']
        f.add_trace(go.Scatter(x=r.index,y=100*(e/e.cummax()-1),name=k,line=dict(color=c,width=2)))
    f.update_layout(**LAYOUT,title='Drawdown comparison',height=340)
    f.update_yaxes(title='Drawdown (%)')
    return f


def monthly_fig(r):
    months=(1+r).resample('ME').prod()-1
    ys=sorted(months.index.year.unique()); z=np.full((len(ys),12),np.nan)
    for d,v in months.items():z[ys.index(d.year),d.month-1]=100*v
    labels=[[f'{v:+.1f}%' if np.isfinite(v) else '' for v in row] for row in z]
    f=go.Figure(go.Heatmap(x=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'],
       y=[str(y) for y in ys],z=z,text=labels,texttemplate='%{text}',colorscale='RdYlGn',zmid=0,
       hovertemplate='%{x} %{y}<br>Return: %{z:+.2f}%<extra></extra>'))
    f.update_layout(**LAYOUT,title='Strategy 2 monthly net returns',height=max(300,75*len(ys)+120))
    f.update_yaxes(autorange='reversed')
    return f


def rolling_fig(r):
    f=go.Figure(go.Scatter(x=r.index,y=np.sqrt(252)*r.rolling(63).mean()/r.rolling(63).std(),
                            line=dict(color=COL['s2']),name='Rolling Sharpe'))
    f.add_hline(y=0,line_dash='dash',line_color='#849496')
    f.update_layout(**LAYOUT,title='Strategy 2 rolling 63-day Sharpe (rf=0)',height=340)
    return f



def risk_return_fig(returns):
    f=go.Figure()
    for name,r in returns.items():
        m=metrics(r)
        color = COL['s1'] if name.startswith('Strategy 1') else COL['s2'] if name.startswith('Strategy 2') else COL['s3'] if name.startswith('Strategy 3') else COL['benchmark']
        f.add_trace(go.Scatter(x=[m['Annual vol']], y=[m['CAGR']], text=[name],
            mode='markers+text', textposition='top center', name=name,
            marker=dict(size=14, color=color, line=dict(color='#D9E8E3',width=1)),
            hovertemplate='%{text}<br>Annualized standard deviation: %{x:.2f}%<br>Annualized return (CAGR): %{y:.2f}%<extra></extra>'))
    f.update_layout(**LAYOUT, title='Annualized volatility vs annualized net return', height=370, showlegend=False)
    f.update_xaxes(title='Annualized standard deviation (%)')
    f.update_yaxes(title='CAGR / annualized return (%)')
    return f

def signal_explorer(prices,agg,depth,cons,tgt):
    """One dropdown controls stock price, aggregate score, and actual target/held weight.

    Trade markers are detected from CHANGES in target weights, not every eligible candidate.
    Chart labels distinguish decisions at close t from held position at t+1.
    """
    tickers=list(tgt.columns)
    f=make_subplots(rows=3,cols=1,shared_xaxes=True,vertical_spacing=.09,
          row_heights=[.39,.37,.24],subplot_titles=('Adjusted close price + position-change decisions',
                  'Aggregate score (negative=cheap / positive=rich)','Held weight (shifted one day)'))
    per_stock=[]
    for ticker in tickers:
        before=len(f.data)
        pp=prices[ticker]
        f.add_trace(go.Scatter(x=pp.index,y=pp,name=f'{ticker} adjusted price',mode='lines',
                    line=dict(color='#92B9E4',width=1.8),hovertemplate='%{x|%Y-%m-%d}<br>Price %{y:.2f}<extra></extra>'),row=1,col=1)
        # A signal generated at close t starts as an exposure on next trading bar.
        changes=tgt[ticker].ne(tgt[ticker].shift(1).fillna(0))
        target=tgt[ticker]
        for sign,condition,color,label,symbol in [
            (1,(changes & (target>0)),COL['cheap'],'Long decision','triangle-up'),
            (-1,(changes & (target<0)),COL['rich'],'Short decision','triangle-down'),
            (0,(changes & (target==0)),COL['benchmark'],'Exit decision','x')]:
            dates=target.index[condition]
            f.add_trace(go.Scatter(x=dates,y=pp.reindex(dates),mode='markers',
                         name=f'{ticker}: {label}',marker=dict(color=color,symbol=symbol,size=11),
                         hovertemplate='%{x|%Y-%m-%d}<br>Decision close; position applies next bar<extra></extra>'),row=1,col=1)
        f.add_trace(go.Scatter(x=agg.index,y=agg[ticker],mode='lines',name=f'{ticker} aggregate score',
                    line=dict(color=COL['gross'],width=1.8),customdata=np.column_stack([depth[ticker].values,cons[ticker].values]),
                    hovertemplate='%{x|%Y-%m-%d}<br>Aggregate %{y:+.2f}<br>Depth %{customdata[0]:.0f}<br>Consistency %{customdata[1]:.2f}<extra></extra>'),row=2,col=1)
        held=target.shift(1).fillna(0)
        f.add_trace(go.Scatter(x=held.index,y=held,mode='lines',name=f'{ticker} held weight',
                    line=dict(shape='hv',color=COL['cheap'],width=2),fill='tozeroy',
                    hovertemplate='%{x|%Y-%m-%d}<br>Held weight %{y:.2f}<extra></extra>'),row=3,col=1)
        per_stock.append(list(range(before,len(f.data))))
    # Default: first ticker only. Dropdown toggles ALL traces consistently.
    for i,tr in enumerate(f.data):tr.visible = i in per_stock[0]
    buttons=[]
    for ticker,indices in zip(tickers,per_stock):
        visibility=[i in indices for i in range(len(f.data))]
        buttons.append(dict(label=ticker,method='update',args=[{'visible':visibility},
                             {'title':f'Signal Explorer — {ticker}'}]))
    f.add_hline(y=0,row=2,col=1,line_color='#889999',line_dash='dash')
    f.add_hline(y=0,row=3,col=1,line_color='#889999',line_dash='dash')
    f.update_layout(**{k:v for k,v in LAYOUT.items() if k not in ('legend','hovermode')},title=f'Signal Explorer — {tickers[0]}',height=900,
        updatemenus=[dict(type='dropdown',buttons=buttons,x=.995,y=1.075,xanchor='right',yanchor='top',
                          bgcolor='#233A3C',font=dict(color='white'))],
        legend=dict(orientation='h',y=-.09,x=0),hovermode='x unified')
    f.update_yaxes(title_text='USD*',row=1,col=1)
    f.update_yaxes(title_text='Score',row=2,col=1)
    f.update_yaxes(title_text='Weight',range=[-.62,.62],row=3,col=1)
    f.update_xaxes(title_text='Trading date',row=3,col=1)
    return f


def scorecard(returns):
    stats={name:metrics(r) for name,r in returns.items()}
    headers='<tr><th>Metric</th>'+''.join('<th>'+html.escape(n)+'</th>' for n in returns)+'</tr>'
    rows=[]
    for k in ['Total return','CAGR','Sharpe (rf=0)','Sortino (rf=0)','Max drawdown','Annual vol','Calmar']:
        cells=''.join('<td>'+(f'{stats[n][k]:+.2f}{"%" if k in ("Total return","CAGR","Max drawdown","Annual vol") else ""}' if np.isfinite(stats[n][k]) else '—')+'</td>' for n in returns)
        rows.append('<tr><td>'+html.escape(k)+'</td>'+cells+'</tr>')
    return '<table><thead>'+headers+'</thead><tbody>'+''.join(rows)+'</tbody></table>'


def position_changes_html(tgt: pd.DataFrame) -> str:
    """Decision log derived from target-weight changes; not broker executions."""
    before = tgt.shift(1).fillna(0)
    changes = tgt.ne(before)
    rows=[]
    for day in tgt.index:
        for ticker in tgt.columns:
            if not changes.at[day,ticker]:
                continue
            old=float(before.at[day,ticker]); new=float(tgt.at[day,ticker])
            action=('EXIT' if new == 0 else 'LONG TARGET' if new > 0 else 'SHORT TARGET')
            rows.append((day.date().isoformat(),ticker,action,old,new))
    if not rows:
        return '<p>No target position changes in this sample.</p>'
    body=''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in (d,t,a,f'{old:+.2f}',f'{new:+.2f}'))+'</tr>'
                 for d,t,a,old,new in rows[::-1])
    return '<table><thead><tr><th>Decision date</th><th>Ticker</th><th>Event</th><th>Previous target</th><th>New target</th></tr></thead><tbody>'+body+'</tbody></table>'


def plot_html(fig):
    return fig.to_html(full_html=False, include_plotlyjs=False, config=PLOT_CONFIG)


def main():
    p=argparse.ArgumentParser(description='PopQuants visual dashboard V3. Reads cached Strategy 1–3 CSVs only.')
    p.add_argument('--root',type=Path,default=BASE)
    p.add_argument('--no-open',action='store_true')
    a=p.parse_args()
    root=a.root.expanduser()
    prices,s1,s2,agg,depth,cons,tgt,returns=get_data(root)
    figs=[('equity',equity_fig(returns,s2.gross_return)),('drawdown',drawdown_fig(returns)),
          ('signal',signal_explorer(prices,agg,depth,cons,tgt)),('monthly',monthly_fig(returns['Strategy 2 (net)'])),
          ('rolling',rolling_fig(returns['Strategy 2 (net)']))]
    nav=''.join(f'<a href="#{name}">{name.title()}</a>' for name,_ in figs) + '<a href="#trades">Position changes</a>'
    blocks=''.join(f'<section class="card" id="{name}">{plot_html(fig)}</section>' for name,fig in figs)
    blocks += '<section class="card" id="trades"><h2>Target position changes (model decisions, not fills)</h2><div class="position-table-wrap">' + position_changes_html(tgt) + '</div></section>'
    page='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PopQuants — Interactive Tear Sheet</title><style>
*{box-sizing:border-box}body{margin:0;background:#071416;color:#D9E8E3;font:15px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{padding:30px 4vw;border-bottom:1px solid #284144}h1{margin:4px 0 9px;font-size:29px}
main{max-width:1480px;margin:auto;padding:24px 3vw}h2{font-size:18px}.muted{color:#9FB8B5}
.card{border:1px solid #284144;border-radius:13px;padding:12px;margin-bottom:22px;background:#0B1B1E;overflow:hidden}
a{color:#90D7C6;text-decoration:none}nav{display:flex;flex-wrap:wrap;gap:20px;margin-top:16px}
.table-wrap{overflow-x:auto}.position-table-wrap{max-height:495px;overflow:auto;overscroll-behavior:contain}.position-table-wrap thead th{position:sticky;top:0;background:#193034;z-index:2}table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:12px;border-bottom:1px solid #284144;text-align:right;white-space:nowrap}th:first-child,td:first-child{text-align:left}
.note{padding:15px 18px;border-left:3px solid #E5BD61;background:#132A2D;line-height:1.6;font-size:13px}
/* Protect users from the promotional Plotly brand link, including old/embedded modebar variants */
.modebar-btn.plotlyjsicon,.modebar .logo,.modebar a[data-title*="Produced with Plotly"],.modebar [data-title*="Produced with Plotly"]{display:none!important}
</style><script>__PLOTLY_JS__</script></head><body>
<header><div class="muted">POPQUANTS · RESEARCH LAB</div><h1>Interactive Tear Sheet — Strategies 1–3</h1>
<div class="muted">Offline report · Same aligned date range · Equal-weight buy &amp; hold benchmark · Strategy 3 shown when completed</div>
<nav>__NAV__</nav></header><main><section class="card"><h2>Performance comparison</h2><div class="table-wrap">__TABLE__</div><div style="margin-top:18px">__RISK_RETURN__</div></section>
<section class="card"><div class="note">Strategy calculations are read from the existing CSVs. No strategy file was modified.
Signals: target decisions at close t; held positions approximated from t+1. Position markers are model decisions, NOT broker fills.
Price series are broker-adjusted research data, check currency. Equal-weight benchmark is 25% each AMAT/LRCX/KLAC/TER initially, long-only with no rebalancing and no modeled costs.
Strategy 2 is the four-stock educational prototype, not the published 13F or 50-stock strategy. 5 bps model cost excludes borrow, financing and dividends.</div></section>
__BLOCKS__<div class="muted" style="padding:20px">PopQuants research education · Read-only cached local CSV · Plotly offline</div></main></body></html>'''
    page=page.replace('__PLOTLY_JS__',get_plotlyjs()).replace('__NAV__',nav).replace('__TABLE__',scorecard(returns)).replace('__RISK_RETURN__',plot_html(risk_return_fig(returns))).replace('__BLOCKS__',blocks)
    out=root/'outputs'/'dashboard'
    out.mkdir(parents=True,exist_ok=True)
    path=out/'interactive_tearsheet.html'
    path.write_text(page,encoding='utf-8')
    print('PASS: read historical local CSV outputs. No API requests.')
    print('Report:',path)
    print('Signal Explorer:',', '.join(tgt.columns))
    print('Plotly logo/link disabled. Legend toggling remains enabled.')
    if not a.no_open:webbrowser.open(path.resolve().as_uri())
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,KeyError) as e:
        print('ERROR:',str(e),file=sys.stderr);raise SystemExit(1)