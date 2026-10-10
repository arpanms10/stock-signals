# Validation and results

What the backtests actually found, including the results that do not flatter.

[← back to the README](../README.md)

---

Backtested over 49 NIFTY 50 names after realistic Indian costs (STT, stamp duty,
exchange fees, GST, slippage -- about 0.35% per round trip). All figures below
were produced *after* the OBV bug fix described in "Data notes"; earlier numbers
computed with the buggy indicator are superseded.

## Baseline, full period 2016-2026

| | 10-year CAGR |
|---|---|
| Equal-weight buy & hold, same 49 stocks | **17.73%** |
| NIFTY 500 index | **11.82%** |
| This strategy | **1.29%** |
| Shuffled-entry controls (7 seeds) | mean 0.60%, sd 0.49, range 0.03-1.39 |

The strategy beat 6 of 7 randomised controls, sitting about 1.4 standard
deviations above the control mean. That is **not** statistical significance, and
one control beat it outright. Read at face value the entry rules are worth
roughly 0.7% a year -- against a 10.5% a year gap to the index. Even if the
effect is real, it is economically irrelevant.

## Fix attempt: loosen the exits (train/holdout)

Hypothesis: the system cut winners during ordinary noise. Tested by widening
stops, moving the trend-break exit from the 50 DMA to the 200 DMA, and dropping
fixed targets and the cooldown. Variants were compared on 2016-2022 only; the
2023-2026 holdout was run after choosing.

| variant | train CAGR | holdout CAGR |
|---|---|---|
| baseline | 1.11% | -- |
| wide_stops | 3.91% | -- |
| trend_200 | 2.87% | -- |
| **let_winners_run** | **5.17%** | **3.31%** |
| NIFTY 500 index | 11.86% | 11.64% |
| buy & hold same names | 23.93% | **16.89%** |

The hypothesis was right about the mechanism and insufficient as a fix. Loosening
exits nearly tripled the average win (9.8% -> 25.6%) and extended holds from 26
to 109 days, so winners genuinely were being cut early. But 3.31% still loses to
holding the same stocks by 13.6 points a year.

Train 5.17% -> holdout 3.31% is a degradation but not a collapse, so the variant
is **not badly overfit**. It generalises to being consistently mediocre.

**The 2023-2026 holdout is now spent.** It was run twice only because the first
run was invalidated by the OBV bug -- a correction of a broken measurement, not a
second attempt at a different variant. Any new idea needs a fresh untouched
period or a different universe.

## What this means

Across the baseline, the R-multiple targets, the loosened exits and the holdout,
every result points the same way: on large-cap Indian equities over this period,
timing entries and exits destroyed value relative to holding. Per-symbol results
are unanimous -- all 50 names show a negative strategy return while most of the
underlying stocks rose several hundred percent.

**Do not trade the v1 signal rules.** They are kept in the repo because the
negative result is evidence, and because the infrastructure beneath them --
data, costs, controls -- is what the momentum work is built on.


## Momentum: trailing stop between rebalances (2026-10-10)

Question: the list is rebuilt monthly, so a stock that falls mid-month is held
until the next rebalance. Would a trailing stop help, and at what distance?

`run_pit_momentum.py --stop pct|atr` trails the highest close since entry.
A stop is judged on the close and sold at the **next session's close**, the
same lag the rebalance has. After a stop, the cash either waits for the next
rebalance (`--stop-refill cash`) or buys the best name from the last ranking
that isn't held, wasn't stopped since, and is above its 200 DMA (`next`).
Current setup otherwise (9-month, 15 stocks, top 200, full costs). Variants
were compared on **2017-12 to 2022-12** (four start dates a week apart) and
then all were run on the **2023-01 to 2026-10 holdout** (four starts).

