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
