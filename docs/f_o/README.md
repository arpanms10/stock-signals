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
```

The dashboard has the same view under the **F&O** tab. Nothing is fetched
until you press **Analyse**.

| flag | |
|---|---|
| `--expiry YYYY-MM-DD` | a specific expiry. Default: the **nearest monthly** (NIFTY weeklies are skipped unless named) |
| `--excel [PATH]` | also write a workbook. Default path `reports/fno/fno_<time>.xlsx`: a summary sheet, then one sheet per symbol with the reasons, the chain with S/R/ATM rows shaded, and an OI-by-strike chart |
| `--no-save` | don't store a snapshot of the chain |

A symbol without F&O contracts gets a clear message, not an empty result.

## What it reads, and why

Option open interest shows where option **writers** have put money. Writers
are mostly institutions selling premium, and they are paid as long as price
stays on their side of the strike. That's what the analysis is built on, and
it's also its limit: it describes **positioning, not a forecast**.

| output | how | reading |
|---|---|---|
| **Support** | the put strike below spot with the most OI (within ±10%, and holding ≥5% of the puts there) | put writers lose below it, so they defend it |
| **Resistance** | the call strike above spot with the most OI, same rules | call writers lose above it |
| **PCR (OI)** | total put OI ÷ total call OI | above 1.2 means writers are defending the downside (bullish). Below 0.7, bearish. Above 1.6 is crowded, so treat it as a contrarian caution |
| **PCR (today's ΔOI)** | put OI added today ÷ call OI added today | what writers did **today**. Blank when either side is unwinding, because a ratio of a gain and a loss means nothing. When present, it decides the headline bias |
| **Max pain** | the expiry price at which option holders, in total, collect the least | where the price is said to gravitate near expiry |
| **Expected move** | ATM call + ATM put premium (bid/ask mid), cross-checked against ATM IV × √(days/365) | what the market charges for a move either way |
| **Range to expiry** | for each side, the **tighter** of the OI wall and spot ± the straddle | a wall inside the priced move is binding. A wall beyond it isn't, so the move sets that edge |
| **Futures buildup** | sign of the futures price change against the sign of the OI change | long buildup, short buildup, short covering, long unwinding |

Every result comes with its reasons in plain sentences, and with warnings:
expiry within 2 sessions (OI is distorted by rollover), a chain with no clear
wall on one side, no ATM quote, or a chain more than 30 minutes old during
market hours.

Only strikes on the out-of-the-money side count as walls. A call written
below spot is already in the money: it's a hedge or a covered position, not a
ceiling.

The thresholds are in the `fno:` section of `config/scoring.yaml`.

## Does any of it work?

That's measured, not assumed: [**validation**](validation.md).

## Snapshots

Every live run (CLI or dashboard) stores the chain in `data/signals.db`
(`option_snapshots`, `futures_snapshots`), keyed by NSE's own timestamp. A
re-run between NSE updates replaces rows instead of adding duplicates. The
dashboard draws today's PCR line from these. The Saturday job doesn't fetch
F&O, because the chain only changes during market hours.

## Code

```
data/sources/nse_derivatives.py   live chain, futures, expiries, lot sizes, F&O list
data/fo_bhavcopy.py               historical chains from the F&O bhavcopy
strategy/fno.py                   PCR, walls, max pain, straddle, range (pure)
fno_report.py                     load + text + Excel, shared by CLI and UI
run_fno.py                        command line
run_fno_backtest.py               the validation
ui/fno_tab.py                     dashboard tab
```

`strategy/fno.py` has no I/O. The validation runs the same functions on
historical chains, so its results describe the code you actually run.

---

See also: [F&O data and its traps](data.md) · [validation](validation.md).
Decision support, not investment advice.
