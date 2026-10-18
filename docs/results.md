# Quarterly results

A scorecard for every quarterly filing of the stocks you follow, read from
NSE's own XBRL, with the market's reaction -- and a test of whether the
verdict predicts anything.

[← back to the README](../README.md)

---

```bash
PYTHONPATH=. .venv/bin/python run_results.py                  # score what's new, report
PYTHONPATH=. .venv/bin/python run_results.py --days 14        # look further back
PYTHONPATH=. .venv/bin/python run_results.py --symbols TCS INFY
PYTHONPATH=. .venv/bin/python run_results.py --no-fetch       # report from what's on file
PYTHONPATH=. .venv/bin/python run_results_backtest.py         # does the verdict predict drift?
```

The dashboard's **Quarterly results** page shows the same, with a filter by why each
stock is followed, one stock's last eight quarters, and who reports next.
**Check for new results** (on the page and on Data and jobs) runs `run_results.py`.
The Holdings, Momentum and Swing tables get a **Results** column:
`Q2 strong · 16 Oct` once this quarter is filed, `reports 21 Oct` before.

## What is followed

`results/coverage.py`, each source read independently:

| Source | From |
|---|---|
| holding | `config/holdings.csv` (or the Kite snapshot) |
| watch | `config/watching.csv` |
| swing | the latest recorded scan's top picks, every universe |
| momentum | the latest month in `history/momentum_picks.xlsx` |

REITs and InvITs are skipped; ETFs file nothing and drop out on their own.

## Reading a filing

A results filing carries each line twice or three times under the same tag:
the quarter, the year to date and, in March, the full year. Taking the first
occurrence -- what the older fundamentals code does -- returns the half-year
whenever it happens to come first. `results/filings.py` reads only facts
whose context spans one quarter (80-100 days), ends on the quarter end and
has no dimension.

A filing does **not** carry last year's quarter, so year-on-year needs the
filing from four quarters back. Eight quarters are fetched per stock: enough
for YoY on the newest and for the three before it, which the acceleration
test needs. Raw filings are cached gzipped in `data/cache/filings/` by URL; a
revision gets a new URL, so a filing is never fetched twice.

**Basis:** one per series -- whichever of consolidated and standalone covers
more of the window, consolidated on a tie (ABB filed consolidated only from
late 2025). Banks are always standalone: their consolidated filing carries
GNPA as 0.00. A quarter missing on the chosen basis is a gap; it is never
filled from the other basis, since that growth rate would measure the
subsidiaries.

**Kind:** a filing with interest expended and operating profit before
provisions is a bank (Schedule III Part A). One where interest earned is at
least half of revenue is an NBFC. Everything else is a company.

## The scorecard

Free sources carry no analyst estimates, so "did it beat?" cannot be asked.
What can be asked is whether the company did better than its own recent
quarters. Each line in `results:` of `config/scoring.yaml` is a `[bad, good]`
band: at or past *good* scores +1, at or past *bad* scores -1.

**Companies**

| Line | Default band | Why |
|---|---|---|
| Revenue YoY | 0% / 15% | the headline, as results are read in India |
| Acceleration | -3 / +3 pp | YoY now against the mean YoY of the prior three quarters |
| EBITDA margin vs year ago | -150 / +100 bps | operating leverage, or the lack of it |
| Core PBT YoY | 0% / 20% | PBT less other income and exceptional items |
| Earnings quality | -1 | other income ≥ 30% of PBT, or a one-off ≥ 10% of PBT |

EBITDA = core PBT + finance costs + depreciation. YoY is the headline because
many Indian businesses are seasonal enough that a quarter-on-quarter fall
means nothing; QoQ is shown, never scored.

**Banks and NBFCs**

