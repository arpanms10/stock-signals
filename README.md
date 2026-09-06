# stock-signals

**A portfolio advisor for NSE equities.** It reads Indian company filings, ranks
a survivorship-free universe, and tells you what to do with what you already
own — including when the honest answer is that it found nothing.

> **[→ Feature overview](https://claude.ai/code/artifact/3e4ddd96-ba4b-4d97-9220-34ef68255dc0)** — a
> visual walkthrough of what it does and what its backtests actually found.
> *(Private link; visible only to people it has been shared with.)*

---

### Read-only with respect to your broker

It never places, modifies or cancels an order — **GTT orders included**. It
computes and explains; every decision and every click stays yours. Decision
support, not investment advice.

### What it gives you

| | |
|---|---|
| **A verdict per holding** | Buy · Trim · Exit · Hold, each with its reasoning, stop price, targets and position size |
| **The distinction that matters** | *Trim* = the position is too big, not bad (you keep it). *Exit* = the reason to own it is gone (sell it all) |
| **Tax-aware execution** | Delivers long-term shares first, waits when waiting pays, splits large trims into tranches |
| **A lender rubric** | Banks scored on NPA, provision coverage and ROA — not on debt/equity and cash flow, which invert for a business that borrows to lend |
| **Honest validation** | Survivorship-free backtests, random-pick controls, a train/holdout harness, and results reported whether or not they flatter |

### The headline result

| | 9-year CAGR |
|---|---|
| Momentum rotation | **14.5%** |
| NIFTY 500 index | 11.3% |
| Random-pick control | 1.8% |
| v1 signal rules *(retired)* | 1.3% |

After realistic Indian costs — STT, stamp duty, exchange fees, GST and slippage.
Momentum sits 8.4 standard deviations above a random control drawn from the same
universe. Its maximum drawdown is −44%, worse than the index's −38%. A real but
modest edge, and the README says so on every page that touches it.

### At a glance

```
3.84M   daily price rows          156   tests, one per bug found
3,638   symbols, delisted included  489   ETFs excluded, with reasons
2,607   trading days                  0   orders it can place
```

---

## Quick start

```bash
uv venv && uv pip install -r requirements.txt

# 1. Your positions -- a broker holdings export works unmodified
cp config/holdings.example.csv config/holdings.csv

# 2. Market history (~70 min, once)
PYTHONPATH=. .venv/bin/python run_market_ingest.py --years 10
PYTHONPATH=. .venv/bin/python run_backfill.py --universe nifty200 --years 10
PYTHONPATH=. .venv/bin/python fetch_fundamentals.py

# 3. Open the dashboard
.venv/bin/python run_ui.py
```

## Contents

- [Setup](#setup) · [Quick start](#quick-start)
- [**Dashboard**](#dashboard) — two sections, badge system, target frames
- [Portfolio advisor](#the-investing-guide-v2) — verdicts, buckets, tax
- [Daily signals](#daily-signals) · [Backtest](#backtest) · [Experiments](#experiments-train--holdout)
- [Refresh data](#refresh-price-data) · [Tests](#tests) · [Watchlist](#the-watchlist) · [Tuning](#tuning)
- [**Current status**](#current-status-what-worked-and-what-didnt) — what worked, what didn't
- [Data notes](#data-notes) — five NSE traps that fail silently
- [Layout](#layout)

---

## Setup

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

## Daily signals

```bash
PYTHONPATH=. .venv/bin/python run_daily.py --no-fetch --portfolio 1000000
```

- `--portfolio` — your capital, in rupees. Drives position sizing, so set it honestly.
- `--no-fetch` — use cached prices. Omit it to refresh from NSE first (~2 min).
- `--as-of YYYY-MM-DD` — evaluate as of a past date, for checking what it would have said.

## Backtest

```bash
PYTHONPATH=. .venv/bin/python run_backtest.py --shuffle
```

`--shuffle` runs a randomised-entry control alongside the strategy. **If the
strategy does not clearly beat its own shuffled control, the entry rules are
adding nothing.** Also accepts `--start` / `--end` for holdout testing.

## The investing guide (v2)

One command answers the four questions in the order they matter -- what the
market is doing, what needs attention in your book, what to own, what to change:

```bash
PYTHONPATH=. .venv/bin/python run_guide.py --universe nifty200 \
    --capital 1000000 --total-capital 3000000 --rebalance
```

### Cross-sectional momentum (the strategy change)

```bash
PYTHONPATH=. .venv/bin/python run_momentum.py --universe nifty200 --shuffle 5
PYTHONPATH=. .venv/bin/python run_momentum.py --universe nifty200 \
    --exclude-universe nifty50            # out-of-sample run
```

v1 tried to time entries and exits and lost because it sat out 70% of the
time. Momentum never asks "should I be in this stock?" -- it asks "which are
strongest?" and holds the top slice, always, rotating monthly. 12-1 momentum
(12 months excluding the most recent, because short-horizon returns reverse),
divided by realised volatility, gated on being above the 200 DMA.

Early read on NIFTY 50 (narrow, in-sample): **15.19% CAGR vs 1.29% for the v1
rules**, beating the index by 3.4 points but still short of equal-weight
buy-and-hold at 18.69%. Holding 15 of 50 names is barely selective; the real
test is NIFTY 200.

### Fundamentals: NSE primary, Yahoo for the gaps

```bash
PYTHONPATH=. .venv/bin/python fetch_fundamentals.py            # all symbols
PYTHONPATH=. .venv/bin/python fetch_fundamentals.py --yahoo-only  # fast, no pledge
```

jugaad-data does **not** cover fundamentals -- prices, bhavcopy, indices and
derivatives only. NSE's own endpoints do, in two layers: a JSON API carrying
metadata and a link, and the XBRL filing carrying the numbers.

| source | supplies | why it wins there |
|---|---|---|
| **NSE** | revenue, PAT, interest cover, promoter %, **pledge %** | Authoritative, always in rupees, and the only source for pledge |
| **Yahoo** | ROE, ROCE, debt/equity, P/E, CFO | Ratios NSE does not publish directly |

Three traps this navigates, each of which silently corrupted the sheet before
it was caught:

1. **Currency.** Yahoo reports Indian IT companies in USD. Converting dollars
   as rupees understated HCLTECH's revenue 85-fold, and mixing a rupee CFO with
   a dollar PAT would have made CFO/PAT wrong by the same factor. Absolute
   values now come from NSE, or from Yahoo only when it reports INR.
2. **Period basis.** NSE files quarterly; Yahoo's cash flow is annual. Pairing
   them reports a ~4x inflated CFO/PAT as excellent cash conversion. The sheet
   carries an explicit `period` column and the score refuses to compute cash
   quality unless both sides agree.
3. **Pledge extraction.** A shareholding filing repeats its pledge tags across
   ~240 contexts -- per promoter entity, once for the group, then padded with
   zeros. Taking the last occurrence returned 0 for a company that genuinely
   has a pledge. It is now computed from the group total against promoter
   holding, the way India quotes it (pledge as % of *promoter* stake, not of
   total shares -- the two differ by roughly the promoter stake).

`pledged: False` (the filing declares no encumbrance) and `pledged: None`
(unreadable) are kept distinct throughout. "No pledge" and "we don't know" are
different facts about a holding.

### Fundamentals sheet

`config/fundamentals.csv` -- one row per stock per quarter, hand-entered from
results you already read. Six numbers matter most: CFO vs PAT (profits that
never become cash), promoter pledge %, debt/equity, interest cover, ROCE, and
P/E against the stock's own history. Missing values are dropped from the score
rather than treated as zero, and every run names the stocks riding on no
fundamentals at all -- "unknown" must never read as "acceptable".

Lenders are flagged and suppressed rather than scored: debt/equity and CFO are
meaningless for a bank.

### Risk monitor

The v1 exit engine pointed at holdings instead of trades. Stop levels, trailing
stops, score collapse, drawdown from peak, sector concentration, correlation
clusters. Everything is a **review prompt, not an instruction** -- acting
mechanically on these signals is exactly what lost money in v1.

## Dashboard

```bash
.venv/bin/python run_ui.py
```

Opens at `http://localhost:8501`. The UI imports the framework directly -- there
is no API layer and no second process. Streamlit runs Python in-process, so an
HTTP boundary between the UI and the engine would add serialisation, a port and
a second copy of the logic to keep in sync, in exchange for nothing.

### Two sections

**My Holdings** -- what you own and what to do about it. Book value, P&L,
core/satellite/legacy split, sector exposure with cap warnings, then one card
per holding: the action, the trade plan, the reasoning, fundamental flags, and
for sells the realised gain, estimated tax, tax saved and tranche schedule.

**Market** -- the ranked universe. Sortable and filterable by sector, top band,
or held-only. Selecting a stock shows its entry levels: the price at which each
rule would fire, with the stop and targets that would apply *there*.

### Badges

| | meaning |
|---|---|
| **B** green | buy or add |
| **T** amber | trim -- the position is too big, not bad. You keep it. |
| **S** red | exit -- the thesis is broken. Sell it all. |
| **S!** red + ring | same, but urgent -- a hard balance-sheet red flag, not a judgement call |
| **H** grey | hold |

Trim and exit carry different letters as well as different colours. They are
different decisions, and a shade of red is not enough to carry that -- nor does
it survive colourblindness.

### Two target frames, deliberately separate

A holding bought cheaply that has since run up has entry targets *below* today's
price. Shown on a row recommending a buy, that reads as "sell lower than you
buy" -- which is how the bug was found.

- **Buying today** uses today's price: `₹1,423 to buy · stop ₹1,379 · T1 ₹1,489 · T2 ₹1,555`
- **Holding a position** uses your entry, marking targets already achieved:
  `₹1,423 now · stop ₹1,408 · T1 ₹1,283 ✓ passed · T2 ₹1,349 ✓ passed`
- **Exits** show no targets at all -- you are leaving.

A target below the current price is never rendered with a negative percentage.

### Running scripts from the UI

Four sidebar buttons -- refresh prices, refresh fundamentals, update
full-market data, suggest buckets. They run as subprocesses with output
streaming into the sidebar. Subprocess rather than in-process because these are
long and chatty: you get real progress, and a hung network fetch cannot take the
dashboard down with it.

### Configuration and privacy

`.streamlit/config.toml` sets:

- `toolbarMode = "minimal"` -- hides Streamlit's **Deploy** button. That button
  offers to publish to Streamlit Community Cloud, where apps are PUBLIC by
  default, and this one renders holdings, average costs and P&L. It does not
  belong on a personal finance dashboard.
- `address = "localhost"` -- loopback only, not reachable from the network.
- `gatherUsageStats = false`.

### One gotcha

**Streamlit does not reliably hot-reload imported modules** -- only the main
script. If you edit `ui/components.py` or `ui/service.py` and the change does
not appear, restart the app rather than pressing Recompute. Edits to
`ui/app.py` itself reload normally.

The full pipeline (200 symbols, indicators, ranking, scoring) takes ~30s and is
cached. **Recompute** clears the cache; the data-refresh buttons clear it too.

## Experiments (train / holdout)

```bash
PYTHONPATH=. .venv/bin/python run_experiment.py                       # compare on train
PYTHONPATH=. .venv/bin/python run_experiment.py --holdout --variant X # ONE shot
```

Variants live in `VARIANTS` in `run_experiment.py`. Each should embody a
*hypothesis about why the strategy fails*, not a threshold sweep -- so that a win
is interpretable rather than accidental. Choose on train, then run the holdout
once. Never iterate against holdout results.

## Refresh price data

```bash
PYTHONPATH=. .venv/bin/python run_backfill.py --years 10
```

Incremental and safe to re-run — it fetches only the gaps, at both ends of what
is already stored. Add `--symbols RELIANCE TCS` to limit it.

## Tests

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/ -q
```

---

## The watchlist

`config/watchlist.csv` — plain CSV, opens in Excel or Numbers. Edit it, save,
and the next run picks it up; nothing to restart.

Currently seeded with the NIFTY 50 as a **development universe, not a
recommendation**. Setting `active` to `no` retires a stock without deleting the
row, so the backtest still sees it — deleting rows would erase past losers and
flatter every result.

## Tuning

`config/scoring.yaml` holds every weight, threshold and risk limit. Change
numbers there, not in code — then re-run the backtest *with a holdout period you
did not tune on* before believing the result.

---

## Current status: what worked and what didn't

Backtested over 49 NIFTY 50 names after realistic Indian costs (STT, stamp duty,
exchange fees, GST, slippage -- about 0.35% per round trip). All figures below
were produced *after* the OBV bug fix described in "Data notes"; earlier numbers
computed with the buggy indicator are superseded.

### Baseline, full period 2016-2026

| | 10-year CAGR |
|---|---|
| Equal-weight buy & hold, same 49 stocks | **17.73%** |
| NIFTY 500 index | **11.82%** |
| This strategy | **1.29%** |
| Shuffled-entry controls (7 seeds) | mean 0.60%, sd 0.49, range 0.03-1.39 |

The strategy beat 6 of 7 randomised controls, sitting about 1.4 standard
deviations above the control mean. That is **not** statistical significance, and
one control beat it outright. Read at face value the entry rules are worth
roughly 0.7% a year -- against a 10.5% a year gap to the index. Even if the
effect is real, it is economically irrelevant.

### Fix attempt: loosen the exits (train/holdout)

Hypothesis: the system cut winners during ordinary noise. Tested by widening
stops, moving the trend-break exit from the 50 DMA to the 200 DMA, and dropping
fixed targets and the cooldown. Variants were compared on 2016-2022 only; the
2023-2026 holdout was run after choosing.

| variant | train CAGR | holdout CAGR |
|---|---|---|
| baseline | 1.11% | -- |
| wide_stops | 3.91% | -- |
| trend_200 | 2.87% | -- |
| **let_winners_run** | **5.17%** | **3.31%** |
| NIFTY 500 index | 11.86% | 11.64% |
| buy & hold same names | 23.93% | **16.89%** |

The hypothesis was right about the mechanism and insufficient as a fix. Loosening
exits nearly tripled the average win (9.8% -> 25.6%) and extended holds from 26
to 109 days, so winners genuinely were being cut early. But 3.31% still loses to
holding the same stocks by 13.6 points a year.

Train 5.17% -> holdout 3.31% is a degradation but not a collapse, so the variant
is **not badly overfit**. It generalises to being consistently mediocre.

**The 2023-2026 holdout is now spent.** It was run twice only because the first
run was invalidated by the OBV bug -- a correction of a broken measurement, not a
second attempt at a different variant. Any new idea needs a fresh untouched
period or a different universe.

### What this means

Across the baseline, the R-multiple targets, the loosened exits and the holdout,
every result points the same way: on large-cap Indian equities over this period,
timing entries and exits destroyed value relative to holding. Per-symbol results
are unanimous -- all 50 names show a negative strategy return while most of the
underlying stocks rose several hundred percent.

**Do not trade the v1 signal rules.** They are kept in the repo because the
negative result is evidence, and because the infrastructure beneath them --
data, costs, controls -- is what the momentum work is built on.

## Data notes

Four NSE traps handled here, all of which fail silently rather than raising:

1. **Multiple series per symbol.** NSE returns bonds and other series alongside
   the equity under one symbol — NTPC comes back at ~1360 on 100 shares of
   volume interleaved with the real stock near 100. Filtered to `SERIES == EQ`
   before deduplicating.
2. **IST date offset.** Trading days arrive as IST midnight expressed in UTC,
   i.e. 18:30 on the previous calendar day. Corrected before reducing to a date.
3. **Unadjusted splits and bonuses.** Raw prices are not adjusted; WIPRO's 1:1
   bonus shows as 584.55 → 291.65, which an unadjusted RSI reads as a crash.
   Raw prices and actions are stored separately and adjusted at read time.
4. **Gaps in NSE's corporate-actions API.** JSW Steel's 10:1 split is simply
   absent; demergers cannot be adjusted without the ratio. Factors are never
   inferred from gap size — an unadjusted split and a real crash look identical —
   so affected history is quarantined and reported instead.

Ingestion flags any unexplained move above 35% as a suspect bar.

A fifth trap was self-inflicted and worth recording: **OBV is a cumulative sum
from an arbitrary origin**, so its percent change is not well defined -- near a
zero crossing it explodes, and the value depends on how much history happened to
be loaded. The same stock scored 46.8 on 750 bars and 42.9 on 1250. Replaced by
`obv_pressure`: net directional volume over n bars as a fraction of volume
actually traded, which is scale-free and origin-independent. Tests assert the
value is identical however far back the data starts.

**How much history do signals need?** 250 trading days (~1 year). Every
indicator is bit-identical from there onward -- the longest lookback is the 200
DMA plus its 21-bar slope, and the 52-week high needs 250. `--years 2` is ample
for daily signals. The 10 years exists for the backtest, which needs multiple
market regimes and enough trades to mean anything.

## Layout

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
