# Data

Sources, the NSE traps that fail silently, and staleness detection.

[← back to the README](../README.md)

---

Five NSE traps handled here, all of which fail silently rather than raising:

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
5. **Quarterly results moved endpoints.** From the quarter ended March 2025,
   results are filed as SEBI's "Integrated Filing" and NSE publishes them on
   `/api/integrated-filing-results`. The old `/api/corporates-financial-results`
   still answers, but stops at the December 2024 quarter -- no error, no new
   rows. `result_filings()` in
   `data/sources/nse_fundamentals.py` reads both and merges them: integrated
   filings win where both cover a quarter, and a revised filing replaces its
   original.

Ingestion flags any unexplained move above 35% as a suspect bar.

**Failed downloads are never recorded as empty days.** `fetch_day()` tries
both NSE archive formats (old-format and UDiFF). It returns an empty day only
when neither has a file, and `None` when a request failed. A failed day is
never recorded, and a missing one is recorded only once it is a week old,
since a recent file may not be published yet. `repair_false_empty_days()`
clears any false empty markers. A few days exist in neither format: gaps in
NSE's own archive.

### Continuous market history for the backtest

`market.db` (the full-market bhavcopy behind `research/run_pit_momentum.py`)
holds raw EQ-series closes. Five things would turn an event in a holding into
a loss that never happened, and each is handled:

| gap | what would go wrong | how it is handled |
|---|---|---|
| A held stock with no price that day | valued at zero until the delisting rule sold it | valued at its last close |
| Splits and bonuses | a 1:1 bonus read as a 50% loss, and the stock looked crashed to the ranking | `market_actions`: every split/bonus since 2016, back-adjusted in `load_wide()` |
| Demergers | the value handed to the spun-off company read as a loss | `market_demergers`: the ex-date fall is adjusted out. The new company's later returns are not captured |
| Ticker changes (ZOMATO -> ETERNAL) | read as a delisting, and the new ticker had no history to rank | tickers sharing an ISIN that never traded together are one series under the current name |
| Trade-for-trade series (BE/BZ) | the stock vanished and was force-sold | `market_t2t`, a separate table that only prices holdings. Ranking and every universe stay EQ-only |

NSE's feed also misses some actions (trap 4 above). For the **backtest
only**, `infer_unrecorded_actions()` treats a remaining fall below 0.55 as a
split when the traded quantity also settles at least 1.3x higher -- a split
multiplies the share count, a crash does not hold volume at a new level.
Genuine crashes (YES Bank 2020, DHFL 2018) are not caught by this rule. The
live signals path quarantines rather than infers.

Not modelled: dividends (returns are price-only, so understated by about 1% a
year; the NIFTY 500 benchmark is a price index too), rights issues, and the
spun-off company in a demerger.

The live ranking reads split-adjusted prices from `signals.db`.
`scripts/run_market_ingest.py` keeps all four tables current.

**OBV is a cumulative sum from an arbitrary origin**, so its percent change is
not well defined -- near a zero crossing it explodes, and the value depends on
how much history is loaded. The signals use `obv_pressure` instead: net
directional volume over n bars as a fraction of volume actually traded, which
is scale-free and origin-independent. Tests assert the
value is identical however far back the data starts.

**How much history do signals need?** 250 trading days (~1 year). Every
indicator is bit-identical from there onward -- the longest lookback is the 200
DMA plus its 21-bar slope, and the 52-week high needs 250. `--years 2` is ample
for daily signals. The 10 years exists for the backtest, which needs multiple
market regimes and enough trades to mean anything.


# Freshness

Staleness is measured in **trading sessions**, not calendar days -- a Monday
reading of Friday's close is current, while the same one-day gap midweek is
not. Prices, market data and fundamentals are checked separately, since
fundamentals age in quarters: a sheet older than a results season describes a
company that has since reported.

Warnings appear at the top of the advisor and as a banner in the dashboard.


# Refresh price data

```bash
PYTHONPATH=. .venv/bin/python scripts/run_backfill.py --years 10
```

Incremental and safe to re-run. With no arguments it refreshes your
watchlist, your holdings and every symbol already in the price table. Add
`--symbols RELIANCE TCS` to limit it.

**How it fetches.** Recent gaps come from NSE's full daily market file
(`sec_bhavdata_full`): one download per missed trading day covers every stock,
with the same fields the per-symbol history returns (open, high, low, close,
previous close, VWAP, volume, trades, delivery quantity and %). Corporate
actions come in one request for the whole window. Catching up four days for
~500 symbols takes about 20 seconds instead of 30–50 minutes.

The per-symbol path is still used for what the daily files can't cover:
new symbols, gaps longer than 30 sessions, the back-fill to `--years`, and
any symbol more than 5 sessions behind the rest (usually a suspended
stock). `--per-symbol` forces per-symbol fetching for everything. A day whose
file fails to download stops the daily pass there, so no hole is left
behind it; those symbols fall back to per-symbol fetching. A day with no
file (a holiday, or today before NSE publishes in the evening) is skipped.

The dashboard runs the same command from **Refresh prices** on the Stocks
page or on Data and jobs (also offered by the data chip when prices are
stale), and reloads the page when it finishes.

---

See also: [validation](validation.md) for how the backtests use this data.
