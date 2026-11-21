#!/usr/bin/env python3
"""PopQuants US Stocks workshop, read-only orchestrator.

Runs the 2026 evaluation window without changing the core backtest rules.
No live orders are implemented.
"""
from __future__ import annotations
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
SETTINGS = json.loads((ROOT / "config" / "workshop.json").read_text(encoding="utf-8"))
START = SETTINGS["evaluation_start"]
END = SETTINGS["evaluation_end"]
TICKERS = tuple(SETTINGS["universe"])
WINDOW = int(SETTINGS["rolling_window_days"])
COST = float(SETTINGS["one_way_cost_bps"])
SOURCE = ROOT / "data" / "prices" / "us_stock_adjusted_daily.csv"
OUTPUTS = ROOT / "outputs"

def ensure_dirs():
    for path in (ROOT/"config"/"local", SOURCE.parent, ROOT/"data"/"sec_13f_zips", OUTPUTS):
        path.mkdir(parents=True, exist_ok=True)

def validate_prices(path=SOURCE):
    import numpy as np
    import pandas as pd
    if not path.is_file():
        raise FileNotFoundError(f"Missing historical prices: {path}. Run 'python run.py import-prices' or 'python run.py fetch-prices'.")
    frame = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    missing = set(TICKERS) - set(frame.columns)
    if missing:
        raise ValueError(f"Missing stock columns: {sorted(missing)}")
    frame = frame[list(TICKERS)].apply(pd.to_numeric, errors="coerce")
    if frame.index.has_duplicates or not np.isfinite(frame.to_numpy()).all() or (frame <= 0).any().any():
        raise ValueError("Prices contain duplicates, missing/infinite values, or nonpositive values.")
    if frame.index.min() > pd.Timestamp("2025-06-01"):
        raise ValueError("Need price history before June 2025 for 2026 indicators warm-up.")
    if frame.loc[START:END].shape[0] < 120:
        raise ValueError("Insufficient trading days within 2026 evaluation window.")
    print(f"Prices: {len(frame)} bars, {frame.index.min().date()} to {frame.index.max().date()}")
    return frame

def period_frame(frame):
    # Preserve earlier warm-up data during signal calculations, then slice the test period.
    result = frame.loc[START:END].copy()
    if len(result) < 100:
        raise ValueError("Not enough observations in the requested period.")
    return result

def refresh_pnl(frame):
    frame = frame.copy()
    frame["equity"] = (1 + frame["net_return"]).cumprod()
    frame["gross_equity"] = (1 + frame["gross_return"]).cumprod()
    frame["drawdown"] = frame["equity"] / frame["equity"].cummax() - 1
    return frame

def metric_table(returns):
    import numpy as np
    r = returns.astype(float)
    equity = (1 + r).cumprod()
    drawdown = equity / equity.cummax() - 1
    sd = r.std(ddof=1)
    vol = sd * np.sqrt(252)
    cagr = equity.iloc[-1] ** (252/len(equity)) - 1 if equity.iloc[-1] > 0 else float("nan")
    downside = np.sqrt(np.square(r.clip(upper=0)).mean())
    return {
      "start": r.index.min().strftime("%Y-%m-%d"),
      "end": r.index.max().strftime("%Y-%m-%d"),
      "trading_days": int(len(r)),
      "total_return": float(equity.iloc[-1]-1),
      "cagr": float(cagr),
      "annualized_volatility": float(vol),
      "sharpe_0rf": float(r.mean()/sd*np.sqrt(252)) if sd > 0 else None,
      "sortino_0rf": float(r.mean()/downside*np.sqrt(252)) if downside > 0 else None,
      "max_drawdown": float(drawdown.min()),
      "calmar": float(cagr/abs(drawdown.min())) if drawdown.min() < 0 else None,
    }

def import_existing(old_root):
    ensure_dirs()
    old = old_root.expanduser() / "popquants_strategy1" / "us_stock_adjusted_daily.csv"
    if not old.is_file():
        raise FileNotFoundError(f"Original price CSV missing: {old}")
    if SOURCE.exists():
        shutil.copy2(SOURCE, SOURCE.with_suffix(".csv.previous"))
    shutil.copy2(old, SOURCE)
    validate_prices()
    print("Imported historical uSMART prices.")

