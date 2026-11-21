# Dependency map — verified on fixture data

| Step | Entrypoint | Reads | Writes |
|---|---|---|---|
| Check balance | `run.py balance` → `src/usmart_v8_asset_diagnostic.py` | `config/local/{api_code,public_key,private_key}` + LOGIN password entered at runtime | stdout only; token not saved |
| Fetch historical | `run.py fetch-prices` → `src/popquants_pairs_strategy1.py` → `src/usmart_nvda_30d.py` | Own credentials | `data/prices/us_stock_adjusted_daily.csv` |
| Import historical | `run.py import-prices` | `~/Documents/usmart/popquants_strategy1/us_stock_adjusted_daily.csv` | `data/prices/us_stock_adjusted_daily.csv` |
| Import SEC | `run.py import-sec` | `~/Documents/usmart/sec_13f_zips/*.zip` | `data/sec_13f_zips/*.zip` |
| Download SEC | `run.py download-13f` → `src/download_sec_13f.py` | SEC public data via contact User-Agent | `data/sec_13f_zips/*.zip` |
| Strategy 1 | `run.py strategy1` → `src/popquants_pairs_strategy1.py` | Full adjusted prices with pre-2026 warmup | `outputs/strategy1/*` |
| Strategy 2 | `run.py strategy2` → `src/popquants_strategy2.py` | Same full prices | `outputs/strategy2/*` |
| Strategy 3 | `run.py strategy3` → `src/popquants_strategy3_2026_teaching.py` | Same prices + SEC 13F ZIPs | `outputs/strategy3/*` |
| Dashboard | `run.py dashboard` → `src/popquants_tearsheet_viewer.py` | Returns/Signals from 1,2,(3 optional) and prices | `outputs/dashboard/interactive_tearsheet.html` |
| Notebook 1–3 | `notebooks/*.ipynb` | `data/prices/...`, `outputs/strategy*` | notebook display; optional audit CSV |

**Date rule:** All final performance CSV files are restricted to **2026-01-02 through 2026-11-20**, truncated automatically at the latest actual market date. Pre-2026 prices may be read to form rolling-window indicators; these dates are not included in reported returns.

**Validation performed:** AST/py_compile on all modules; Strategy 1 and Strategy 2 end-to-end with an isolated synthetic PRICE fixture; Strategy 3 SEC parser/Word2Vec/backtest with an isolated synthetic SEC fixture; three teaching Notebooks executed from the project folder; final dashboard built with all three strategies and benchmark. The test fixtures and their results are deliberately absent from the distribution package.

**Not validated:** Credentials/asset access using another learner's account, downloading from live SEC/Market Data in this runtime, numeric stability of gensim 4.4.0 on the user's Mac, or live order execution (which is not implemented).