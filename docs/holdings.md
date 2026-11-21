# Recording what you own

How to get your holdings from any broker into the app, and keep them current.

[← back to the README](../README.md)

---

The app reads your stock holdings from `config/holdings.xlsx` (or
`config/holdings.csv`; keep only one). `setup_project.py` creates the workbook
with a *How to fill* sheet. It stays on your computer and is never committed
to git; [data storage](storage.md) lists every file the app keeps and
what to back up. Every broker's holdings download looks a little different,
so bring yours into this shape:

| Column | Required | What goes in it |
|---|---|---|
| `symbol` | yes | The **NSE trading symbol**, e.g. `RELIANCE`, `BAJAJ-AUTO`. Not the company name, not a BSE code. Drop exchange suffixes like `.NS` or `-EQ` |
| `quantity` | yes | Total shares you hold, pledged ones included |
| `avg_price` | yes | Your average buy price per share, in rupees |
| `lt_quantity`, `st_quantity` | no | Shares held more than / at most 12 months. Fill one and the other is worked out. Used for the tax estimate on a sell |
| `purchase_date` | no | `YYYY-MM-DD`. The trailing stop is measured from it |
| `bucket` | no | `core`, `satellite` or `trade`. Blank lets the app suggest core or satellite; only you can mark `trade` |
| `notes` | no | Anything for yourself |

```
symbol,quantity,lt_quantity,st_quantity,avg_price,purchase_date,bucket,notes
RELIANCE,40,40,0,1250.50,,core,
IRCTC,200,120,80,727.46,2025-03-14,satellite,
```

## Option 1: import the broker file

Most broker downloads already carry these columns under other names, and the
importer recognises the common ones: *Instrument / Scrip / Stock* for the
symbol, *Qty. / Shares / Holding Qty* for quantity, *Avg. cost / Average
Price / Buy Avg* for the price, *Long Term Qty / Short Term Qty* for the tax
split. It finds the header row even below a client-details preamble, skips
ETFs, bonds, REITs and InvITs (and lists them), and merges a stock that
appears twice.

Put the downloaded `.xlsx` or `.csv` in `config/` and run:

```bash
PYTHONPATH=. .venv/bin/python scripts/import_holdings.py                     # newest broker file in config/
PYTHONPATH=. .venv/bin/python scripts/import_holdings.py path/to/file.xlsx
PYTHONPATH=. .venv/bin/python scripts/import_holdings.py --trade CPPLUS SANSERA   # also mark these as trade
```

If it says *no holdings table found*, the headers didn't match: rename them in
the broker file (for example *Security Name* → `symbol`, *Net Qty* →
`quantity`, *Avg Rate* → `avg_price`) and run it again, or use option 2.

## Option 2: fill it in by hand

Open the broker file in Excel, Numbers or Google Sheets and copy the columns
into `config/holdings.xlsx` (or a `.csv` with the header above):

1. Keep one row per stock. If the same stock appears on two lines (different
   series or demat accounts), add the quantities and use the weighted average
   price.
2. Replace company names with NSE symbols. Search the name on
   [nseindia.com](https://www.nseindia.com) if unsure; the dashboard's search
   box also resolves names and ISINs.
3. Remove mutual funds, ETFs, bonds, gold bonds, REITs and InvITs: only NSE
   equities are ranked. Mutual funds go in `config/mf_holdings.xlsx` instead
   (see [mutual fund evaluation](mf_evaluation.md)).
4. Numbers only in the number columns. `₹` and thousands commas are fine;
   text like "40 shares" is not.
5. If your broker shows only total quantity, leave `lt_quantity` and
   `st_quantity` blank.

## After each purchase

Re-import (or edit the file), then re-run the advisor so sizing, sector caps
and the decision log include the new positions.

- Your `bucket`, `notes` and `purchase_date` are kept for every stock you
  still hold when you re-import. The old file is backed up to
  `config/holdings.xlsx.bak` (or `.csv.bak`).
- **A new holding imported without `--trade` has a blank bucket, and blank is
  not trade** -- the app only ever suggests core or satellite. The importer
  lists new holdings and reminds you.
- `--trade` overrides any existing bucket, and warns about any symbol that is
  not in the broker file.
- A stock has one bucket. Buying more of a core holding keeps the whole
  position core.
