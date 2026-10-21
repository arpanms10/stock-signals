# Validation

How the backtests work, and how to run them yourself.

[← back to the README](../README.md)

---

Every idea in this framework comes with a backtest you can run on your own
data. Run them after a refresh, or after changing a setting in
`config/scoring.yaml`, and judge the result yourself.

## Principles every backtest follows

- **Costs included.** STT, stamp duty, exchange fees, GST and slippage --
  about 0.35% per round trip. Returns are before tax.
- **Survivorship-free where it matters.** Testing only on companies that still
  exist today flatters every result. The momentum and factor backtests use a
  point-in-time universe rebuilt from the full-market bhavcopy, delisted names
  included. The GARP / QARP and results backtests offer `--universe pit` for
  the same reason.
- **Point in time.** Each rebalance only sees prices and filings published by
  that date. The live code and the backtest call the same functions, so the
  backtest is evidence about what you actually run.
- **Controls, not just the index.** An idea must beat random picks from the
  same universe with the same turnover (`--shuffle`), not just the index.
- **Train, then holdout once.** Compare variants on a train period, choose,
  then run the holdout a single time. Never iterate against holdout results;
  once seen, a holdout is no longer a clean test.
- **Rules fixed before the run.** Where a script has a pass rule, it is written
  in its docstring before the first run, so a pass can't be tuned into
  existence.
- **Continuous prices.** Splits, bonuses, demergers, ticker changes and
  trade-for-trade spells are adjusted so they don't read as losses
  ([data](data.md)). Dividends are not included, so returns are price-only;
  the NIFTY 500 benchmark is a price index too.

## Momentum

```bash
PYTHONPATH=. .venv/bin/python research/run_pit_momentum.py                        # as configured
PYTHONPATH=. .venv/bin/python research/run_pit_momentum.py --shuffle 10           # random-pick controls
PYTHONPATH=. .venv/bin/python research/run_pit_momentum.py --universe-skip 200 --universe-size 300   # ranks 201-500
```

Top 200 by liquidity, rebuilt monthly, 15 stocks, full costs. Reports CAGR,
worst fall, Sharpe, and the two halves of the period separately. Options for
testing variants:

| Flag | Tests |
|---|---|
| `--lookback-months`, `--skip-months` | 9-1 against 12-1, or no skipped month |
| `--score nse_blend` | NSE's Nifty200 Momentum 30 scoring in this engine |
| `--selection overlap` | holding only names in both the 9- and 12-month lists |
| `--risk-off-scale` | holding fewer stocks when the market is below its 200 DMA |
| `--stop pct\|atr`, `--stop-refill` | a trailing stop between rebalances |
| `--vol-target`, `--cash-yield-pct` | volatility targeting, with cash earning a liquid-fund rate |
| `--start`, `--end` | restricting to a train or holdout window |
| `--export` | trades and equity to Excel |

Run the start date a few days apart to see how much the result depends on
which days the rebalance falls.

## 52-week high and low volatility

```bash
PYTHONPATH=. .venv/bin/python research/run_factor_backtest.py
PYTHONPATH=. .venv/bin/python research/run_factor_backtest.py --holdout   # once
```

Two well-known factors run through the momentum engine: the point-in-time top
200 by liquidity, monthly, 15 names equal-weighted, costs included, no 200-DMA
gate. Compared with momentum, the NIFTY 500 and random picks. The pass rule is
in the script's docstring.

## Swing, GARP & QARP, quarterly results, mutual funds, F&O

Each has its own backtest, described with the feature:

| Feature | Command | Write-up |
|---|---|---|
| Swing setups and score | `scripts/run_swing.py --backtest`, `research/run_swing_experiment.py` | [swing](swing.md#backtest) |
| GARP & QARP | `scripts/run_picker.py --backtest` | [GARP & QARP](picker.md#backtest) |
| Results verdict and reaction | `research/run_results_backtest.py`, `research/run_drift_backtest.py` | [quarterly results](results.md#backtest-verdict-and-reaction) |
| Mutual fund states | `scripts/run_mf.py --backtest` | [mutual fund evaluation](mf_evaluation.md#backtest) |
| Expected ranges | `scripts/run_range_backtest.py` | [ranges](ranges.md#calibration) |
| F&O range and signals | `scripts/run_fno_backtest.py`, `research/run_option_backtest.py` | [F&O validation](f_o/validation.md), [option rules](f_o/backtest.md) |

Results are written to `config/*_calibration.json` and shown on the matching
dashboard page.

## Signal-rule backtest

```bash
PYTHONPATH=. .venv/bin/python research/run_backtest.py --shuffle
```

Walk-forward simulation of entry and exit rules on the watch list.
`--shuffle` runs a randomised-entry control alongside the strategy: **if the
strategy does not clearly beat its own shuffled control, the entry rules are
adding nothing.** Also accepts `--start` / `--end` for holdout testing.

## Experiments (train / holdout)

```bash
PYTHONPATH=. .venv/bin/python research/run_experiment.py                       # compare on train
PYTHONPATH=. .venv/bin/python research/run_experiment.py --holdout --variant X # ONE shot
```

Variants live in `VARIANTS` in `research/run_experiment.py`. Each should embody
a *hypothesis about why the strategy fails*, not a threshold sweep -- so that
a win is interpretable rather than accidental. Choose on train, then run the
holdout once.

---

See also: [strategy](strategy.md) for the rules being tested, [data](data.md)
for how the price history is kept continuous.
