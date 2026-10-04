# Code layout

Where everything lives and why the seams sit where they do.

[← back to the README](../README.md)

---

```
config/holdings.csv        your positions (broker export works as-is)
config/watchlist.csv       stocks to track
config/fundamentals.csv    quality inputs, auto-filled from NSE + Yahoo
config/etfs.csv            instruments excluded from the universe, with reasons
config/equities.csv        the company-equity pool the ranking selects from
config/scoring.yaml        weights, thresholds, risk limits, tax rates

watchlist.py               read/write/validate the watchlist
portfolio.py               holdings from CSV or Kite snapshot
fundamentals.py            quality score, incl. the lender rubric
instruments.py             equity vs fund vs DVR classification
import_kite_holdings.py    Kite .csv/.xlsx -> config/holdings.csv, buckets kept
set_buckets.py             pre-fill the core/satellite/legacy column

data/sources/prices.py     OHLCV via jugaad-data
data/sources/nse_fundamentals.py   results, shareholding and pledge from XBRL
data/corporate_actions.py  split/bonus parsing and back-adjustment
data/bhavcopy.py           full-market history -- the survivorship-free universe
data/quality.py            suspect-bar detection and quarantine
data/store.py              SQLite cache (raw prices + actions)

indicators/core.py         RSI, MACD, ATR, ADX, OBV, Bollinger
scoring/timing.py          five buckets -> 0-100
signals/engine.py          entries, exits, stops, sizing (pure functions)
strategy/momentum.py       12-1 cross-sectional ranking
strategy/advisor.py        buy / add / trim / exit decisions
strategy/buckets.py        core / satellite / legacy classification
strategy/tax.py            capital gains, LT/ST delivery, trim timing
strategy/allocation.py     target weights, drift, concentration
strategy/risk_monitor.py   stops and alerts on held positions
backtest/pit_engine.py     point-in-time, survivorship-free
backtest/engine.py         walk-forward trade simulation

ui/app.py                  dashboard: holdings and market sections
ui/service.py              cached pipeline feeding the UI
ui/components.py           badges and formatting
run_ui.py                  launch the dashboard
```

`signals/engine.py` is deliberately pure -- the backtest runs the same functions
as the nightly job, so the backtest stays evidence about what you actually run.

---

See also: [ROADMAP](../ROADMAP.md) for what is built and what is still wrong.
