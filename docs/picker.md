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
cap stops a recovery from a near-zero base (BHEL, 2026: +3,077%) from topping
every list.

Growth is measured in **total**, not per share. Bonus issues and splits
change the share count without changing the business, and a per-share series
across one shows a fake 50% collapse.

**P/E** is market cap (the unadjusted close × the share count filed then)
over TTM profit. The share count is paid-up capital ÷ face value, checked
against profit ÷ EPS: one filer put the paid-up figure in the face-value
field.

**P/E in own range** is the share of the stock's quarterly P/Es over the last
three years that sit below today's: 0% means the cheapest it has been in that
window. Many quality names sit there in late 2026 -- they have derated.

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

## Does it work?

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
NIFTY 500.

**Tested 2026-10-17**, 17 quarterly periods, Jun 2022 to Aug 2026:

| Universe on each date | GARP vs universe, per quarter | QARP vs universe, per quarter | GARP CAGR | QARP CAGR | Universe CAGR |
|---|---|---|---|---|---|
| All of today's Nifty 200 | **+3.15%** (t = 2.1, ahead 65%) | -3.64% (t = -3.2, ahead 18%) | 43.5% | 11.7% | 28.0% |
| **Largest 150 by market cap on the day** (default) | **+2.33%** (t = 2.0, ahead 71%) | -2.60% (t = -2.2, ahead 29%) | 35.3% | 12.9% | 24.2% |
| Largest 100 on the day | +1.08% (t = 1.0, ahead 65%) | -1.74% (t = -1.5, ahead 41%) | 23.4% | 11.2% | 18.5% |
| **Point in time: 200 largest on the day, whole market** | +0.35% (t = 0.3, ahead 59%) | -1.91% (t = -1.8, ahead 29%) | 20.1% | 10.9% | 18.8% |
| Point in time, largest 150 of those | +0.74% (t = 0.7, ahead 59%) | -1.74% (t = -1.6, ahead 47%) | 21.2% | 10.7% | 18.0% |
| Point in time, largest 100 of those | +1.56% (t = 1.5, ahead 71%) | -1.35% (t = -1.2, ahead 41%) | 23.7% | 11.0% | 16.6% |

The NIFTY 500 returned 13.0% a year over the same span. Its gap to the
equal-weight universe (18-28%) is mostly survivorship: today's members are,
by construction, the stocks that did well.

The point-in-time rows (re-tested 2026-10-18) drop today's index entirely.
On each date the universe is the 200 largest NSE companies by market cap
from the full-market bhavcopy -- delisted, merged and since-demoted ones
included -- with filings fetched for all of them (386 names over the window,
192 of them not in today's Nifty 200). Built by `research/run_pit_universe.py`,
described in `results/pit_universe.py`. 173-197 members are scored on each
date (the rest lack eight quarters of filings or a price). Two limits:
market cap is total, not free float, so companies with a large promoter
stake rank higher than in the real index; and seven pool names had no NSE
filing list at all (ABBOTINDIA, MCX, ORIENTBANK, SPICEJET, GRUH, and two
old tickers since linked to their new ones) and could not be ranked.

What it says:

**On a survivorship-free universe GARP's edge is gone.** +0.35% a quarter,
t = 0.3, against +2.3% (t = 2.0) on today's index. The edge was hindsight
about which companies would grow into the Nifty 200, which is what the
market-cap cut had hinted at. The equal-weight universe itself falls from
24% to 19% a year once the losers are back in. GARP stays a shortlist to
research; it does not rank. QARP still lags (-1.9%, t = -1.8).

From the first test, on today's index:

1. **GARP looks good, but part of that is hindsight.** Today's index holds
   companies that grew *into* it after 2022 -- exactly what GARP selects.
   Restricting each rebalance to what was already large on the day shrinks
   the edge from 3.2% to 2.3% a quarter, and to 1.1% (not significant) in
   the largest 100. Some of the edge may be real; how much cannot be told
   without a survivorship-free universe.
2. **QARP lagged in every version.** 2022-26 rewarded high-beta growth --
   PSUs, capital goods, smallcaps -- and punished expensive quality. QARP's
   worst quarter was milder than the universe's (-4% against -8%), which is
   what quality is for; but over these four years it cost about 2.6% a
   quarter. Four years is one regime: quality's long-run record comes from
   decades that include crashes, and this window has no deep one.
3. **Seventeen periods is thin evidence.** One results season is one data
   point. Neither list ranks anything else in the framework; both are
   shortlists to research, with the backtest shown beside them.

The survivorship-free test is done (row group above) and GARP failed it.
What is left untested is longer history: filings before 2020 would add a
bear market (2018-20) to a window that has none.
