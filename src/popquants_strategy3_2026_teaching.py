#!/usr/bin/env python3
"""PopQuants Strategy 3: SEC 13F Word2Vec relationship selection + triangulated signals.

NO SYNTHETIC HOLDINGS, NO BROKER ORDERS, NO BROKER API.
Inputs: official SEC 13F quarterly bulk ZIPs; predownloaded uSMART adjusted daily CSV.
Important: A four-stock portfolio is a classroom experiment, NOT the Quantitativo replication.
"""
from __future__ import annotations
import argparse, io, json, sys, zipfile, webbrowser, html
from pathlib import Path
from itertools import combinations
import numpy as np
import pandas as pd
from gensim.models import Word2Vec
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs

DEFAULT_ROOT=Path.home()/"Documents/usmart"
# Confirmed common-stock CUSIPs; adjust mapping for corporate-action affected periods.
CUSIP_MAP={'AMAT':'038222105','LRCX':'512807306','KLAC':'482480100','TER':'880770102'}
MIN_SENTENCES=50
MAX_HOLDINGS_PER_FILING=160  # memory cap; candidate investment universe is NOT synthetic


def load_sec_zips(sec_dir:Path, max_funds:int=0, earliest_period=None):
    """Extract authentic as-filed 13F-HR filings via accession join in zipped TSVs.

    Keep filings from original 13F-HR only; exclude 13F-NT/13F-HR/A;
    ignore PUT/CALL lines and non-share (`PRN`) securities. SEC monthly ZIPs may
    include multiple report-period quarters. Every filing retains its own date.
    """
    files=sorted(sec_dir.glob('*.zip'))
    if not files:raise FileNotFoundError(f'No SEC 13F ZIP files in {sec_dir}. Download official SEC ZIP files first.')
    collected=[]; coverage=[]
    for archive in files:
        # For the short 2026 experiment, earlier archives cannot contain the 2025 Q3+ vintages we need.
        if earliest_period is not None:
            name=archive.name.lower()
            # SEC pre-2025 naming as well as monthly archive start dates.
            if name.startswith(('2023','2024','01jan2025','01mar2025','01jun2025','01dec2024')):
                print('SKIP historic archive:', archive.name, flush=True)
                continue
        print('SEC:',archive.name,flush=True)
        with zipfile.ZipFile(archive) as z:
            subs=[n for n in z.namelist() if Path(n).name.upper().startswith('SUBMISSION') and n.lower().endswith(('.tsv','.txt'))]
            infos=[n for n in z.namelist() if Path(n).name.upper().startswith('INFOTABLE') and n.lower().endswith(('.tsv','.txt'))]
            if not subs or not infos:raise ValueError(f'{archive.name}: cannot find SUBMISSION.tsv / INFOTABLE.tsv in ZIP')
            sub=pd.read_csv(z.open(subs[0]),sep='\t',dtype=str,low_memory=False,encoding='utf-8-sig')
            sub.columns=sub.columns.str.strip().str.upper()
            for col in ('ACCESSION_NUMBER','CIK','FILING_DATE','PERIODOFREPORT','SUBMISSIONTYPE'):
                if col not in sub:raise ValueError(f'{archive.name}: missing SUBMISSION.{col}')
            sub=sub[sub.SUBMISSIONTYPE.eq('13F-HR')].copy()
            sub['filing_date']=pd.to_datetime(sub.FILING_DATE,format='%d-%b-%Y',errors='coerce')
            sub['period']=pd.to_datetime(sub.PERIODOFREPORT,format='%d-%b-%Y',errors='coerce')
            # Avoid filing_dates after backtest cutoff by requiring period consistency.
            sub=sub[sub.filing_date.notna() & sub.period.notna()]
            if earliest_period is not None:
                sub=sub[sub.period >= pd.Timestamp(earliest_period)]
            # Choose one earliest original filing for each CIK/quarter (avoid duplication).
            sub=sub.sort_values(['filing_date','ACCESSION_NUMBER']).drop_duplicates(['CIK','period'],keep='first')
            keys=sub[['ACCESSION_NUMBER','CIK','filing_date','period']]
            wanted=set(keys.ACCESSION_NUMBER)
            chunks=[]
            for part in pd.read_csv(z.open(infos[0]),sep='\t',dtype=str,encoding='utf-8-sig',chunksize=200_000):
                part.columns=part.columns.str.strip().str.upper()
                needed=['ACCESSION_NUMBER','CUSIP','VALUE','PUTCALL']
                if not all(x in part.columns for x in needed[:3]):raise ValueError(f'{archive.name}: missing INFOTABLE fields')
                part=part.loc[part.ACCESSION_NUMBER.isin(wanted)].copy()
                if 'PUTCALL' in part:part=part[part.PUTCALL.isna() | (part.PUTCALL.str.strip()=='')]
                if 'SSHPRNAMTTYPE' in part:part=part[part.SSHPRNAMTTYPE.fillna('').str.strip().eq('SH')]
                part['VALUE']=pd.to_numeric(part.VALUE,errors='coerce')
                part=part[part.VALUE.gt(0) & part.CUSIP.notna()]
                part['CUSIP']=part.CUSIP.str.strip().str.upper()
                # Aggregate within accession by CUSIP; keep all available stocks for vocabulary.
                reduced=part.groupby(['ACCESSION_NUMBER','CUSIP'],as_index=False)['VALUE'].sum()
                reduced=reduced.sort_values('VALUE',ascending=False).groupby('ACCESSION_NUMBER',group_keys=False).head(MAX_HOLDINGS_PER_FILING)
                chunks.append(reduced)
            if not chunks:continue
            hold=pd.concat(chunks,ignore_index=True).groupby(['ACCESSION_NUMBER','CUSIP'],as_index=False)['VALUE'].sum()
            hold=hold.sort_values('VALUE',ascending=False).groupby('ACCESSION_NUMBER',group_keys=False).head(MAX_HOLDINGS_PER_FILING)
            hold=hold.merge(keys,on='ACCESSION_NUMBER',how='inner',validate='many_to_one')
            collected.append(hold)
            coverage.append({'zip':archive.name,'rows':len(hold),'filings':len(keys),'periods':','.join(sorted(hold.period.dt.strftime('%Y-%m-%d').unique()))})
    if not collected:raise ValueError('No authentic holdings found in SEC files')
    df=pd.concat(collected,ignore_index=True)
    df=df.sort_values('filing_date').drop_duplicates(['CIK','period','CUSIP'],keep='first')
    return df,pd.DataFrame(coverage)


