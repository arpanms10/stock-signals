# stock-signals

**A portfolio advisor for NSE equities.** It reads Indian company filings, ranks
a survivorship-free universe, and tells you what to do with what you already
own — including when the honest answer is that it found nothing.

> **New here? → [Getting started](GETTING_STARTED.md)**: install, set up and
> open the dashboard in plain steps, no coding needed. Unfamiliar words are in
> the [glossary](docs/glossary.md).

**Read-only with respect to your broker.** It never places, modifies or cancels
an order — GTT included. It computes and explains; you decide and click.
Decision support, not investment advice.

---

## Quick start

```bash
python3 setup_project.py            # guided: environment, databases, your files, data, jobs
.venv/bin/python run_ui.py          # dashboard on http://localhost:8501
```

The setup asks before each step, creates `.venv` itself, never overwrites a
file you already have, and can be re-run to resume a download.
`python3 setup_project.py --check` reports what is set up, missing or stale.
On Windows use `py setup_project.py` and `.venv\Scripts\python run_ui.py`.
[Getting started](GETTING_STARTED.md) walks through it.

By hand, if you prefer:

```bash
uv venv && uv pip install -r requirements.txt
PYTHONPATH=. .venv/bin/python run_market_ingest.py --years 10      # ~70 min, once
PYTHONPATH=. .venv/bin/python run_backfill.py --universe nifty500 --years 10
PYTHONPATH=. .venv/bin/python fetch_fundamentals.py
PYTHONPATH=. .venv/bin/python install_schedule.py --install recommended   # optional
```

## Commands

| | |
|---|---|
| `setup_project.py` | Guided setup on a new computer; `--check` for status ([getting started](GETTING_STARTED.md)) |
| `run_ui.py` | Dashboard, localhost only: Today, Portfolio, Ideas, Market and Research menus ([dashboard](docs/dashboard.md)) |
| `record_ranges.py` / `run_range_backtest.py` | Today's expected ranges: forward log and calibration ([ranges](docs/ranges.md)) |
| `run_advisor.py` | Verdicts on your book, as text |
| `run_momentum.py` | Rank the universe |
| `run_pit_momentum.py` | Survivorship-free backtest |
| `run_factor_backtest.py` | 52-week high and low volatility on the same engine, pre-registered rule; neither passed ([validation](docs/validation.md)) |
| `run_weekly.py` | Refresh everything, then report |
| `install_schedule.py` | Scheduled jobs on macOS, Linux or Windows: `--list`, `--status`, `--install`, `--remove` |
| `import_kite_holdings.py` | Broker holdings download → `config/holdings.xlsx` (or `.csv`) |
| `set_buckets.py` | Pre-fill core / satellite (trade is only ever set by you) |
| `record_momentum.py` | Save this month's momentum picks to `history/` |
| `run_fno.py` | F&O: PCR, OI support/resistance, range to expiry (`--excel` for a workbook) |
| `run_fno_backtest.py` | How often that range actually held (`--ingest` first) |
| `record_fno.py` | Monthly F&O forward log → `history/fno_ranges.xlsx`, scored at expiry |
| `run_option_backtest.py` | What a simple option rule would have done, after costs (`--compare`, `--excel`) |
| `run_swing.py` | Swing setups in the Nifty 200/500, scored, with a trade plan (`--all`, `--capital`, `--backtest`, `--recorded`) |
| `run_swing_experiment.py` | Alternative swing rankings on a train / holdout split |
| `record_swing.py` | Record the day's swing scan in `history/swing/`, rebuild `history/swing_picks.xlsx` with outcomes |
| `run_swing_daily.py` | Refresh prices, record the swing scans, check the watch list for stops and targets, score new results -- what the daily job runs |
| `run_results.py` | Quarterly results: score new filings for holdings, watch list and picks; price reaction; who reports next |
| `run_results_backtest.py` | Whether the results verdict predicts post-results drift (Nifty 200, 24 quarters) |
| `run_drift_backtest.py` | Whether buying stocks that rallied on their results beats the universe after costs (point-in-time universe) |
| `run_picker.py` | GARP and QARP shortlists for the Nifty 200 from filings (`--refresh`, `--as-of`, `--backtest`) |
| `run_mf.py` | Mutual funds vs their category: EXIT / TRIM / ADD / WATCH for your MF book (`config/mf_holdings.csv` or `.xlsx`, % only) or any named fund (`--refresh`, `--compare`, `--excel`, `--backtest`) |

