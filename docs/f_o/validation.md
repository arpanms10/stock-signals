# F&O validation

Did the OI range, the walls, PCR and max pain tell you anything about where
price ended up at expiry?

[← F&O overview](README.md) · [← back to the README](../../README.md)

---

**Short answer: no, not beyond what the distance from spot already tells
you.** On about 14,000 stock observations and 360 index observations from
January 2024 to September 2026, OI support and resistance held exactly as
often as any level the same distance from spot. PCR did not predict
direction, and max pain was a worse guess for the expiry close than "no
change". The straddle is the one honest number: it is the market's own
price for the move, and the expiry close landed inside ±straddle about
half to three-fifths of the time.

```bash
PYTHONPATH=. .venv/bin/python run_fno_backtest.py --ingest   # ~80 min the first time
PYTHONPATH=. .venv/bin/python run_fno_backtest.py
```

Run on 2026-10-08. Full tables: `reports/fno/validation_<date>.md`; one row
per observation: `reports/fno/validation_obs.csv`.

## Method

For every F&O underlying and every monthly expiry, the near-monthly chain was
read at **20, 10 and 5 sessions before expiry** from NSE's end-of-day F&O
bhavcopy. It was run through `strategy/fno.py`, the same code the live view
uses. The result was then compared with the underlying's close on expiry day
and with its daily closes in between. Cycles with a split or bonus were
dropped (39 observations; see [data](data.md)).

**Every hit rate is set against a null.** "Support held 78% of the time"
means nothing by itself, because a level far enough below spot always holds.
The null asks how often a level **at the same distance from spot** would have
held, judged against every observation's actual return (same kind,
same offset). If OI placement carries information, the real hit rate beats
the null. The gap is reported as **edge**, in percentage points.

A stricter second null scales every level and every return by that
observation's own straddle. That removes the effect of volatile stocks
having both farther walls and bigger moves. The two nulls agree.

| sample | observations | underlyings | expiries |
|---|---|---|---|
| stocks, 5 sessions out | 6,690 | 280 | 33 |
| stocks, 10 sessions out | 5,898 | 280 | 29 |
| stocks, 20 sessions out | 2,216 | 280 | 11 |
| indices, 5 / 10 / 20 out | 158 / 147 / 56 | 6 | 70 / 68 / 26 |

There are 280 stock underlyings because names joined and left the F&O list
over the period. The index expiries include all six indices, each with its
own monthly date. The 20-session sample is smaller because a monthly cycle is
often shorter than 20 weekdays, so that offset falls in the previous cycle.

## Results

### Support and resistance: no edge

| | distance from spot | held | null | **edge** |
|---|---|---|---|---|
| stocks, support, 5 out | 3.3% | 77.8% | 78.1% | **−0.3** |
| stocks, resistance, 5 out | 4.2% | 84.0% | 84.3% | **−0.4** |
| stocks, support, 10 out | 3.6% | 72.7% | 72.6% | **+0.1** |
| stocks, resistance, 10 out | 4.5% | 80.5% | 80.9% | **−0.4** |
| indices, support, 5 out | 2.5% | 83.5% | 82.0% | **+1.6** |
| indices, resistance, 5 out | 3.5% | 87.3% | 85.5% | **+1.8** |
| indices, support, 10 out | 2.0% | 76.0% | 77.3% | **−1.3** |

For stocks, one standard error is about 0.6 points, and every edge is within
it, on both nulls. For indices, one standard error is about 4 points, so
+1.8 or −1.3 can't be told apart from zero. At this size, only an index edge
of about 8 points or more would reliably show up, so a smaller index edge
can't be ruled out. The stock sample has no such excuse.

### The range: the walls only made it narrower

| | width | close inside at expiry | null | edge | closes never left it |
|---|---|---|---|---|---|
| stocks, wall/straddle range, 5 out | 5.1% | 49.4% | 50.2% | −0.8 | 32.3% |
| stocks, ±straddle, 5 out | 6.2% | 61.2% | 61.4% | −0.2 | 47.7% |
| stocks, wall/straddle range, 10 out | 6.6% | 46.4% | 47.1% | −0.7 | 24.2% |
| indices, wall/straddle range, 5 out | 2.9% | 46.8% | 46.3% | +0.5 | 31.6% |
| indices, ±straddle, 5 out | 3.3% | 49.3% | 51.9% | −2.7 | 38.1% |
| indices, wall/straddle range, 10 out | 3.8% | 45.5% | 49.7% | −4.1 | 26.2% |

The wall/straddle range (the live default before 2026-10-08) took the
tighter of the wall and the straddle on each side, so it was narrower than
either. Narrower means it held less often, and it held exactly as often as
its width predicts: the walls made the range narrower without making it
more accurate.