def build_sentences(df,period,min_holdings=6,max_holdings=90,top_vocab=3000):
    """Fund as sentence; active tilt = position weight / pooled weight proxy.
    Tilt sorting is an approximation over retained top-160 13F holdings, NOT full portfolios.
    Research exact full-holdings definition before claiming replication.
    """
    q=df[df.period.eq(period)].copy()
    if q.empty:return [],{},q
    # Only filings publicly available at start_date after period+45 days;
    # caller applies date filter before this function.
    q=q.groupby(['CIK','CUSIP'],as_index=False)['VALUE'].sum()
    q['total']=q.groupby('CIK').VALUE.transform('sum')
    q['weight']=q.VALUE/q.total
    market=q.groupby('CUSIP').VALUE.sum()
    market=market/market.sum()
    q['tilt']=q.weight/q.CUSIP.map(market).clip(lower=1e-10)
    # Top held (by participating fund count) includes sufficiently diverse equity vocab.
    vc=q.groupby('CUSIP').CIK.nunique().sort_values(ascending=False)
    vocab=set(vc.head(top_vocab).index)
    q=q[q.CUSIP.isin(vocab)]
    sentences=[]
    for _,g in q.groupby('CIK'):
        tokens=g.sort_values('tilt',ascending=False).CUSIP.tolist()[:max_holdings]
        if len(tokens)>=min_holdings:sentences.append(tokens)
    return sentences,vc.to_dict(),q


def train_quarter(df,period,cutoff,seed=42,top_vocab=3000):
    # 13F information is accessible no earlier than individual filing date AND
    # 45 calendar days after report-end, conservative classroom timing.
    use=df[df.filing_date.le(cutoff) & df.period.eq(period)]
    sentences,freq,_=build_sentences(use,period,top_vocab=top_vocab)
    if len(sentences)<MIN_SENTENCES:
        raise ValueError(f'{period.date()}: only {len(sentences)} institutional portfolios; minimum {MIN_SENTENCES}. Need more SEC ZIPs.')
    model=Word2Vec(sentences=sentences,vector_size=48,window=12,min_count=3,sg=1,
                   workers=1,epochs=12,negative=6,seed=seed)
    if not np.isfinite(model.wv.vectors).all():
        raise ValueError(f"{period.date()}: non-finite Word2Vec embeddings; stop before backtest")
    return model, len(sentences), freq


