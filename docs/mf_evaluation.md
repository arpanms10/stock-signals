# Mutual fund evaluation

Name a fund, or list your MF book, and get **EXIT / TRIM / ADD / WATCH / HOLD**
for each, with the reasons and the numbers behind them.

[← back to the README](../README.md)

---

```bash
PYTHONPATH=. .venv/bin/python scripts/run_mf.py --refresh                  # first run ~25 min, then seconds a month
PYTHONPATH=. .venv/bin/python scripts/run_mf.py                            # your book
PYTHONPATH=. .venv/bin/python scripts/run_mf.py "parag parikh flexi cap" "hdfc flexi cap" --compare
PYTHONPATH=. .venv/bin/python scripts/run_mf.py --excel                    # reports/mf/MF_Evaluation.xlsx
PYTHONPATH=. .venv/bin/python scripts/run_mf.py --backtest                 # ~1.5 min
```

Also on the dashboard: Portfolio › **Mutual funds** for your book, Research › **Compare funds** for any funds.

## Your book, without amounts

`config/mf_holdings.csv` **or** `config/mf_holdings.xlsx`, but not both. Both
are git-ignored; copy `config/mf_holdings.example.csv` to start:

```
fund,pct,held_over_1y,notes
Parag Parikh Flexi Cap,30,yes,
UTI Nifty 50 Index Fund,25,yes,
Kotak Emerging Equity,20,no,
```

- `pct` is the fund's share of the **whole MF portfolio**, every fund and type,
  so the column sums to 100. No rupee amounts, no share of net worth.
- `held_over_1y` is optional. It only changes the warning on a sell. Under a
  year, most equity funds charge an exit load of about 1% and gains are
  short-term. Blank means "check before selling".
- In Excel, the first sheet is read. Headers can be loose ("Fund Name",
  "Scheme", "Weight", "Allocation %"), and a column formatted as a percentage
  (30% stored as 0.3) is read as 30.
- `fund` is a name as you'd say it, or the AMFI code. Old names work
  ("Axis Bluechip", "Kotak Emerging Equity"); an ambiguous one ("hdfc") lists
  candidates instead of guessing.

Percentages are enough for every sizing decision. Tax amounts would need
rupees and purchase lots, which is why the engine only warns about tax.

## Data

AMFI's free NAV files, nothing paid:

| File | Gives |
|---|---|
| NAV history report | Every scheme's NAV over a date range. Closed and merged schemes are included, which makes the backtest survivorship-free |
| NAVAll.txt | Today's NAV for every live scheme |

One request per month since January 2013, when direct plans started, goes into
`data/mf/mf.db`, about 11 MB. Only Direct Growth plans are kept. They carry no
distributor commission, and their NAV is a total return.

**A fund's category is its latest AMFI label.** The history report re-labels
every surviving scheme with today's category, even on 2014 dates. Schemes that
closed before SEBI's 2017-18 recategorisation keep the old "Growth" label; for
those, the category is read off the name ("small cap", "tax saver"...).

## What is measured

Everything uses month-end NAVs and compares the fund with **its SEBI
category**: the funds a holder could actually switch to.

| Measure | |
|---|---|
| Rank 1y / 3y / 5y | Share of category peers beaten. 100% = best |
| vs median | CAGR minus the category median CAGR, per year |
| vs index fund | CAGR minus the category's index fund (`index_funds` in the rules). The cheap alternative |
| Consistency | Share of rolling 1-year windows in the last 3 years where it beat the median |
| Downside capture | Average return in the category's down months / the median's. Below 1 falls less |
| Volatility, max drawdown | 3 years, monthly |
| Near-twins | Correlation of monthly returns between your funds. Above 0.95, two funds move as one |

## From measures to verdict

**State**, judged on the fund alone (`config/mf_rules.yaml`):

- **YOUNG**: under 36 months of history.
- **WEAK**: bottom quarter over 3 years, still bottom half over the last
  year, and 2+ points a year behind the median. Or 2+ points a year behind
  the category's index fund while only mid-table.