| stop | train CAGR avg (range) | train worst fall | holdout CAGR avg (range) | holdout worst fall | stops / yr |
|---|---|---|---|---|---|
| **none** | **9.8** (8.7 - 11.3) | -46% | **13.1** (10.3 - 16.0) | -40% | 0 |
| 10%, cash | 9.0 (5.6 - 11.7) | -35% | 6.5 (3.2 - 10.9) | -44% | 72 |
| 15%, cash | 9.1 (5.7 - 12.2) | -34% | 9.3 (6.4 - 12.5) | -43% | 41 |
| 20%, cash | 8.1 (5.8 - 10.0) | -40% | 10.7 (9.5 - 12.1) | -42% | 24 |
| 25%, cash | 7.9 (5.8 - 10.2) | -42% | 12.9 (10.6 - 15.8) | -39% | 13 |
| 30%, cash | 8.9 (7.2 - 11.2) | -41% | 11.4 (10.3 - 13.3) | -42% | 9 |
| 15%, next | 7.7 (2.6 - 10.2) | -43% | 11.2 (8.2 - 13.4) | -43% | 45 |
| 20%, next | 10.4 (8.6 - 12.1) | -46% | 11.1 (9.7 - 12.4) | -44% | 25 |
| 25%, next | 8.7 (7.8 - 10.5) | -52% | 12.2 (9.6 - 16.7) | -41% | 15 |
| 4.5x ATR, cash | 6.7 (2.9 - 10.7) | -35% | 8.0 (4.9 - 13.3) | -40% | 109 |
| 3.5x ATR, cash | 3.6 (-2.9 - 9.5) | -33% | 3.3 (-4.2 - 10.4) | -41% | 133 |

NIFTY 500 over the same starts: about 10.7% (train) and 9.5% (holdout).
10% next and the 2.5x / next-refill ATR variants were worse still.

**Verdict: no stop. The config stays `stop: none`.**

- **No stop beat holding to the rebalance in both periods.** The best on
  train, 20% with refill, won by 0.6 points on 2 of 4 starts, then lost on
  all 4 holdout starts (11.1% vs 13.1%).
- **The one real benefit didn't survive.** Tight stops (10-15%, cash) cut
  the train worst fall from -46% to about -35%, for under a point of CAGR.
  On the holdout the same stops lost 4-7 points a year and the worst fall
  was *deeper* (-43/-44% vs -40%). Selling, then sitting in cash while the
  stock and the market rebound, didn't protect against the falls that
  mattered here.
- **Momentum stocks are volatile enough that tight stops just churn.** A
  15% stop fired ~41 times a year across 15 positions -- each holding
  stopped out almost three times a year -- and costs rose by ~0.5 point.
- **Refilling with the next-ranked name didn't help** on average; it adds
  trading on a ranking up to a month old.
- **ATR stops did worst**, but that test is weaker: the market table has
  closes only, so ATR is built from close-to-close moves, which is smaller
  than a true high/low ATR. Even 4.5x fired 100+ times a year.
- Caveat: selling at the next close is harsher than a resting stop order
  for a fast fall, and kinder for a gap. Wider stops (25-30%) fire rarely
  and land close to no stop either way, so they cost little and gave nothing
  measurable here.

The monthly rebalance already works as a slow stop: a stock that has really
broken drops out of the ranking (`exit_rank: 30`) or below its 200 DMA and is
sold at the next rebalance. What the ranking doesn't protect against is the
portfolio-wide fall (-40% to -46%), and per-stock stops didn't fix that.

## Momentum, re-run on the repaired market history (2026-10-08)

Everything below this section was run on a market history that, it turned
out, had **249 trading days (about 9% since 2016) recorded as empty**: failed
downloads saved as "no data" and never retried ([data](data.md)). 227 of
them were refilled, and the current setup was re-run on the repaired
history: top 200 by liquidity, monthly, 15 stocks, `risk_off_scale: 1.0`,
data to 2026-10-08.

| | CAGR range, 4 start dates | average | worst fall | vs random picks (10 seeds) |
|---|---|---|---|---|
| **9-month (main)** | **11.1 - 12.1** | **11.7** | -39 to -46% | beats 10/10, +7.6 pts, **3.2 sd** |
| 12-month | 9.2 - 11.3 | 10.4 | -42 to -50% | beats 10/10, +7.2 pts, 2.8 sd |
| overlap of both | 5.8 - 11.6 | 8.4 | -41 to -51% | |
| NIFTY 500 | 9.9 - 10.3 | | -38% | |
| random picks (9-month universe) | -0.1 - 8.0 | 3.8 | -48 to -55% | |

**Split in halves** (default start): 9-month 11.3% to 2022-04 (index 11.7%)
and 11.6% from 2022-05 (index 9.0%); 12-month 13.1% and 12.0%.

What changed, and what still holds:

- **The headline is lower and much less sensitive to dates.** 9-month
  averages 11.7%, not 12.5%, and the spread across start dates shrank from
  5 points to 1. Against the index's ~10.1% over the same starts, the edge
  is **about 1.5 points a year**, with deeper falls (-39% to -46% against
  -38%). The ranking still clearly carries information: it beats every
  random pick from the same universe, by about 3 standard deviations.
- **9 months now leads 12 months** on all four start dates (11.7% vs
  10.4% on average). Split into halves, 12 months does a little better in
  both, so the lead depends on the path. 9 stays the main ranking with 12
  alongside, as before.
- **Skipping the latest month still helps** (9-month): 11.4 / 12.1 / 12.1 /
  11.1% with the skip, against 10.4 / 13.3 / 8.3 / 7.6% without. That's 3
  of 4, averaging 11.7% vs 9.9%.
- **The overlap is worse than either list**, more clearly than before.
- **NSE-blend scoring, semi-annual: still not adopted.** It returned 10.8 /
  10.8 / 14.7% on three starts, against 11.4 / 12.1 / 12.1% for the current
  ranking, with falls of -48% to -53%.
- **Reopened: the market-regime rule.** Holding half the stocks when the
  index is below its 200-day average (`risk_off_scale: 0.5`) now returns
  15.7 / 10.9 / 12.3 / 14.5% against 11.4 / 12.1 / 12.1 / 11.1% without it.
  That's 3 of 4 starts, averaging 13.4% vs 11.7%, with similar worst falls
  (-42% to -43% vs -39% to -46%). On the old history the rule lost with 12
  months and won only with 9. The reasons it was dropped haven't changed:
  the live advisor doesn't do it, it concentrates rather than de-risks, and
  it was tuned on this same data. **The config is unchanged.** If it's
  revisited, it needs a period it wasn't chosen on.

## Momentum: does skipping the latest month earn its place? (2026-10-04)

The ranking scores 12-1 momentum: the return from 12 months ago to 1 month
ago, leaving out the latest month because one-month returns tend to reverse.
That came from the academic literature (mostly US data) and had never been
tested here. Tested now on the survivorship-free engine, data 2016-10 to
2026-10, top 200 by liquidity, monthly rebalance, 15 names, full costs:

```bash
PYTHONPATH=. .venv/bin/python run_pit_momentum.py --skip-months 0   # plain 12-month return
PYTHONPATH=. .venv/bin/python run_pit_momentum.py --skip-months 1   # 12-1 (current)
```

> **Corrected 2026-10-04.** The first version of this section was run with
> ETFs in the universe. The ISIN filter that should keep them out had never
> run: ingestion discarded the ISIN, the instrument map in `market.db` was
> empty, and the universe functions skipped the filter when it was empty.
> LIQUIDCASE, a liquid-fund ETF, was in the latest holdings. The map is now
> built at ingestion, an empty map is an error, and every figure below was
> re-run on equities only. The default 12-1 run fell from 14.0% to **11.8%**.

**12-month lookback, CAGR %** (each row is the same test from a different
start date, so the monthly rebalance falls on different days):

| start | skip 0 | skip 1 | difference |
|---|---|---|---|
| 2017-12-07 (default) | 8.6 | **11.8** | +3.2 |
| 2017-12-14 | 14.1 | **14.4** | +0.3 |
| 2017-12-21 | 12.2 | **13.4** | +1.2 |
| 2017-12-28 | 9.6 | **12.8** | +3.2 |
| first half, to 2022-04 | **9.8** | 9.1 | -0.7 |
| second half, from 2022-05 | 5.6 | **12.8** | +7.2 |

The skip wins five of the six, by 0.3 to 7.2 points, with higher Sharpe in
the same five. It loses the first half, narrowly (Sharpe 0.48 vs 0.49), and
most of its edge comes from the second half. **Kept**, but the case is weaker
than it first looked: it rests mainly on 2022-2026.

It depends on the lookback, though. With 9 months the skip still helps
(15.0 vs 16.9). With 6 months it hurts (8.6 vs 7.5): dropping a month from
six throws away a sixth of the signal. The skip belongs with long lookbacks.
The 9-month 12-1 figure (16.9%) beats the 12-month one (11.8%) on this start
date. That is one draw and has not been checked across start dates, so it is
a lead to test, not a reason to change the lookback.

**The dates alone move the result by 2.6 points.** The same strategy returned
11.8% to 14.4% depending only on which days the monthly rebalance fell on. The
headline CAGR figures in this repo are one draw from that range, not a precise
number. (The 14.5% once quoted in the README predated both the data moving
forward and the ETF fix.)

