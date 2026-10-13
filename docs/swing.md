# Swing trading

Pullback and breakout setups in the Nifty 200 or 500, scored, with a trade
plan each -- and what the backtest says about them.

[← back to the README](../README.md)

---

```bash
PYTHONPATH=. .venv/bin/python run_swing.py                        # top 10, Nifty 200
PYTHONPATH=. .venv/bin/python run_swing.py --universe nifty500 --all
PYTHONPATH=. .venv/bin/python run_swing.py --capital 1000000      # with share counts
PYTHONPATH=. .venv/bin/python run_swing.py --backtest             # -> config/swing_calibration.json
```

The dashboard's **Swing** tab shows the same scan (top 10, or the full list
with the toggle), the methodology, and the latest backtest. Nothing is
computed until **Scan** is pressed; the result is cached for the day.

## Recording the picks

```bash
PYTHONPATH=. .venv/bin/python record_swing.py               # today's top 10
PYTHONPATH=. .venv/bin/python record_swing.py --update      # outcomes only
```

`history/swing_picks.xlsx` keeps what the tab actually suggested: the top 10
for each price date and universe, with the entry, stop and targets as shown.
Each date is recorded once; a second run on the same data keeps the first,
and `--force` replaces it. Every run also plays unsettled picks forward
through the prices since, with the recorded plan. Each pick is then marked
*not triggered* (the buy-stop expired), *open* (valued at the last close, not
a result yet) or *closed*, with the exit reason and the result in R and %
after costs. The **Summary** sheet totals the closed trades by setup. This
forward record is the evidence that can confirm or overturn the backtest. The
Swing tab's **Save top 10** button does the same.

## How a suggestion is made

A swing trade holds for days to a few weeks, with the stop, targets and time
limit fixed before entry. Delivery (CNC) only, so overnight gaps are part of
the risk. Three gates, each on data up to the scan date only:

1. **Eligible** -- above a rising 200 DMA with the 50 DMA above it; price at
   least ₹50; 20-day average traded value at least ₹20 cr; daily ATR between
   1% and 6% of price.
2. **Setup** -- one of:
   - *Pullback*: the low came within 0.5 ATR of the 20 EMA in the last three
     sessions, RSI(14) is 38-58, and today closed up and above the EMA.
   - *Breakout*: close above the prior 20-session high on at least 1.5x
     average volume, out of a base no wider than 15%, within 10% of the
     52-week high.

   Names reporting quarterly results within 5 days (NSE board-meeting
   calendar) are held back.
