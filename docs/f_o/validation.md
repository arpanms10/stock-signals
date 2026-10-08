# F&O validation

What, in the option chain, tells you anything about where price ends up by
expiry, or how far it moves?

[← F&O overview](README.md) · [← back to the README](../../README.md)

---

**Short answer.** As levels and as targets, open interest is worth nothing:
OI walls and max pain did no better than distance from spot on ~13,000
stock and ~2,000 index observations. PCR has no absolute directional signal.
What the chain *does* carry is the **size** of the move: the ATM straddle
prices it roughly fairly, and two conditions shift that measurably for
stocks. Cheap options (IV percentile below 20) and a results date before
expiry both mean the straddle holds a few points less often. Two
directional hints survived several checks and are still **tentative**:
among stocks on the same day, high PCR leaned up; for indices, a steep put
skew was followed more often by a rise.

```bash
PYTHONPATH=. .venv/bin/python run_fno_backtest.py --ingest         # incremental; the Saturday job runs it
PYTHONPATH=. .venv/bin/python run_fno_backtest.py --ingest-old     # 2019-2023 index history, once (~1 hour)
PYTHONPATH=. .venv/bin/python run_fno_backtest.py
```

Run on 2026-10-08. Every table: `reports/fno/validation_<date>.md`; one row
per observation: `reports/fno/validation_obs.csv`.

## Method

**Observations.** Every F&O underlying's chain, read from NSE's end-of-day
F&O bhavcopy:
- **monthly expiries:** 20, 10 and 5 trading sessions before expiry;
- **weekly index expiries:** 4, 2 and 1 sessions before.

Each chain goes through `strategy/fno.py`, the code the live view runs, and
is compared with the expiry-day close and the daily closes and highs/lows in
between. Sessions count NSE's holidays. 43 stock observations spanning a
split or bonus were dropped ([data](data.md)).

**A null for every hit rate.** "Support held 76% of the time" means nothing
alone, because a level far enough below spot always holds. Each level is
compared with how often a level **the same distance from spot** held, judged
against every observation's actual outcome (same group, same offset). The
gap is the **edge**, in percentage points. A stricter second null measures
distances and moves in units of each observation's own straddle; both agree.

**Two robustness checks for anything that looks like an effect.**
- **Split by time:** 2024 vs 2025–26, and for indices 2019–23 vs 2024–26.
- **Within-date comparison:** compare stocks against each other *on the same
  date*. Thousands of stocks on one date share one market move, so a pooled
  pattern can come from a handful of dates. An effect has to survive both to
  be used.

| sample | observations | underlyings | expiries | period |
|---|---|---|---|---|
| stocks, 5 / 10 / 20 sessions out | 6,690 / 6,689 / 2,431 | 280 | 33 | Jan 2024 – Sep 2026 |
| indices, monthly, 5 / 10 / 20 out | 313 / 313 / 113 | 6 | 165 | Jan 2019 – Sep 2026 |
| indices, weekly, 1 / 2 / 4 out | 725 / 722 / 530 | 4 | 532 | Jan 2019 – Oct 2026 |

Stocks start in 2024: older files have no underlying price, and only index
history could be added from the index's own record. There are 280 stock
underlyings because names joined and left the F&O list over the period.

## Open interest: walls, PCR, max pain

### Support and resistance: no edge

| | distance | held | null | **edge** |
|---|---|---|---|---|
| stocks, support, 5 out | 3.3% | 75.7% | 75.9% | **−0.2** |
| stocks, resistance, 5 out | 4.2% | 83.2% | 83.4% | **−0.2** |
| stocks, support, 10 out | 3.5% | 74.6% | 74.6% | **0.0** |
| stocks, resistance, 10 out | 4.6% | 76.5% | 76.8% | **−0.3** |
| indices, support, 5 out | 2.4% | 79.9% | 79.5% | **+0.4** |
| indices, resistance, 5 out | 2.6% | 83.0% | 80.3% | **+2.6** |
| indices, support, 10 out | 2.6% | 78.5% | 79.8% | **−1.3** |
| weekly, resistance, 1 / 2 / 4 out | 1.5–2.2% | 91.4 / 88.1 / 80.9% | 88.8 / 85.4 / 78.9% | **+2.6 / +2.7 / +2.0** |
| weekly, support, 1 / 2 / 4 out | 1.4–2.0% | 88.8 / 85.5 / 84.3% | 87.9 / 86.9 / 84.8% | **+0.9 / −1.4 / −0.5** |

