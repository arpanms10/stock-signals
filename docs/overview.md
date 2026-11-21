# Portfolio overview

What you own, in what proportions, against the Core–Satellite plan.

[← back to the README](../README.md)

---

Dashboard: **Portfolio › Overview** and **Portfolio › Import holdings**.

## What the numbers are

| | |
|---|---|
| Market value | Each holding × the latest NSE session close on file (never assumed to be today's). The date is shown at the top |
| Invested | Quantity × your average cost, for every holding that has a cost |
| Unrealised P/L | On holdings with both a price and a cost |
| Day change | The latest close against the session before |

Money is computed in decimal, not floating point, so it adds up to the paisa.
A holding with no price (bonds, gold bonds, anything not traded on NSE) is
neither valued at zero nor at cost: it is left out of the weights and listed
under *Missing or qualified data* with its cost.

## Sleeves

Every holding is labelled by type (`book/securities.py`), from its ISIN and
its broker suffix, and the two are cross-checked. Where they disagree, or the
suffix is unknown, you are asked to verify rather than given a guess.

| Sleeve | What | In the 70:30? |
|---|---|---|
| Operating equity | Company shares | yes |
| Alternative income / listed real assets | REITs (`-RR`), InvITs (`-IV`), priced from NSE's RR/IV bhavcopy | no |
| ETFs and funds | `INF` ISINs | no |
| Bonds and gold bonds | debt series, SGBs | no |
| Other / to verify | rights entitlements, unknown suffixes | no |

## Core vs Satellite

Measured on operating equity only. **Your bucket column is the role**: core
is core; satellite, trade and a blank bucket are satellite. Targets and the
review triggers live in `config/core_satellite.yaml`:

| | default |
|---|---|
| Target | Core 70%, Satellite 30% |
| Review triggers | Core below 67% or above 73%; Satellite above 33% or below 27% |
| Largest position | core 8%, satellite 4%, small cap 2% of the book (the tightest that applies) |
| Largest sector | 25% of operating equity |

Each is a review note, not an instruction to trade.

**Market-cap band.** SEBI's cut is by rank: 1–100 large, 101–250 mid, the
rest small, published half-yearly by AMFI. AMFI's file wasn't reachable, so
the band comes from NSE index membership (Nifty 100 = large, Nifty Midcap 150
= mid, other listed shares = small). Those indices track the same ranks; the
page says which source it used.

**Sector** is NSE's industry for Nifty 500 constituents; others show as
*Unclassified* and are listed.

## Importing a broker file

```bash
PYTHONPATH=. .venv/bin/python scripts/import_holdings.py --dry-run   # preview only
PYTHONPATH=. .venv/bin/python scripts/import_holdings.py             # preview, then asks
```

Or upload the file on **Import holdings**. Either way you get a preview
first: new, removed and changed positions, and every validation finding:

- no quantity, no average cost, or an invested amount that does not match
  quantity × cost
- long-term quantity larger than the holding (an error: nothing is written)
- a symbol not in the NSE market data, or an ISIN now trading under another
  symbol (ticker change, merger)
- not traded for 10+ sessions (suspended or delisted?)
- a split or bonus in the last 60 days (check the broker already reflects it)
- duplicate lines (merged at the blended cost)
- a security type to verify

Nothing is written until you confirm. The old file is kept as `.bak`, your
bucket, notes and purchase dates carry over, a long/short-term split on file
is kept when the new file lacks one and the quantity is unchanged, and the
import is recorded in the audit log in `data/research.db`.

## Snapshots

*Save snapshot* stores the book as valued: quantity, cost, the price used and
its source, sector and band for each holding, plus the summary. Later phases
compare reviews against these.
