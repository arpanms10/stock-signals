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
   rows. Found 2026-10-04, when every quality score turned out to rest on
   results nearly two years old. `result_filings()` in
   `data/sources/nse_fundamentals.py` reads both and merges them: integrated
   filings win where both cover a quarter, and a revised filing replaces its
   original.

Ingestion flags any unexplained move above 35% as a suspect bar.

A sixth trap was in the ingestion itself, found 2026-10-08: **a failed
download was recorded as a day with no data, and never retried.**
`fetch_day()` turned any error (a dropped connection, throttling) into an
empty day, and `ingest_range` marks every day it handles as done, so the
next run skipped it. On top of that, the old-format archive is missing
some early-2024 dates (2024-06-12 answers 404) that exist in the UDiFF
format, which was only tried from July 2024. Together they left **249
weekday trading days, about 9% since 2016, recorded as empty.**

Fixed: `fetch_day` tries both formats. It returns an empty day only when
neither has a file, and `None` when a request failed. A failed day is
never recorded, and a missing one is recorded only once it is a week old,
since a recent file may not be published yet. `repair_false_empty_days()`
cleared the false markers, and a re-ingest refilled 227 of the 249 days.
The other 22 exist in neither format, including 10 days in 2020–23 when
the index traded: gaps in NSE's own archive. Backtest results computed
before this fix ran on the holey history.

### The backtest's market history was not continuous (found 2026-10-10)

`market.db` (the full-market bhavcopy behind `run_pit_momentum.py`) held
raw EQ-series closes and nothing else, and the backtest read them as they
were. Five gaps, each turning something that happened to a holding into a
loss that never happened:

| gap | size | what the backtest did | fix |
|---|---|---|---|
| A held stock with no price that day | any day it didn't print | valued it at **zero** until the 10-day delisting rule sold it; a fake -47% in June 2021 | valued at its last close |
| Splits and bonuses | 790 in NSE's feed; 851 overnight falls >30% | kept the share count, so a 1:1 bonus became a 50% loss (HDFCBANK Aug 2025, BAJFINANCE Jun 2025, BRITANNIA, DIXON, ...), and the stock looked crashed to the ranking for a year | `market_actions`: every split/bonus since 2016, back-adjusted in `load_wide()` |
| Demergers | 99 | the value handed to the spun-off company was a loss (RAYMOND, TMPV, VEDL, SKFINDIA) | `market_demergers`: the ex-date fall is adjusted out. The new company's later returns are not captured |
| Ticker changes | 304 (ZOMATO -> ETERNAL, CADILAHC -> ZYDUSLIFE) | a delisting: force-sold at -5%, and the new ticker had no history to rank for a year | tickers sharing an ISIN that never traded together are one series under the current name |
| Trade-for-trade series (BE/BZ) | 494k rows; 487 spells in liquid names | the stock vanished, so it was force-sold at -5% (SUPPETRO, TATASTLLP, MAGMA in 2021) | `market_t2t`, a separate table that only prices holdings. Ranking and every universe stay EQ-only |

NSE's feed also misses some actions (trap 4 above). For the **backtest
only**, `infer_unrecorded_actions()` treats a remaining fall below 0.55 as a
split when the traded quantity also settles at least 1.3x higher -- a split
multiplies the share count, a crash does not hold volume at a new level.
That found 111, 16 in liquid names (JSWSTEEL Jan 2017, KARURVYSYA,
PHILIPCARB, CAPLIPOINT, ...). Real crashes checked against it -- YES Bank
2020, DHFL 2018, Jet Airways 2019, ZEEL 2024, the Reliance ADAG names 2019
-- are not caught. A version that also took falls near 2/3 caught them, so
a 1:2 bonus missing from the feed is left alone. The live signals path is
unchanged and still quarantines rather than infers.

Still not modelled: dividends (returns are price-only, so understated by
about 1% a year; the NIFTY 500 benchmark is a price index too), rights
issues, and the spun-off company in a demerger.

The live ranking was not affected: it reads split-adjusted prices from
`signals.db`. Every `run_pit_momentum.py` figure before 2026-10-10 was.
`run_market_ingest.py` now keeps all four tables current.

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


# Freshness

Staleness is measured in **trading sessions**, not calendar days -- a Monday
reading of Friday's close is current, while the same one-day gap midweek is
not. Prices, market data and fundamentals are checked separately, since
fundamentals age in quarters: a sheet older than a results season describes a
company that has since reported.

Warnings appear at the top of the advisor and as a banner in the dashboard.


# Refresh price data

```bash
PYTHONPATH=. .venv/bin/python run_backfill.py --years 10
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
stock). `--per-symbol` forces the old behaviour for everything. A day whose
file fails to download stops the daily pass there, so no hole is left
behind it; those symbols fall back to per-symbol fetching. A day with no
file (a holiday, or today before NSE publishes in the evening) is skipped.

The dashboard runs the same command from **Refresh prices** on the Stocks
page or on Data and jobs (also offered by the data chip when prices are
stale), and reloads the page when it finishes.

---

See also: [validation](validation.md) — removing survivorship bias cost 14 points of apparent return.