def import_sec(old_root):
    import zipfile
    ensure_dirs()
    src = old_root.expanduser() / "sec_13f_zips"
    dest = ROOT / "data" / "sec_13f_zips"
    archives = sorted(src.glob("*.zip"))
    if not archives:
        raise FileNotFoundError(f"No SEC ZIP archives in {src}")
    copied = 0
    for item in archives:
        with zipfile.ZipFile(item) as z:
            if not z.namelist():
                raise ValueError(f"Empty SEC archive: {item.name}")
        target = dest/item.name
        if target.exists() and target.stat().st_size == item.stat().st_size:
            continue
        shutil.copy2(item, target)
        copied += 1
    print(f"SEC archives: {len(archives)} found, {copied} copied.")

def fetch_prices():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import pandas as pd
    from popquants_pairs_strategy1 import login_and_get_prices
    ensure_dirs()
    config = ROOT / "config" / "local"
    for key in ("api_code", "public_key", "private_key"):
        if not (config/key).is_file():
            raise FileNotFoundError(f"Place your own {key} in {config}")
    today = datetime.now(ZoneInfo("America/New_York")).date()
    days = max(850, min((today-pd.Timestamp("2024-11-01").date()).days+1, 3500))
    data = login_and_get_prices(days=days, page_size=200, max_pages=22, folder=config)
    data.index.name = "date"
    if SOURCE.exists():
        shutil.copy2(SOURCE, SOURCE.with_suffix(".csv.previous"))
    data.to_csv(SOURCE, float_format="%.8f")
    validate_prices()
    print("Saved historical prices:", SOURCE)

def strategy1():
    from popquants_pairs_strategy1 import backtest, build_report
    ensure_dirs()
    p = validate_prices()
    px, bt, events, _ = backtest(p, window=WINDOW, entry=float(SETTINGS["entry_z"]),
                                  exit_z=float(SETTINGS["exit_z"]), cost_bps=COST)
    bt = refresh_pnl(period_frame(bt))
    px = px.loc[bt.index]
    if len(events):
        events = events.loc[events.signal_date.between(START, END)].copy()
    m = metric_table(bt.net_return)
    m.update({
        "pair": "AMAT/LRCX",
        "universe": list(TICKERS),
        "entry_z": float(SETTINGS["entry_z"]),
        "exit_z": float(SETTINGS["exit_z"]),
        "rolling_window": WINDOW,
        "number_of_signal_events": len(events),
        "daily_win_rate": float((bt.net_return > 0).mean()),
        "active_days_pct": float((bt.executed_position != 0).mean()),
        "one_way_turnover_sum": float(bt.turnover.sum()),
        "cost_bps_per_one_way_turnover": COST,
        "notes": "Evaluation window 2026; no broker orders or actual borrow fees."
    })
    out = OUTPUTS / "strategy1"
    build_report(px, bt, events, m, out)
    print("Strategy 1:", {k:m[k] for k in ("start","end","total_return","sharpe_0rf","max_drawdown")})

def strategy2():
    import pandas as pd
    from popquants_strategy2 import build_strategy
    ensure_dirs()
    p = validate_prices()
    bt,z,agg,dep,con,pos,ev = build_strategy(
        p, window=WINDOW,
        min_depth=int(SETTINGS["strategy2_min_depth"]),
        min_consistency=float(SETTINGS["strategy2_min_consistency"]),
        cost_bps=COST)
    bt = refresh_pnl(period_frame(bt))
    dates = bt.index
    z,agg,dep,con,pos = [x.loc[dates] for x in (z,agg,dep,con,pos)]
    if not ev.empty:
        ev = ev.loc[pd.to_datetime(ev["date"]).between(pd.Timestamp(START), pd.Timestamp(END))].copy()
    benchmark_equity = (p.loc[dates] / p.loc[dates].iloc[0]).mean(axis=1)
    bt["benchmark_return"] = benchmark_equity.pct_change().fillna(0.0)
    bt["benchmark_equity"] = (1 + bt.benchmark_return).cumprod()
    out = OUTPUTS / "strategy2"
    out.mkdir(parents=True, exist_ok=True)
    for name, frame in {
        "daily_backtest.csv": bt, "pair_zscores.csv": z,
        "aggregate_signals.csv": agg, "depth.csv": dep,
        "consistency.csv": con, "target_weights.csv": pos,
    }.items():
        frame.to_csv(out/name, index_label="date")
    ev.to_csv(out/"trade_signals.csv", index=False)
    (out/"metrics.json").write_text(json.dumps(metric_table(bt.net_return), indent=2), encoding="utf-8")
    print("Strategy 2:", metric_table(bt.net_return))

