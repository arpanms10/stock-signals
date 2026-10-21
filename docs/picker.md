# GARP & QARP picker

Two fundamental shortlists for the Nifty 200 -- growth at a reasonable price
and quality at a reasonable price -- built only from NSE's own filings and
the price on the day, and backtested the same way.

[← back to the README](../README.md)

---

```bash
PYTHONPATH=. .venv/bin/python scripts/run_picker.py --refresh        # read new filings (minutes; first run over an hour)
PYTHONPATH=. .venv/bin/python scripts/run_picker.py                  # today's picks
PYTHONPATH=. .venv/bin/python scripts/run_picker.py --as-of 2025-06-05
PYTHONPATH=. .venv/bin/python scripts/run_picker.py --backtest       # -> config/picker_calibration.json
```

The dashboard's **GARP / QARP** page shows both lists, everything scored,
the method and the backtest. **Refresh filings** (on the page and on
Data and jobs) runs `--refresh`.

## The two methods

**GARP** -- Peter Lynch's growth at a reasonable price. A company whose
profit is growing fast, *and has been for a while*, bought at a P/E not far
above that growth rate:

> PEG = core P/E ÷ growth

Passes when growth is at least 15% a year, PEG at most 1.5 (Lynch's own line
was 1.0), the top line is growing, interest cover is at least 3x and
debt/equity at most 1. Ranked half on growth, half on a low PEG.

**QARP** -- quality at a reasonable price: Buffett and Munger's "wonderful
company at a fair price", formalised as the quality factor (AQR's *Quality
Minus Junk*, MSCI Quality). A business that earns well, steadily and in cash
on little debt, bought when it is not near its own most expensive.

Passes when quality is in the top 40% of peers, profit is still growing, and
the P/E is not above 80% of its own last three years. Ranked 60% quality,
40% price.

Thresholds and weights are in `picker:` in `config/scoring.yaml`.

## The measures

Every measure is a **percentile rank** within the universe on that date, so
80 means "better than 80% of peers", whatever the level of the market. A
missing measure is left out of that stock's average -- never counted as
average, which would let unknown risk pass as acceptable.

| | Companies | Banks and NBFCs |
|---|---|---|
| **Quality** | ROE, ROCE, CFO/PAT, interest cover, debt/equity (low), EBITDA margin, margin stability, other income share of profit (low), top-line consistency | ROE, ROA, gross NPA (low, banks), credit cost (low), consistency |
| **Growth** | core profit growth, top-line growth, consistency | same, with NII as the top line |
| **Price (QARP)** | P/E against its own last 3 years, P/E against sector median, FCF yield | P/E against own history and sector |

Lenders are ranked only against other lenders. ROE, margins and debt mean
something different for a business whose raw material is borrowed money.

**Core profit** is PAT less other income, exceptional items and discontinued
operations, after tax. A treasury gain, an asset sale or the sale of a
division is not what you are buying.

**Growth used** is the *lower* of one-year growth and two-year annualised
growth in trailing-twelve-month core profit, capped at 50%. Lynch's PEG
wants expected growth. Trailing growth off a depressed base is the classic
way PEG misleads, and taking the minimum asks the growth to have lasted. The
cap stops a recovery from a near-zero base (which can show growth in the
thousands of percent) from topping every list.

Growth is measured in **total**, not per share. Bonus issues and splits
change the share count without changing the business, and a per-share series
across one shows a fake 50% collapse.

**P/E** is market cap (the unadjusted close × the share count filed then)
over TTM profit. The share count is paid-up capital ÷ face value, checked
against profit ÷ EPS: one filer put the paid-up figure in the face-value
field.

**P/E in own range** is the share of the stock's quarterly P/Es over the last
three years that sit below today's: 0% means the cheapest it has been in that
window.

**CFO** over twelve months comes from the half-yearly cash-flow statements: a
March filing covers the year; a September one is H1, plus last year, less
last year's H1.

## Point in time

Every number is what NSE had published on the date: a filing counts from its
broadcast time (or 45 days after the quarter, 60 for March, where that is
missing), and the price is that day's close. The same code produces today's
picks and every backtest rebalance.

What is **not** point in time:

- The universe is **today's** Nifty 200. Stocks that fell out of the index
  are missing, which flatters all strategies, the benchmarks included. The
  equal-weight universe is the fairer comparison for that reason.
- The balance sheet -- ROE, ROCE, debt, cash flow -- is in filings only from
  the 2025 integrated format. Before that, quality rests on the P&L measures
  (margin, its stability, interest cover, other income, consistency).
- Each filing is its latest revision. Revisions are rare and usually small.

## Data

`data/results/universe_quarters.csv` holds 24 quarters per stock, shared with
`research/run_results_backtest.py`. Raw filings are cached gzipped in
`data/cache/filings/`, so a refresh reads only new quarters and a new field
re-reads the cache rather than NSE.

## Backtest

```bash
PYTHONPATH=. .venv/bin/python scripts/run_picker.py --backtest                   # largest 150 on each date (default)
PYTHONPATH=. .venv/bin/python scripts/run_picker.py --backtest --top-by-mcap 0   # all of today's index
PYTHONPATH=. .venv/bin/python research/run_pit_universe.py --fetch                # point-in-time universe (once)
PYTHONPATH=. .venv/bin/python scripts/run_picker.py --backtest --universe pit    # the 200 largest on each date
```

Rebalance after each results season closes (20 Feb, 5 Jun, 20 Aug, 20 Nov),
top 15 of each, equal weight, held to the next rebalance, 0.35% per round
trip on the names that change. Compared with the **equal-weight universe**
on the same dates -- the same survivors, so the fair benchmark -- and the
NIFTY 500. The result is written to `config/picker_calibration.json` and
shown on the page.

Which universe to test on matters:

- **Today's Nifty 200** holds companies that grew *into* the index -- exactly
  what GARP selects -- so it flatters GARP. `--top-by-mcap 150` (the default)
  keeps only what was already large on each date, which removes part of that
  hindsight.
- **Point in time** (`--universe pit`) drops today's index entirely. On each
  date the universe is the 200 largest NSE companies by market cap from the
  full-market bhavcopy -- delisted, merged and since-demoted ones included --
  with filings fetched for all of them. Built by `research/run_pit_universe.py`,
  described in `results/pit_universe.py`. This is the fairest test. Two
  limits: market cap is total, not free float, so companies with a large
  promoter stake rank higher than in the real index; and a few names have no
  NSE filing list and cannot be ranked.

Each results season is one data point, so a few years of history is a short
sample. Neither list ranks anything else in the framework; both are
shortlists to research, with the backtest shown beside them.