Stocks: every edge is within one standard error (~0.6 points). Monthly
indices: within noise (one standard error ~2.5 points). **Weekly call walls
are the one consistent positive**, +2 to +3 points at every offset, each
about 2 standard errors. The offsets share the same weeks, so this is closer
to one finding than three. It's possibly real, and too small to trade on.

### As price targets: reached no more often than any level

"Reached" means a daily close at or beyond the level before expiry.

| | distance | reached | null | edge |
|---|---|---|---|---|
| stocks, call wall, 5 / 10 out | 4.2 / 4.6% | 23.3 / 36.5% | 23.0 / 36.4% | +0.2 / +0.1 |
| stocks, put wall, 5 / 10 out | 3.3 / 3.5% | 32.9 / 38.2% | 32.7 / 38.2% | +0.2 / 0.0 |
| stocks, max pain below spot, 5 / 10 out | 1.9 / 1.8% | 43.9 / 51.9% | 46.2 / 54.1% | −2.4 / −2.2 |
| indices, call wall, 5 / 10 out | 2.6 / 2.8% | 23.2 / 33.4% | 24.4 / 35.1% | −1.3 / −1.7 |
| indices, put wall, 5 / 10 out | 2.4 / 2.6% | 29.1 / 33.9% | 28.3 / 32.6% | +0.8 / +1.2 |
| weekly, call / put wall, 2 out | 2.1 / 1.9% | 15.1 / 16.6% | 17.8 / 15.5% | −2.7 / +1.1 |

