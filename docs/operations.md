# Day-to-day operations

Every command, setup, the watchlist, tuning, tests, and the daily signal run.

[← back to the README](../README.md)

---

# Commands

| | |
|---|---|
| `setup_project.py` | Guided setup on a new computer; `--check` for status ([getting started](../GETTING_STARTED.md)) |
| `run_ui.py` | Dashboard, localhost only: Today, Portfolio, Ideas, Market and Research menus ([dashboard](dashboard.md)) |
| `scripts/record_ranges.py` / `scripts/run_range_backtest.py` | Today's expected ranges: forward log and calibration ([ranges](ranges.md)) |
| `scripts/run_advisor.py` | Verdicts on your book, as text |
| `scripts/run_momentum.py` | Rank the universe |
| `research/run_pit_momentum.py` | Survivorship-free backtest |
| `research/run_factor_backtest.py` | 52-week high and low volatility factors on the same backtest engine |
| `scripts/run_weekly.py` | Refresh everything, then report |
| `scripts/install_schedule.py` | Scheduled jobs on macOS, Linux or Windows: `--list`, `--status`, `--install`, `--remove` |
| `scripts/import_holdings.py` | Any broker's holdings download → `config/holdings.xlsx` (or `.csv`); see [recording what you own](holdings.md) |
| `scripts/set_buckets.py` | Pre-fill core / satellite (trade is only ever set by you) |
| `scripts/record_momentum.py` | Save this month's momentum picks to `history/` |
| `scripts/run_fno.py` | F&O: PCR, OI support/resistance, range to expiry (`--excel` for a workbook) |
| `scripts/run_fno_backtest.py` | How often that range actually held (`--ingest` first) |
| `scripts/record_fno.py` | Monthly F&O forward log → `history/fno_ranges.xlsx`, scored at expiry |
| `research/run_option_backtest.py` | What a simple option rule would have done, after costs (`--compare`, `--excel`) |
| `scripts/run_swing.py` | Swing setups in the Nifty 200/500, scored, with a trade plan (`--all`, `--capital`, `--backtest`, `--recorded`) |
| `research/run_swing_experiment.py` | Alternative swing rankings on a train / holdout split |
| `scripts/record_swing.py` | Record the day's swing scan in `history/swing/`, rebuild `history/swing_picks.xlsx` with outcomes |
| `scripts/run_swing_daily.py` | Refresh prices, record the swing scans, check the watch list for stops and targets, score new results -- what the daily job runs |
| `scripts/run_results.py` | Quarterly results: score new filings for holdings, watch list and picks; price reaction; who reports next |
| `research/run_results_backtest.py` | Whether the results verdict predicts post-results drift (Nifty 200, 24 quarters) |
| `research/run_drift_backtest.py` | Whether buying stocks that rallied on their results beats the universe after costs (point-in-time universe) |
| `scripts/run_picker.py` | GARP and QARP shortlists for the Nifty 200 from filings (`--refresh`, `--as-of`, `--backtest`) |
| `scripts/run_mf.py` | Mutual funds vs their category: EXIT / TRIM / ADD / WATCH for your MF book (`config/mf_holdings.csv` or `.xlsx`, % only) or any named fund (`--refresh`, `--compare`, `--excel`, `--backtest`) |

Run them from the project folder as `.venv/bin/python scripts/<name>.py`
(Windows: `.venv\Scripts\python scripts\<name>.py`); they find the project's
packages on their own, so `PYTHONPATH=.` in the examples is optional.
`scripts/` holds what you and the jobs run; `research/` holds the backtests
behind [validation](validation.md). [Code layout](layout.md) maps
the rest. Each script's `--help` lists its flags.


# Daily signal run

```bash
PYTHONPATH=. .venv/bin/python scripts/run_daily.py --no-fetch --portfolio 1000000
```

- `--portfolio` — your capital, in rupees. Drives position sizing, so set it honestly.
- `--no-fetch` — use cached prices. Omit it to refresh from NSE first (~2 min).
- `--as-of YYYY-MM-DD` — evaluate as of a past date, for checking what it would have said.


# The watchlist

`config/watchlist.csv` — plain CSV, opens in Excel or Numbers. Edit it, save,
and the next run picks it up; nothing to restart.

Seeded with the NIFTY 50 as a starting universe, **not a recommendation**.
Setting `active` to `no` retires a stock without deleting the
row, so the backtest still sees it — deleting rows would erase past losers and
flatter every result.


# Tuning

`config/scoring.yaml` holds every weight, threshold and risk limit. Change
numbers there, not in code — then re-run the backtest *with a holdout period you
did not tune on* before believing the result.

---


# Tests

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/ -q
```

GitHub Actions runs the same suite on every pull request and push to main
(`.github/workflows/tests.yml`). The tests use fixtures only -- no local
database, cache, holdings file or network -- so a test that needs any of those
will fail in CI before it reaches main.

---


# Setup

`python3 setup_project.py` does all of this, asking first
([getting started](../GETTING_STARTED.md)). By hand:

Dependencies are declared in two places, for two purposes:

- **`requirements.txt`** — exact pinned versions, verified working. Use this to
  reproduce the environment this was built and tested in.
- **`pyproject.toml`** — loose lower bounds. Use this if you would rather pick up
  newer compatible releases.

```bash
uv venv
uv pip install -r requirements.txt
```

or:

```bash
uv venv
uv pip install -e ".[dev]"
```

Every command below needs `PYTHONPATH=.` so the local packages resolve.

One dependency worth knowing about: **`jugaad-data`** scrapes NSE rather than
using an official API. That is what makes it free and key-less, and it is also
its weakness — it breaks when NSE changes its site. If price fetching suddenly
fails, upgrading that package is the first thing to try.

---

See also: [advisor](advisor.md), [dashboard](dashboard.md).
