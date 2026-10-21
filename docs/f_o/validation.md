# F&O validation

How to test what the option chain says about where price ends up by expiry,
and how far it moves.

[← F&O overview](README.md) · [← back to the README](../../README.md)

---

```bash
PYTHONPATH=. .venv/bin/python scripts/run_fno_backtest.py --ingest         # incremental; the Saturday job runs it
PYTHONPATH=. .venv/bin/python scripts/run_fno_backtest.py --ingest-old     # 2019-2023 index history, once (~1 hour)
PYTHONPATH=. .venv/bin/python scripts/run_fno_backtest.py
```

Every table goes to `reports/fno/validation_<date>.md`, one row per
observation to `reports/fno/validation_obs.csv`, and the hit rates the live
view quotes to `config/fno_calibration.json`.

## Method

**Observations.** Every F&O underlying's chain, read from NSE's end-of-day
F&O bhavcopy:
- **monthly expiries:** 20, 10 and 5 trading sessions before expiry;
- **weekly index expiries:** 4, 2 and 1 sessions before.

Each chain goes through `strategy/fno.py`, the code the live view runs, and
is compared with the expiry-day close and the daily closes and highs/lows in
between. Sessions count NSE's holidays. Stock observations spanning a split
or bonus are dropped ([data](data.md)). Stock history starts in 2024, because
older files have no underlying price; index history goes back to 2019 from
the index's own record.

**A null for every hit rate.** "Support held 76% of the time" means nothing
alone, because a level far enough below spot always holds. Each level is
compared with how often a level **the same distance from spot** held, judged
against every observation's actual outcome (same group, same offset). The
gap is the **edge**, in percentage points. A stricter second null measures
distances and moves in units of each observation's own straddle.

**Two robustness checks for anything that looks like an effect.**
- **Split by time:** an earlier period against a later one.
- **Within-date comparison:** compare stocks against each other *on the same
  date*. Thousands of stocks on one date share one market move, so a pooled
  pattern can come from a handful of dates. An effect has to survive both to
  be used.

## What is tested

| Test | Question |
|---|---|
| **Support and resistance** | Did the OI walls hold more often than any level the same distance from spot? |
| **Walls as targets** | Were the walls reached more often than any level the same distance away? |
| **PCR** | Did the share of rises by expiry change with PCR, pooled and within dates? Stocks are read against stock norms (bearish < 0.5, bullish > 0.8, stretched > 1.05); index PCR uses 0.7 / 1.2 / 1.6 |
| **Max pain** | Was it closer to the expiry close than today's price more than half the time? |
| **The range** | How often did the close finish inside spot ± the ATM straddle, against the null? In theory ±1 straddle is about ±0.8 standard deviations, or 58% |
| **Volatility context** | Split by IV percentile, IV / realised vol, skew, term structure and results inside the cycle: does the share inside the straddle shift steadily? |
| **Direction** | Did skew or PCR lean the outcome, pooled and within dates? |
| **Reach probabilities** | Built on an earlier period and scored on the later one: do predicted probabilities of trading, closing and finishing beyond a level match what happened? |
| **Market regime** | Does the share inside the straddle differ with the NIFTY 500 above or below its 200-day average? |

"Traded there" uses each day's high/low. For stocks that's estimated from
the near-month future's high/low minus the closing basis; for indices it's
the index's own.

## Limits

- **End of day only.** Today's change-in-OI PCR and anything intraday is
  untested here. The half-hourly snapshots (`scripts/install_schedule.py
  --fno-intraday`) build the history for that.
- **Overlapping observations.** Stocks share dates and indices share weeks,
  so the true sample is smaller than the counts suggest. That's why the
  within-date and time-split checks decide what is used.
- **Many tests.** With many factors tested, one result near 2.5 standard
  errors is expected by chance. Only effects that repeat across periods and
  survive within-date checks should be acted on.
- **Historical prices are closes; live prices are bid/ask mids.** The
  difference is small at the money.

## How the live view uses it

- **Range:** spot ± straddle, quoting the measured hit rate for its group
  and offset (weekly expiries have their own), plus a conditional rate when a
  condition such as low IV percentile or results before expiry changes it.
- **Reach:** the historical columns plus the model.
- **Positioning** (walls, PCR, max pain) and **volatility context** are shown
  as context, not as signals. PCR and skew are recorded in the forward log so
  they can be judged out of sample.

---

See also: [F&O data](data.md) · the equity-side [validation](../validation.md).
