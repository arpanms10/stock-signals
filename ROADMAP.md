# Roadmap

Two parts: what was built and what it turned out to be worth, then what is
still wrong with it.

---

## Part 1 — Built

The plan was to move from a timing engine to an investing guide. That happened.
Every item below is implemented and tested.

### DONE · Cross-sectional momentum ranking

12-1 momentum (twelve months excluding the most recent, because short-horizon
returns reverse), divided by realised volatility, gated above the 200 DMA.
Monthly rotation with hysteresis so boundary names do not churn. Regime overlay
scales the number of positions down rather than going to cash.

`strategy/momentum.py`, `backtest/pit_engine.py`

### DONE · Fundamentals, from NSE rather than by hand

The original plan assumed a hand-typed sheet. NSE's XBRL filings turned out to
carry quarterly results, shareholding history and promoter pledge — so
`fetch_fundamentals.py` populates it automatically, with Yahoo filling the
ratios NSE does not publish. Quality score covers growth, profitability,
balance sheet, cash quality and governance.

`fundamentals.py`, `data/sources/nse_fundamentals.py`

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

### DONE · Core / satellite / legacy

Classified on business characteristics, never price direction. **Legacy** was
not in the original plan and had to be added: defaulting unclassified holdings
to satellite meant rank-based exits fired on positions bought years earlier on
other reasoning, which produced a recommendation to liquidate 60% of the book
on first run. Legacy takes red flags but never rank.

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

`data/bhavcopy.py`, `instruments.py`

### DONE · Dashboard

Two sections — holdings with verdicts and trade plans, market with entry
levels. Localhost only, deploy button disabled.

`ui/`

### DONE · Signals kept, changed, dropped

Kept the regime filter, ATR stops and R-multiple targets. Changed the Timing
Score from a trigger to a ranking input. Dropped the 50 DMA trend-break exit,
the RSI trim and the entry cooldown — all shown to hurt or add nothing.

### NOT BUILT · Telegram, scheduling, event calendar

Deliberately deferred. See Part 2 — stale data is a real failure mode and
scheduling is the fix, but the advice needs to be executable first.

---

## What it was worth

| | 9-year CAGR |
|---|---|
| Momentum rotation | **14.5%** |
| NIFTY 500 index | 11.3% |
| Random-pick control | 1.8% |
| v1 signal rules (retired) | 1.3% |

Momentum sits 8.4 standard deviations above a random control drawn from the
same universe, so the ranking carries real information. But the edge is 3.2
points with a **−44% maximum drawdown against the index's −38%**, and a Sharpe
of 0.64.

Removing survivorship bias cost 14 points of apparent return (28.7% → 14.5%)
and dropped the random control from 17.7% to 1.8%. That collapse is the bias
measured directly.

**Honest split: roughly 70% of this framework's value is monitoring and
discipline — red flags, stops, concentration, tax-aware selling. Perhaps 30% is
momentum alpha.** That should govern how much capital sits behind the momentum
side.

---

## Part 2 — What is still wrong

Ordered by how much they undermine the tool's usefulness.

### 1. The advice is not executable

The advisor suggests roughly ₹4.5L of adds against ₹1.1L raised from sells.
There is no cash constraint anywhere in `strategy/advisor.py`. A plan that
cannot be executed is a ranked wish-list.

**Fix:** rank adds by conviction, fund them from the sells in the same plan,
stop when the money runs out. Show the shortfall explicitly rather than
implying every line can be actioned.

### 2. The largest risk in the book produces no action

Financial Services sits at ~27% against a 25% cap, across eight holdings. The
sector rule in `advise()` only trims **satellites** — and all eight are core or
legacy. The one concentration the framework identifies, it does nothing about.

**Fix:** enforce the sector cap across all buckets. A core holding can be
trimmed for concentration without any view on the business — that is exactly
what the trim/exit distinction exists to express.

### 3. Nothing records what actually happened

The roadmap called for three months of paper trading. There is no mechanism to
log what the advisor said on a date and what the stock did afterward, so live
behaviour can never be compared against the backtest.

**Fix:** a decision log — every run appends its verdicts with date and price to
a table. Three months of that is worth more than ten years of backtest.

### 4. The drawdown, not the return, is the real problem

−44% is worse than the index. A 3.2-point edge does not obviously compensate.

**Fix:** volatility targeting — scale gross exposure to hit a target portfolio
volatility. Best-evidenced improvement to momentum's Sharpe, and a structural
change rather than a threshold tweak.

**Validation constraint:** the 2023–26 holdout is spent. It was used for the v1
exit experiments, and momentum was then run across the full period on the
argument that it is a different strategy class. That argument is defensible but
not clean. Declare everything from today forward as live out-of-sample and stop
touching 2023–26.

### 5. The universe can admit junk

Top-200 by turnover has no market-cap or price floor. A thinly-held smallcap
pumped for a month is precisely what ranks first on risk-adjusted momentum.

**Fix:** a minimum price and market cap in `liquid_universe()`.

### 6. Quality scores are shallower than they look

Growth needs five quarters of results; one is on file. So "quality 96" is a
snapshot of ratios, not a trend — and trend is what separates a good business
from a good quarter. Banks have no capital adequacy at all; it is not in NSE's
XBRL.

**Fix:** fetch 5–8 quarters per symbol. Enter `car_pct` by hand or from RBI
data. Track the ₹1.25L LTCG exemption across the year instead of applying the
rate flat.

### 7. Data goes stale silently

Every refresh is manual. A dashboard showing three-week-old prices with no
warning is a genuine failure mode, and the one most likely to cause a bad
decision.

**Fix:** a `launchd` job at 16:30 IST on trading days, and a staleness banner
in the UI when the newest bar is more than a few sessions old. Silence must
never mean "nothing happened".

---

## Validation rules

Unchanged, and they are the reason the numbers above can be trusted.

- Every new signal goes through the harness before it reaches the digest:
  random controls across seeds, train/holdout, per-symbol table.
- **The bar is buy-and-hold of the same names**, never the index alone.
- Report failures as plainly as wins. A tool that only reports its successes is
  worse than no tool.
- Paper-trade before real money — which needs item 3 above to exist first.
