# Roadmap

Two parts: what was built and what it turned out to be worth, then what is
still wrong with it.

---

## Part 1 — Built

The plan was to move from a timing engine to an investing guide. That happened.
Every item below is implemented and tested.

### DONE · Cross-sectional momentum ranking

9-1 momentum (nine months excluding the most recent, because short-horizon
returns reverse), divided by realised volatility, gated above the 200 DMA.
12-1 is ranked alongside for comparison; it was the main ranking until
2026-10-04.
Monthly rotation with hysteresis so boundary names do not churn. Regime overlay
scales the number of positions down rather than going to cash.

`strategy/momentum.py`, `backtest/pit_engine.py`

### DONE · Fundamentals, from NSE rather than by hand

The original plan assumed a hand-typed sheet. NSE's XBRL filings turned out to
carry quarterly results, shareholding history and promoter pledge — so
`scripts/fetch_fundamentals.py` populates it automatically, with Yahoo filling the
ratios NSE does not publish. Quality score covers growth, profitability,
balance sheet, cash quality and governance.

`scoring/fundamentals.py`, `data/sources/nse_fundamentals.py`

### DONE · Lender rubric

Banks scored on asset quality (35%), ROA (25%), efficiency and provisioning
(30%), capital (10%). Debt/equity and CFO are excluded rather than
down-weighted — for a business that borrows to lend, they invert. NBFCs, which
file the ordinary schedule and so have no NPA data, get a separate path.

### DONE · Portfolio advisor

Buy / Add / Trim / Exit / Hold per holding, each with reasoning. The
distinction the design turns on: **trim** is a sizing correction on a holding
you keep, **exit** is a broken thesis and always the whole position.

`strategy/advisor.py`

### DONE · Core / satellite, and trade

Classified on business characteristics, never price direction. Rotating
unclassified holdings on rank fired exits on positions bought years earlier on
other reasoning -- a recommendation to liquidate 60% of the book on first run.
So only what you mark **trade** rotates on rank. **Satellite** (one to ten
years) takes red flags and sizing, and a review prompt when it leaves the
ranking; it is the default. Renamed 2026-10-19 from core / satellite / legacy,
where "satellite" meant the rotated bucket.

`strategy/buckets.py`

### DONE · Tax-aware execution

Delivers long-term shares first (12.5% against 20%), reading the long/short
split from a broker statement. Waits when a position is near the twelve-month
line. Will not trim into weakness. Splits large trims into tranches.

`strategy/tax.py`

### DONE · Risk monitoring

The v1 exit engine pointed at holdings instead of trades: stop levels,
trailing stops, drawdown from peak, score collapse — as review prompts, not
instructions.

`strategy/risk_monitor.py`

### DONE · Survivorship-free backtesting

Full-market bhavcopy: 3.84M rows, 2,607 days, 3,638 symbols including the 1,238
that no longer trade. The universe is rebuilt at every rebalance from stocks
actually trading and liquid on that date. ETFs and DVRs excluded by ISIN and
listed in `config/etfs.csv` with reasons.

`data/bhavcopy.py`, `data/instruments.py`

### DONE · Dashboard

Top-bar menus: Today, Portfolio, Ideas, Market, Research, and Data and jobs.
Each page states how far its signals held up in a backtest. Localhost only
([dashboard](dashboard.md)).

`ui/`

### DONE · Signals kept, changed, dropped

Kept the regime filter, ATR stops and R-multiple targets. Changed the Timing
Score from a trigger to a ranking input. Dropped the 50 DMA trend-break exit,
the RSI trim and the entry cooldown — all shown to hurt or add nothing.

### DONE · Scheduling, freshness and the results calendar

launchd jobs: Saturday (`scripts/run_weekly.py`), weekday evenings after the close
(`scripts/run_swing_daily.py`), and half-hourly F&O snapshots in market hours
(`scripts/install_schedule.py`). The data chip and a warning line on every page say
when a source is behind; Data and jobs runs whatever is stale. Results dates
for everything you follow are on Today and Quarterly results.

### DONE · Forward records

Every recommendation that can be scored later is written down before the
outcome is known, append-only: the advisor's verdicts (`book/decision_log.py`),
monthly momentum picks, every swing scan, the monthly F&O ranges, and the
daily Today ranges for every F&O underlying (`history/`).

### DONE · Today, expected ranges, sector checklist

Today gathers every decision from every page, plus next-session and
five-session ranges for Nifty and core holdings with their measured hit rate
([ranges](ranges.md)). Analyze a stock adds a sector checklist: the
long-term metrics that matter for that sector, and what to check by hand.

### DONE · Tested and shown with their verdicts

Swing setups, the F&O read, the quarterly results verdict, GARP / QARP and the
mutual fund states were each built, backtested and either shown calibrated or
kept as reading aids. 52-week high and low volatility failed a pre-registered
rule and were not built further. See [validation](validation.md).

### NOT BUILT · Telegram

Deliberately deferred: the dashboard and macOS notifications from the jobs
cover it.

---

## What it was worth