3. **Score, 0-100** -- orders the setups: trend 25%, relative strength
   against NIFTY 500 (percentile among today's eligible names) 25%, setup
   quality 30%, up/down volume 10%, stop tightness 10%.

**Trade plan**: buy-stop 0.1% above the signal high, valid two sessions.
Stop at the 5-session low minus 0.25 ATR, kept between 1 and 3 ATR below the
entry. Book half at 1.5R and move the stop to entry; the rest exits at 3R or
at the close after 15 sessions. Size so a stop-out loses 0.75% of capital,
capped at 15% of capital in one name. About 0.35% round-trip costs.

The market backdrop (NIFTY 500 against its 200 DMA, and the share of the
universe above its 50 DMA) is shown with every scan. It doesn't filter
anything unless `regime_gate: true`.

Every number is in `config/scoring.yaml` under `swing:`.

## What the backtest found (2026-10-13)

Nifty 200, January 2018 to October 2026. Every signal is played out with the
plan above, after costs. Gaps through the stop fill at the open, and a bar
that touches both the stop and a target counts as the stop.

| | trades | win % | avg R | avg % per trade |
|---|---|---|---|---|
| All eligible (uptrend, no setup) | 97,689 | 46.1 | +0.007 | +0.19 |
| Any setup | 17,732 | 47.8 | +0.043 | +0.34 |
| **Top 10 by score** (what the tab suggests) | 11,543 | 47.1 | +0.029 | +0.28 |
| Pullback | 15,300 | 47.5 | +0.041 | +0.32 |
| Breakout | 2,438 | 49.6 | +0.051 | +0.41 |

- **The score doesn't rank.** By score quintile, the lowest band did best
  (+0.10R) and the other four were flat (0.00 to +0.05R). Split into
  2018-22 and 2023-26, no score component and no raw input (RSI, ATR,
  volume multiple, relative strength, stop width, close location, distance
  from the high) was better at its top end in both halves.
- **The setups add a little.** They beat plain uptrend names by about 0.04R
  per trade, which is about 0.15% per trade more after costs. It's positive,
  but small.
- **The backdrop doesn't matter here.** Setups averaged +0.05R when the
  backdrop was favourable, +0.02R when mixed and +0.07R when unfavourable,
  so a regime gate would not have helped. That's why it's off.
- **The results-week filter is neutral.** From 2024 (when stored results
  dates begin), setups with results due within 5 days averaged -0.04R, and
  the rest -0.05R.
- **As a portfolio:** taking the day's top 10 into 8 slots made **3.4% a
  year** against **9.9%** for NIFTY 500 over the same span, with a worst
  fall of 30% in closed-trade equity. Results swing by
  year, from +0.23R (2023) to -0.18R (2026 to date).

The universe is today's index list applied backwards, which flatters the
absolute numbers. The comparisons between rows share that bias.

**Decision (2026-10-13):** ship the tab as built, calibrated: suggestions
are shown with the backtest verdict above them, presented as setups to
review rather than a ranking to trust. Same approach as the
[F&O view](f_o/validation.md). A ranking that holds out-of-sample would have
to come from a different idea, not from tuning these weights on the same
data.

## Alternative rankings, train / holdout (2026-10-13)

```bash
PYTHONPATH=. .venv/bin/python run_swing_experiment.py                        # train
PYTHONPATH=. .venv/bin/python run_swing_experiment.py --holdout --ranking X  # once
```

The same setups and plan; only the order of the day's setups changes. Train
2018-22, holdout 2023-26. The pass rule was fixed before the first run:

- the top 10 must beat all setups by at least 0.05R,
- the quintiles must rise (Spearman ≥ 0.7),
- the top 10 must lead in at least 4 of 5 train years.

At most one ranking would go to the holdout, and only once.

| Ranking (train, 2018-22) | top 10 avg R | edge over all setups | quintiles, worst → preferred | rho | years ahead |
|---|---|---|---|---|---|
| Shipped score | +0.031 | −0.014 | +0.10, −0.01, +0.03, +0.04, +0.06 | 0.0 | 1/5 |
| 9-1 momentum / vol | +0.037 | −0.007 | +0.01, +0.07, +0.06, +0.01, +0.08 | 0.3 | 2/5 |
| Deepest 5-day dip | +0.035 | −0.010 | +0.04, +0.03, +0.03, +0.13, +0.00 | −0.3 | 1/5 |
| Lowest volatility | +0.032 | −0.013 | +0.09, +0.07, −0.00, +0.04, +0.03 | −0.7 | 2/5 |
| Relative strength alone | +0.033 | −0.012 | +0.10, +0.04, −0.03, +0.00, +0.11 | 0.1 | 1/5 |
| **Random order** | **+0.041** | **−0.004** | | 0.6 | 2/5 |
| Wider stop *(seen in 2023-26 earlier, so not eligible)* | +0.034 | −0.011 | +0.03, +0.02, +0.07, +0.03, +0.07 | 0.5 | 2/5 |

All setups averaged +0.045R over the same period. The top 10 by any rule
falls short of that partly because days with many setups (broad pullbacks)
were good days, and a top-10 cut takes only part of them. That's why random
order is the real bar, and **no ranking beat random order**.

RSI(2) mean reversion was tested as a separate setup: buy the next open
after RSI(2) falls below 10 in an uptrend, sell on the first close above the
5-day average or after 5 sessions, with a 3 ATR stop. It won 59% of trades
but averaged −0.018R after costs, and taking the lowest RSI(2) first did
worse (−0.032R).

**Outcome:** nothing passed, so the holdout was not run and stays clean for a
future idea. Within these setups, which ones are taken matters less than
whether any are taken: the setups' edge over plain uptrend names (about
+0.04R) is the only effect that has held up. The Swing tab is unchanged.
