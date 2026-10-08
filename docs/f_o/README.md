# F&O analysis

PCR, support and resistance from open interest, and an expected range to
expiry, for any NSE F&O underlying: NIFTY, BANKNIFTY and the other indices,
and about 213 stocks.

[← back to the README](../../README.md)

---

```bash
PYTHONPATH=. .venv/bin/python run_fno.py NIFTY
PYTHONPATH=. .venv/bin/python run_fno.py NIFTY BANKNIFTY RELIANCE --excel
PYTHONPATH=. .venv/bin/python run_fno.py RELIANCE --expiry 2026-11-23
PYTHONPATH=. .venv/bin/python run_fno.py --selftest       # are NSE's endpoints still working?
```

The dashboard has the same view under the **F&O** tab. Nothing is fetched
until you press **Analyse**.

| flag | |
|---|---|
| `--expiry YYYY-MM-DD` | a specific expiry. Default: the **nearest monthly** (NIFTY weeklies are skipped unless named) |
| `--excel [PATH]` | also write a workbook. Default path `reports/fno/fno_<time>.xlsx`: a summary sheet, then one sheet per symbol with the reasons, the chain with S/R/ATM rows shaded, and an OI-by-strike chart |
| `--no-save` | don't store a snapshot of the chain |

A symbol without F&O contracts gets a clear message, not an empty result.

## What it shows, and how far to trust it

The view has two parts, and [validation](validation.md) is why they are
separate.

**The range: spot ± the ATM straddle.** The ATM call plus put premium
(bid/ask mid) is what the market charges for a move either way by expiry. It
is shown with **how often the expiry close actually landed inside it**,
measured from January 2024 to September 2026:

| sessions to expiry | stocks | indices (monthly) | NIFTY weekly |
|---|---|---|---|
| ~1–2 | | | 64–65% |
| ~4–5 | 60% | 50% | 62% |
| ~10 | 63% | 57% | |
| ~20 | 54% | 67% (small sample) | |

For stocks it also says when one of the two measured conditions applies:
options cheap for that stock (IV percentile below 20: held 56%) or results
before expiry (57%), against 61% overall.

So this is a band with roughly even odds, not a target. If there's no ATM
quote, the range falls back to ±1σ from ATM IV and says that this hit rate
hasn't been measured. The rates come from `config/fno_calibration.json`, which every validation
run rewrites (the values in `config/scoring.yaml` are a fallback). Weekly
NIFTY expiries are quoted from their own weekly measurements.

**Positioning: walls, PCR, max pain, futures buildup.** These are shown
because they describe where option writers have put money, which is worth
knowing. On about 13,000 stock and 2,000 index observations, none of them
reliably told you where price ended up (stock PCR has a tentative relative
tilt, below):

| output | how | what validation found |
|---|---|---|
| **Put wall** ("support") | the put strike below spot with the most OI (within ±10%, holding ≥5% of the puts there) | held exactly as often as any level the same distance below spot |
| **Call wall** ("resistance") | the call strike above spot with the most OI, same rules | same |
| **PCR (OI)** | put OI ÷ call OI, read against **its own kind's norms**: indices bearish < 0.7, bullish > 1.2, stretched > 1.6. Stocks bearish < 0.5, bullish > 0.8, stretched > 1.05 (their 20th/80th/97th percentiles; stock PCR runs much lower) | no absolute direction. Among stocks on the same day, high PCR leaned up by ~5–6 points: tentative, not a live signal |
| **PCR (today's ΔOI)** | put OI added today ÷ call OI added today; blank when either side is unwinding | not tested (the history is end-of-day) |
| **Max pain** | the expiry price at which option holders, in total, collect the least | a worse guess for the expiry close than today's price |
| **Futures buildup** | sign of the futures price change against the sign of the OI change | not tested |

Only out-of-the-money strikes count as walls. A call written below spot is
already in the money: it's a hedge or a covered position, not a ceiling.

Warnings appear for expiry within 2 sessions (OI is distorted by rollover),
a chain with no clear wall on one side, no ATM quote, or a chain more than 30
minutes old during market hours. The thresholds are in the `fno:` section of
`config/scoring.yaml`.

### What changed after validation

Until 2026-10-08 the range was the tighter of the OI wall and the straddle on
each side, and the PCR thresholds were index ones for everything. Validation
showed the walls only made the range narrower, never more accurate, and that
index thresholds labelled 71% of stocks "bearish". Both were changed.
`run_fno_backtest.py` still reports the old range for comparison.

## Volatility context

```
Volatility: Straddle IV 37.6%   IV percentile 96   realised 20d 31.2% (IV/RV 1.21)   skew -0.4 pts   next month -3.1 pts
```

| | how | why it's there |
|---|---|---|
| **Straddle IV** | annualised volatility implied by the ATM straddle | the same measure is computed from closes for the history, so the comparisons below are like with like. NSE's own IV column doesn't exist in the history |
| **IV percentile** | share of the past year's daily straddle IVs below today's | Kite shows this too. It's the percentile (how often IV was lower), not the rank (where it sits between the year's high and low), because one spike sets the rank's scale for a year. It needs 120 days of history, refreshed within the last week |
| **Realised (20d)** and **IV/RV** | how much it actually moved, annualised, over the last 20 sessions | are options pricing more movement than is happening? |
| **Skew** | IV of the put one straddle below spot minus the call one straddle above | about the 25-delta wings, but scaled to each stock's own move. IV is backed out of the quote, as in the history |
| **Next month** | next monthly's straddle IV minus this one | negative (inverted) usually means an event before this expiry |
| **Results due** | NSE board-meeting announcements that mention financial results, between today and expiry | IV is bid up into results and collapses after ("IV crush") |