def strategy3():
    from types import SimpleNamespace
    import pandas as pd
    from popquants_strategy3_2026_teaching import execute
    ensure_dirs()
    validate_prices()
    sec = ROOT / "data" / "sec_13f_zips"
    if not list(sec.glob("*.zip")):
        raise FileNotFoundError("No SEC 13F ZIPs. Run import-sec or download-13f.")
    args = SimpleNamespace(
        root=str(ROOT), sec_dir=str(sec), prices=str(SOURCE),
        start=START, end=END, window=WINDOW,
        vocab=3000, neighbors=int(SETTINGS["strategy3_top_k"]),
        min_depth=int(SETTINGS["strategy3_min_depth"]),
        consistency=float(SETTINGS["strategy3_min_consistency"]),
        cost_bps=COST, seed=42, no_browser=True, verbose=True)
    execute(args)
    path = OUTPUTS / "strategy3" / "daily_backtest.csv"
    if not path.is_file():
        raise RuntimeError(f"Strategy 3 missing result: {path}")
    frame = pd.read_csv(path, parse_dates=["date"])
    if frame.date.min() < pd.Timestamp(START) or frame.date.max() > pd.Timestamp(END):
        raise ValueError("Strategy 3 has observations outside evaluation period.")
    print("Strategy 3 results:", path)

def dashboard(open_browser=True):
    from popquants_tearsheet_viewer import main as viewer
    old = sys.argv
    try:
        sys.argv = ["popquants_tearsheet_viewer.py","--root",str(ROOT)] + ([] if open_browser else ["--no-open"])
        viewer()
    finally:
        sys.argv = old

def check_balance():
    from usmart_v8_asset_diagnostic import main as balance_main
    ensure_dirs()
    local = ROOT / "config" / "local"
    for f in ("api_code","public_key","private_key"):
        if not (local/f).is_file():
            raise FileNotFoundError(f"Missing private local credential {local/f}")
    old = sys.argv
    try:
        sys.argv = ["usmart_v8_asset_diagnostic.py","--folder",str(local),"--balance"]
        balance_main()
    finally:
        sys.argv = old

def download_13f(email):
    cmd = [sys.executable, str(ROOT/"src"/"download_sec_13f.py"),"--email",email,
           "--output",str(ROOT/"data"/"sec_13f_zips")]
    subprocess.run(cmd, check=True)

def doctor():
    ensure_dirs()
    print("Project:", ROOT)
    print("Evaluation:", START, "to", END, "(latest available market bar)")
    for key in ("api_code","public_key","private_key"):
        print(f"Credential {key}:", "local present" if (ROOT/"config"/"local"/key).exists() else "not configured")
    print("Price file:", SOURCE if SOURCE.exists() else "missing")
    if SOURCE.exists():
        validate_prices()
    print("SEC ZIP count:", len(list((ROOT/"data"/"sec_13f_zips").glob("*.zip"))))
    for i in (1,2,3):
        print(f"Strategy {i}:", "available" if (OUTPUTS/f"strategy{i}"/"daily_backtest.csv").exists() else "not run")

def main():
    parser = argparse.ArgumentParser(description="PopQuants read-only workshop runner")
    parser.add_argument("task", choices=[
        "doctor","balance","import-prices","import-sec","fetch-prices","download-13f",
        "strategy1","strategy2","strategy3","dashboard","all"])
    parser.add_argument("--old-root",default=str(Path.home()/"Documents"/"usmart"))
    parser.add_argument("--email",help="SEC contact email for downloading 13F")
    parser.add_argument("--no-browser",action="store_true")
    a = parser.parse_args()
    tasks = {
        "doctor": doctor, "balance": check_balance,
        "import-prices": lambda: import_existing(Path(a.old_root)),
        "import-sec": lambda: import_sec(Path(a.old_root)),
        "fetch-prices": fetch_prices,
        "strategy1": strategy1, "strategy2": strategy2, "strategy3": strategy3,
        "dashboard": lambda: dashboard(not a.no_browser),
    }
    try:
        if a.task == "download-13f":
            if not a.email:
                parser.error("--email is required for SEC User-Agent")
            download_13f(a.email)
        elif a.task == "all":
            for stage in ("strategy1","strategy2","strategy3","dashboard"):
                print("\n====",stage.upper(),"====",flush=True)
                tasks[stage]()
        else:
            tasks[a.task]()
    except (FileNotFoundError,ValueError,RuntimeError,OSError,subprocess.CalledProcessError) as err:
        print("STOP:",err,file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