| | 9-year CAGR, avg of 4 start dates |
|---|---|
| Momentum rotation, 9-1 (main) | **23.0%** (20.0 - 24.4) |
| Momentum rotation, 12-1 (alongside) | 20.2% |
| NIFTY 500 index | 10.2% |
| Random-pick control | 9.3% |
| v1 signal rules *(retired)* | 1.3% |

After realistic Indian costs -- STT, stamp duty, exchange fees, GST, slippage
-- but **before tax** (most gains are short-term). Momentum beat all 10
random controls drawn from the same universe by about 15 points (4.9
standard deviations), held up in both halves of the period, and returned
28% on liquidity ranks 201-500, a universe none of its settings were tuned
on. **The fall is the weak point: -36% to -42%, against the index's -38%.**
Neither a trailing stop nor volatility targeting fixed it; both cost more
return than they saved.

*Corrected 2026-10-11:* earlier figures (11.4%, and everything before it)
ran on raw prices that read every split, bonus and demerger in a held stock
as a loss, and a ticker change or a spell in the trade-for-trade series as
a forced sale. See [data](data.md) and [validation](validation.md).
The live ranking was never affected.

Removing survivorship bias, measured on the old history, cost 14 points of
apparent return (30.8% → 16.9%) and dropped the random control from 17.2% to
5.0% -- the bias measured directly.

**Honest split: roughly 70% of this framework's value is monitoring and
discipline — red flags, stops, concentration, tax-aware selling. Perhaps 30% is
momentum alpha.** That should govern how much capital sits behind the momentum
side.

---

## Part 2 — What is still wrong

Ordered by how much they undermine the tool's usefulness. Reviewed
2026-10-19; what was fixed since the first version of this list is at the end.

### 1. The drawdown, not the return, is the real problem

−36% to −42%, against the index's −38%. The return is well clear of the index
(23% a year against 10%, on the continuous price history), but the fall is
the part most people cannot sit through.

**Tried, 2026-10-11:** volatility targeting (on liquidity ranks 201-500, an
untouched universe) cut volatility from 25% to 20% but cost about 6 points of
CAGR for 2.6 points off the average worst fall, with no better Sharpe. Trailing
stops: tight ones cut the fall and cost 3-9 points a year. Neither adopted
([validation](validation.md)). Still open: something that reacts faster
than a monthly, 60-day volatility estimate.

**Validation constraint:** the 2023–26 holdout is spent for momentum and the
v1 rules. Declare everything from today forward as live out-of-sample. (It is
still unseen for 52-week high and low volatility, which failed on train.)

### 2. Nothing else has an edge yet

Momentum is the only ranking that beat the index after costs on a
survivorship-free universe. Swing setups, OI walls / PCR / max pain, the
results verdict, GARP, QARP, 52-week high and low volatility did not. The one
other signal that survived, the post-results reaction drift (t=2.6), failed
its cost test as a long-only rule.

**Next:** let the forward logs build. Due for review in early November 2026:
the Today ranges (from 16 Oct), swing scans (from 13 Oct), the first scored F&O
cycle, and the intraday F&O snapshots. An untested idea still open: promoter
and insider buying, as an event study (needs NSE's disclosure feed first).

### 3. The universe has no market-cap floor

Price (₹20) and turnover (₹5 cr a day) floors are in place, which stops
penny stocks. A thinly held mid-cap pumped on volume can still rank. A cap on
distance above the 200 DMA was tested and left off: the results did not trend
as the cap tightened (`config/scoring.yaml`).

**Fix:** a free-float market-cap floor in `liquid_universe()`. Market cap is
on file only as total, not free float.

### 4. Quality scores still miss some figures

Six quarters are fetched now, so growth is a trend. But banks' capital
adequacy and CASA are not in NSE's XBRL or Yahoo: the sector checklist shows
them as "not on file". The LTCG exemption is spread across one plan's sales,
not tracked against gains already taken earlier in the financial year.

**Fix:** enter `car_pct` and `casa_pct` by hand or from RBI data; keep a
year-to-date realised-gains figure for the exemption.

### Fixed since the first version of this list

- **The advice was not executable.** Adds are now funded from the sells in
  the same plan, best first, and stop when the money runs out; the shortfall
  is shown (`strategy/advisor.py`).
- **The largest risk produced no action.** The sector cap trims every bucket,
  core included, as sizing with no view on the business.
- **Nothing recorded what happened.** `book/decision_log.py`, plus the forward
  records above.
- **Data went stale silently.** Scheduled jobs, the data chip, and a warning
  line on every page.
- **Penny stocks could rank first.** Price and turnover floors.

---

## Validation rules

Unchanged, and they are the reason the numbers above can be trusted.

- Every new signal goes through the harness before it reaches the digest:
  random controls across seeds, train/holdout, per-symbol table.
- **The bar is buy-and-hold of the same names**, never the index alone.
- Report failures as plainly as wins. A tool that only reports its successes is
  worse than no tool.
- Paper-trade before real money: the decision log and forward records are
  how; give them months, not weeks.
