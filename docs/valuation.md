# Valuation

**Portfolio › Valuation** shows what each holding's price implies, and gives
bear, base and bull values. Every value is a **scenario** built from the
assumptions in `config/valuation.yaml`, **not a price target**: change an
assumption and every value moves with it.

Valuation uses only data already on disk. Nothing is fetched.

```bash
.venv/bin/python scripts/value_holdings.py
.venv/bin/python scripts/value_holdings.py --export data/results/valuations.json
```

## Four reads

| Read | Question it answers | How |
|---|---|---|
| Own band | Is today's multiple high or low for this stock? | Month-end multiple over the last 5 years. Each month uses only results filed by then (45 days after a quarter, 60 after March), so there is no look-ahead |
| Peers | Is it high or low for its industry? | The same multiple across the same NSE industry and kind of business (banks with banks, NBFCs with NBFCs) in the Nifty 200 filings. Needs at least 3 peers |
| Reverse DCF | What does the price assume? | The growth that makes today's price earn the cost of equity, compared with the growth actually delivered |
| Scenarios | What is it worth if…? | Bear, base and bull values from explicit assumptions |

Market cap on any date is the raw close × the share count filed by then. The
share count is carried forward through later splits and bonuses.

If a stock was renamed (ZOMATO → ETERNAL), its earlier price history is joined
on when the market data shows a clean hand-over: one symbol stops trading the
session before the other starts, at a close within 3%.

## The method suits the business

| Business | Method | Headline multiple |
|---|---|---|
| Free cash flow positive in 4 of 5 years | DCF, with earnings × own P/E band as a cross-check | P/E |
| Other companies | Next year's earnings × own P/E band | P/E |
| Loss-making, or too little P/E history | Revenue × own price/sales band (low confidence) | P/S |
| Cyclicals (cement, metals, chemicals, energy) | Mid-cycle (5-year average) EBITDA × own EV/EBITDA band | EV/EBITDA |
| Banks and NBFCs | Justified P/B = (ROE − g) / (r − g) on the 3-year ROE | P/B |
| REITs and InvITs | Gordon model on the trailing distribution | Distribution yield |

DCF is never forced. Where free cash flow is unreliable, the page says why it
used another method.

## Assumptions (`config/valuation.yaml`)

| Assumption | Base | Bear / bull |
|---|---|---|
| Cost of equity | risk-free 6.5% + premium 5.5% = 12% | +1.5 / −1.0 pts |
| Terminal growth | 5% | 4% / 5.5% |
| Revenue growth, years 1–5 | own 5-year CAGR (else 3-year), capped at 20%, floor 0% | × 0.5 / × 1.3 |
| Growth, years 6–10 | fades in a straight line to terminal growth | |
| FCF margin | 3-year average | × 0.8 / × 1.1 |
| Multiples | own 5-year median | 20th / 80th percentile |
| Lender ROE | 3-year average | −3 / +2 pts |
| REIT required return, distribution growth | risk-free + 3%, 3% | +3.5% and 2% / +2.5% and 4% |

Update `risk_free_pct` when the 10-year G-sec yield moves meaningfully. Each
scenario lists the assumptions that produced it.

**Margin of safety** = (base − price) / base.

## How to read it

- **A low own-band percentile isn't a buy signal.** A stock can be cheap
  against its past because its prospects fell. Read it with the growth the
  price implies.
- **Single-stage models are strict on fast growers.** At a 12% cost of equity,
  a DCF or justified P/B values today's growth only for 5 years and then fades
  it. Highly rated consumer and banking names therefore show large negative
  margins of safety. The reverse DCF says how much growth the market is paying
  for, which is usually the more useful read.
- **Lenders with depressed ROE get very low values.** The 3-year average ROE
  is used, and no recovery is assumed. If ROE is at or below long-run growth,
  no value is given; a holding company is the usual case.
- **REIT and InvIT values use yield only.** NAV is not on file; it is published
  only in the trusts' own reports.

## In the scorecard

Valuation's 15 points in the [scorecard](scorecard.md) come from here:

| Criterion | Points | 0 points at | Full points at |
|---|---:|---|---|
| Own-band percentile of the headline multiple | 5 | 90th | 20th |
| Headline multiple / peer median | 4 | 1.6× | 0.8× |
| Margin of safety, base scenario | 3 | −40% | +20% |
| Implied minus delivered growth (ROE for lenders) | 3 | +8 pts | −2 pts |

Nothing here is backtested. Like the scorecard, it describes; it does not rank.
