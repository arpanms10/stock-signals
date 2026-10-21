# Quarterly results

A scorecard for every quarterly filing of the stocks you follow, read from
NSE's own XBRL, with the market's reaction, and backtests you can run on it.

[← back to the README](../README.md)

---

```bash
PYTHONPATH=. .venv/bin/python scripts/run_results.py                  # score what's new, report
PYTHONPATH=. .venv/bin/python scripts/run_results.py --days 14        # look further back
PYTHONPATH=. .venv/bin/python scripts/run_results.py --symbols TCS INFY
PYTHONPATH=. .venv/bin/python scripts/run_results.py --no-fetch       # report from what's on file
PYTHONPATH=. .venv/bin/python research/run_results_backtest.py         # verdict and reaction vs later returns
```

The dashboard's **Quarterly results** page shows the same, with a filter by why each
stock is followed, one stock's last eight quarters, and who reports next.
**Check for new results** (on the page and on Data and jobs) runs `scripts/run_results.py`.
The Holdings, Momentum and Swing tables get a **Results** column:
`Q2 strong · 16 Oct` once this quarter is filed, `reports 21 Oct` before.

## What is followed

`results/coverage.py`, each source read independently:

| Source | From |
|---|---|
| holding | `config/holdings.csv` (or the Kite snapshot) |
| watch | `config/watching.csv` |
| swing | the latest recorded scan's top picks, every universe |
| momentum | the latest month in `history/momentum_picks.xlsx` |

REITs and InvITs are skipped; ETFs file nothing and drop out on their own.

## Reading a filing

A results filing carries each line twice or three times under the same tag:
the quarter, the year to date and, in March, the full year. Taking the first
occurrence returns the half-year
whenever it happens to come first. `results/filings.py` reads only facts
whose context spans one quarter (80-100 days), ends on the quarter end and
has no dimension.

A filing does **not** carry last year's quarter, so year-on-year needs the
filing from four quarters back. Eight quarters are fetched per stock: enough
for YoY on the newest and for the three before it, which the acceleration
test needs. Raw filings are cached gzipped in `data/cache/filings/` by URL; a
revision gets a new URL, so a filing is never fetched twice.

**Basis:** one per series -- whichever of consolidated and standalone covers
more of the window, consolidated on a tie. Banks are always standalone: their consolidated filing carries
GNPA as 0.00. A quarter missing on the chosen basis is a gap; it is never
filled from the other basis, since that growth rate would measure the
subsidiaries.

**Kind:** a filing with interest expended and operating profit before
provisions is a bank (Schedule III Part A). One where interest earned is at
least half of revenue is an NBFC. Everything else is a company.

## The scorecard

Free sources carry no analyst estimates, so "did it beat?" cannot be asked.
What can be asked is whether the company did better than its own recent
quarters. Each line in `results:` of `config/scoring.yaml` is a `[bad, good]`
band: at or past *good* scores +1, at or past *bad* scores -1.

**Companies**

| Line | Default band | Why |
|---|---|---|
| Revenue YoY | 0% / 15% | the headline, as results are read in India |
| Acceleration | -3 / +3 pp | YoY now against the mean YoY of the prior three quarters |
| EBITDA margin vs year ago | -150 / +100 bps | operating leverage, or the lack of it |
| Core PBT YoY | 0% / 20% | PBT less other income and exceptional items |
| Earnings quality | -1 | other income ≥ 30% of PBT, or a one-off ≥ 10% of PBT |

EBITDA = core PBT + finance costs + depreciation. YoY is the headline because
many Indian businesses are seasonal enough that a quarter-on-quarter fall
means nothing; QoQ is shown, never scored.

**Banks and NBFCs**