| Line | Default band |
|---|---|
| NII YoY | 5% / 15% |
| PAT YoY | 0% / 15% |
| Credit cost vs year ago (provisions ÷ pre-provision profit) | +5 / -5 pp |
| Gross NPA vs last quarter (banks only; NBFCs don't file it in XBRL) | +20 / -10 bps |

For an NBFC, NII is interest earned less finance costs, and impairment is
the provision line.

**Verdict:** total ≥ 2 is *strong*, ≤ -2 is *weak*, between is *in line*.

**Flags** spell out what a number alone hides: profit propped up by other
income, a one-off this quarter, a one-off in last year's quarter that makes
PAT growth meaningless (read core PBT instead), profit up on falling revenue,
a margin down 200 bps or more, swinging to a loss or back to profit, a jump
in bad loans or provisions. Growth from a loss is left blank rather than
printed as a percentage.

Cards are rebuilt from the stored quarterly lines on every run, so a change
to the bands reaches every card already on file.

## The reaction

`results/reaction.py`. The reaction day is the first session whose close
reflects the filing: the same day if NSE broadcast it before 15:30,
otherwise the next session. Most results land after hours.

| Field | Meaning |
|---|---|
| gap | the open against the previous close: the first read |
| day | the reaction-day close against the previous close |
| ATR | the day's move in multiples of the stock's 14-day ATR; ±1 or more is a reaction, less is *muted* |
| vs index | less the NIFTY 500 the same day |
| 5 sessions | the close four sessions after the reaction day, and against the index |

The reaction is what stands in for the missing consensus. Good numbers that
sell off usually mean the market expected better; the detail view says so
when the verdict and the reaction disagree.

Prices come from the local store; a holding outside the Nifty 500 is fetched
from NSE directly (unadjusted, which only matters if a split lands inside the
few weeks measured).

## Storage

`data/results/scorecards.csv` and `data/results/quarters.csv`, both outside
git. The figures are public, but the list of symbols includes your holdings,
and the `.gitignore` keeps holdings out of the repo.

## Does the verdict predict anything?

```bash
PYTHONPATH=. .venv/bin/python run_results_backtest.py               # fetch + report
PYTHONPATH=. .venv/bin/python run_results_backtest.py --report-only
PYTHONPATH=. .venv/bin/python run_results_backtest.py --report-only --universe pit   # survivorship-free
```

Every Nifty 200 filing for 24 quarters is scored as it would have been on
the day, on its own filing and the seven before it. Entry is at the
**reaction-day close** -- after both the numbers and the market's first read
are known -- and returns are measured over 20 and 60 sessions, less the
NIFTY 500. A results season packs most events into a few weeks, so the
headline spreads are averaged within each quarter and tested across quarters,
not across events.

**Tested 2026-10-17:** 3,226 filings, 188 stocks, 23 quarters (Mar 2021 to
Sep 2026).

| | Reaction day | 20 sessions on | 60 sessions on |
|---|---|---|---|
| Strong (1,108) | **+1.96%** | +1.03% | +4.13% |
| In line (1,440) | +0.28% | +1.06% | +3.69% |
| Weak (678) | **-0.77%** | +0.64% | +2.25% |
| Strong minus weak, per quarter | | +0.42% (t = 0.7) | +1.73% (t = 1.6, better in 13 of 19) |
| **Rallied minus sold off**, per quarter | | **+1.18% (t = 2.6)** | **+3.61% (t = 2.6)** |

(Mean excess over the NIFTY 500. Every row is positive because the universe
is today's Nifty 200 -- survivors -- which is why only the spreads are read.)

**Re-tested 2026-10-18 on a survivorship-free universe:** each quarter's
filings come from the 200 largest NSE companies at that quarter end, by
market cap from the full-market bhavcopy, delisted and merged ones included
(`run_pit_universe.py`; see [GARP & QARP](picker.md#does-it-work) for how
it is built and its limits). 3,553 filings, 334 stocks, 27 quarters (Mar
2020 to Sep 2026); 753 of the filings are from companies not in today's
Nifty 200.

| | Reaction day | 20 sessions on | 60 sessions on |
|---|---|---|---|
| Strong (1,178) | +0.46% | +0.44% | +1.76% |
| In line (1,562) | -0.15% | +0.30% | +1.68% |
| Weak (813) | -0.94% | +0.23% | +0.23% |
| Strong minus weak, per quarter | | +0.04% (t = 0.1) | +1.64% (t = 1.6, better in 14 of 20) |
| **Rallied minus sold off**, per quarter | | **+1.46% (t = 3.3)** | **+3.51% (t = 2.6)** |

Checks on the rallied-minus-sold-off spread at 60 sessions:

| Subset | Filings | Spread | t |
|---|---|---|---|
| All | 3,553 | +3.51% | 2.6 |
| Priced with open/high/low (drops the 105 priced from closes only) | 3,448 | +3.00% | 2.2 |
| Companies in today's Nifty 200 | 2,800 | +2.66% | 1.7 |
| Companies no longer in it | 753 | +4.64% | 2.3 |

The 105 filings from companies the price store never tracked (delisted or
fallen out of the Nifty 500) are priced from the bhavcopy, which has no
open, high or low, so their reaction is sized on the close-to-close range,
not the ATR.

What it says:

1. **The scorecard measures what the market cares about.** Strong cards
   beat the index by 2% on the reaction day, weak ones lag. The verdict is
   not noise.
2. **Once the reaction is known, the verdict adds little.** Strong minus
   weak over 60 sessions is +1.7% a quarter, positive in 13 of 19 quarters,
   but t = 1.6: suggestive, short of evidence. It stays a reading aid and
   feeds no ranking.
3. **The reaction itself carries on.** Stocks that rallied on their results
   (≥1 ATR) beat those that sold off by 1.2% over 20 sessions and 3.6% over
   60, t ≈ 2.6 at both horizons -- the post-earnings drift the literature
   describes, showing up in Indian large and mid caps. The market's first
   read is the stronger signal, and it is the one the Results tab's
   *Reaction* column shows.

4. **Both survive the survivorship-free universe.** The verdict is
   unchanged: +1.6% over 60 sessions, t = 1.6, still short of evidence. The
   reaction drift holds at +3.5% (t = 2.6), and is stronger at 20 sessions
   (+1.5%, t = 3.3). It is not carried by survivors: it is larger among
   companies that have since left the index. Within today's members alone
   it is weaker (t = 1.7). Read the drift as real but modest, not as a
   large-cap anomaly.

Not yet acted on anywhere. Survivorship is settled; whether a rule built on
it survives costs is the next section.

## Does the drift survive costs as a rule?

```bash
PYTHONPATH=. .venv/bin/python run_drift_backtest.py               # 500 random draws, ~5 min
PYTHONPATH=. .venv/bin/python run_drift_backtest.py --ohlc-only   # drop close-only filings
```

**Tested 2026-10-18 on the survivorship-free events above. It does not
pass.** Nothing ranks on the reaction.

The rule, fixed before running: after every filing whose reaction was
*rallied* (≥1 ATR), buy at the reaction-day close and sell at the close 20,
40 or 60 sessions later. The book has 20 equal slots (5% of NAV per name,
the position cap); a rally that finds every slot taken, or the name already
held, is skipped, and same-day rallies fill slots in broadcast order. Idle
capital sits in the NIFTY 500 (an index fund, no cost charged), so the book
is always fully in equities. 0.35% per round trip, half on each side. A
holding that stops trading keeps its last price and is sold at it. Book
from 14 Oct 2021 (the first full results season on file) to 16 Oct 2026.

The gate, also fixed before running: the 20-slot book's excess over the
equal-weight point-in-time universe, per calendar quarter, after costs,
t ≥ 2 at 20 or 60 sessions.

| 20 slots | CAGR | Worst drawdown | Turnover | Excess a quarter vs EW universe | Rallies skipped |
|---|---|---|---|---|---|
| Hold 20 sessions | 10.6% | -21.5% | 4.7x a year | -0.02% (t = -0.0, 12 of 21) | 231 of 703 |
| Hold 40 | 12.8% | -23.9% | 4.0x | +0.51% (t = 0.9) | 298 |
| **Hold 60** | 13.2% | -25.5% | 3.9x | **+0.69% (t = 0.9, 11 of 21)** | 310 |
| Equal-weight universe | 10.2% | -23.7% | | | |
| NIFTY 500 | 7.1% | -18.8% | | | |

Controls on the same entry dates, holds and slots (500 draws each; the
book's quarterly excess over the control's mean):

| 20 slots | Random universe member | Random same-day filer | Idle capital in the EW universe instead |
|---|---|---|---|
| Hold 20 | +0.96% (t = 3.2), CAGR 6.3% | +0.43% (t = 1.2), CAGR 8.6% | +0.51% vs EW (t = 1.7) |
| Hold 40 | +1.33% (t = 3.0), 6.9% | +0.95% (t = 2.4), 8.4% | +0.82% (t = 1.8) |
| Hold 60 | +1.16% (t = 1.4), 8.2% | +1.12% (t = 1.7), 8.3% | +0.67% (t = 0.8) |

Per filing rather than per book: each rallied stock's return over the hold,
less the round trip, less the equal-weight universe over the same window,
averaged within each filing quarter (21 quarters):

| | 20 sessions | 40 sessions | 60 sessions |
|---|---|---|---|
| Rallied, net, vs EW universe | +0.29% (t = 1.3) | +1.20% (t = 2.6) | +1.82% (t = 2.5) |
| ... without the 105 close-only filings | +0.19% (t = 0.6) | +0.90% (t = 1.3) | +1.46% (t = 1.6) |
| Rallied minus sold off, both legs paying (reference; no shorting) | +0.40% (t = 0.9) | +2.20% (t = 2.8) | +2.71% (t = 2.2) |

What it says:

1. **The 20-session drift is gone after costs.** Its t = 3.3 was a spread
   with a short leg; long only, after 0.35%, rallied stocks match the
   universe.
2. **At 40-60 sessions a per-filing edge of 1-2% a quarter remains, but it
   is fragile.** It halves and loses significance without the 105 filings
   priced from closes only (companies the price store never tracked, whose
   reaction is sized on the close-to-close range).
3. **A capped book does not capture it.** Results season bunches rallies
   into a few weeks: 20 slots fill in the first days and skip a third of
   them at 20 sessions, 44% at 60, in broadcast order, which is arbitrary. The book lands at 0.7% a
   quarter, t = 0.9, with a deeper drawdown than the universe and 4x a year
   of turnover (about 1.4% a year in costs).
4. **Against random picks with the same turnover it does better**, which
   says the rally carries some information -- but most of that gap is the
   control paying costs on random stocks (6-8% a year, below the universe).
   Against that day's other filers it is t = 1.2-2.4.
5. **Not pre-registered, and so not a pass:** with 40 slots (no cap in
   practice, ~30 names) and a 60-session hold the book does +1.1% a
   quarter, t = 2.4 (2.4 without the close-only filings too), CAGR 15.5%,
   drawdown -20.5%. It is the best of nine book settings, found after
   looking. If anything is to be tracked forward, it is this one, on paper,
   from the next results season.