Scripts need `PYTHONPATH=.`; the launchers above run fine via `.venv/bin/python`.
Full flags in [operations](docs/operations.md).

## Recording what you buy

After buying, download your holdings from Kite (Console's `.xlsx` carries the
long/short-term split; the web `.csv` also works), put the file in `config/`,
and import it. Name anything bought purely to trade with `--trade` so it is
rotated on rank:

```bash
PYTHONPATH=. .venv/bin/python import_kite_holdings.py --trade CPPLUS SANSERA
```

- Quantities and average prices come from Kite; your `bucket`, `notes` and
  `purchase_date` are kept for every stock you still hold. The old file is
  backed up to `config/holdings.xlsx.bak` (or `.csv.bak`).
- **A new holding imported without `--trade` has a blank bucket, and blank is
  not trade** -- the framework only ever suggests core or satellite. The
  importer lists new holdings and reminds you.
- `--trade` overrides any existing bucket, and warns about any symbol that
  is not in the Kite file.
- Optionally add `purchase_date` (YYYY-MM-DD) to the new rows in
  `config/holdings.xlsx`: Kite does not export it, and the trailing stop is
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

The dashboard's **Swing** page looks for short-term trades -- days to a few
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

## Quarterly results

The dashboard's **Quarterly results** page scores each quarterly filing for your
holdings, the watch list and the latest swing and momentum picks, straight
from NSE's XBRL. For an ordinary company it covers YoY revenue, EBITDA and
profit, whether revenue growth is speeding up against its own recent pace,
EBITDA margin against the same quarter last year, and how much of the profit
came from other income or one-offs. Banks and NBFCs are scored on NII,
credit cost and gross NPA movement instead. Each filing gets a verdict
(strong / in line / weak), plus the price reaction in multiples of the stock's
ATR. Other tables gain a **Results** column: this quarter's verdict, or the
date the company reports.

The verdict compares a company with **its own trend, not analyst estimates**,
which no free source carries. It feeds no ranking.
`run_results_backtest.py` tests whether it predicts anything once the
reaction is known; the tab's backtest section shows the answer. Details in
[quarterly results](docs/results.md).

```bash
PYTHONPATH=. .venv/bin/python run_results.py                  # score what's new, report
PYTHONPATH=. .venv/bin/python run_results.py --symbols TCS INFY
PYTHONPATH=. .venv/bin/python run_results_backtest.py         # does the verdict predict drift?
```

The swing daily job runs `run_results.py` after its price refresh.

**What the backtest found** (3,226 filings, 2021-26): strong cards beat the
index by 2% on the reaction day, so the scorecard measures what the market
cares about -- but once that reaction is known, the verdict adds little
(t = 1.6 at 60 sessions). The reaction itself keeps going: stocks that
rallied on their results beat those that sold off by 3.6% over the next 60
sessions (t = 2.6).

## GARP & QARP picker

The **GARP / QARP** page ranks the Nifty 200 two ways, on fundamentals taken
only from NSE's filings and the price on the day. **GARP** (growth at a
reasonable price) looks for core profit growing at least 15% a year, lastingly,
at a PEG of 1.5 or less. **QARP** (quality at a reasonable price) looks for the
top 40% on quality -- ROE, ROCE, cash conversion, margins and their stability,
debt -- at a P/E that isn't near its own three-year high. The same code
produces today's picks and every backtest rebalance.

**Read the backtest first.** From 2022 to 2026, GARP beat the equal-weight
universe by 2.3% a quarter (t = 2.0) once hindsight about index membership is
partly stripped out, and that edge shrinks further the harder the check. QARP
lagged by 2.6% a quarter, with milder drawdowns. Seventeen quarters is one
market regime. Details in [GARP & QARP](docs/picker.md).

```bash
PYTHONPATH=. .venv/bin/python run_picker.py --refresh     # new filings for the Nifty 200
PYTHONPATH=. .venv/bin/python run_picker.py               # today's two shortlists
PYTHONPATH=. .venv/bin/python run_picker.py --backtest
```