| Line | Default band |
|---|---|
| NII YoY | 5% / 15% |
| PAT YoY | 0% / 15% |
| Credit cost vs year ago (provisions ÷ pre-provision profit) | +5 / -5 pp |
| Gross NPA vs last quarter (banks only; NBFCs don't file it in XBRL) | +20 / -10 bps |

For an NBFC, NII is interest earned less finance costs, and impairment is
the provision line.

**Verdict:** total ≥ 2 is *strong*, ≤ -2 is *weak*, between is *in line*.

**Flags** spell out what a number alone hides: profit propped up by other
income, a one-off this quarter, a one-off in last year's quarter that makes
PAT growth meaningless (read core PBT instead), profit up on falling revenue,
a margin down 200 bps or more, swinging to a loss or back to profit, a jump
in bad loans or provisions. Growth from a loss is left blank rather than
printed as a percentage.

Cards are rebuilt from the stored quarterly lines on every run, so a change
to the bands reaches every card already on file.

## The reaction

`results/reaction.py`. The reaction day is the first session whose close
reflects the filing: the same day if NSE broadcast it before 15:30,
otherwise the next session. Most results land after hours.

| Field | Meaning |
|---|---|
| gap | the open against the previous close: the first read |
| day | the reaction-day close against the previous close |
| ATR | the day's move in multiples of the stock's 14-day ATR; ±1 or more is a reaction, less is *muted* |
| vs index | less the NIFTY 500 the same day |
| 5 sessions | the close four sessions after the reaction day, and against the index |

The reaction is what stands in for the missing consensus. Good numbers that
sell off usually mean the market expected better; the detail view says so
when the verdict and the reaction disagree.

Prices come from the local store; a holding outside the Nifty 500 is fetched
from NSE directly (unadjusted, which only matters if a split lands inside the
few weeks measured).

## Storage

`data/results/scorecards.csv` and `data/results/quarters.csv`, both outside
git. The figures are public, but the list of symbols includes your holdings,
and the `.gitignore` keeps holdings out of the repo.

## Backtest: verdict and reaction

```bash
PYTHONPATH=. .venv/bin/python research/run_results_backtest.py               # fetch + report
PYTHONPATH=. .venv/bin/python research/run_results_backtest.py --report-only
PYTHONPATH=. .venv/bin/python research/run_results_backtest.py --report-only --universe pit   # survivorship-free
```

Every Nifty 200 filing for 24 quarters is scored as it would have been on
the day, on its own filing and the seven before it. Entry is at the
**reaction-day close** -- after both the numbers and the market's first read
are known -- and returns are measured over 20 and 60 sessions, less the
NIFTY 500. A results season packs most events into a few weeks, so the
headline spreads are averaged within each quarter and tested across quarters,
not across events. It reports strong / in line / weak, and stocks that
rallied (≥1 ATR) against those that sold off. The result is written to
`config/results_calibration.json`.

On today's Nifty 200 every row looks good, because the universe is survivors;
read the spreads between rows, not the levels. `--universe pit` uses each
quarter's 200 largest NSE companies by market cap, delisted and merged ones
included (`research/run_pit_universe.py`; see [GARP & QARP](picker.md#backtest)
for how it is built and its limits). Filings from companies the price store
never tracked are priced from the bhavcopy, which has no open, high or low, so
their reaction is sized on the close-to-close range, not the ATR.

## Backtest: the drift as a rule

```bash
PYTHONPATH=. .venv/bin/python research/run_drift_backtest.py               # 500 random draws, ~5 min
PYTHONPATH=. .venv/bin/python research/run_drift_backtest.py --ohlc-only   # drop close-only filings
```

Tests whether buying stocks that rallied on their results beats the universe
after costs, on the point-in-time events above. Nothing in the framework ranks
on the reaction.

The rule: after every filing whose reaction was *rallied* (≥1 ATR), buy at the
reaction-day close and sell at the close 20, 40 or 60 sessions later. The book
has 20 equal slots (5% of NAV per name, the position cap); a rally that finds
every slot taken, or the name already held, is skipped, and same-day rallies
fill slots in broadcast order. Idle capital sits in the NIFTY 500 (an index
fund, no cost charged), so the book is always fully in equities. 0.35% per
round trip, half on each side. A holding that stops trading keeps its last
price and is sold at it.

The gate: the 20-slot book's excess over the equal-weight point-in-time
universe, per calendar quarter, after costs, t ≥ 2 at 20 or 60 sessions.

It reports CAGR, worst drawdown, turnover and rallies skipped for each hold,
then compares the book with controls on the same entry dates, holds and slots
(500 draws each): a random universe member, a random same-day filer, and idle
capital in the equal-weight universe instead of the index. A per-filing view
shows each rallied stock's return over the hold, less costs and the
equal-weight universe.