How likely a target is to be reached depends on its distance and the
volatility, not on the OI sitting there. That's what
[reach](README.md#reach-is-a-target-or-stop-realistic) estimates.

### PCR: no absolute direction; a tentative relative one for stocks

Stocks are read against stock norms (bearish < 0.5, bullish > 0.8,
stretched > 1.05). Index PCR uses 0.7 / 1.2 / 1.6.

| went up by expiry | bearish | neutral | bullish | stretched |
|---|---|---|---|---|
| stocks, 5 out | 45.2% | 44.8% | 46.7% | 50.3% (n=155) |
| stocks, 10 out | 46.5% | 49.4% | 54.2% | 56.9% (n=225) |
| indices, 5 out | 55.6% (n=63) | 47.6% | 52.9% (n=34) | 33% (n=6) |
| indices, 10 out | 61.0% (n=59) | 50.0% | 52.6% (n=57) | 50% (n=14) |

The stock rows rise with PCR 10 sessions out, which is exactly how a
dates effect can look. So, as with skew, stocks were compared against
each other **on the same date**: the highest-PCR fifth against the lowest.

| stocks, high-PCR fifth minus low-PCR fifth, same date | went-up gap | dates |
|---|---|---|
| 5 sessions out | **+6.1 ± 1.9 points** | 33 |
| 10 sessions out | **+5.4 ± 2.3 points** | 33 |

By period, PCR fifths lowest → highest, % up:

| | 2024 | 2025–26 |
|---|---|---|
| 5 out | 49.3 → 57.0 | 42.3 → 43.4 (flat) |
| 10 out | 48.6 → 58.8 | 46.9 → 54.1 |

So relative to other stocks that day, a stock with more put OI than call OI
rose more often by expiry. That's the usual Indian reading: put writers
defend the stock. It doesn't say the market will rise, only which stocks
leaned up within it. It's **tentative**: 3 standard errors at 5 out, but
flat there in 2025–26; consistent at 10 out, but 2.3 standard errors. It's
one of about 18 factor tests. Not shown as a signal live; PCR is recorded in
the forward log, and that record decides.

Indices: no consistent ordering. "Bearish" index PCR was followed by more
rises than falls, if anything, on about 60 observations.

### Max pain: no better than "no change"

Max pain was closer to the expiry close than today's price only **37–45%**
of the time for stocks, **42–51%** for indices and **43–45%** for weekly
expiries. If price gravitated to it, that would be above 50%.

## The range: what the straddle is worth

| | width | close inside at expiry | null | edge | closes never left it |
|---|---|---|---|---|---|
| stocks, ±straddle, 5 out | 6.5% | 59.5% | 59.2% | +0.3 | 46.5% |
| stocks, ±straddle, 10 out | 9.7% | 62.5% | 62.1% | +0.3 | 45.4% |
| stocks, ±straddle, 20 out | 12.5% | 53.6% | 54.1% | −0.6 | 31.4% |
| indices, ±straddle, 5 out | 3.4% | 49.5% | 50.8% | −1.3 | 37.2% |
| indices, ±straddle, 10 out | 4.9% | 57.4% | 56.9% | +0.4 | 37.8% |
| weekly, ±straddle, 1 / 2 / 4 out | 1.5 / 2.1 / 3.0% | 63.9 / 64.6 / 62.4% | 63.3 / 62.8 / 62.0% | +0.6 / +1.9 / +0.4 | |
| stocks, tighter of wall/straddle (old default), 5 out | 5.2% | 47.1% | 47.5% | −0.4 | 30.6% |

In theory, ±1 straddle is about ±0.8 standard deviations, or 58%. Stocks
come in near that. Monthly indices come in a little under it 5 sessions out
(the straddle slightly underprices index moves), and weekly expiries a little
over. The live view quotes these measured rates, per group and offset, from
`config/fno_calibration.json`.

The old "tighter of wall or straddle" range held exactly as often as its
narrower width predicts (edge −0.4). The walls only narrowed it.

## Volatility context: what changes the size of the move

For each factor: split observations into fifths by the factor and check
whether the share inside the straddle, and the average move in straddles,
shift steadily (stocks and indices 5–10 sessions out).

| stocks (n) | Spearman ρ vs move | inside, bottom fifth | inside, top fifth | verdict |
|---|---|---|---|---|
| **IV percentile** (9,848) | −0.1 | **55.2%** | 59.9% | **bottom fifth consistently weak** |
| IV / realised vol (12,436) | 0.0 | 62.4% | 59.8% | no effect |
| skew (12,959) | +0.1 | 61.6% | 57.3% | small, not robust (below) |
| next month − this month IV (10,436) | 0.0 | 61.7% | 58.9% | no effect |
| **results inside the cycle** (1,597 vs 11,367) | | **57.4%** with | 61.4% without | **holds** |

**IV percentile.** When a stock's straddle IV is in the bottom fifth of its
own past year, the move more often exceeds the straddle. The bottom fifth
was the weakest band in 2024 (53.8% inside) and in 2025–26 (54.8%). Within
dates, the most expensive fifth beat the cheapest by 3.0 ± 2.0 points inside,
and moved 0.08 ± 0.03 straddles less. **Cheap options tend to be slightly
too cheap.** The middle and top bands moved around between periods
(2024: 57–69%; 2025–26: 58–65%), so only the bottom band is used.

**Results inside the cycle.** IV is already higher into results (median
35.3% vs 28.6%), so the market prices most of the extra move, but not all:
57.4% inside vs 61.4%.

**In the live view:** for stocks in either condition, the range shows its
condition's hit rate next to the overall one, when they differ by 3 points
or more: "IV percentile below 20 … held 56%", "results inside the cycle …
held 57%".

Indices (n ≈ 420–530): the same direction for IV percentile (49.0% → 58.2%
inside), and no effect for IV/RV or term structure, but too few observations
to split further. Not used.

## Direction

**Stocks: skew does not predict direction.** Pooled, a steeper put skew
looked like it preceded more rises (43.4% up in the bottom fifth, 50.0% in
the top). Within dates the gap vanishes: 0.1 ± 1.3 points. The pooled
pattern came from *when* skew was high, not *which stocks* had it.

**Indices: a steep put skew preceded more rises. Tentative.**

| | lower 60% of skew | top 40% of skew |
|---|---|---|
| all index observations, went up | 43.0% (n=316) | 60.4% (n=212) |
| 2019–2023 | 44.4% | 64.2% |
| 2024–2026 | 42.9% | 54.4% |
| one observation per date (index average) | 47.6% (n=168) | 63.3% (n=113) |

It shows up in both periods and in four of the five indices, and survives
collapsing the indices to one observation per date (about 2.6 standard
errors). It fits a familiar idea: expensive downside protection marks fear
that tends to be overdone. But it's one of the two strongest of about 18 factor tests
run here, so some of it is luck, and the recent period alone is weaker
(1.8 standard errors). **Not shown as a signal in the live view.** Skew is
recorded in the forward log, and the out-of-sample record decides.

## Reach probabilities: calibrated, out of sample

The curve was built on observations before July 2025 and scored on
everything after, at the levels someone would look up: both walls and
spot ± straddle.

| stocks, predicted | traded there (intraday) | closed there | beyond at expiry (historical) | beyond at expiry (model) |
|---|---|---|---|---|
| 0–10% | 5.1 → 3.2 | 4.9 → 3.7 | 4.9 → 3.2 | 5.0 → 3.0 |
| 10–20% | 14.8 → 12.1 | 15.0 → 12.8 | 18.8 → 18.0 | 15.0 → 11.1 |
| 20–30% | 24.9 → 21.7 | 27.5 → 26.2 | 24.9 → 24.8 | 21.9 → 19.9 |
| 30–40% | 38.3 → 36.7 | 34.9 → 32.6 | 34.9 → 38.7 | 34.8 → 37.9 |
| 40–50% | 44.9 → 46.0 | 44.8 → 46.4 | 44.7 → 51.0 | 44.7 → 50.2 |
| 50–70% | 59.6 → 59.6 | 59.5 → 63.2 | | |
| 70–100% | 82.5 → 85.1 | 73.5 → 80.7 | | |

For stocks, every historical column is within about 1–4 points
(predicted → actual), slightly overstating long shots and understating
likely moves. The model is 2–4 points high below 20%.

**Indices:** the historical columns ran **4–8 points high** out of sample
(for example, 33.6% predicted vs 26.1% actual for "closed there", n=257),
because index moves after mid-2025 were smaller than in 2019–2025. The model
fit within ~1–4 points (21.7% predicted vs 20.4% actual, n=269). For indices
the model is the better guide, and the live view says so.

"Traded there" uses each day's high/low. For stocks that's estimated from
the near-month future's high/low minus the closing basis; for indices it's
the index's own.

## Market regime

| | NIFTY 500 below its 200-day average | above |
|---|---|---|
| stocks, inside the straddle | 56.6% (n=3,738) | 62.7% (n=9,226) |
| indices, inside the straddle | 50.4% (n=131) | 54.2% (n=397) |

Moves run larger than priced in falling markets. Each regime is a handful of
long stretches, not thousands of independent cases, so this describes the
period, not a rule. Not used.

## What this does and does not show

- **End of day only.** Today's change-in-OI PCR and anything intraday is
  untested here. The half-hourly snapshots (`install_schedule.py
  --fno-intraday`) build the history for that.
- **Stocks only from 2024.** That's 33 monthly cycles, most of them in a
  falling market. Indices go back to 2019.
- **Overlapping observations.** Stocks share dates and indices share weeks,
  so the true sample is smaller than the counts suggest. That's why the
  within-date and time-split checks decide what is used.
- **Many tests.** About 18 factor tests ran here. At that count, one result
  near 2.5 standard errors is expected by chance. Only effects that repeat
  across periods and survive within-date checks are acted on.
- **Historical prices are closes; live prices are bid/ask mids.** The
  difference is small at the money.

## What it means for the live view

- **Range:** spot ± straddle, quoting the measured hit rate for its group
  and offset (weekly expiries have their own), plus the conditional rate
  for stocks with cheap options or results before expiry.
- **Positioning:** walls and max pain stay labelled "no measured edge". PCR
  is labelled with its tentative cross-stock tilt for stocks, not as a
  signal.
- **Volatility context** is shown: IV percentile, realised volatility,
  skew, term structure, results dates. Only IV percentile (bottom band) and
  results change the quoted odds.
- **Reach:** both historical columns plus the model; for indices the model
  is flagged as the better guide.
- **Stock PCR (relative) and index skew → direction** stay out of the live
  view as signals until the forward log confirms them.

History of changes: 2026-10-08, first version (walls range → straddle
range, stock PCR norms); second round the same day (holidays, weekly
expiries, 2019–23 index history, volatility context, intraday reach,
conditional hit rates).

---

See also: [F&O data](data.md) · the equity-side [validation](../validation.md),
which found and reported its own failures the same way.
