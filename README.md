# stock-signals

**A portfolio advisor for NSE equities.** It reads Indian company filings, ranks
a survivorship-free universe, and tells you what to do with what you already
own.

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
PYTHONPATH=. .venv/bin/python scripts/run_market_ingest.py --years 10      # ~70 min, once
PYTHONPATH=. .venv/bin/python scripts/run_backfill.py --universe nifty500 --years 10
PYTHONPATH=. .venv/bin/python scripts/fetch_fundamentals.py
PYTHONPATH=. .venv/bin/python scripts/install_schedule.py --install recommended   # optional
```

## What it does

| Feature | Dashboard page | Read more |
|---|---|---|
| **Portfolio overview**: value, REIT sleeve, Core vs Satellite against 70:30, caps, sectors | Overview, Import holdings | [overview](docs/overview.md) |
| **Scorecard and valuation**: 100-point descriptive scorecard and suggested role; own band, peers, reverse DCF, scenarios | Scorecard, Valuation | [scorecard](docs/scorecard.md), [valuation](docs/valuation.md) |
| **Risk, plan and tax**: historical risk, a 70:30 plan for new money, tax lots and trims timed over 3/6/12 months | Risk & plan, Tax & transition | [risk](docs/risk.md), [tax](docs/tax.md) |
| **Reviews**: thesis-check events from NSE filings, what changed between reviews, a holdings report | Reviews | [monitoring](docs/monitoring.md) |
| **Research assistant**: `/research` in Claude Code, answering only from the app's figures, checked | Research assistant | [research](docs/research.md) |
| **Portfolio advisor**: hold / add / trim / exit for each holding, with reasons, sizing, sector caps and tax | Today, Stocks | [advisor](docs/advisor.md) |
| **Expected ranges**: how far Nifty and your core holdings could move in the next session and week | Today | [ranges](docs/ranges.md) |
| **Momentum ranking** of the liquid NSE universe, rebalanced monthly | Momentum | [strategy](docs/strategy.md) |
| **Swing setups**: pullbacks and breakouts with a fixed trade plan | Swing | [swing trading](docs/swing.md) |
| **Quarterly results** scored from NSE filings, with the price reaction | Quarterly results | [quarterly results](docs/results.md) |
| **GARP & QARP** shortlists from company filings | GARP / QARP | [GARP & QARP](docs/picker.md) |
| **Mutual funds** judged against their category, for your fund book or any fund | Mutual funds, Compare funds | [mutual fund evaluation](docs/mf_evaluation.md) |
| **Analyze a stock**: fundamental and technical read of any NSE listing | Analyze a stock | [analyze](docs/analyze.md) |
| **F&O**: option-chain positioning and the range to expiry | F&O | [F&O analysis](docs/f_o/README.md) |
| **PPFAS Flexi Cap**: monthly portfolio changes, read as manager decisions | | [PPFAS portfolio](docs/mutual_funds.md) |

Each idea that makes a call has a backtest you can run on your own data; see
[validation](docs/validation.md).

## Your files

You fill in three files in `config/` (`setup_project.py` creates them blank,
each with a *How to fill* sheet): `holdings.xlsx` for your shares,
`mf_holdings.xlsx` for your mutual funds (percentages only) and `watching.xlsx`
for stocks you're watching. They stay on your computer and are never committed
to git.

- **Shares:** import your broker's holdings download, or fill the file by
  hand. See [recording what you own](docs/holdings.md).
- **Mutual funds:** see [your book, without amounts](docs/mf_evaluation.md#your-book-without-amounts).
- **Everything the app stores**, and what to back up: [data storage](docs/storage.md).

## Keeping it fresh

Scheduled jobs refresh prices, filings and fund NAVs on their own (launchd on
macOS, cron on Linux, Task Scheduler on Windows). `setup_project.py` offers to
install them; see [scheduled jobs](docs/automation.md#scheduled-jobs) for what
each one does. Every command the project has is listed in
[operations](docs/operations.md#commands).

## Documentation

| | |
|---|---|
| [**Getting started**](GETTING_STARTED.md) | Install, set up and open the dashboard, in plain steps |
| [**Glossary**](docs/glossary.md) | Every term the dashboard uses, in plain language |
| [**Troubleshooting**](docs/troubleshooting.md) | NSE not answering, stale data, jobs, errors |
| [**Portfolio overview**](docs/overview.md) | Value, REIT sleeve, Core vs Satellite against 70:30, caps, sectors, import preview, snapshots |
| [**Scorecard**](docs/scorecard.md) | 100-point fundamental scorecard per holding, sector models, governance override, suggested role; descriptive |
| [**Valuation**](docs/valuation.md) | Own 5-year band, peers, reverse DCF, bear/base/bull scenarios; not price targets |
| [**Risk & plan**](docs/risk.md) | Historical risk of today's mix, exposures, and a 70:30 plan for new money; a simulation |
| [**Tax & transition**](docs/tax.md) | Tax lots from your tradebooks, this year's gains, trims timed for tax over 3/6/12 months |
| [**Monitoring & reviews**](docs/monitoring.md) | Thesis-check events from NSE announcements, shareholding and results, each with its filing; what changed between reviews; a holdings report covering every current holding |
| [**Research assistant**](docs/research.md) | `/research` in Claude Code: answers from a pack of the app's own figures, every figure cited and checked |
| [**Holding data**](docs/holding_data.md) | Each holding's financials and market figures, with source, period, confidence and freshness |
| [**Dashboard**](docs/dashboard.md) | The menus, badges and the two target frames |
| [**Recording what you own**](docs/holdings.md) | Getting holdings from any broker into the app |
| [**Operations**](docs/operations.md) | Every command, setup, watchlist, tuning, tests |
| [**Automation**](docs/automation.md) | The scheduled jobs and their failure modes |
| [**Portfolio advisor**](docs/advisor.md) | Verdicts, cash constraint, concentration, tax, decision log |
| [**Strategy**](docs/strategy.md) | Momentum ranking, fundamentals, the lender rubric |
| [**Validation**](docs/validation.md) | How the backtests work and how to run them |
| [**Data**](docs/data.md) | Sources, the NSE traps handled, freshness |
| [**Data storage**](docs/storage.md) | What each database, Excel file and CSV holds, and what to back up |
| [**Code layout**](docs/layout.md) | Where things live |

Feature docs are linked from [What it does](#what-it-does).

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
and only that rotates on rank.

Everything runs locally on free public data: no API key, no subscription, no
broker credentials.

## Disclaimer and licence

Decision support, not investment advice. Nothing here is a recommendation to
buy or sell any security. The author is not a SEBI-registered investment
adviser. Backtests describe the past; they don't promise the future. Check
anything you act on, and decide for yourself.

[MIT licence](LICENSE): use, change and share it freely, keeping the copyright
notice.
