# Mutual fund portfolio tracking

What Parag Parikh Flexi Cap Fund's manager bought, sold and held, month by
month, from the fund's own portfolio disclosures.

[← back to the README](../README.md)

---

```bash
PYTHONPATH=. .venv/bin/python run_mf_ppfas.py
```

Run it once a month, after about the 10th; PPFAS publishes 7-10 days after
month-end. The script downloads any missing month into `data/mf/ppfas_raw/`,
parses every file, and writes:

| Output | What it is |
|---|---|
| `reports/mf/PPFAS_Flexi_Cap_Portfolio_Analysis.xlsx` | The workbook. Start at *Year Summary*, then *Monthly Story* and *Decisions Grid* |
| `data/mf/ppfas_flexi_cap_holdings.csv` | Every line of every disclosure, normalized |

Fund: Direct Growth ISIN `INF879O01027`, AMFI scheme code `122639`. Holdings
are disclosed per scheme, not per plan, so the plan only affects the NAV used.

## The file

There is one sheet per month at
`amc.ppfas.com/downloads/portfolio-disclosure/<year>/PPFCF_PPFAS_Monthly_Portfolio_Report_<Month>_<day>_<year>.xlsx`.
Files were `.xls` up to Feb 2026 (read with `xlrd`). The sections, in order:

- domestic listed equity
- **arbitrage** (stock long + future short)
- special situations (rights shares)
- REITs
- foreign equity
- money market
- liquid fund units / TREPS
- net receivables and the grand total
- futures
- notes: NAVs and turnover

The section lettering is unstable. Foreign equity is its own block most months
but "(d)" inside the domestic block in Feb 2026, and unlisted appears as both
"(c)" and "(b)". The parser therefore matches headings with the letter stripped.
Every month must reconcile, holdings plus receivables to the grand total, or a
warning is printed.

## Three corrections before reading anything as a decision

1. **Shares, not weights.** Weight moves with price; the share count is the
   manager's choice.
2. **Inflows.** Units outstanding grow by (net-assets growth / NAV growth) each
   month, and a fund with inflows buys some of everything just to stay invested.
   "Beyond inflows" is the share-count change after dividing that factor out.
3. **Splits and bonuses.** NSE's own record (`corporate_actions` in
   `signals.db`) is used when it has the stock. Otherwise a split is inferred,
   but never from the price gap alone, which is the trap [data](data.md) warns
   about. It also needs either a **new ISIN** (face-value splits always issue
   one) or a share count that rose by the same ratio. A crash changes neither.
   Holdings are matched on the first nine ISIN characters, so a stock's history
   continues across a face-value change.

Arbitrage positions carry no price risk and are treated as cash, never as
stock picks. A stock moving from the arbitrage book into the core portfolio
(TCS in Nov 2025, DLF in Aug 2026) is labelled as such.

## Limits

These are month-end snapshots, so trades inside a month are invisible. Traded
values are estimated at the month-end price. The workbook describes what the
fund did; it is not a signal that has been tested for returns (see
[validation](validation.md) for how this project tests signals).