## Mutual funds

The **Mutual funds** page (and `run_mf.py`) judges any mutual fund against its
SEBI category and gives **EXIT / TRIM / ADD / WATCH / HOLD**, with reasons.
It uses month-end NAVs of Direct Growth plans from AMFI's free files, and
funds that later closed or merged are included.

**Your book, no amounts.** Put `config/mf_holdings.csv` **or**
`config/mf_holdings.xlsx` in config/; both are git-ignored, so keep only one.
List each fund and its share of your whole MF portfolio, summing to 100:

| fund | pct | held_over_1y |
|---|---|---|
| Parag Parikh Flexi Cap | 30 | yes |
| Kotak Emerging Equity | 20 | no |

- Fund names can be as you'd say them, including old names ("Axis
  Bluechip"), or AMFI codes. A vague name ("hdfc") lists candidates instead of
  guessing.
- In Excel, the first sheet is read and headers can be loose: "Fund Name",
  "Scheme", "Weight", "Allocation %". A column formatted as % works.
- `held_over_1y` is optional. It only changes the exit-load / short-term
  gains warning on a sell.

```bash
PYTHONPATH=. .venv/bin/python run_mf.py --refresh                 # NAV history: ~25 min once, then quick
PYTHONPATH=. .venv/bin/python run_mf.py                           # verdicts for your book
PYTHONPATH=. .venv/bin/python run_mf.py "ppfas flexi" "hdfc flexi cap" --compare
PYTHONPATH=. .venv/bin/python run_mf.py --excel                   # reports/mf/MF_Evaluation.xlsx
```

**What to expect.** It's good at organising your book and weak at predicting
which fund will do best. A survivorship-free backtest (531 funds since 2018)
found past category rank barely predicts the next year. Funds marked WEAK beat
their category median 44% of the time from 2022, and 50% in 2018-21. Funds
marked STRONG did no better than chance.

| Verdict | Worth |
|---|---|
| TRIM for size, category cap or near-twin funds | Act on it: it's arithmetic on your own book, not a forecast |
| EXIT | A prompt to compare with the alternatives it lists. The category's index fund is the low-regret switch. Mind exit load and tax first |
| WATCH | Look again in 3-6 months; good funds have bad years |
| ADD | No better than average at predicting the next year; sizing matters more than the pick |
| HOLD | Usually the right answer: switching costs tax and exit load |

Run it once a quarter. Debt, index and thematic funds aren't ranked, but they
still count towards your caps. Caps and thresholds are in
`config/mf_rules.yaml`. Details in [mutual fund evaluation](docs/mf_evaluation.md).

## Analyze any stock

The dashboard's **Analyze a stock** page looks at one stock on demand: any NSE listing,
not only the ranked universe. Type a ticker, a company name or an ISIN, then
pick an analyst:

- **Fundamental Analyst**: business quality from NSE filings and Yahoo.
  Growth, returns, balance sheet, cash conversion, promoter holding and
  pledge, valuation, and a stance (Strong / Sound / Mixed / Weak) that any red
  flag lowers one step.
- **Technical Analyst**: the chart. Trend state, indicator readings,
  delivery %, support and resistance from swing pivots, returns against the
  NIFTY 500, and today's swing setup if there is one.
- **Both**: the two verdicts side by side, then both reports.

Both describe the stock; neither predicts its price. A name that matches
several companies asks which one you meant. Details in
[analyze](docs/analyze.md).

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

Five jobs, defined once in `jobs.py` and installed with each system's own
scheduler: launchd on macOS, cron on Linux, Task Scheduler on Windows. Install
them yourself, or let `setup_project.py` ask. Each changes what the computer
does on its own; none touches git or your broker.

| Job | When | Runs | Feeds |
|---|---|---|---|
| **daily** | Weekdays 19:30 | `run_swing_daily.py`: catch up prices, record the swing scans and Today's ranges, date stops and targets in the watch list, score new results | Today, Stocks, Swing, Quarterly results |
| **weekly** | Saturday 09:30 | `run_weekly.py`: full-market history, fundamentals, F&O history and validation, momentum and F&O records, range calibration, a report in `reports/` | Market, Momentum, Analyze, F&O, Today |
| **monthly** | 5th, 10:00 | `run_mf.py --refresh`: month-end fund NAVs | Mutual funds, Compare funds |
| **quarterly** | 15 Feb/May/Aug/Nov, 10:00 | `run_picker.py --refresh`: Nifty 200 filings | GARP / QARP |
| fno-intraday *(optional)* | Weekdays 09:30–15:30, half-hourly | `snapshot_fno.py`, plus a keep-awake agent on macOS | F&O |