What each of these is worth, measured on the history, is in
[validation](validation.md).

## Greeks

The option-chain table (dashboard expander, Excel sheets) has delta, gamma,
theta (per day), vega (per vol point) and **prob ITM** for every strike.
They're Black-Scholes, from NSE's IV for that strike, with
`fno.risk_free_pct` (6.5%) in `config/scoring.yaml`. Prob ITM is N(d2), the
model's chance of finishing in the money. Its calibration is the "model"
column of the reach check in [validation](validation.md).

## Reach: is a target or stop realistic?

```bash
PYTHONPATH=. .venv/bin/python run_fno.py RELIANCE --level 1250 1150
```

For any price, plus both walls automatically:

```
  call wall  1,250 (+5.2%, 1.3 moves): reached on a close before expiry 18%, beyond it at expiry 14% (model 16%)
  level      1,150 (-3.2%, 0.8 moves): reached on a close before expiry 35%, beyond it at expiry 24% (model 26%)
```

The distance is measured in **moves**, meaning ATM straddles, so a 5% move in
a volatile stock and a 5% move in NIFTY aren't treated alike. "Reached" is
the share of past observations where a move that size closed at or beyond
the level on some day before expiry. "Beyond at expiry" is the share that
finished there. "Model" answers the same question from the
straddle-implied normal distribution.

The dashboard shows the same numbers under each wall and in a **Reach a
level** box.

What it isn't: **a forecast of direction.** Up and down moves are pooled, so
+5% and −5% get the same number. The sample's own direction was a falling
market, and baking that in would bias every estimate. It also counts daily
closes only, so an intraday touch is more likely than it says. Use it to
judge whether a target is realistic for this expiry, or how often a stop at
that distance would have been hit, not whether price is headed there.

The curves live in `config/fno_reach.json`, rebuilt by every
`run_fno_backtest.py` run from the observations 5 and 10 sessions out. Out
of sample (built on Jan 2024 – Jun 2025, tested on Jul 2025 onward), the
historical numbers were within about 1–4 points of what happened, and the
model ran 2–4 points high for stocks. See [validation](validation.md).

## Intraday snapshots

```bash
PYTHONPATH=. .venv/bin/python install_schedule.py --fno-intraday           # install
PYTHONPATH=. .venv/bin/python install_schedule.py --fno-intraday --remove  # remove
```

Installs a second launchd agent that runs `snapshot_fno.py` every 30 minutes,
09:30–15:30 on weekdays, for the forward-log basket. It skips exchange
holidays and does nothing outside market hours, and it only runs while the
Mac is awake. This builds the intraday history that today's change-in-OI PCR
needs before it can be tested. Until then it's labelled untested.

## Keeping it current

The Saturday job (`run_weekly.py`) now also:

1. runs `run_fno.py --selftest`, which checks every live NSE endpoint
   answers with real data (NSE changed this API once already, and one
   retired endpoint answers 200 with an empty body);
2. runs `run_fno_backtest.py --ingest`, which adds the week's bhavcopy,
   index history and results dates, then re-runs the validation. That
   rewrites `config/fno_calibration.json` (the hit rates the range quotes)
   and `config/fno_reach.json` (the reach curves), so neither goes stale or
   needs editing by hand.

`--ingest-old` adds the 2019–2023 index history; it only needs running once.

## Forward log

```bash
PYTHONPATH=. .venv/bin/python record_fno.py               # score expired cycles, record this one
PYTHONPATH=. .venv/bin/python record_fno.py --check-only  # score only
```

`history/fno_ranges.xlsx` gets one tab per monthly expiry. Each tab holds
every underlying's range, the hit rate the backtest expects, the walls, PCR
and max pain, all written down **before** the outcome is known. After expiry,
the next run fills in the expiry close (from NSE's F&O bhavcopy) and whether
each level held. The **Summary** tab compares actual hit rates with expected
ones, per cycle and in total.

The default basket is NIFTY, BANKNIFTY, FINNIFTY and MIDCPNIFTY plus every
NIFTY 50 stock with F&O (about 54 underlyings, about a minute). Override it
with `--symbols`. The first record of a cycle is kept, and `--force`
replaces it. The Saturday job runs it, so a cycle is normally logged 15–20
sessions before expiry.

Judge it over several cycles. Stocks move together, so one large market move
can push most of a cycle outside its range. The log started with the
October 2026 expiry.

## Snapshots

Every live run (CLI or dashboard) stores the chain in `data/signals.db`
(`option_snapshots`, `futures_snapshots`), keyed by NSE's own timestamp. A
re-run between NSE updates replaces rows instead of adding duplicates. The
dashboard draws today's PCR line from these. The Saturday job doesn't fetch
F&O, because the chain only changes during market hours.

## Code

```
data/sources/nse_derivatives.py   live chain, futures, expiries, lot sizes, F&O list, holidays
data/sources/nse_events.py        results dates from board-meeting announcements
data/fo_bhavcopy.py               historical chains from the F&O bhavcopy
strategy/fno.py                   PCR, walls, max pain, straddle, range (pure)
fno_report.py                     load + text + Excel, shared by CLI and UI
run_fno.py                        command line
run_fno_backtest.py               the validation; writes the calibration files
record_fno.py                     the monthly forward log
snapshot_fno.py                   intraday snapshots (launchd)
ui/fno_tab.py                     dashboard tab
```

`strategy/fno.py` has no I/O. The validation runs the same functions on
historical chains, so its results describe the code you actually run.

---

See also: [F&O data and its traps](data.md) · [validation](validation.md).
Decision support, not investment advice.
