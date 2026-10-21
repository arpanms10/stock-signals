# Swing trading

Pullback and breakout setups in the Nifty 200 or 500, scored, with a trade
plan each, and a backtest to judge them by.

[← back to the README](../README.md)

---

```bash
PYTHONPATH=. .venv/bin/python scripts/run_swing.py                        # top 10, Nifty 200
PYTHONPATH=. .venv/bin/python scripts/run_swing.py --universe nifty500 --all
PYTHONPATH=. .venv/bin/python scripts/run_swing.py --capital 1000000      # with share counts
PYTHONPATH=. .venv/bin/python scripts/run_swing.py --backtest             # -> config/swing_calibration.json
```

The dashboard's **Swing** page shows the same scan (top 10, or the full list
with the toggle), the methodology, and the backtest. Nothing is
computed until **Scan** is pressed; the result is cached for the day.

## The forward record

```bash
PYTHONPATH=. .venv/bin/python scripts/run_swing_daily.py               # refresh + record (what the job runs)
PYTHONPATH=. .venv/bin/python scripts/record_swing.py                  # record one scan now
PYTHONPATH=. .venv/bin/python scripts/record_swing.py --update         # rebuild the workbook only
PYTHONPATH=. .venv/bin/python scripts/run_swing.py --recorded          # replay the recorded scans
PYTHONPATH=. .venv/bin/python scripts/install_schedule.py --install daily   # weekdays 19:30
```

What the tab suggested on a day can't be rebuilt later, because the code,
the config and the index membership all move on. So every scan is written
down once, in full, as plain CSV under `history/swing/` (`records/swing_store.py`):

| File | One row per | Holds |
|---|---|---|
| `runs.csv` | scan (price date × universe) | market backdrop, breadth, NIFTY 500 close, code commit (and whether code was uncommitted), the exact `swing:` settings as JSON |
| `scans/YYYY-MM.csv` | eligible stock in a scan | rank, suggested or not, setup, results date, score and each component, entry / stop / targets, the day's OHLCV and every input the score used |
| `members.csv` | member, when the universe changes | the index list as it stood, so later backtests have a point-in-time universe instead of today's list applied backwards |

These files only grow. A price date and universe already recorded are left
alone; only `--force` replaces that one scan. A new month starts a new file.
Recording the Nifty 200 and Nifty 500 (`swing.record_universes`) adds about
10 MB of CSV a year.

**Outcomes are not stored.** They follow from prices and the recorded plan,
so they are recomputed whenever needed, each with the settings of the scan
it came from. A later change to `scoring.yaml` doesn't re-score old picks,
and a new exit rule can be replayed over the same recorded entries.

`history/swing_picks.xlsx` is a report rebuilt from the record on every run:
the top 10 of each scan, with outcomes (*not triggered*, *open* or *closed*)
and a Summary sheet. Edits made in Excel are overwritten. The Swing tab's
**Save top 10 to Excel** button, next to Scan, records the scan and rebuilds
the workbook. The Backtest view shows the forward record above the
historical backtest.

**Keeping it gap-free.** `scripts/install_schedule.py --install daily` runs
`scripts/run_swing_daily.py` every weekday at 19:30. It catches Nifty 500 prices up
and records each universe. A missed run (Mac asleep) fires on wake, and a
holiday or second run writes nothing new. The job never touches git: commit
`history/swing/` yourself, for example with each week's other changes.

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

## Backtest

`scripts/run_swing.py --backtest` plays every past signal out with the plan
above, after costs, and writes `config/swing_calibration.json`, which the
Backtest sub-tab shows. Gaps through the stop fill at the open, and a bar that
touches both the stop and a target counts as the stop. It compares all
eligible uptrend names, any setup, the top 10 by score, and each setup type,
by win rate and average R, and breaks the score into quintiles so you can see
whether a higher score picked better trades.

The universe is today's index list applied backwards, which flatters the
absolute numbers. The comparisons between rows share that bias, and
`members.csv` builds a point-in-time list for later tests.

## Alternative rankings, train / holdout

```bash
PYTHONPATH=. .venv/bin/python research/run_swing_experiment.py                        # train
PYTHONPATH=. .venv/bin/python research/run_swing_experiment.py --holdout --ranking X  # once
```

The same setups and plan; only the order of the day's setups changes. Rankings
are compared on a train period, and at most one goes to the holdout, once, if
it passes a rule fixed before the first run:

- the top 10 must beat all setups by at least 0.05R,
- the quintiles must rise (Spearman ≥ 0.7),
- the top 10 must lead in at least 4 of 5 train years.

Random order is included as the bar any ranking has to clear.
