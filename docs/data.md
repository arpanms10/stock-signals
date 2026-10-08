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

Incremental and safe to re-run — it fetches only the gaps, at both ends of what
is already stored. Add `--symbols RELIANCE TCS` to limit it.

---

See also: [validation](validation.md) — removing survivorship bias cost 14 points of apparent return.
