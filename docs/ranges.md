# Expected ranges (Today)

How far Nifty and each core holding could move in the next session and the
next five sessions -- not which way.

[← back to the README](../README.md)

---

```bash
PYTHONPATH=. .venv/bin/python run_range_backtest.py   # calibrate on F&O history
PYTHONPATH=. .venv/bin/python record_ranges.py        # fetch, record, score
```

## Why a range, not a direction

Every directional read this framework has tested lost to chance: OI walls,
PCR and max pain ([F&O validation](f_o/validation.md)), the swing score and
RSI(2) ([swing](swing.md)). What did hold up is the size of the move the
options market prices in. So Today shows a range centred on the last close,
next to how often such a range has held. It makes no call on direction.

## The band

`strategy/ranges.py`. The last close ± one standard deviation, in log space:

| Source | One session's standard deviation | Used for |
|---|---|---|
| **Implied** | The near-monthly ATM straddle IV (`fo_spot.iv_straddle`) ÷ √252. In expiry week (≤ 2 sessions left) it uses the next month's, because the near contract's IV is distorted then | Nifty and every holding with options |
| **Realised** | Standard deviation of the last 20 daily log returns | Holdings with no options (e.g. IRCTC) |

The five-session band is √5 times as wide. A normal distribution would put
68% of closes inside a one-sigma band.

## Calibration

`run_range_backtest.py` runs that code on every F&O underlying and every day in
`data/fo_history.db`: indices since 2019, stocks since 2024, about 570,000
bands. Windows where the price moved more than 60% are dropped as
splits or bonuses (359 of them). It writes `config/range_calibration.json`,
which Today quotes as **Held before**. The Saturday job reruns it.

Run of 2026-10-19:

| Share of closes inside | Next session | Next 5 sessions |
|---|---|---|
| Nifty, implied | 75.5% | 72.1% |
| Stocks, implied, no results in the window | 74.3% | 72.4% |
| Stocks, implied, results in the window | 68.5% | 63.4% |
| Stocks, realised, no results | 71.5% | 70.1% |
| Stocks, realised, results in the window | 56.7% | 53.5% |

- **Implied bands hold more often than 68%.** Options price in a little more
  movement than usually comes: the volatility risk premium. That held in
  every year from 2019 to 2026 (70–79% next session for indices).
- **A results date inside the window is when bands fail**, and realised
  volatility misses it badly, because the last 20 days don't contain the
  results move. Options do price some of it in. That's why a stock
  without options and with results due carries a note.
- About 5% of closes land beyond two sigma, against 4.6% for a normal
  distribution. Tails are a little fat, not wildly so.

## Forward log

`record_ranges.py` (in the weekday job, `run_swing_daily.py`) does three things:

1. Catches the F&O bhavcopy up to the latest close.
2. Records every F&O underlying's bands for that close to
   `history/ranges/YYYY-MM.csv`. That's both methods and both horizons for all
   ~220 names. Your holdings aren't recorded, so nothing private reaches git.
3. Scores every recorded band whose close is now in.

Two rules keep the log honest:

- **Append-only.** A date already recorded is never rewritten.
- **Written before the outcome.** A band only counts if it was recorded before
  the close it's scored against (15:30 on the end date). The job catches up
  to five missed sessions, but those late rows are kept and never scored.
  A new log starts with the latest close, not a backfill.

Today shows the forward hit rates under the table once there are any.

## Reading it

- **Close** is the last close the band starts from: the latest F&O
  bhavcopy, or the price history for a stock without options.
- The next-session column header is the session the band covers. A warning
  appears if that date has already passed. Run *Record Today ranges* on Data
  and jobs.
- **Held before** is two figures: the next-session rate, then the
  five-session rate. Each is for the same source, the same kind (index or
  stock) and the same results situation as the row.
