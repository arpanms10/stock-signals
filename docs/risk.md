# Risk & plan

**Portfolio › Risk & plan** answers three questions about your operating
equity (REITs and other sleeves are left out):

1. How has today's mix behaved? *(historical, backward-looking)*
2. What is it exposed to? *(themes, overlaps, flags, liquidity)*
3. Where should new money go to move toward 70:30 within your limits?
   *(a simulation: nothing is written, nothing is ordered)*

Settings: [config/risk.yaml](../config/risk.yaml). Targets, position caps and
the sector limit: [config/core_satellite.yaml](../config/core_satellite.yaml).
Code: `strategy/portfolio_risk.py`, `strategy/optimiser.py` (pure), and
`book/risk_view.py` (reads your book).

## 1. Historical risk

Your **current weights held constant** over the last three years, using
split-adjusted daily closes from `signals.db`, against the NIFTY 500.

| Figure | Meaning |
|---|---|
| Volatility | Standard deviation of daily returns, annualised (× √252) |
| Beta | How much the mix moved per 1% move in the NIFTY 500 |
| Maximum drawdown | Largest fall from a peak, with the peak and trough dates |
| Worst month | Worst calendar-month return |
| 1-day loss, 95% | On 1 day in 20 the mix lost at least this much |
| Growth of ₹100 | Weekly path of the mix and the benchmark |

What this is not: a forecast, or your actual past returns (you didn't hold
these weights three years ago). Caveats:

- **Price returns.** Dividends are left out of both the mix and the index.
- **Demergers** show as a fall in the parent (ITC on the ITC Hotels
  record date), since the new company isn't in the constant-weight mix
  until it lists.
- A holding that listed later (JSWCEMENT, ITCHOTELS) counts from its
  listing; on earlier days the others are re-weighted to sum to 100%.
- A holding with no price history is left out and named on the page.

## 2. Exposures and flags

- **Correlation:** the 10 largest holdings over one year; pairs at 0.70 or
  above move together: one risk, two names.
- **Themes** cut across NSE sectors by convention (a bank is rate-sensitive,
  a metal maker commodity-exposed). Each has a review level in risk.yaml.
- **Overlaps:** sector groups held through more than one company.
- **Risk flags:** weight in holdings with a governance flag, a rich valuation
  (≥ 80th percentile of own 5-year band *and* ≥ 1.3× the peer median), a
  trailing-twelve-month loss, slow liquidity, or an exit-candidate tier.
- **Effective number of holdings:** 1 / Σw². Twenty equal positions = 20;
  one big position pulls it down.
- **Satellite mix:** satellite categories from the scorecard against the
  guideline in core_satellite.yaml.
- **Liquidity:** days to sell each position trading at most 20% of its
  20-day average traded value; over 5 days is flagged.

## 3. The plan for new money

Enter a monthly amount and a number of months. The plan is built from rules,
in this order:

1. **Budget:** new money C = monthly × months.
2. **Buckets:** core 70% / satellite 30% of operating equity after C.
3. **Hold-only:** no new money into a holding with an exit-candidate tier,
   a governance flag, a rich valuation, or a position already at its
   liquidity limit. It keeps its current value as its target.
4. **Targets:** each bucket's budget is shared in proportion to what you
   hold now (your existing conviction), never above the position cap
   (core 8%, satellite 4%, small cap 2% of the whole book) or the liquidity
   limit. What a capped holding can't take flows to the others.
5. **Sectors:** a sector above 25% is scaled back; the budget freed flows to
   other holdings.
6. **Trims:** only to bring a holding under its cap or a sector under its
   limit, never on score or rank, and only when new money alone can't fix it
   within 12 months. Breaches under 0.5 points and trades under ₹2,000 are
   ignored. Each trim shows its estimated tax (from your average cost and
   long/short split; Phase 6 adds lot-level detail).
7. **Buys:** new money goes to holdings below target in proportion to the
   shortfall.

**Why no scorecard tilt.** The plan uses the scorecard only as a gate (step
3), never to weight one holding above another, because the score hasn't been
backtested. Earlier momentum, swing and GARP scores looked convincing and
failed when tested.

**Blank buckets.** A holding with no bucket counts as satellite and is never
trimmed: its 4% cap may not be the cap you mean. The page offers a what-if
that fills blank buckets with the scorecard's suggested role. Your holdings
file is not changed; set `bucket` there to make a role yours.

**Months to 70% core** assumes every rupee of new money goes to core and
nothing is sold.

## Not done

- No optimiser over expected returns or covariances. Allocation is a
  transparent water-fill with explicit rules (no scipy, nothing to tune).
- No forward-looking risk (stress scenarios, factor exposures).
