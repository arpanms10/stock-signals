# Day-to-day operations

Setup, the watchlist, tuning, tests, and the daily signal run.

[← back to the README](../README.md)

---

```bash
PYTHONPATH=. .venv/bin/python run_daily.py --no-fetch --portfolio 1000000
```

- `--portfolio` — your capital, in rupees. Drives position sizing, so set it honestly.
- `--no-fetch` — use cached prices. Omit it to refresh from NSE first (~2 min).
- `--as-of YYYY-MM-DD` — evaluate as of a past date, for checking what it would have said.


# The watchlist

`config/watchlist.csv` — plain CSV, opens in Excel or Numbers. Edit it, save,
and the next run picks it up; nothing to restart.

Currently seeded with the NIFTY 50 as a **development universe, not a
recommendation**. Setting `active` to `no` retires a stock without deleting the
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

---


# Setup

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
