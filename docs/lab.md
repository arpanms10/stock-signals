# Strategy lab

Test a new trading strategy the same way every earlier one was tested, in
about thirty lines of code, and keep testing it forward once it's live.

[← back to the README](../README.md)

---

## What the lab does

You write a ranking: a function that scores every stock on a date. The lab
does the rest, identically for every strategy:

| Step | What happens |
|---|---|
| Universe | The 200 most liquid NSE stocks on each rebalance date, rebuilt from the full-market history, delisted companies included |
| Costs | STT, stamp duty, exchange fees, GST and slippage on every trade |
| Tax | Each purchase is a lot; sales are taxed first in, first out at 20% short term and 12.5% long term above Rs 1.25 lakh, plus 4% cess; losses set off and carried as the law allows. Each year's tax is paid from the portfolio |
| Bars to beat | NIFTY 500; random picks from the same eligible stocks (5 seeds); and every eligible stock held in equal weight |
| Luck checks | Deflated Sharpe, a 90% range for the yearly edge, each calendar year, and each half of the window |
| Rule | A pass rule written and committed before the first run |
| Ledger | A record, in git, of which windows each idea has already seen |
| Forward | Strategies marked forward record their picks every month in `history/lab/` |

The dashboard's **Research → Strategy lab** page shows all of it.

## Commands

```bash
PYTHONPATH=. .venv/bin/python research/run_lab.py list                  # strategies, families, status
PYTHONPATH=. .venv/bin/python research/run_lab.py show high52           # its settings and rule
PYTHONPATH=. .venv/bin/python research/run_lab.py train high52          # 2018-07 .. 2022
PYTHONPATH=. .venv/bin/python research/run_lab.py holdout high52        # 2023 .. today, once
PYTHONPATH=. .venv/bin/python research/run_lab.py robustness high52     # liquidity ranks 201-500
PYTHONPATH=. .venv/bin/python research/run_lab.py measure momentum_9_1  # whole window, no verdict
PYTHONPATH=. .venv/bin/python research/run_lab.py compare high52 lowvol --weight 0.5
PYTHONPATH=. .venv/bin/python scripts/record_lab.py                     # forward picks, monthly
```

`train`, `holdout`, `robustness` and `measure` take `--tax current`
(default: today's rates on every sale), `--tax historical` (the rates in
force on each sale date) or `--tax none`, and `--no-exemption` if your
Rs 1.25 lakh is used up elsewhere. A train run takes about five minutes:
the strategy, its after-tax run, five random controls and the equal-weight
universe.

## Adding a strategy

1. **Write the ranking** in `lab/strategies.py`. It receives the engine's
   `Inputs`: wide date × stock frames of split-adjusted closes, daily
   returns, volatility and the 200 DMA, plus the settings. Return a frame
   of scores, higher is better, or a list of `(frame, weight)` to z-score
   and blend. A score on a date may only use rows up to that date; rolling
   windows and `shift()` keep it that way.

   ```python
   register(Strategy(
       "momentum_9_1_quarterly", "momentum",
       "9-1 momentum, rebalanced every quarter",
       pit.score_ram, {"lookback_months": 9, "rebalance_days": 63}))
   ```

   `settings` override the `momentum_strategy` block of
   `config/scoring.yaml`: how many names, enter and exit ranks, rebalance
   days, the 200-DMA gate, price and turnover floors, stops.
   `warmup_sessions` asks for more history than the default.

2. **Choose the family.** A variant of an existing idea belongs to its
   family. If the family's holdout is spent (momentum's is), the variant can
   only be measured and tracked forward, never holdout-tested. A new idea is
   a new family.

3. **Write the pass rule** in `lab/rules.py` before running anything: the
   checks train must pass, and the checks the holdout must pass. Metrics
   are listed in `lab/harness.py` `METRICS`; for example:

   ```python
   "my_idea": PassRule("2026-11-02",
       train=(C("excess_after_tax_pct", ">=", 2.0),
              C("edge_vs_controls_sd", ">=", 1.0),
              C("both_halves_beat_index", ">=", 1)),
       holdout=(C("excess_after_tax_pct", ">", 0.0),),
       note="beat the index after tax by 2 points, in both halves"),
   ```

   Commit it. The ledger keeps the rule's fingerprint with every run.

4. **Run train.** If it fails, the idea is recorded as failed and its
   holdout stays unseen. Don't change the rule and run again: the ledger
   will then refuse the holdout, because a rule chosen with the train result
   in view isn't a test. Register a genuinely different idea under a new
   name instead.

5. **Run the holdout once**, if train passed. Commit
   `research/lab_ledger.json` with the result.

6. **Track it forward** by setting `status="forward"`. The Saturday job
   records its picks from the next month.

## The ledger

`research/lab_ledger.json` is the lab's memory, and it is in git because
it describes the research, not you. For each family it records whether the
2023+ holdout has been seen, other windows already looked at, and how many
variants have been tried. Families from before the lab were entered by
hand from the [roadmap](ROADMAP.md) and the feature write-ups; their trial
counts are lower bounds.

The harness refuses:

| Run | Refused when |
|---|---|
| train | there is no pass rule |
| holdout | the family's holdout is spent; train hasn't run or failed; the rule changed after train; it already ran |
| measure | the family's holdout is unseen (the window includes it) |

As of 2026-10-29 the holdout is **spent** for momentum, the v1 rules, the
results reaction, GARP / QARP, the mutual fund states and the F&O work. It
is **unseen** for 52-week high and low volatility (both failed train, so
it stays that way) and for swing (clean for one idea).

## Reading a result

- **CAGR and after tax.** "After tax" sells everything at the end and pays
  the tax, so it compares fairly with the index held throughout and sold
  once (`excess_after_tax_pct`). A strategy that trades monthly pays mostly
  short-term tax; momentum loses about 3 points a year to it.
- **vs random picks.** The same number of names drawn at random from the
  same eligible stocks. This is what the ranking adds; the index comparison
  also includes what the universe and the 200-DMA gate add.
- **vs the universe.** Every eligible name in equal weight: buy-and-hold of
  what the strategy picks from.
- **Deflated Sharpe.** The probability the edge over the index is real,
  allowing for how many variants of the idea were tried (Bailey and Lopez
  de Prado, 2014). Above 0.95 is convincing; a short window rarely gets
  there.
- **90% range.** Resampled months of daily excess returns. A range that
  includes zero means the window can't rule out no edge.
- **Halves.** An edge that comes from one half only is a regime, not a
  strategy.

## Forward records

`scripts/record_lab.py` ranks each forward strategy at the latest close
exactly as the engine's rebalance would, keeps last month's names while
they rank within the exit rank, and appends the picks to
`history/lab/<strategy>.csv`. The first run of a month records it; the
Saturday job runs it. `--report` shows each month's equal-weight return to
the next record against NIFTY 500, before costs.

The record follows the backtest's universe, the 200 most liquid names, not
the Nifty 500 list the Momentum page ranks, so it measures what the
backtest numbers describe.

## Comparing and blending

`run_lab.py compare` shows how strategies' monthly returns move together
over the same window, and what a fixed blend of two would have done,
rebalanced monthly before costs. The page's **Compare and blend** section
does the same.

## What the lab doesn't cover yet

- Event strategies (a buy on a results reaction, held N sessions) and option
  strategies run on their own engines (`research/run_drift_backtest.py`,
  `research/run_option_backtest.py`) and aren't in the lab.
- Dividends: returns are price-only, like the NIFTY 500 benchmark.
- Grandfathering of pre-2018 gains, surcharge, and your other income.