- **SLIPPING**: a bottom-quarter last year, or 4+ points behind the median.
- **STRONG**: top 40% over 3 years, beat the median in at least half the
  rolling windows, not in the bottom quarter this year, and not falling
  harder than peers.
- **OK**: the rest.

**Action**, which also weighs your book:

| Held | |
|---|---|
| EXIT | WEAK. Lists STRONG funds and the index fund in the same category |
| TRIM | Above the single-fund cap (25%); the weakest fund in a category over its cap; the weaker of two near-twins |
| WATCH | SLIPPING or YOUNG |
| ADD | STRONG with room under the caps. Says how far: "room to 25%" |
| HOLD | Everything else. Index, debt and thematic funds are not ranked, but still obey category caps |

A fund you don't hold gets ADD (STRONG), AVOID (WEAK), WATCH or NEUTRAL.
The caps are preferences, not findings, so edit them.

Debt funds are not ranked on purpose. Their returns mostly reflect the duration
and credit risk they take, so ranking by return rewards whoever took the most
risk, until the year it goes wrong.

## Does it work? Mostly no

`scripts/run_mf.py --backtest` gave every fund in a judged category a state at every
month-end from June 2018, using only NAVs up to that month and the same rules
as the live view. It then looked at the fund's **rank in its category over the
following 12 and 36 months**. Funds that later merged or closed stay in,
carried at the category median. That matters because the worst funds are the
ones that get merged away. 531 funds, 91 since closed. The thresholds were set
before the first run and have not been changed.

Next 12 months, rank in category (0.50 = the state told you nothing):

| | WEAK | SLIPPING | OK | STRONG | STRONG − WEAK |
|---|---|---|---|---|---|
| 2018-06 to 2021-12 (train) | 0.50 | 0.46 | 0.51 | 0.49 | 0.00 (t 0.1) |
| 2022-01 on (holdout) | 0.46 (t −2.1) | 0.47 | 0.49 | 0.52 | 0.06 (t 1.7) |

- **In the first period the states predicted nothing.** STRONG and WEAK
  funds ranked the same afterwards.
- **In the holdout, WEAK funds went on to lag slightly**: they beat the median
  44% of the time instead of 50%. STRONG funds' edge is not distinguishable
  from noise.
- **Over 36 months the sign flips between halves.** STRONG funds lagged
  after 2018-21 and led after 2022. Each half holds only one or two independent
  36-month periods, so no t-statistic is given.

This is the same result as published research on Indian and US funds: past
category rank says little about the next few years. It is also the same
pattern as this project's swing, results and picker tests. The engine ships
**calibrated**: the backtest line is printed next to every verdict. Read EXIT
as "this fund has earned a look at the alternatives", not as a forecast that
it will keep lagging. The robust parts of a verdict are the ones that don't
rely on prediction: costs (an index fund is the cheapest way to own the
category), concentration and near-duplicates.

**Known limits.**
- The category is the latest label, not the one at the time (AMFI keeps no
  history). In practice this mostly re-files 2018-20 multi caps as flexi caps.
- Index funds for anything but large caps start in 2019-21, so the
  "vs index fund" test has short history outside large cap.
- Month-end data misses anything inside a month.

## Code

| | |
|---|---|
| `mf/amfi.py` | Parse both AMFI files; category labels |
| `mf/navdb.py` | `data/mf/mf.db`: download and read the month-end panel |
| `mf/lookup.py` | Name to AMFI code, including former names |
| `mf/metrics.py` | Peer-relative measures at a month; forward outcome for the backtest |
| `mf/verdict.py` | State and action |
| `mf/holdings.py` | `config/mf_holdings.csv` |
| `mf/engine.py`, `scripts/run_mf.py`, `ui/mf_tab.py` | Glue, CLI, dashboard |
| `mf/backtest.py` | The test above; writes `config/mf_calibration.json` |
| `tests/test_mf_engine.py` | Parsing, lookup, metrics and verdicts |
