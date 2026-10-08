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

## What it shows, and how far to trust it

The view has two parts, and [validation](validation.md) is why they are
separate.

**The range: spot ± the ATM straddle.** The ATM call plus put premium
(bid/ask mid) is what the market charges for a move either way by expiry. It
is shown with **how often the expiry close actually landed inside it**,
measured from January 2024 to September 2026:

| sessions to expiry | stocks | indices |
|---|---|---|
| ~5 | 61% | 49% |
| ~10 | 63% | 56% |
| ~20 | 54% | 58% (small sample) |

So this is a band with roughly even odds, not a target. If there's no ATM
quote, the range falls back to ±1σ from ATM IV and says that this hit rate
hasn't been measured. The rates are in `fno.range_hit_pct` in
`config/scoring.yaml`; re-run the validation to update them.

**Positioning: walls, PCR, max pain, futures buildup.** These are shown
because they describe where option writers have put money, which is worth
knowing. On about 14,000 stock and 360 index expiries, none of them told you
where price ended up:

| output | how | what validation found |
|---|---|---|
| **Put wall** ("support") | the put strike below spot with the most OI (within ±10%, holding ≥5% of the puts there) | held exactly as often as any level the same distance below spot |
| **Call wall** ("resistance") | the call strike above spot with the most OI, same rules | same |
| **PCR (OI)** | put OI ÷ call OI, read against **its own kind's norms**: indices bearish < 0.7, bullish > 1.2, stretched > 1.6. Stocks bearish < 0.5, bullish > 0.8, stretched > 1.05 (their 20th/80th/97th percentiles; stock PCR runs much lower) | no direction in either |
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
