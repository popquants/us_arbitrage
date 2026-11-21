#!/usr/bin/env python3
"""PopQuants Strategy 1: educational U.S.-stock pairs trading tear sheet.

Uses the previously user-verified uSMART V8 login and the uSMART quote-signing
function from usmart_nvda_30d.py. READ ONLY: Login + K-line calls, no orders.

Universe: AMAT, LRCX, KLAC, TER. Pre-registered demonstration pair: AMAT/LRCX.
Signal: rolling log-price spread z-score; no in-sample pair mining.
Execution: close-t signal, t+1 close-to-close PnL, next-bar position (one-bar lag).
Cost: 5bp * one-way dollar turnover, including entry, exit, reversal.
Daily book: +50% / -50% when invested (100% gross, dollar-neutral).
No short-borrow cost, dividends or financing modeled. Research ONLY.
"""
from __future__ import annotations
import argparse, base64, getpass, json, math, re, sys, time
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

try:
    import numpy as np
    import pandas as pd
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import TwoSlopeNorm
    import requests
    from cryptography.hazmat.primitives.asymmetric import padding
except ImportError as exc:
    raise SystemExit('Missing package. Run: python3 -m pip install requests cryptography pandas numpy matplotlib') from exc

UNIVERSE = ('AMAT', 'LRCX', 'KLAC', 'TER')
PAIR = ('AMAT', 'LRCX')
ROOT = Path.home() / 'Documents' / 'usmart'
DEFAULT_OUT = ROOT / 'popquants_strategy1'


def login_and_get_prices(days: int, page_size: int, max_pages: int, folder: Path) -> pd.DataFrame:
    # Import working scripts from the same folder, NOT from third-party packages.
    try:
        from usmart_v8_asset_diagnostic import load_key, signed_post, LOGIN
        from usmart_nvda_30d import quote_post, as_date
    except ImportError as ex:
        raise RuntimeError('Place this file beside usmart_v8_asset_diagnostic.py and usmart_nvda_30d.py') from ex

    api_code = (folder / 'api_code').read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{3,32}', api_code):
        raise RuntimeError('Invalid API Code format')
    pub = load_key(folder / 'public_key', private=False)
    prv = load_key(folder / 'private_key', private=True)
    area = input('Country calling code (e.g. 66): ').strip().lstrip('+')
    phone = input('Registered uSMART login phone: ').strip().replace(' ', '')
    if not re.fullmatch(r'\d{1,4}', area) or not re.fullmatch(r'\d{6,16}', phone):
        raise RuntimeError('Invalid area code / phone format')
    password = getpass.getpass('uSMART LOGIN password (hidden, NOT trading PIN): ')
    if not password:
        raise RuntimeError('Empty password')
    enc = lambda txt: base64.urlsafe_b64encode(pub.encrypt(txt.encode(), padding.PKCS1v15())).decode('ascii')
    payload = {'phoneNumber': enc(phone), 'password': enc(password), 'areaCode': area}
    del password
    end = datetime.now(ZoneInfo('America/New_York')).date()
    start = end - timedelta(days=days - 1)
    out = {}
    with requests.Session() as session:
        print('Logging in using VERIFIED V8 flow (token never printed/saved) ...')
        auth = signed_post(session, LOGIN, payload, api_code, prv, x_type='12')
        if auth.get('code') != 0:
            raise RuntimeError('Login rejected; error code: ' + str(auth.get('code')))
        token = (auth.get('data') or {}).get('token')
        if not token:
            raise RuntimeError('Login returned no token')
        print('Login OK. Downloading adjusted daily K-lines (right=1).')
        for ticker in UNIVERSE:
            seen, cursor = {}, 0
            for page in range(max_pages):
                args = {'secuId': 'us' + ticker, 'type': 7, 'start': cursor,
                        'count': page_size, 'right': 1}
                resp = quote_post(session, args, api_code, prv, token)
                if resp.get('code') != 0:
                    raise RuntimeError(f'{ticker} quote error code: {resp.get("code")}')
                data = resp.get('data') or {}
                records = data.get('list', []) if isinstance(data, dict) else []
                if not isinstance(records, list) or not records:
                    break
                oldest = None
                added = 0
                for record in records:
                    if not isinstance(record, dict):
                        continue
                    dt = as_date(record.get('latestTime'))
                    if dt is None:
                        continue
                    stamp = record.get('latestTime')
                    try:
                        stamp_int = int(stamp)
                    except (ValueError, TypeError):
                        continue
                    oldest = min(oldest, stamp_int) if oldest is not None else stamp_int
                    if start <= dt <= end:
                        try:
                            close = float(record['close'])
                        except (ValueError, KeyError, TypeError):
                            continue
                        if math.isfinite(close) and close > 0 and dt not in seen:
                            seen[dt] = close
                            added += 1
                if oldest is None or oldest == cursor:
                    break
                if min(seen, default=end) <= start or (len(records) < page_size and page > 0):
                    break
                # K-line documentation says subsequent `start` is timestamp of the oldest candle.
                # Abort if server ignores pagination rather than silently duplicating rows.
                if page > 0 and added == 0:
                    print(f'  {ticker}: pagination stopped; no additional observations')
                    break
                cursor = oldest
                time.sleep(0.20)
            out[ticker] = pd.Series(seen, name=ticker, dtype=float)
            print(f'  {ticker}: {len(seen)} rows, pages <= {max_pages}')
    data = pd.DataFrame(out).sort_index()
    data.index = pd.to_datetime(data.index)
    data.index.name = 'date'
    return data