```bash
PYTHONPATH=. .venv/bin/python install_schedule.py --list                    # what each does
PYTHONPATH=. .venv/bin/python install_schedule.py --install recommended     # daily, weekly, monthly, quarterly
PYTHONPATH=. .venv/bin/python install_schedule.py --status
PYTHONPATH=. .venv/bin/python install_schedule.py --remove daily            # or: --remove all
```

- **Missed runs.** macOS and Windows run a job missed while the computer was
  off or asleep when it wakes. Linux cron skips it, and the next run catches
  up. Every job fetches only what is missing, so a late or repeated run is
  harmless.
- **Logs.** Each job writes `reports/<job>.log`. The weekly job also writes a
  dated report, and a failure raises a notification on macOS.
- **Options.** `--portfolio 2000000` gives the weekly report your total
  capital. `swing.record_universes` in `config/scoring.yaml` picks the
  universes the daily job records.
- **Removing a job** leaves its records (`history/`), workbooks and logs in
  place. Delete those by hand only if you want the history gone; it can't be
  rebuilt.

## Documentation

| | |
|---|---|
| [**Getting started**](GETTING_STARTED.md) | Install, set up and open the dashboard, in plain steps |
| [**Glossary**](docs/glossary.md) | Every term the dashboard uses, in plain language |
| [**Troubleshooting**](docs/troubleshooting.md) | NSE not answering, stale data, jobs, errors |
| [**Portfolio advisor**](docs/advisor.md) | Verdicts, cash constraint, concentration, tax, decision log |
| [**Dashboard**](docs/dashboard.md) | Two sections, badges, the two target frames |
| [**Strategy**](docs/strategy.md) | Momentum ranking, fundamentals, the lender rubric |
| [**Validation**](docs/validation.md) | What the backtests found, including what failed |
| [**Data**](docs/data.md) | Sources, five silent NSE traps, freshness |
| [**Automation**](docs/automation.md) | The Saturday job and its failure modes; all jobs in [Scheduled jobs](#scheduled-jobs) |
| [**Operations**](docs/operations.md) | Setup, watchlist, tuning, tests |
| [**GARP & QARP**](docs/picker.md) | Growth / quality at a reasonable price from filings, point in time, and the backtest |
| [**Quarterly results**](docs/results.md) | Scorecards from NSE filings, the reaction, and whether the verdict predicts drift |
| [**Analyze**](docs/analyze.md) | Fundamental and Technical Analysts for any one stock, by ticker, name or ISIN |
| [**Expected ranges**](docs/ranges.md) | Today's next-session and 5-session ranges for Nifty and core holdings, how often they held, the forward log |
| [**Swing trading**](docs/swing.md) | Setups, trade plan, backtest, alternative rankings, the forward record |
| [**F&O analysis**](docs/f_o/README.md) | PCR, OI walls, expected range -- and [whether it works](docs/f_o/validation.md); [option rule backtest](docs/f_o/backtest.md) |
| [**Mutual fund evaluation**](docs/mf_evaluation.md) | Any fund against its category, verdicts for your MF book, and the survivorship-free backtest (a weak edge) |
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
profits on their winners. Holdings are **core** (a decade or more, never sold
on rank) or **satellite** (one to ten years, not a trade: red flags and sizing
apply, and falling out of the ranking only prompts a review; the default).
Anything bought purely to trade is marked **trade** in `config/holdings.xlsx`,
and only that rotates on rank. An old `legacy` value is read as satellite.

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

## Disclaimer and licence

Decision support, not investment advice. Nothing here is a recommendation to
buy or sell any security. The author is not a SEBI-registered investment
adviser. Backtests describe the past; they don't promise the future. Check
anything you act on, and decide for yourself.

[MIT licence](LICENSE): use, change and share it freely, keeping the copyright
notice.
