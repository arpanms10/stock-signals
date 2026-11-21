# Holding data

**Portfolio › Holding data** shows the financial and market figures behind
each holding. Each number records where it came from. The page only describes
the data. It does not rank, score or recommend anything yet; the scorecard is
Phase 3, and it stays descriptive until its backtest passes.

Refresh it with the page's **Refresh data** button, or from the command line:

```bash
.venv/bin/python scripts/refresh_holding_data.py                  # every holding
.venv/bin/python scripts/refresh_holding_data.py --symbols TCS INFY
.venv/bin/python scripts/refresh_holding_data.py --max-age-days 7 # skip recent ones
```

A full refresh of about 30 holdings takes under two minutes.

## Sources, in order of trust

| Source | What it gives | Confidence |
|---|---|---|
| NSE XBRL filings | Quarterly results, balance sheet (half-yearly), cash flow (year to date), shares outstanding. Consolidated when at least 4 quarters exist; banks use standalone | high |
| Calculated from filings | EBIT, EBITDA, margins, FY sums, TTM, ROE, ROCE, debt/equity, interest cover, CAGR | high |
| Yahoo Finance | Annual statements (older years), dividends | medium; high when NSE agrees within 3%, low when it disagrees |
| NSE bhavcopy / signals.db | Close, 52-week high/low, average volume, 1-year volatility, beta vs NIFTY 500, price returns | high |
| NSE shareholding pattern | Promoter %; non-promoter % as a stand-in for free float | high / low |

## What each value records

Each row in `financial_metric` records:

- the value and its unit (₹ cr, ₹, %, x);
- the period: quarter, half, fy, ttm, instant (balance sheet date) or point
  (market date);
- the method: reported, calculated or estimated;
- the status: ok, missing or not_applicable;
- the confidence, the source, a reference link, a note, and when it was
  retrieved.

## Rules

- **Missing is not zero.** A value that was looked for and not found is
  stored as missing and shows "—".
- **Not applicable is labelled.** For example, EBITDA and current ratio for
  banks and NBFCs are stored as not_applicable and show "n/a".
- **Growth compares like with like.** It uses the same source at both ends.
  EPS is adjusted for splits and bonuses, and Yahoo EPS is never used for
  growth.
- **Foreign-currency statements stay separate.** Some Yahoo statements come
  in USD (Infosys, for example). They are stored as "USD cr" with low
  confidence and left out of comparisons.
- **A failed source keeps its old rows.** Every attempt is logged in
  `retrieval_log` as ok, empty, failed or mismatch. The freshness check then
  shows those rows ageing.
- **NSE vs Yahoo differences are flagged, not resolved.** Most come from
  definitions: standalone vs consolidated, gross vs net revenue, excise. The
  page shows the count, and the provenance table shows both values.

## Freshness

Freshness is judged only on the latest value of each series; older periods
are history.

| Value | Stale when |
|---|---|
| Quarter or TTM | The next quarter's results are more than 60 days past and not on file |
| Balance sheet | The same, allowing one more quarter (balance sheets are filed half-yearly) |
| FY | The next year's results are more than 60 days past |
| Market figures | Older than the latest market session |
| Dividends (TTM) | Only by retrieval age: the period ends on the last payout date |
| Any value | Not re-checked in 14 days |

The Overview's **Data** column summarises market cap, TTM revenue and PAT,
close and dividends: fresh, stale, or none (never refreshed).

## REITs and InvITs

Only distributions and distribution yield are fetched. NOI, occupancy, NAV per
unit, net debt to assets, WALE and top-tenant share appear only in each trust's
own reports. They are stored as missing ("pending manual input") and are never
guessed. Manual input comes with Phase 3.

## Not yet

- **Market-cap band** still uses NSE index membership as a proxy for the
  AMFI list: Nifty 100 = large, Midcap 150 = mid. The AMFI file could not be
  reached.
- **Pledge, auditor and governance events** come in Phase 3, with the
  governance override.