def load_local_prices(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    if 'date' not in df.columns:
        raise ValueError('CSV must contain date,AMAT,LRCX,KLAC,TER columns')
    missing = set(UNIVERSE) - set(df.columns)
    if missing:
        raise ValueError('Missing columns: ' + ', '.join(sorted(missing)))
    df['date'] = pd.to_datetime(df['date'])
    return df.set_index('date')[list(UNIVERSE)].sort_index()


def backtest(prices: pd.DataFrame, window: int, entry: float, exit_z: float, cost_bps: float):
    prices = prices.loc[:, UNIVERSE].apply(pd.to_numeric, errors='coerce')
    prices = prices.replace([np.inf, -np.inf], np.nan).dropna(how='any')
    prices = prices[~prices.index.duplicated(keep='last')].sort_index()
    if len(prices) < max(180, window + 75):
        raise RuntimeError(f'Insufficient aligned daily data: {len(prices)} rows. Need >= {max(180, window+75)}. Fetch more history or supply a prepared adjusted-price CSV.')
    if (prices <= 0).any().any():
        raise ValueError('Non-positive adjusted prices detected')
    # Quality check for split/corporate-action artifacts (not an infallible check).
    jumps = prices.pct_change().abs().max()
    if (jumps > .45).any():
        print('WARNING: unusually large price jumps; validate adjustment and corporate actions:',
              jumps[jumps > .45].round(3).to_dict())

    a, b = PAIR
    spread = np.log(prices[a]) - np.log(prices[b])
    # Use historical observations ending at t to calculate a signal at close t.
    roll_mean = spread.rolling(window, min_periods=window).mean()
    roll_std = spread.rolling(window, min_periods=window).std(ddof=1)
    z = (spread - roll_mean) / roll_std.replace(0, np.nan)
    target = pd.Series(0.0, index=prices.index)
    state = 0.0
    events = []
    for dt, x in z.items():
        previous = state
        if pd.isna(x):
            state = 0.0
        elif state == 0:
            if x <= -entry:
                state = 1.0  # long AMAT, short LRCX
            elif x >= entry:
                state = -1.0 # short AMAT, long LRCX
        else:
            if abs(x) <= exit_z:
                state = 0.0
            elif state == 1 and x >= entry:
                state = -1.0
            elif state == -1 and x <= -entry:
                state = 1.0
        target.loc[dt] = state
        if state != previous:
            events.append({'signal_date': dt.date().isoformat(), 'z_score': round(float(x), 4) if pd.notna(x) else None,
                           'old_position': previous, 'new_position': state,
                           'action': 'ENTER LONG AMAT / SHORT LRCX' if state == 1 else 'ENTER SHORT AMAT / LONG LRCX' if state == -1 else 'EXIT'})

    # Daily t return is generated by target decided after close t-1.
    live_position = target.shift(1).fillna(0.0)
    r = prices[[a, b]].pct_change().fillna(0.0)
    gross_ret = live_position * 0.5 * (r[a] - r[b])
    # Position turnover: 0->+1 means buy 50% A and short 50% B = 100% gross turnover.
    turnover = live_position.diff().abs().fillna(live_position.abs())
    net_ret = gross_ret - turnover * cost_bps / 10000.0
    # Exclude warm-up before first legal z score.
    valid_index = z.first_valid_index()
    if valid_index is None:
        raise RuntimeError('No valid z-score; adjust window or dataset')
    start_idx = prices.index.get_loc(valid_index)
    if start_idx >= len(prices) - 60:
        raise RuntimeError('Fewer than 60 observations after first signal')
    px = prices.iloc[start_idx:].copy()
    series = pd.DataFrame({'z':z,'signal_position':target,'executed_position':live_position,
                           'gross_return':gross_ret,'net_return':net_ret,'turnover':turnover},index=prices.index).iloc[start_idx:]
    series['equity'] = (1 + series.net_return).cumprod()
    series['gross_equity'] = (1 + series.gross_return).cumprod()
    series['drawdown'] = series.equity / series.equity.cummax() - 1
    series['spread'] = spread.loc[series.index]
    # Signal dates before backtest period are not reported as trades.
    events = [v for v in events if v['signal_date'] >= str(series.index[0].date())]
    returns = series.net_return
    n = len(returns)
    years = n / 252
    vol = float(returns.std(ddof=1))
    downside = float(returns[returns < 0].std(ddof=1))
    annual = float(series.equity.iloc[-1] ** (1 / years) - 1) if years > 0 and series.equity.iloc[-1] > 0 else float('nan')
    metrics = {
        'universe': list(UNIVERSE), 'pair': f'{a}/{b}', 'trading_days': n,
        'start': series.index[0].date().isoformat(), 'end': series.index[-1].date().isoformat(),
        'total_return': float(series.equity.iloc[-1] - 1), 'cagr': annual,
        'annualized_volatility': vol * math.sqrt(252),
        'sharpe_0rf': (float(returns.mean()) / vol * math.sqrt(252)) if vol > 0 else None,
        'sortino_0rf': (float(returns.mean()) / downside * math.sqrt(252)) if downside > 0 else None,
        'max_drawdown': float(series.drawdown.min()),
        'calmar': annual / abs(float(series.drawdown.min())) if series.drawdown.min() < 0 else None,
        'daily_win_rate': float((returns > 0).mean()),
        'active_days_pct': float((series.executed_position != 0).mean()),
        'one_way_turnover_sum': float(series.turnover.sum()),
        'cost_bps_per_one_way_turnover': cost_bps,
        'number_of_signal_events': len(events), 'entry_z': entry, 'exit_z': exit_z,
        'rolling_window': window, 'lookahead_guard': 'signal_t executed from t+1',
        'notes': 'Research backtest, NOT executable PnL. Forward-adjusted broker price (right=1); no borrow fees, dividends, financing or true fill modeling.'
    }
    return px, series, pd.DataFrame(events), metrics


def build_report(prices, bt, trades, metrics, outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True)
    prices.to_csv(outdir / 'us_stock_adjusted_daily.csv', float_format='%.8f')
    bt.to_csv(outdir / 'daily_backtest.csv', float_format='%.10f')
    trades.to_csv(outdir / 'trade_signals.csv', index=False)
    (outdir / 'metrics.json').write_text(json.dumps(metrics, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    a,b = PAIR
    fig = plt.figure(figsize=(18, 13), constrained_layout=True)
    grid = fig.add_gridspec(3, 2)
    ax1 = fig.add_subplot(grid[0, :])
    ax2 = fig.add_subplot(grid[1, 0]); ax3 = fig.add_subplot(grid[1, 1])
    ax4 = fig.add_subplot(grid[2, 0]); ax5 = fig.add_subplot(grid[2, 1])
    fig.suptitle(f'PopQuants | Strategy 1: {a}/{b} Pairs Trading | Research Tear Sheet',fontsize=19, fontweight='bold')
    ax1.plot(bt.index, bt.equity, label='Net equity (5 bp default)', linewidth=1.8)
    ax1.plot(bt.index, bt.gross_equity, label='Gross equity', linewidth=1.0, alpha=.7)
    ax1.axhline(1, color='gray', linewidth=.8)
    ax1.set_title('Equity Curves | close-signal / next-bar execution'); ax1.legend();ax1.grid(alpha=.25)
    ax2.fill_between(bt.index, bt.drawdown * 100, 0, alpha=.55)
    ax2.set_title('Underwater / Drawdown (%)');ax2.grid(alpha=.25)
    ax3.plot(bt.index, bt.z, label='Spread Z-score')
    ax3.axhline(metrics['entry_z'], linestyle='--', linewidth=1, color='firebrick')
    ax3.axhline(-metrics['entry_z'], linestyle='--', linewidth=1, color='teal')
    ax3.axhline(0, color='gray', linewidth=.7)
    ax3.set_title('Trade Signal (Z-score)');ax3.grid(alpha=.25)
    for action,marker,color in [('ENTER LONG AMAT / SHORT LRCX','^','teal'),('ENTER SHORT AMAT / LONG LRCX','v','firebrick'),('EXIT','x','gray')]:
        rows=trades.loc[trades.action==action] if not trades.empty else pd.DataFrame()
        if not rows.empty:
            xs=pd.to_datetime(rows.signal_date)
            ys=bt.z.reindex(xs)
            ax3.scatter(xs,ys,marker=marker,s=40,color=color,label=action, zorder=5)
    ax3.legend(fontsize=7,loc='best')
    rolls = bt.net_return.rolling(63)
    rolling_sharpe = rolls.mean() / rolls.std(ddof=1).replace(0,np.nan)*np.sqrt(252)
    ax4.plot(bt.index,rolling_sharpe)
    ax4.axhline(0,color='gray',linewidth=.8)
    ax4.set_title('Rolling 63D Sharpe (rf=0)');ax4.grid(alpha=.25)
    ax5.hist(bt.net_return*100,bins=45,alpha=.8)
    ax5.axvline(0,color='gray',linewidth=.8)
    ax5.set_title('Daily Net Return Distribution (%)');ax5.grid(alpha=.25)
    fig.text(.01,.005,'IMPORTANT: Historical research only. No dividends, short borrow, financing, or actual execution modeled. No guarantee of return.',fontsize=9)
    fig.savefig(outdir/'tearsheet.png',dpi=170,bbox_inches='tight')
    plt.close(fig)

    month_ret=(1+bt.net_return).resample('ME').prod()-1
    years=sorted(month_ret.index.year.unique())
    hm=np.full((len(years),12),np.nan)
    for date,val in month_ret.items():hm[years.index(date.year),date.month-1]=val*100
    fig,ax=plt.subplots(figsize=(14,max(3,len(years)*.7+2)))
    max_abs=max(2.0,float(np.nanmax(np.abs(hm)))) if np.isfinite(hm).any() else 2
    im=ax.imshow(hm,aspect='auto',cmap='RdYlGn',norm=TwoSlopeNorm(vmin=-max_abs,vcenter=0,vmax=max_abs))
    ax.set_xticks(range(12),['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'])
    ax.set_yticks(range(len(years)),[str(x) for x in years])
    for i in range(len(years)):
        for j in range(12):
            if np.isfinite(hm[i,j]): ax.text(j,i,f'{hm[i,j]:+.1f}%',ha='center',va='center',fontsize=9)
    ax.set_title('Monthly Net Return Heatmap (partial first/last months included)')
    fig.colorbar(im,ax=ax,label='Monthly return (%)',shrink=.8)
    fig.savefig(outdir/'monthly_heatmap.png',dpi=180,bbox_inches='tight');plt.close(fig)
    fig,ax=plt.subplots(figsize=(14,5))
    ax.plot(prices.index,prices[a]/prices[a].iloc[0],label=a)
    ax.plot(prices.index,prices[b]/prices[b].iloc[0],label=b)
    ax.set_title('Relative Adjusted Price Index (start=1)');ax.grid(alpha=.25);ax.legend()
    fig.savefig(outdir/'pair_prices.png',dpi=180,bbox_inches='tight');plt.close(fig)
    print('\n=== POPQUANTS | STRATEGY 1 RESULTS ===')
    for key in ['start','end','trading_days','total_return','cagr','annualized_volatility','sharpe_0rf','sortino_0rf','max_drawdown','calmar','daily_win_rate','active_days_pct','number_of_signal_events']:
        value=metrics[key]
        if isinstance(value,float):
            value=f'{100*value:.2f}%' if key in ('total_return','cagr','annualized_volatility','max_drawdown','daily_win_rate','active_days_pct') else f'{value:.3f}'
        print(f'{key:27} {value}')
    print('Output:',outdir)
    print('Files: tearsheet.png, monthly_heatmap.png, pair_prices.png, metrics.json, daily_backtest.csv, trade_signals.csv, us_stock_adjusted_daily.csv')
    print('Warning: Signals use historical daily closes and next-day execution proxy; no real short/borrow costs modeled.')


def main():
    p=argparse.ArgumentParser(description='uSMART read-only Pairs Trading PyFolio-like tear sheet (AMAT/LRCX).')
    p.add_argument('--days',type=int,default=1095,help='Calendar days requested (default 1095 ~ 3 years)')
    p.add_argument('--page-size',type=int,default=200)
    p.add_argument('--max-pages',type=int,default=9)
    p.add_argument('--window',type=int,default=60)
    p.add_argument('--entry',type=float,default=2.0)
    p.add_argument('--exit',dest='exit_z',type=float,default=.5)
    p.add_argument('--cost-bps',type=float,default=5)
    p.add_argument('--folder',type=Path,default=ROOT)
    p.add_argument('--outdir',type=Path,default=DEFAULT_OUT)
    p.add_argument('--csv',type=Path,help='Offline aligned adjusted prices CSV with date,AMAT,LRCX,KLAC,TER')
    args=p.parse_args()
    if not (180<=args.days<=3650 and 1<=args.page_size<=300 and 1<=args.max_pages<=35 and 20<=args.window<=250 and .5<=args.entry<=5 and 0<=args.exit_z<args.entry and 0<=args.cost_bps<=100):
        p.error('Parameter out of allowed range')
    print('Universe:',', '.join(UNIVERSE), '| Fixed demonstration pair:', '/'.join(PAIR))
    prices=load_local_prices(args.csv.expanduser()) if args.csv else login_and_get_prices(args.days,args.page_size,args.max_pages,args.folder.expanduser())
    if len(prices)<180:
        raise RuntimeError(f'Only {len(prices)} raw daily rows returned; cannot calculate credible performance. Try increasing max-pages or use longer verified historical data.')
    px,bt,trades,metrics=backtest(prices,args.window,args.entry,args.exit_z,args.cost_bps)
    build_report(px,bt,trades,metrics,args.outdir.expanduser())
    return 0

if __name__=='__main__':
    try:sys.exit(main())
    except (ValueError, RuntimeError, OSError, requests.RequestException) as ex:
        print('STOPPED SAFELY:',str(ex).splitlines()[0][:250],file=sys.stderr);sys.exit(1)
    except KeyboardInterrupt:
        print('\nCancelled. No order requests were made.',file=sys.stderr);sys.exit(130)