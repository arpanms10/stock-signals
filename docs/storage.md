# Data storage

Where everything the app downloads, records and reads is kept, and what you
need to back up.

[← back to the README](../README.md)

---

**Rule of thumb:** the four SQLite databases hold large public data the app
downloads and can rebuild at any time. The Excel files are either things you
type in, or records and reports the app writes for you to read. Plain CSV and
JSON files hold the daily logs and test results the dashboard reads back.

## Databases

In `data/`, never in git.

| Database | Approx. size | What's in it | Main tables | Filled by |
|---|---|---|---|---|
| `signals.db` | 150+ MB | Day-by-day prices for your watch list, holdings and the Nifty 500 | `prices`, `index_prices`, `corporate_actions`, `fetch_log` | daily job |
| | | Live option-chain snapshots | `option_snapshots`, `futures_snapshots` | F&O page, intraday job |
| | | The advisor's past advice, for checking later | `decision_log` | advisor |
| `market.db` | 500+ MB | NSE's full daily market file for every listed stock, delisted ones included (the universe and the momentum backtest) | `market`, `market_days`, `instruments` | weekly job |
| | | Splits, bonuses, demergers, trade-for-trade spells | `market_actions`, `market_demergers`, `market_t2t` | weekly job |
| `fo_history.db` | 100+ MB | Daily option chains and their prices, index highs and lows, results-meeting dates | `fo_chain`, `fo_spot`, `fo_index_ohlc`, `fo_results`, `fo_days` | weekly and daily jobs |
| `mf/mf.db` | ~10 MB | Month-end NAV of every Direct Growth fund since 2013 | `nav`, `schemes`, `fetched` | monthly job |

Everything here comes from free public sources (NSE, AMFI; see
[data](data.md)). If a database is lost, `setup_project.py` downloads it
again. `signals.db` and `fo_history.db` use WAL mode, so the dashboard can
read while a job writes.

## Excel files

**Inputs you fill in.** In `config/`, private and git-ignored. Each can be a
`.csv` instead (keep only one), and each `.xlsx` has a *How to fill* sheet.
`setup_project.py` creates them blank.

| File | Contents |
|---|---|
| `holdings.xlsx` | Your shares: symbol, quantity, average price, long/short-term split, bucket, notes ([recording what you own](../README.md#recording-what-you-own)) |
| `mf_holdings.xlsx` | Your mutual funds, as a percentage of your fund portfolio. No rupee amounts |
| `watching.xlsx` | Stocks you're keeping an eye on. The app adds to it when you press *Watch* |

**Records.** In `history/`, meant for git. Each is written before the outcome
is known, so how things turned out can be checked later.

| File | Contents |
|---|---|
| `momentum_picks.xlsx` | One tab per month: that month's top momentum picks, with prices on the day |
| `swing_picks.xlsx` | Each day's top 10 swing setups and how each played out. Rebuilt from `history/swing/` on every run, so edits made in Excel are overwritten |
| `fno_ranges.xlsx` | One tab per expiry: each underlying's expected range, then whether it held |

**Reports.** In `reports/`, git-ignored, safe to delete: `mf/MF_Evaluation.xlsx`,
`fno/fno_<date>.xlsx` from the F&O analysis, backtest workbooks and trade logs
in `backtests/`, and the weekly job's dated report and logs.

## CSV and JSON files

| Where | Contents | In git |
|---|---|---|
| `history/swing/` (`runs.csv`, `scans/YYYY-MM.csv`, `members.csv`) | Every swing scan, in full, append-only ([swing](swing.md#the-forward-record)) | yes |
| `history/ranges/YYYY-MM.csv` | Every day's expected ranges, append-only ([ranges](ranges.md#forward-log)) | yes |
| `data/results/` | Quarterly results per company, scorecards, backtest working files | no |
| `data/cache/` | Raw NSE filings, gzipped, so they are never fetched twice | no |
| `config/*_calibration.json`, `config/fno_reach.json` | The latest backtest results the dashboard shows. Each backtest command rewrites its own file | no |
| `config/equities.csv`, `config/etfs.csv`, `config/fundamentals.csv` | Derived lists and fundamentals, rebuilt by the jobs | no |
| `config/scoring.yaml`, `config/mf_rules.yaml` | Settings and thresholds you can change | yes |

## What to back up

Only your input files in `config/` (holdings, mutual funds, watch list) and
the `history/` records can't be rebuilt. Commit `history/` now and then, or
copy it with your `config/` files; everything else can be downloaded or
recomputed.
