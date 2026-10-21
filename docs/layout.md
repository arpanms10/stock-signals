# Code layout

Where everything lives and why the seams sit where they do.

[← back to the README](../README.md)

---

## At a glance

```
setup_project.py   start here: guided setup on a new computer
run_ui.py          open the dashboard
scripts/           everything you or the scheduled jobs run: refreshes, records, reports
research/          backtests and experiments (see docs/validation.md)
config/            settings, and the files you fill in (holdings, MF book, watch list)
data/ history/     downloaded data (not in git) / forward records (in git)
docs/              how each part works
tests/             the test suite (pytest)

book/              your book: holdings, watch lists, the files you fill in, decision log
records/           forward-record stores and their reports
ops/               the scheduled jobs (launchd, cron, Task Scheduler)
analysis/ backtest/ data/ indicators/ mf/ picker/ results/ scoring/ signals/
strategy/ ui/      the engine, by subject (below)
```

Every script finds the project's packages on its own, so `python
scripts/run_backfill.py` works from the project folder without setting
`PYTHONPATH`.

## In detail

```
config/holdings.xlsx       your positions (or .csv; a broker export works as-is)
config/watchlist.csv       stocks to track
config/fundamentals.csv    quality inputs, auto-filled from NSE + Yahoo
config/etfs.csv            instruments excluded from the universe, with reasons
config/equities.csv        the company-equity pool the ranking selects from
config/scoring.yaml        weights, thresholds, risk limits, tax rates

setup_project.py           guided setup: environment, databases, your files, data, jobs
book/userfiles.py          the files you fill in (holdings, MF, watch list) as .xlsx or .csv
book/portfolio.py          holdings from config/holdings.xlsx/.csv or the Kite snapshot
book/watching.py           the watch list: baselines, stops and targets reached
book/watchlist.py          read/write/validate the watchlist
book/decision_log.py       every advisor verdict, dated, for scoring later
ops/jobs.py                the scheduled jobs, for launchd, cron and Task Scheduler
scripts/install_schedule.py   --list / --status / --install / --remove for those jobs
scoring/fundamentals.py    quality score, incl. the lender rubric
data/instruments.py        equity vs fund vs DVR classification
scripts/import_holdings.py broker .csv/.xlsx -> config/holdings.xlsx (or .csv), buckets kept;
                           --trade SYMBOL... marks positions bought to trade
scripts/set_buckets.py     pre-fill the core/satellite column
scripts/record_momentum.py monthly momentum picks -> history/momentum_picks.xlsx
scripts/record_fno.py      monthly F&O ranges -> history/fno_ranges.xlsx, scored at expiry
scripts/record_swing.py    every swing scan -> history/swing/ (records/swing_store.py)
scripts/record_ranges.py   daily Today ranges -> history/ranges/ (records/ranges_store.py)
history/                   records kept in git (momentum picks, F&O, swing, ranges)

data/sources/prices.py     OHLCV via jugaad-data
data/sources/nse_fundamentals.py   results, shareholding and pledge from XBRL

results/filings.py         one quarter per filing, period-aware; disk cache by URL
results/scorecard.py       YoY / acceleration / margin / quality; lender card
results/reaction.py        reaction day, move in ATRs, vs NIFTY 500
results/coverage.py        holdings + watch list + swing and momentum picks
results/engine.py          fetch -> score -> store; badges for other tables
results/store.py           data/results/*.csv (outside git: coverage shows holdings)
scripts/run_results.py     daily results run and report
research/run_results_backtest.py   post-results drift test -> config/results_calibration.json

picker/metrics.py          point-in-time TTM growth, P/E, PEG, quality inputs
picker/score.py            percentile ranks, GARP / QARP gates and scores
picker/engine.py           evaluate on a date, quarterly backtest
scripts/run_picker.py      today's shortlists, --refresh, --backtest
data/corporate_actions.py  split/bonus parsing and back-adjustment
data/bhavcopy.py           full-market history -- the survivorship-free universe
data/quality.py            suspect-bar detection and quarantine
data/store.py              SQLite cache (raw prices + actions)

indicators/core.py         RSI, MACD, ATR, ADX, OBV, Bollinger
scoring/timing.py          five buckets -> 0-100
signals/engine.py          entries, exits, stops, sizing (pure functions)
strategy/momentum.py       12-1 cross-sectional ranking
strategy/advisor.py        buy / add / trim / exit decisions
strategy/buckets.py        core / satellite / trade classification
strategy/tax.py            capital gains, LT/ST delivery, trim timing
strategy/allocation.py     target weights, drift, concentration
strategy/risk_monitor.py   stops and alerts on held positions
backtest/pit_engine.py     point-in-time, survivorship-free
backtest/engine.py         walk-forward trade simulation
backtest/option_backtest.py   option rules on the F&O bhavcopy history
backtest/report.py         trade stats, drawdown/streaks, year x month, Excel export
backtest/reconcile.py      rebuild sampled trades from source before trusting metrics

data/sources/nse_derivatives.py   live option chain, futures, expiries, lot sizes
data/fo_bhavcopy.py        historical F&O chains -> data/fo_history.db
strategy/fno.py            PCR, OI walls, max pain, straddle, range (pure)
records/fno_report.py      F&O load/text/Excel, shared by CLI and dashboard
scripts/run_fno.py         F&O analysis; scripts/run_fno_backtest.py validates it
docs/f_o/                  F&O docs: overview, data traps, validation

analysis/lookup.py         ticker / company name / ISIN -> NSE symbol
analysis/fundamental.py    Fundamental Analyst: one stock, on demand
analysis/technical.py      Technical Analyst: one stock, on demand

ui/app.py                  dashboard: top navigation and page list
ui/shell.py                search box and data chip on every page
ui/state.py                cached snapshot, data age, job runner, shared columns
ui/service.py              cached pipeline feeding the UI; the job list
ui/components.py           badges, evidence lines and formatting
ui/views/                  Today, Stocks, Market, Momentum, Search, Data and jobs
ui/fno_tab.py              the F&O page
ui/analyze_tab.py          the Analyze a stock page
ui/mf_tab.py               the Mutual funds and Compare funds pages
run_ui.py                  launch the dashboard
```

`signals/engine.py` is deliberately pure -- the backtest runs the same functions
as the nightly job, so the backtest stays evidence about what you actually run.