The straddle range covered 61% of stock expiries and about 50% of index
expiries 5 sessions out. In theory, ±1 ATM straddle is about ±0.8 standard
deviations, which is about 58%. So the market's priced move is roughly fair
for stocks and slightly too tight for indices over this period.

### PCR: no direction

| | PCR | median | went up by expiry | mean return |
|---|---|---|---|---|
| stocks, 5 out | "bearish" (< 0.7) | 0.5 | 45.2% | −0.2% |
| | "neutral" | 0.8 | 48.1% | −0.1% |
| | "bullish" (1.2–1.6) | 1.3 | 58.2% (n=55) | +0.5% |
| indices, 5 out | "bearish" | 0.6 | 48.9% (n=45) | 0.0% |
| | "neutral" | 0.9 | 47.6% | 0.0% |
| | "bullish" | 1.3 | 50.0% (n=10) | +0.3% |

Two problems. First, **the index thresholds don't fit stocks.** Stock PCR
normally sits around 0.5–0.6, so 71% of stock observations 5 sessions out
(two-thirds across all offsets) were labelled "bearish". A label that applies to nearly everything says nothing. Second,
even where the labels separate, the differences in "went up" are a few
points, on small "bullish" buckets. Over a period when most stocks fell,
"bearish" stocks fell about as often as the rest.

### Max pain: worse than no change

| | max pain closer than spot | median miss: max pain | median miss: no change |
|---|---|---|---|
| stocks, 5 out | 35.6% | 3.3% | 2.4% |
| stocks, 10 out | 38.1% | 4.0% | 3.4% |
| indices, 5 out | 43.0% | 1.8% | 1.6% |
| indices, 10 out | 36.7% | 2.2% | 1.9% |

If price really gravitated to max pain, max pain would beat "price stays
where it is" more than half the time. It beat it 36–43% of the time.

### As price targets: reached no more often than any level

Could an options level serve as a **target**, even if it isn't a barrier?
Here, "reached" means any daily close at or beyond the level before expiry
(intraday highs and lows aren't in the data). The null is how often price
reached a level the same distance away.

| | distance | reached | null | edge |
|---|---|---|---|---|
| stocks, call wall as upside target, 5 out | 4.2% | 22.6% | 22.6% | 0.0 |
| stocks, call wall as upside target, 10 out | 4.5% | 32.4% | 32.5% | −0.1 |
| stocks, put wall as downside target, 10 out | 3.6% | 39.3% | 39.6% | −0.3 |
| stocks, max pain (above spot), 10 out | 2.0% | 52.3% | 52.7% | −0.4 |
| indices, call wall, 10 out | 3.0% | 28.8% | 28.1% | +0.7 |
| indices, put wall, 10 out | 2.0% | 37.0% | 34.3% | +2.7 |

Every stock edge is within ±1.1 points at the 5- and 10-session offsets. One
max-pain cell at 10 out is −3.1, which is worse, not better. The index edges
fall inside their roughly ±4–8 point noise. A target taken from the option
chain is a level at some distance from spot. How likely price is to reach it
depends on that distance and on volatility, not on the OI sitting there.

## What this does and does not show

- It covers **end-of-day** chains, and 20/10/5 sessions before **monthly**
  expiries. It says nothing about intraday OI shifts or weekly NIFTY expiries,
  where the "pinning" folklore is strongest. The live view's change-in-OI PCR
  and the intraday snapshots are not tested here.
- The index sample is small (360 observations across 6 indices, and these
  overlap in time, so they are not independent). "No edge found" there
  means no large edge.
- Two and a half years, mostly a falling market for stocks. A different
  regime could differ, but the stock sample is large enough that an edge of
  even 2 points would have shown.
- The historical straddle uses closing prices. The live one uses the bid/ask
  mid. The difference is small at the money.

## What it means for the live view

Changed on 2026-10-08, from these results:

- **The range is now spot ± straddle,** shown with its measured hit rate
  (61–63% for stocks, about 50–56% for indices, 5–10 sessions out). The old
  "tighter of wall or straddle" range held 46–49%, exactly what its narrower
  width predicts, so the walls added nothing. Re-measured on the new
  definition, the live range scores −0.1 / +0.3 points against its null for
  stocks, as a fair price should.
- **Walls, PCR and max pain are labelled "positioning, no measured edge".**
- **Stock PCR is read against stock norms:** bearish below 0.5, bullish above
  0.8, stretched above 1.05 (the 20th/80th/97th percentiles of stock PCR).
  The labels now split about 23% / 62% / 15%, not 71% "bearish". They still
  don't predict direction. 5 sessions out, "bearish" stocks rose 45% of the
  time, "neutral" 46%, "bullish" 48%, "stretched" 54% (n=171), with no
  consistent pattern at 10 or 20 sessions out.

---

See also: [F&O data](data.md) · the equity-side [validation](../validation.md),
which found and reported its own failures the same way.