def candidates_from_model(model,tickers,neighbors=20):
    """Require mutual top-K embedding neighbors; no fallback pairs or synthetic links."""
    cusips={t:CUSIP_MAP[t] for t in tickers}
    missing=[t for t,c in cusips.items() if c not in model.wv]
    if missing:print('WARNING CUSIP not embedded:',missing)
    mutual=[]; similarities=[]
    for a,b in combinations(tickers,2):
        ca,cb=cusips[a],cusips[b]
        if ca not in model.wv or cb not in model.wv:continue
        na={key for key,_ in model.wv.most_similar(ca,topn=min(neighbors,len(model.wv)-1))}
        nb={key for key,_ in model.wv.most_similar(cb,topn=min(neighbors,len(model.wv)-1))}
        sim=float(model.wv.similarity(ca,cb))
        similarities.append((a,b,sim,ca,cb in na and ca in nb))
        if cb in na and ca in nb:mutual.append((a,b))
    return mutual,pd.DataFrame(similarities,columns=['ticker_a','ticker_b','cosine','CUSIP_a','mutual_topk']) if similarities else pd.DataFrame()


def load_prices(path:Path):
    x=pd.read_csv(path)
    datecol='date' if 'date' in x.columns else x.columns[0]
    x[datecol]=pd.to_datetime(x[datecol]);x=x.set_index(datecol).sort_index()
    if not set(CUSIP_MAP).issubset(x.columns):raise ValueError('Price CSV missing required AMAT/LRCX/KLAC/TER columns')
    p=x[list(CUSIP_MAP)].apply(pd.to_numeric,errors='coerce').dropna()
    if (p<=0).any().any():raise ValueError('Invalid nonpositive adjusted prices')
    return p


def signals_backtest(prices,relationships,window=60,min_depth=1,cons_min=.5,cost_bps=5):
    """Quarter-specific selected pair list. Prior-close decision, next-bar return.
    Pair votes +z,-z; score unweighted (same downstream as teaching Strategy 2).
    This is NOT full Quantitativo Sharpe-weighted / 25x25 methodology.
    """
    ix=prices.index; tickers=list(prices.columns); logs=np.log(prices)
    votes={t:[] for t in tickers}
    pairrows=[]
    for q, pairs in relationships:
        start=pd.Timestamp(q['start']);end=pd.Timestamp(q['end'])
        mask=(ix>=start)&(ix<=end)
        for a,b in pairs:
            s=logs[a]-logs[b]
            # rolling stats only prior closes (shift), decision at close t
            mean=s.rolling(window,min_periods=window).mean().shift(1)
            sd=s.rolling(window,min_periods=window).std().shift(1)
            z=((s-mean)/sd.replace(0,np.nan)).clip(-6,6).where(mask)
            votes[a].append(z.rename(f'{a}_{b}'))
            votes[b].append((-z).rename(f'{b}_{a}'))
            pairrows.append({'period':q['period'],'active_from':str(start.date()),'pair':a+'/'+b})
    agg=pd.DataFrame(index=ix,columns=tickers,dtype=float)
    depth=pd.DataFrame(0,index=ix,columns=tickers,dtype=int)
    cons=pd.DataFrame(0.,index=ix,columns=tickers)
    for t in tickers:
        if not votes[t]:continue
        vv=pd.concat(votes[t],axis=1)
        agg[t]=vv.mean(axis=1)
        depth[t]=vv.notna().sum(axis=1)
        cons[t]=vv.apply(np.sign).sum(axis=1).abs()/depth[t].replace(0,np.nan)
    eligibility=(depth>=min_depth) & (cons>cons_min)
    eligible=agg.where(eligibility)
    target=pd.DataFrame(0.,index=ix,columns=tickers)
    for d in ix:
        row=eligible.loc[d].dropna()
        if len(row)<2:continue
        long_name=row.idxmin();short_name=row.idxmax()
        if row.loc[long_name]>=0 or row.loc[short_name]<=0:continue
        target.loc[d,long_name]=+.5
        target.loc[d,short_name]=-.5
    daily_stock=prices.pct_change().fillna(0)
    held=target.shift(1).fillna(0)
    gross=(held*daily_stock).sum(axis=1)
    turnover=target.diff().abs().sum(axis=1).fillna(0)
    turnover.iloc[0]=target.iloc[0].abs().sum()
    net=gross-turnover*cost_bps/10000
    # If signals at last close have no execution next day, associated cost is not yet realized.
    net.iloc[-1]=gross.iloc[-1]
    return pd.DataFrame({'gross_return':gross,'net_return':net,'turnover':turnover},index=ix),agg,depth,cons,target,pd.DataFrame(pairrows)


