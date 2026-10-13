# stock-signals

**A portfolio advisor for NSE equities.** It reads Indian company filings, ranks
a survivorship-free universe, and tells you what to do with what you already
own — including when the honest answer is that it found nothing.

> **[→ Feature overview](https://claude.ai/code/artifact/3e4ddd96-ba4b-4d97-9220-34ef68255dc0)** —
> a visual walkthrough. *(Private link.)*

**Read-only with respect to your broker.** It never places, modifies or cancels
an order — GTT included. It computes and explains; you decide and click.
Decision support, not investment advice.

---

## Quick start

```bash
uv venv && uv pip install -r requirements.txt

PYTHONPATH=. .venv/bin/python import_kite_holdings.py   # Kite .csv/.xlsx dropped in config/

PYTHONPATH=. .venv/bin/python run_market_ingest.py --years 10   # ~70 min, once
PYTHONPATH=. .venv/bin/python run_backfill.py --universe nifty500 --years 10
PYTHONPATH=. .venv/bin/python fetch_fundamentals.py

PYTHONPATH=. .venv/bin/python run_ui.py                            # dashboard on :8501
```

Optional — jobs that run on this machine by themselves (details, checking and
removing them in [Scheduled jobs](#scheduled-jobs)):

```bash
PYTHONPATH=. .venv/bin/python install_schedule.py --day saturday --portfolio 2000000   # weekly report
PYTHONPATH=. .venv/bin/python install_schedule.py --swing-daily                        # swing record, weekdays 19:30
```

## Commands

| | |
|---|---|
| `run_ui.py` | Dashboard: holdings and market, localhost only |
| `run_advisor.py` | Verdicts on your book, as text |
| `run_momentum.py` | Rank the universe |
| `run_pit_momentum.py` | Survivorship-free backtest |
| `run_weekly.py` | Refresh everything, then report |
| `install_schedule.py` | Install / check / remove the scheduled jobs (weekly, `--swing-daily`, `--fno-intraday`) |
| `import_kite_holdings.py` | Kite holdings download → `config/holdings.csv` |
| `set_buckets.py` | Pre-fill core / satellite / legacy |
| `record_momentum.py` | Save this month's momentum picks to `history/` |
| `run_fno.py` | F&O: PCR, OI support/resistance, range to expiry (`--excel` for a workbook) |
| `run_fno_backtest.py` | How often that range actually held (`--ingest` first) |
| `record_fno.py` | Monthly F&O forward log → `history/fno_ranges.xlsx`, scored at expiry |
| `run_option_backtest.py` | What a simple option rule would have done, after costs (`--compare`, `--excel`) |
| `run_swing.py` | Swing setups in the Nifty 200/500, scored, with a trade plan (`--all`, `--capital`, `--backtest`, `--recorded`) |
| `run_swing_experiment.py` | Alternative swing rankings on a train / holdout split |
| `record_swing.py` | Record the day's swing scan in `history/swing/`, rebuild `history/swing_picks.xlsx` with outcomes |
| `run_swing_daily.py` | Refresh prices, then record the swing scans -- what the daily job runs |

Scripts need `PYTHONPATH=.`; the launchers above run fine via `.venv/bin/python`.
Full flags in [operations](docs/operations.md).

## Recording what you buy

After buying, download your holdings from Kite (Console's `.xlsx` carries the
long/short-term split; the web `.csv` also works), put the file in `config/`,
and import it. Name the momentum buys with `--satellite` so they are rotated on
rank:

```bash
PYTHONPATH=. .venv/bin/python import_kite_holdings.py --satellite CPPLUS SANSERA
```

- Quantities and average prices come from Kite; your `bucket`, `notes` and
  `purchase_date` are kept for every stock you still hold. The old file is
  backed up to `config/holdings.csv.bak`.
- **A new holding imported without `--satellite` has a blank bucket, and blank
  is not satellite** -- the framework only ever suggests core or legacy. The
  importer lists new holdings and reminds you.
- `--satellite` overrides any existing bucket, and warns about any symbol that
  is not in the Kite file.
- Optionally add `purchase_date` (YYYY-MM-DD) to the new rows in
  `config/holdings.csv`: Kite does not export it, and the trailing stop is
  measured from it. It is kept on later imports.
- A stock has one bucket. Buying more of a core holding keeps the whole
  position core.

Then re-run the advisor, so sizing, sector caps and the decision log include
the new positions.

## Momentum history

`record_momentum.py` saves the month's picks -- every stock in the top 15 on
either lookback, with ranks, scores, the price on the day and the NIFTY 500
level -- to `history/momentum_picks.xlsx`, one tab per month named like
`Oct 2026`. Each new month is a new tab at the end. It records once per month
(the first run of the month is kept; `--force` replaces that tab), and the
Saturday job runs it. Started October 2026.

The point is to measure later how the picks actually did against the backtest,
from the prices on the day they were suggested -- recomputing old months with
today's code would measure today's code instead. The file is meant for git;
what you held is deliberately not in it.

## Swing trading

The dashboard's **Swing** tab looks for short-term trades -- days to a few
weeks -- in the Nifty 200 or 500: liquid stocks in an uptrend showing a
**pullback** to the 20 EMA or a **breakout** from a tight base. Each gets a
0-100 score and a fixed plan: buy above the signal day's high, stop under
the recent swing low, half at 1.5R, the rest at 3R or after 15 sessions,
sized so a stop-out loses 0.75% of capital.

1. Pick the universe and your capital, press **Scan** (about 30 s, cached for
   the day).
2. The top 10 are shown; **Show full list** adds every eligible stock and why
   it is not suggested. Pick a stock under the list for its trade plan.
3. **Save top 10 to Excel** (next to Scan, greyed out until a scan has
   picks) records the scan -- see below.
4. **Methodology** and **Backtest** sub-tabs explain the rules and show how
   they have done.

**Read the backtest before using the list.** From 2018 to 2026 the setups
beat plain uptrend stocks by only about 0.04R a trade, and the score did not
pick better trades than random order -- nor did any alternative ranking
tested on a train / holdout split. The list is setups to review, not a
ranking to trust. Details in [swing trading](docs/swing.md).

```bash
PYTHONPATH=. .venv/bin/python run_swing.py                       # top 10, Nifty 200
PYTHONPATH=. .venv/bin/python run_swing.py --universe nifty500 --all --capital 1000000
PYTHONPATH=. .venv/bin/python run_swing.py --backtest            # refresh the Backtest sub-tab
```

## Swing history

Every scan is written down once, in full, so the live suggestions can be
judged later from what was actually shown -- rebuilding old days with
today's code would measure today's code. Plain CSV in git, only ever
appended to:

| File | One row per | Holds |
|---|---|---|
| `history/swing/runs.csv` | scan (price date × universe) | market backdrop, breadth, NIFTY 500 close, code commit, the exact `swing:` settings |
| `history/swing/scans/YYYY-MM.csv` | eligible stock in a scan | rank, suggested or not, setup, score and its parts, entry / stop / targets, the day's prices and every input the score used |
| `history/swing/members.csv` | index member, when the list changes | the universe as it stood -- a point-in-time universe for future backtests |
| `history/swing_picks.xlsx` | top-10 pick | **a report, rebuilt from the CSVs on every save**: each pick played forward -- *pending*, *not triggered*, *open* or *closed* with its result in R and % after costs -- plus a Summary sheet |

- A price date and universe already recorded are kept; `record_swing.py
  --force` replaces that one scan. Earlier scans are never touched.
- Outcomes are not stored: they are replayed from prices with **each scan's
  own settings**, so editing `scoring.yaml` later does not re-score old picks.
- Edits made in Excel are overwritten on the next save; close the workbook
  before saving, or Excel's copy can overwrite the update.
- About 10 MB of CSV a year for both universes.

```bash
PYTHONPATH=. .venv/bin/python record_swing.py                    # record today's Nifty 200 scan
PYTHONPATH=. .venv/bin/python record_swing.py --universe nifty500
PYTHONPATH=. .venv/bin/python record_swing.py --update           # rebuild the workbook only
PYTHONPATH=. .venv/bin/python run_swing.py --recorded            # replay the recorded scans
```

**Commit `history/swing/` yourself** now and then (the daily job never
touches git). Until it is on GitHub, the record exists only on this Mac.

## Scheduled jobs

Jobs are macOS launchd agents in `~/Library/LaunchAgents`. Install them
yourself -- each changes what this machine does on its own. None of them
touches git or your broker.

| Job | When | Runs | Install | Check | Remove |
|---|---|---|---|---|---|
| Weekly report | Saturday 09:30 | `run_weekly.py`: refresh everything, advisor report, momentum and F&O records | `install_schedule.py --day saturday` | `install_schedule.py --status` | `install_schedule.py --remove` |
| **Swing daily** | Weekdays 19:30 | `run_swing_daily.py`: catch up Nifty 500 prices, record the Nifty 200 and 500 scans, rebuild `swing_picks.xlsx` | `install_schedule.py --swing-daily` | `install_schedule.py --swing-daily --status` | `install_schedule.py --swing-daily --remove` |
| F&O intraday | Weekdays 09:30-15:30, half-hourly | `snapshot_fno.py`, plus a keep-awake agent | `install_schedule.py --fno-intraday` | `install_schedule.py --fno-intraday --status` | `install_schedule.py --fno-intraday --remove` |

All commands run as `PYTHONPATH=. .venv/bin/python install_schedule.py ...`.

**Swing daily job, in detail:**

- **Time:** 19:30 on weekdays, after NSE's end-of-day files are out. Change
  it with `--hour` / `--minute`, e.g. `--swing-daily --hour 20 --minute 15`
  (re-running the install replaces the old schedule).
- **Asleep or off:** launchd runs a missed job when the Mac next wakes. That
  is harmless whenever it lands: the job records the latest price date once.
- **Holidays and repeats:** no new price date means nothing new is written.
  Weekends are skipped outright.
- **Which universes:** `swing.record_universes` in `config/scoring.yaml`
  (default Nifty 200 and Nifty 500).
- **Logs:** `reports/swing_daily.log` (each step's output, appended per run),
  plus `reports/swing_daily.out.log` / `.err.log` from launchd. A failure also
  raises a macOS notification.
- **Run it by hand:** `PYTHONPATH=. .venv/bin/python run_swing_daily.py`
  (`--skip-refresh` to record on the prices already on file).

**Removing the swing job:**

```bash
PYTHONPATH=. .venv/bin/python install_schedule.py --swing-daily --remove
PYTHONPATH=. .venv/bin/python install_schedule.py --swing-daily --status   # "not installed"
```

This unloads the agent and deletes
`~/Library/LaunchAgents/com.arpanmuk.stock-signals.swing-daily.plist`.
Nothing else is removed: the record in `history/swing/`, the workbook and the
logs stay. Delete those by hand only if you want the history gone too -- it
cannot be rebuilt.

## Documentation

| | |
|---|---|
| [**Portfolio advisor**](docs/advisor.md) | Verdicts, cash constraint, concentration, tax, decision log |
| [**Dashboard**](docs/dashboard.md) | Two sections, badges, the two target frames |
| [**Strategy**](docs/strategy.md) | Momentum ranking, fundamentals, the lender rubric |
| [**Validation**](docs/validation.md) | What the backtests found, including what failed |
| [**Data**](docs/data.md) | Sources, five silent NSE traps, freshness |
| [**Automation**](docs/automation.md) | The Saturday job and its failure modes; all jobs in [Scheduled jobs](#scheduled-jobs) |
| [**Operations**](docs/operations.md) | Setup, watchlist, tuning, tests |
| [**Swing trading**](docs/swing.md) | Setups, trade plan, backtest, alternative rankings, the forward record |
| [**F&O analysis**](docs/f_o/README.md) | PCR, OI walls, expected range -- and [whether it works](docs/f_o/validation.md); [option rule backtest](docs/f_o/backtest.md) |
| [**Mutual funds**](docs/mutual_funds.md) | PPFAS Flexi Cap: monthly portfolio changes, read as manager decisions |
| [**Code layout**](docs/layout.md) | Where things live |
| [ROADMAP](ROADMAP.md) | What is built, and what is still wrong with it |

## The idea in one table

| verdict | meaning |
|---|---|
| **B** Buy / Add | Below its intended weight, quality intact |
| **T** Trim | The position is too **big**, not bad — you keep it |
| **S** Exit | The **reason to own it** is gone — sell it all |
| **S!** Exit, urgent | A balance-sheet fact, not a judgement call |
| **H** Hold | No action, stated rather than left as silence |

Conflating trim and exit is how people average down into failures and take
profits on their winners. Holdings sit in one of three buckets — **core** (never
sold on rank), **satellite** (rotates on rank), **legacy** (red flags apply,
rank ignored; the safe default for anything bought before this existed).

## Results

| | 9-year CAGR, avg of 4 start dates |
|---|---|
| Momentum rotation, 9-1 (main) | **23.0%** (20.0 - 24.4) |
| Momentum rotation, 12-1 (alongside) | 20.2% |
| NIFTY 500 index | 10.2% |
| Random-pick control | 9.3% |
| v1 signal rules *(retired)* | 1.3% |

After realistic Indian costs -- STT, stamp duty, exchange fees, GST, slippage
-- but **before tax** (most gains are short-term). Momentum beat all 10
random controls drawn from the same universe by about 15 points (4.9
standard deviations), held up in both halves of the period, and returned
28% on liquidity ranks 201-500, a universe none of its settings were tuned
on. **The fall is the weak point: -36% to -42%, against the index's -38%.**
Neither a trailing stop nor volatility targeting fixed it; both cost more
return than they saved.

*Corrected 2026-10-11:* earlier figures (11.4%, and everything before it)
ran on raw prices that read every split, bonus and demerger in a held stock
as a loss, and a ticker change or a spell in the trade-for-trade series as
a forced sale. See [data](docs/data.md) and [validation](docs/validation.md).
The live ranking was never affected.

Removing survivorship bias, measured on the old history, cost 14 points of
apparent return (30.8% → 16.9%) and dropped the random control from 17.2% to
5.0% -- the bias measured directly.

**Honest split: roughly 70% of this framework's value is monitoring and
discipline; perhaps 30% is momentum alpha.** Two ideas that sounded good and
failed their out-of-sample tests are recorded in
[validation](docs/validation.md) rather than quietly dropped.

## Scale

```
3.84M   daily price rows          175   tests, one per bug found
3,638   symbols, delisted included  489   ETFs excluded, with reasons
2,607   trading days                  0   orders it can place
```

Everything runs locally. Free public data throughout: no API key, no
subscription, no broker credentials.
