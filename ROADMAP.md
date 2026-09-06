# Roadmap: from timing engine to investing guide

## Review of where v1 landed

The infrastructure works and is tested: clean adjusted data, quality
quarantine, honest backtest with Indian costs, shuffle controls, train/holdout
discipline. Keep all of it.

The strategy class is the problem. Every technical variant failed the same
way, and the TCS trade log shows why at human scale: 57% of trades won, the
strategy captured 1.3% of an 85% rise, because it was in the market 30% of the
time. On large caps with strong upward drift, **being out of the market is the
dominant cost**, and better entry signals cannot fix a design whose problem is
absence.

Three conclusions drive everything below:

1. **Stop trying to time.** Move to strategies that stay invested and rotate
   by rank. Cross-sectional momentum is the one technical factor with robust
   evidence globally and in India (NSE's own NIFTY200 Momentum 30 index has
   outperformed its parent for years). It never goes to cash on a stock-level
   signal -- it swaps weaker names for stronger ones monthly.
2. **Bring fundamentals in.** They were cut from v1 for data reasons, not
   because they don't matter. For a small watchlist, a quarterly hand-entered
   sheet is entirely workable and turns your manual analysis into something the
   tool can score, rank and track over time.
3. **Repurpose the exit machinery as a risk monitor.** Stops, trailing stops,
   score collapse and regime detection are tested and correct. Their failure
   was in *driving trades*; as *alerts on a buy-and-hold book* they are exactly
   what a newer investor needs.

Plus one framing rule the tool should state on its own output: **core +
satellite**. The evidence says an index fund beats everything tested here.
The framework's job is the satellite -- a deliberately sized slice -- not the
whole portfolio.

---

## Tier 1 -- What to own (strongest evidence, build first)

### 1a. Cross-sectional momentum ranking
- 12-month return skipping the most recent month (12-1), the standard
  definition; optionally blend with 6-1.
- Rank the universe monthly; hold the top N (say 10-15) equal-weighted;
  rebalance monthly, swapping only names that drop out of the top band
  (hysteresis: enter top 10, exit below top 20 -- cuts turnover).
- Volatility-scaled variant: divide return by realised volatility so a calm
  30% beats a violent 30%. Evidence says this improves Sharpe materially.
- **Regime overlay kept**: momentum crashes hard in sharp reversals (2020).
  When the NIFTY 500 is below its 200 DMA, scale exposure down rather than
  to zero.
- Universe should be NIFTY 200 or 500, not NIFTY 50: momentum needs
  dispersion to rank across.

### 1b. Fundamentals sheet + Quality score
`config/fundamentals.csv`, one row per stock per quarter, hand-entered from
the results you already read:

| symbol | quarter | revenue | pat | cfo | roe | roce | debt_equity | promoter_pct | pledge_pct | pe |

Tool computes the Quality Score from the original plan:
- Growth (revenue and EPS CAGR, consistency)
- Profitability (ROE, ROCE, margin trend)
- Balance sheet (D/E, interest cover)
- **Cash quality: CFO/PAT** -- the single best Indian-market screen for paper
  profits
- **Promoter holding change and pledge %** -- India-specific, loud
  pre-collapse signal
- Valuation vs the stock's own 5-year history, not an arbitrary threshold

Banks and NBFCs flagged and excluded from the generic score until a lender
rubric exists.

### 1c. Combined rank
Final rank = momentum rank x quality gate. Quality below a floor removes a
stock from candidacy regardless of momentum. This is the "what to own" list.

---

## Tier 2 -- How much (portfolio construction)

- **Kite holdings import** -- connect the MCP or hand-write `holdings.json`.
  Everything in this tier is about *your* book, so it needs the book.
- **Target weights**: equal weight by default, volatility parity as an
  option (size inversely to volatility so each position risks similar
  amounts -- the position-sizing logic already does this per trade).
- **Drift report**: current weight vs target, flag anything more than X%
  off, suggest the rebalancing trades (as suggestions -- never executed).
- **Concentration checks**: single-stock cap, sector cap, correlation
  clusters (three IT stocks are one bet, not three).
- **Core/satellite split**: state how much of total capital the framework
  governs and show the rest as index exposure.

---

## Tier 3 -- When to worry (risk monitoring, repurposed exits)

The existing exit engine, pointed at holdings instead of trades:
- Hard and trailing stop levels per holding, shown daily; alert on breach.
  Whether to act is yours -- the alert exists so a 40% drawdown never arrives
  unnoticed.
- Score-collapse and death-cross flags as *review prompts*, not sells.
- Portfolio drawdown from peak; regime state; days since regime changed.
- **Event calendar** from NSE announcements: results dates, dividend
  ex-dates, upcoming splits/bonuses/rights on holdings, AGM.
- **India-specific risk flags** from free NSE data:
  - promoter pledge changes (shareholding pattern, quarterly)
  - bulk and block deals in your names
  - insider trading disclosures (SAST/PIT filings)
  - **delivery %** -- already in the price data and unused; a rising
    delivery share on up-days suggests institutional accumulation, on
    down-days distribution. Untested; worth testing because it is free.

---

## Tier 4 -- Signals worth keeping, changing, or dropping

**Keep, with evidence**
- Regime filter (index vs its 200 DMA) -- reduces drawdown in every test.
- ATR-based stops and R-multiple targets -- tested, correct, useful as
  levels even when not driving trades.
- Relative strength -- extend to *vs sector* as well as vs index.

**Change**
- Timing Score becomes a *ranking* input, not a trigger. Its buckets are
  fine; its use as a cross-60 entry signal is what failed.
- Volume bucket: add delivery % once tested.

**Drop**
- 50 DMA trend-break exit -- proven to fire on noise and cut winners.
- RSI-based trim -- no evidence it adds anything; adds turnover.
- Entry cooldown -- irrelevant in a monthly-rebalance design.

---

## Tier 5 -- Delivery (now worth building)

Held back while the tool was a failed trader; worth it once it is a monitor:
- Telegram daily digest: holdings status, any stop breach, upcoming events,
  monthly rebalance suggestion on rebalance day.
- Streamlit dashboard: rank table, holdings vs targets, drift, drawdown,
  event calendar, backtest results, editable watchlist and fundamentals grid.
- `launchd` job at 16:30 IST on trading days with a failure notification --
  silence must never mean "nothing happened".

---

## Validation rules for everything above

- **Every new signal goes through the harness before it reaches the digest.**
  Shuffle controls across seeds, train/holdout, per-symbol table.
- **The 2023-2026 holdout is spent for timing-rule variants.** For the
  momentum strategy -- a different strategy class -- validate on a different
  universe (NIFTY 200/500 ex-NIFTY 50) as the out-of-sample set, and treat
  the NIFTY 50 result as in-sample.
- **The bar is always buy-and-hold of the same names**, not the index.
- **Paper-trade the rebalance for three months** before real money.
- Report failures as plainly as v1 did. A tool that only reports wins is
  worse than no tool.

---

## Suggested order

1. Fundamentals sheet + Quality score (needs your input; everything else
   can be built while you fill it)
2. Momentum ranking on NIFTY 200, validated on the harness
3. Kite holdings import, drift and concentration report
4. Risk monitor + event calendar + India risk flags
5. Delivery layer
6. Delivery % and sector relative strength as *tested* additions