def report(out,prices,bt,agg,depth,cons,target,selected,coverage,quarter_log,sec_mode):
    out.mkdir(parents=True,exist_ok=True)
    bt.to_csv(out/'daily_backtest.csv',index_label='date')
    agg.to_csv(out/'aggregate_signals.csv',index_label='date')
    depth.to_csv(out/'depth.csv',index_label='date')
    cons.to_csv(out/'consistency.csv',index_label='date')
    target.to_csv(out/'target_weights.csv',index_label='date')
    selected.to_csv(out/'selected_pairs.csv',index=False)
    coverage.to_csv(out/'sec_zip_coverage.csv',index=False)
    pd.DataFrame(quarter_log).to_csv(out/'quarter_model_audit.csv',index=False)
    r=bt.net_return; equity=(1+r).cumprod(); dd=equity/equity.cummax()-1
    benchmark=(prices/prices.iloc[0]).mean(axis=1)
    benchmark=benchmark/benchmark.iloc[0]
    compare={'Strategy 3 (net)':r,'Equal-weight benchmark':benchmark.pct_change().fillna(0)}
    for label,sub in [('Strategy 1 (net)','popquants_strategy1'),('Strategy 2 (net)','popquants_strategy2')]:
        p=out.parent/sub/'daily_backtest.csv'
        if p.exists():
            raw=pd.read_csv(p)
            dcol='date' if 'date' in raw else raw.columns[0]
            raw[dcol]=pd.to_datetime(raw[dcol]);raw=raw.set_index(dcol)
            common=raw.index.intersection(r.index)
            if len(common)>=30 and 'net_return' in raw:
                # Mark pre-overlap as NaN, so chart does not invent performance before eligibility.
                compare[label]=pd.to_numeric(raw.net_return,errors='coerce').reindex(r.index)
    f=go.Figure()
    f.add_trace(go.Scatter(x=equity.index,y=equity,name='Strategy 3 net',line=dict(color='#5CD6C2',width=2)))
    f.add_trace(go.Scatter(x=benchmark.index,y=benchmark,name='Equal-weight buy & hold',line=dict(color='#E9BA64',width=2)))
    f.update_layout(title='Equity: SEC 13F Word2Vec pairs vs Equal-weight benchmark',template='plotly_dark',height=390)
    for label in ('Strategy 1 (net)','Strategy 2 (net)'):
        if label in compare:
            z=compare[label].dropna()
            f.add_trace(go.Scatter(x=z.index,y=(1+z).cumprod(),name=label, line=dict(width=1.6,dash='dot')))
    ddfig=go.Figure(go.Scatter(x=dd.index,y=dd*100,name='Strategy 3 drawdown',fill='tozeroy'))
    ddfig.update_layout(title='Drawdown (%)',template='plotly_dark',height=330)
    m=(1+r).resample('ME').prod()-1
    ys=sorted(m.index.year.unique());heat=np.full((len(ys),12),np.nan)
    for d,v in m.items():heat[ys.index(d.year),d.month-1]=100*v
    hm=go.Figure(go.Heatmap(x=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'],y=ys,z=heat,colorscale='RdYlGn',zmid=0,text=np.where(np.isfinite(heat),np.round(heat,1).astype(str),' '),texttemplate='%{text}'))
    hm.update_layout(title='Monthly net returns (%)',template='plotly_dark',height=320)
    sharpe=np.sqrt(252)*r.mean()/r.std(ddof=1) if r.std(ddof=1)>0 else np.nan
    stat={'total_return_pct':float((equity.iloc[-1]-1)*100),'annualized_vol_pct':float(r.std(ddof=1)*np.sqrt(252)*100),
          'sharpe_rf0':float(sharpe),'max_drawdown_pct':float(dd.min()*100),'days':len(r),'SEC_real_13F':True,
          'word2vec':True,'sample_funds_source':'SEC 13F filings', 'mode':sec_mode}
    (out/'metrics.json').write_text(json.dumps(stat,indent=2),encoding='utf-8')
    plot_config={'displaylogo':False,'responsive':True}
    sections=''.join(g.to_html(full_html=False,include_plotlyjs=False,config=plot_config) for g in (f,ddfig,hm))
    audit=pd.DataFrame(quarter_log).to_html(index=False,escape=True)
    cmp=[]
    for label,rr in compare.items():
        rr=rr.dropna()
        if len(rr)<30:continue
        vv=rr.std(ddof=1)*np.sqrt(252)
        eqr=(1+rr).cumprod()
        cmp.append({'strategy':label,'observations':len(rr),'total_return_pct':round(100*(eqr.iloc[-1]-1),2),
                    'annual_vol_pct':round(100*vv,2),
                    'sharpe_rf0':round(np.sqrt(252)*rr.mean()/rr.std(ddof=1),3) if rr.std(ddof=1)>0 else np.nan})
    cmptable=pd.DataFrame(cmp).to_html(index=False,escape=True)
    pd.DataFrame(cmp).to_csv(out/'performance_comparison.csv',index=False)
    page=f'''<!doctype html><html><head><meta charset="utf-8"><title>PopQuants Strategy 3 (Real SEC 13F)</title>
<style>body{{background:#071416;color:#E1EEE8;font:15px system-ui;margin:3vw;}}.notice{{background:#193234;padding:16px;border-left:3px solid #E7B75B}}table{{border-collapse:collapse}}th,td{{padding:8px;border-bottom:1px solid #456}}</style>
<script>{get_plotlyjs()}</script></head><body><h1>Strategy 3 — Real SEC 13F / Word2Vec</h1>
<div class="notice">REAL SEC 13F FILES. NO SYNTHETIC HOLDINGS. Fund top-160 truncation is a resource-saving approximation. Not a full replication of the published study; four-stock trading universe. Delayed 13F, modeled execution and costs; short-borrow/financing excluded.</div>
<h3>Backtest metrics</h3><pre>{json.dumps(stat,indent=2)}</pre><h3>Comparison (note differing eligible windows)</h3>{cmptable}{sections}<h3>Quarter audit (train date, candidates)</h3>{audit}</body></html>'''
    (out/'interactive_tearsheet.html').write_text(page,encoding='utf-8')
    return stat,out/'interactive_tearsheet.html'




def write_education_tearsheet(out,prices,bt,agg,depth,cons,tgt,stats):
    """Additional teaching dashboard. NEVER changes saved backtest values."""
    from plotly.subplots import make_subplots
    from plotly.offline import get_plotlyjs
    import plotly.graph_objects as go
    c={'strategy':'#6CE3C7','bench':'#F2C56C','risk':'#F38B97','text':'#DCEAE4'}
    r=bt.net_return.astype(float)
    bench=(prices/prices.iloc[0]).mean(axis=1).pct_change().fillna(0)
    # compare on intersection only, always annualize from daily returns
    series={'Strategy 3 (net)':r,'Equal-weight buy & hold':bench}
    for label,sub in [('Strategy 1 (net)','popquants_strategy1'),('Strategy 2 (net)','popquants_strategy2')]:
        f=out.parent/sub/'daily_backtest.csv'
        if not f.exists():continue
        raw=pd.read_csv(f)
        dc='date' if 'date' in raw.columns else raw.columns[0]
        raw[dc]=pd.to_datetime(raw[dc]);raw=raw.set_index(dc).sort_index()
        if 'net_return' in raw:series[label]=pd.to_numeric(raw['net_return'],errors='coerce').reindex(r.index)
    names=list(series)
    common=r.index
    for ss in series.values():common=common.intersection(ss.dropna().index)
    if len(common)<30:common=r.index
    def m(rr):
        rr=rr.loc[common].astype(float).dropna();eq=(1+rr).cumprod()
        st=rr.std(ddof=1);vol=float(st*np.sqrt(252)*100)
        return {'CAGR':float((eq.iloc[-1]**(252/len(rr))-1)*100) if eq.iloc[-1]>0 else float('nan'),
                'Total return':float((eq.iloc[-1]-1)*100),
                'Annual vol':vol,'Sharpe':float(rr.mean()/st*np.sqrt(252)) if st>0 else float('nan'),
                'Max DD':float((eq/eq.cummax()-1).min()*100)}
    metrics_table={name:m(ss) for name,ss in series.items()}
    mr=go.Figure()
    colors=['#6CE3C7','#F2C56C','#86AEDE','#D49AE9']
    for i,(name,mm) in enumerate(metrics_table.items()):
        mr.add_trace(go.Scatter(x=[mm['Annual vol']],y=[mm['CAGR']],text=[name],mode='markers+text',
             textposition='top center',name=name,marker=dict(size=14,color=colors[i]),
             customdata=[[mm['Sharpe'],mm['Max DD']]],
             hovertemplate='%{text}<br>Annual std: %{x:.2f}%<br>Annual net return (CAGR): %{y:.2f}%<br>Sharpe: %{customdata[0]:.2f}<br>Max DD: %{customdata[1]:.2f}%<extra></extra>'))
    mr.update_layout(title='Risk vs return — same common dates',xaxis_title='Annualized standard deviation (%)',yaxis_title='Annual net return / CAGR (%)')
    months=(1+r).resample('ME').prod()-1
    ys=sorted(set(months.index.year));mat=np.full((len(ys),12),np.nan)
    for d,v in months.items():mat[ys.index(d.year),d.month-1]=100*v
    hm=go.Figure(go.Heatmap(x=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'],y=ys,
                           z=mat,zmid=0,colorscale='RdYlGn',text=[[f'{z:+.1f}%' if np.isfinite(z) else '' for z in row] for row in mat],texttemplate='%{text}'))
    hm.update_layout(title='Strategy 3 monthly net returns (%)')
    eq=go.Figure()
    for i,(name,rr) in enumerate(series.items()):
        rr=rr.loc[common].astype(float).dropna()
        eq.add_trace(go.Scatter(x=rr.index,y=(1+rr).cumprod(),name=name,line=dict(color=colors[i],width=2)))
    eq.update_layout(title='Common-window cumulative equity (1.0 = initial capital)',yaxis_title='Portfolio NAV')
    dd=(1+r).cumprod();dd=dd/dd.cummax()-1
    ddf=go.Figure(go.Scatter(x=dd.index,y=dd*100,fill='tozeroy',name='Drawdown',line=dict(color='#F38B97')))
    ddf.update_layout(title='Strategy 3 drawdown',yaxis_title='Drawdown (%)')
    event=make_subplots(rows=2,cols=1,shared_xaxes=True,vertical_spacing=.13,
                        subplot_titles=('Aggregate signal by stock','Target weight (decision; held from next trading bar)'))
    for i,ticker in enumerate(tgt.columns):
        event.add_trace(go.Scatter(x=agg.index,y=agg[ticker],name=f'{ticker} signal',
                         line=dict(color=colors[i%4])),row=1,col=1)
        event.add_trace(go.Scatter(x=tgt.index,y=tgt[ticker],name=f'{ticker} target',
                         line=dict(color=colors[i%4],dash='dot')),row=2,col=1)
    event.update_layout(title='Ticker signals and model decisions',height=580)
    ev= pd.DataFrame({'date':bt.index,'gross':bt.gross_return.values,'turnover':bt.turnover.values,'net':bt.net_return.values}).tail(12).copy()
    ev['date']=ev['date'].dt.strftime('%Y-%m-%d')
    for col in ['gross','net']:ev[col]=ev[col].map(lambda x:f'{100*x:+.3f}%')
    ev['turnover']=ev['turnover'].map(lambda x:f'{x:.3f}×')
    embpath=out/'embedding_3d_pca.csv'
    plots=[eq,ddf,mr,hm,event]
    if embpath.exists():
        df=pd.read_csv(embpath)
        if len(df):
            last=sorted(df.period.unique())[-1];df=df[df.period.eq(last)]
            f3=go.Figure()
            labels=[x if isinstance(x,str) and x else '13F CUSIP' for x in df.ticker]
            f3.add_trace(go.Scatter3d(x=df.pc1,y=df.pc2,z=df.pc3,mode='markers+text',text=[lab if lab!='13F CUSIP' else '' for lab in labels],
                marker=dict(size=[9 if q else 3 for q in df.is_target],color=['#F2C56C' if q else '#6CE3C7' for q in df.is_target]),
                customdata=df.cusip,hovertemplate='CUSIP %{customdata}<br>PC1 %{x:.3f}, PC2 %{y:.3f}, PC3 %{z:.3f}<extra></extra>'))
            f3.update_layout(title=f'Real 13F learned embeddings — PCA projection 3D ({last}); projection distances are not cosine scores',height=570)
            plots.insert(3,f3)
    config={'displaylogo':False,'responsive':True}
    for fig in plots:
        fig.update_layout(template='plotly_dark',paper_bgcolor='#102123',plot_bgcolor='#102123',font=dict(color=c['text']),margin=dict(l=52,r=35,t=60,b=50))
    parts=''.join('<section>'+fig.to_html(full_html=False,include_plotlyjs=False,config=config)+'</section>' for fig in plots)
    trows=''.join('<tr><td>'+html.escape(name)+'</td>'+''.join(f'<td>{val:+.2f}</td>' for val in (mm['Total return'],mm['CAGR'],mm['Annual vol'],mm['Sharpe'],mm['Max DD']))+'</tr>' for name,mm in metrics_table.items())
    page="""<!doctype html><html><head><meta charset="utf-8"><title>PopQuants Strategy 3 · Teaching Report</title><style>
    body{background:#071416;color:#DCEAE4;font:15px system-ui,sans-serif;margin:24px auto;max-width:1400px;padding:0 20px}
    section{border:1px solid #284345;border-radius:12px;padding:8px;margin:18px 0;background:#102123}
    h1{font-size:30px}table{width:100%;border-collapse:collapse}td,th{padding:11px;text-align:right;border-bottom:1px solid #284345}td:first-child,th:first-child{text-align:left}
    .note{padding:15px;background:#203438;border-left:4px solid #F2C56C;line-height:1.65}</style><script>__JS__</script></head><body>
    <h1>POPQUANTS · STRATEGY 3 / 2026 RESEARCH REPORT</h1><div class="note">REAL SEC 13F DATA + Word2Vec, only AMAT/LRCX/KLAC/TER in trading universe.
    Results are from a historical prototype, not live trades. IMPORTANT: repeated gensim our_dot_float errors may invalidate vector reliability; this is not validated alpha.
    Point-in-time assumption: available after filing and 45-day reporting lag. Model uses top-160 holdings per fund, unweighted votes, min depth 1, no short-borrow costs. 2026 result from original backtest must be verified independently.</div>
    <h2>Common-window performance comparison</h2><table><thead><tr><th>Strategy</th><th>Total return %</th><th>CAGR %</th><th>Annual std %</th><th>Sharpe</th><th>Max DD %</th></tr></thead><tbody>__ROWS__</tbody></table>
    __PARTS__<h2>Recent strategy 3 daily calculations</h2>__EV__
    <p>Data sources: downloaded SEC 13F ZIP archives and uSMART adjusted daily prices (local CSV). All returns modeled; no orders sent.</p></body></html>"""
    page=page.replace('__JS__',get_plotlyjs()).replace('__ROWS__',trows).replace('__PARTS__',parts).replace('__EV__',ev.to_html(index=False,escape=True))
    fp=out/'interactive_tearsheet_detailed.html';fp.write_text(page,encoding='utf-8')
    print('Detailed teaching report:',fp)


def execute(args):
    root=Path(args.root).expanduser()
    prices=load_prices(Path(args.prices).expanduser() if args.prices else root/'popquants_strategy1/us_stock_adjusted_daily.csv')
    start=pd.Timestamp(args.start)
    end=pd.Timestamp(args.end)
    if end < start: raise ValueError('--end must be on or after --start')
    if end > prices.index.max():
        print(f'NOTE: requested end {end.date()} exceeds final available price {prices.index.max().date()}; clipping to actual data.')
        end=prices.index.max()
    if start > prices.index.max(): raise ValueError('No prices at or after --start')
    # Keep 2025 Q3 to ensure a model is already available on 2026-01-01; do not use future filings.
    earliest_period=pd.Timestamp('2025-09-30') if start >= pd.Timestamp('2026-01-01') else None
    data,coverage=load_sec_zips(Path(args.sec_dir).expanduser() if args.sec_dir else root/'sec_13f_zips',earliest_period=earliest_period)
    # Require meaningful time coverage and a sequence of vintage reports. Conservative
    # point-in-time floor: quarter end +45 calendar days, and initial filings by that day.
    periods=sorted(data.period.dropna().unique())
    selected={};audit=[];latest_embedded=[];embedding_frames=[]
    for idx,q in enumerate(periods):
        q=pd.Timestamp(q)
        effective=q+pd.Timedelta(days=46) # after 45-day deadline
        quarter_end=(pd.Timestamp(periods[idx+1])+pd.Timedelta(days=46)-pd.Timedelta(days=1)) if idx+1<len(periods) else min(prices.index.max(),effective + pd.Timedelta(days=94))
        if quarter_end < start or effective > end: continue
        quarter_data=data[(data.period.eq(q)) & (data.filing_date <= effective)]
        try:
            model,fund_count,freq=train_quarter(quarter_data,q,effective,seed=args.seed,top_vocab=args.vocab)
            pairs,sim=candidates_from_model(model,list(prices.columns),neighbors=args.neighbors)
            # Research-only snapshot: project FULL learned vocabulary to three dimensions
            # via PCA. This does not change pair selection or the backtest.
            try:
                v=np.asarray(model.wv.vectors,dtype=float)
                norms=np.linalg.norm(v,axis=1)
                valid=(norms>1e-12)&np.isfinite(norms)
                if valid.all() and len(v)>=4:
                    unit=v/norms[:,None]
                    center=unit-unit.mean(axis=0)
                    # PCA via SVD, performed on all words; score is a projection.
                    _,_,vh=np.linalg.svd(center,full_matrices=False)
                    xyz=center@vh[:3].T
                    cusip_names={cusip:t for t,cusip in CUSIP_MAP.items()}
                    # Store targets plus nearest neighbors, not arbitrary sample positions.
                    keep=set(cusip_names)&set(model.wv.key_to_index)
                    for cus in list(keep):
                        keep.update(w for w,_ in model.wv.most_similar(cus,topn=12))
                    for cus in keep:
                        j=model.wv.key_to_index[cus]
                        embedding_frames.append({'period':str(q.date()),'cusip':cus,
                              'ticker':cusip_names.get(cus,''),
                              'pc1':float(xyz[j,0]),'pc2':float(xyz[j,1]),'pc3':float(xyz[j,2]),
                              'fund_count':fund_count,'is_target':bool(cus in cusip_names)})
            except (ValueError,np.linalg.LinAlgError) as ex:
                print('WARNING: PCA visualization skipped:',str(ex))
        except ValueError as e:
            audit.append({'period':str(q.date()),'status':'SKIPPED','reason':str(e)});continue
        if args.verbose:print(q.date(),'Funds:',fund_count,'pairs:',pairs,flush=True)
        audit.append({'period':str(q.date()),'effective':str(effective.date()),'fund_count':fund_count,
                      'candidate_pairs':len(pairs),'vocabulary':len(model.wv),'status':'OK' if pairs else 'NO_MUTUAL_PAIRS'})
        selected[(str(q.date()),str(effective.date()),str(quarter_end.date()))]=pairs
    active=[({'period':p,'start':s,'end':e},pairs) for (p,s,e),pairs in selected.items() if pairs]
    if not active:
        print(pd.DataFrame(audit).to_string(index=False))
        raise RuntimeError('No admissible real-13F mutual Word2Vec pairs; refusing to fabricate results. Expand SEC history or trading universe.')
    # Support missing quarters by explicitly leaving those days flat (not carrying a stale list).
    daily,agg,depth,cons,tgt,pairdf=signals_backtest(prices,active,window=args.window,min_depth=args.min_depth,
                                                    cons_min=args.consistency,cost_bps=args.cost_bps)
    # Do not report pre-first-model warm-up as tested returns.
    first=min(pd.Timestamp(q['start']) for q,_ in active)
    mask=(daily.index>=max(first,start)) & (daily.index<=end)
    if mask.sum()<60:raise RuntimeError('Fewer than 60 test bars after first valid SEC embedding model; no robust tear sheet.')
    prices,daily,agg,depth,cons,tgt=[x.loc[mask] for x in (prices,daily,agg,depth,cons,tgt)]
    outdir=root/'outputs'/'strategy3'
    stats,output=report(outdir,prices,daily,agg,depth,cons,tgt,pairdf,coverage,audit,'SEC_13F_PIT_WORD2VEC_2026')
    pd.DataFrame(embedding_frames).to_csv(outdir/'embedding_3d_pca.csv',index=False)
    write_education_tearsheet(outdir,prices,daily,agg,depth,cons,tgt,stats)

    print('SUCCESS: 2026-only REAL SEC 13F + Word2Vec. NO synthetic data, NO orders.')
    print('Test window:',str(prices.index.min().date()),'through',str(prices.index.max().date()))
    print('Results:',json.dumps(stats,indent=2));print('HTML:',output)
    print('RESEARCH WARNING: Repeated Gensim our_dot_float exceptions can affect numerical reliability; finite-vector checks do not prove training correctness.')
    if not args.no_browser:webbrowser.open(output.resolve().as_uri())


def main():
    p=argparse.ArgumentParser(description='Real SEC 13F Word2Vec strategy 3 (no synthetic fallback)')
    p.add_argument('--root',default=str(DEFAULT_ROOT));p.add_argument('--sec-dir');p.add_argument('--prices')
    p.add_argument('--start',default='2026-01-01',help='First test date (inclusive)')
    p.add_argument('--end',default='2026-11-20',help='Last test date (inclusive); clips to last CSV date')
    p.add_argument('--window',type=int,default=60);p.add_argument('--vocab',type=int,default=3000)
    p.add_argument('--neighbors',type=int,default=20);p.add_argument('--min-depth',type=int,default=1)
    p.add_argument('--consistency',type=float,default=.5);p.add_argument('--cost-bps',type=float,default=5)
    p.add_argument('--seed',type=int,default=42);p.add_argument('--no-browser',action='store_true');p.add_argument('--verbose',action='store_true')
    args=p.parse_args()
    if not 0<=args.consistency<=1:raise ValueError('consistency must be 0..1')
    execute(args)

if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,FileNotFoundError,KeyError,zipfile.BadZipFile) as exc:
        print('STOP (no fabricated backtest):',exc,file=sys.stderr);sys.exit(1)