### 9-month lookback, and holding only the 12/9 overlap (2026-10-04)

The 9-month lookback looked better on one run above, so it was tested across
the same start dates. Then a third variant: hold only the stocks in the top 15
on **both** the 12-month and the 9-month ranking. A stock is bought when it is
top 15 on both, and sold when it falls past 30 on either. That holds a
varying number of stocks, 10.8 on average.

```bash
PYTHONPATH=. .venv/bin/python run_pit_momentum.py --lookback-months 9
PYTHONPATH=. .venv/bin/python run_pit_momentum.py --selection overlap
```

**15 stocks, CAGR %:**

| start | 12-month | 9-month | overlap |
|---|---|---|---|
| 2017-12-07 (default) | 11.8 | **16.9** | 13.6 |
| 2017-12-14 | 14.4 | **17.3** | 14.6 |
| 2017-12-21 | **13.4** | 11.9 | 11.4 |
| 2017-12-28 | 12.8 | **16.4** | 15.5 |
| first half, to 2022-04 | 9.1 | **13.9** | 7.2 |
| second half, from 2022-05 | 12.8 | **18.9** | 14.7 |
| average of the four starts | 13.1 | **15.6** | 13.8 |
| worst fall, four starts | -41 to -46 | -40 to -52 | -42 to -54 |

The 9-month lookback beats 12 months in five of six runs, including both
halves of history, and beats random picks by 3.3 standard deviations (12-month:
2.0). It trades more, costing about 0.5 points a year extra, already
included. The caveat: 12, 9 and 6 months were all tried on the same nine
years and the best kept, so expect some of the gain to shrink.

The overlap did **not** beat the 9-month list on any start date. In the first
half it returned 7.2%, below the index's 11.7%. Requiring agreement between
the two windows sounds safer, but it held fewer stocks and had deeper falls.

**Adopted 2026-10-04: 9 months is now the main ranking**, with 12 months
kept alongside for comparison. *Same day, later:* every run in this section
held half as many stocks in RISK-OFF (`risk_off_scale: 0.5`). With that rule
removed, 9 and 12 months tie; see the next section. Both are kept. The daily guide (section 3b) and the dashboard
(Momentum tab) show both ranks and scores and mark the names in the top 15 on
both.

At 10 stocks the 9-month lookback is worse: 6.1% to 18.2% across the four
starts, with falls of -53% to -58% on three of them. Fewer stocks and a
shorter window together are too jumpy.

### Market regime filter: fewer stocks in RISK-OFF (2026-10-04)

When the NIFTY 500 was below its 200-day average, the backtest held half as
many stocks (7 instead of 15), with **all** the money split across those 7 --
not half in cash. It made the book more concentrated, not safer. The live advisor
never did this, so the backtest behind the advice was not testing what the
advice actually tells you to do. Tested by switching it off (`risk_off_scale: 1.0`):

```bash
PYTHONPATH=. .venv/bin/python run_pit_momentum.py --risk-off-scale 0.5
PYTHONPATH=. .venv/bin/python run_pit_momentum.py --risk-off-scale 1.0
```

**CAGR %, half the stocks in RISK-OFF (0.5) vs no change (1.0):**

| start | 12-month: 0.5 | 12-month: 1.0 | 9-month: 0.5 | 9-month: 1.0 |
|---|---|---|---|---|
| 2017-12-07 (default) | 11.8 | **12.4** | **16.9** | 11.7 |
| 2017-12-14 | **14.4** | 12.5 | **17.3** | 15.9 |
| 2017-12-21 | **13.4** | 11.6 | **11.9** | 11.6 |
| 2017-12-28 | 12.8 | **13.4** | **16.4** | 10.9 |
| first half, to 2022-04 | 9.1 | **10.5** | **13.9** | 12.7 |
| second half, from 2022-05 | 12.8 | **13.2** | **18.9** | 11.0 |
| 0.5 wins | 2 of 6 | | 6 of 6 | |

With 12 months the rule wins 2 of 6. With 9 months it wins all six, but
that lookback was itself chosen on this data, with the rule switched on.
In neither case did it do what a regime filter is for, which is no surprise
once you see it never held cash: the worst falls are no shallower (12-month: -43% either way, and in the second half -38.5% with the
rule against -34.0% without; 9-month: -52% with it on one start date, -43%
without). Two settings tuned together on the same nine years can look good
together by chance.

**Adopted 2026-10-04: `risk_off_scale: 1.0`.** The regime is still shown on
the dashboard, as information. Backtest and live advice now behave the same
in a falling market.

With the rule off, across the four start dates (**superseded**: on the
repaired history these are 11.7%, 10.4% and 8.4%; see the section at the top):

| | CAGR range | average | worst fall | vs random picks |
|---|---|---|---|---|
| 9-month (main) | 10.9 - 15.9 | 12.5 | -39 to -43% | 3.0 sd |
| 12-month | 11.6 - 13.4 | 12.5 | -43 to -45% | 3.1 sd |
| overlap of both | 10.0 - 12.1 | 11.1 | -46 to -47% | |
| NIFTY 500 | 10.4 | | -37% | |

The two lookbacks tie: 9 months has the shallower falls, 12 months the
steadier returns across start dates. Neither is proven better, so both stay,
9 as the main ranking and 12 alongside. The overlap is worse than either.

**The honest headline** (updated 2026-10-08): the ranking clearly carries
information (it beats random picks from the same universe by about 3
standard deviations), but the edge over simply holding the index is about
1.5 points a year, with deeper falls.
The earlier 16.9% depended on the regime rule paired with the 9-month
lookback, and did not survive removing the rule.

The skip-month table above and the NSE-blend table below were run with the
rule on. Their comparisons stand, since both sides of each comparison used the
same settings, but their absolute CAGRs are not current.

### NSE's Nifty200 Momentum 30 scoring, tested here

The index scores each stock on 6-month **and** 12-month return, each divided
by the volatility of daily returns over the last year, z-scored across the
eligible stocks and averaged 50/50. It rebalances semi-annually and weights by
free-float market cap times the score, capped at 5%. The published methodology
does not say whether the latest month is skipped.

The scoring formula alone, swapped into this engine (`--score nse_blend`),
everything else unchanged:

| variant | CAGR % | max DD % | Sharpe |
|---|---|---|---|
| current: 12-1 / vol, monthly | 11.8 | **-43** | 0.57 |
| NSE blend, skip 0, monthly | 7.5 | -53 | 0.41 |
| NSE blend, skip 1, monthly | 5.4 | -51 | 0.34 |
| NSE blend, skip 0, semi-annual | **16.4** | -49 | **0.65** |

The 6-month half pulls the monthly blend down, the same thing the 6-month
lookback test showed. Semi-annual NSE blend wins this one run, but on timing
it is a coin toss. Across three start dates it returned **16.4%, 10.6% and
14.3%**, with drawdowns of -49% to -54%. Monthly 12-1 over the same three
starts returned 11.8% to 14.4%, with drawdowns of -41% to -44%. Semi-annual
12-1 did worst, at 8.9% to 10.0%. With only ~17 rebalances in nine years, one
badly timed rebalance decides the result.

**Not adopted.** On average the semi-annual NSE blend roughly ties monthly
12-1 (13.8% vs 13.2% across the three starts), with a much wider spread and
drawdowns 6 to 10 points deeper. Nothing here beats the current ranking
reliably. Not tested: the index's market-cap weighting and 5% cap, since this
engine holds equal weights. That is a different portfolio, not a different
ranking.

The dashboard no longer shows the 12-1 figure, which nobody can check against
a broker. It shows plain 12M % and 1M % returns, plus the Score the rank
actually sorts by.

# Backtest

```bash
PYTHONPATH=. .venv/bin/python run_backtest.py --shuffle
```

`--shuffle` runs a randomised-entry control alongside the strategy. **If the
strategy does not clearly beat its own shuffled control, the entry rules are
adding nothing.** Also accepts `--start` / `--end` for holdout testing.


# Experiments (train / holdout)

```bash
PYTHONPATH=. .venv/bin/python run_experiment.py                       # compare on train
PYTHONPATH=. .venv/bin/python run_experiment.py --holdout --variant X # ONE shot
```

Variants live in `VARIANTS` in `run_experiment.py`. Each should embody a
*hypothesis about why the strategy fails*, not a threshold sweep -- so that a win
is interpretable rather than accidental. Choose on train, then run the holdout
once. Never iterate against holdout results.

---

See also: [strategy](strategy.md) for the rules being tested, [data](data.md) for why survivorship bias mattered so much.
