# stock-signals

**A portfolio advisor for NSE equities.** It reads Indian company filings, ranks
a survivorship-free universe, and tells you what to do with what you already
own — including when the honest answer is that it found nothing.

> **[→ Feature overview](https://claude.ai/code/artifact/3e4ddd96-ba4b-4d97-9220-34ef68255dc0)** —
> a visual walkthrough. *(Private link.)*

**Read-only with respect to your broker.** It never places, modifies or cancels
an order — GTT included. It computes and explains; you decide and click.
Decision support, not investment advice.

---

## Quick start

```bash
uv venv && uv pip install -r requirements.txt

PYTHONPATH=. .venv/bin/python import_kite_holdings.py   # Kite .csv/.xlsx dropped in config/

PYTHONPATH=. .venv/bin/python run_market_ingest.py --years 10   # ~70 min, once
PYTHONPATH=. .venv/bin/python run_backfill.py --universe nifty500 --years 10
PYTHONPATH=. .venv/bin/python fetch_fundamentals.py

PYTHONPATH=. .venv/bin/python run_ui.py                            # dashboard on :8501
```

Optional — a report every Saturday, on this machine:

```bash
PYTHONPATH=. .venv/bin/python install_schedule.py --day saturday --portfolio 2000000
```

## Commands

| | |
|---|---|
| `run_ui.py` | Dashboard: holdings and market, localhost only |
| `run_advisor.py` | Verdicts on your book, as text |
| `run_momentum.py` | Rank the universe |
| `run_pit_momentum.py` | Survivorship-free backtest |
| `run_weekly.py` | Refresh everything, then report |
| `install_schedule.py` | Install / check / remove the Saturday job |
| `import_kite_holdings.py` | Kite holdings download → `config/holdings.csv` |
| `set_buckets.py` | Pre-fill core / satellite / legacy |
| `record_momentum.py` | Save this month's momentum picks to `history/` |
| `run_fno.py` | F&O: PCR, OI support/resistance, range to expiry (`--excel` for a workbook) |
| `run_fno_backtest.py` | How often that range actually held (`--ingest` first) |
| `record_fno.py` | Monthly F&O forward log → `history/fno_ranges.xlsx`, scored at expiry |
| `run_option_backtest.py` | What a simple option rule would have done, after costs (`--compare`, `--excel`) |

Scripts need `PYTHONPATH=.`; the launchers above run fine via `.venv/bin/python`.
Full flags in [operations](docs/operations.md).

## Recording what you buy

After buying, download your holdings from Kite (Console's `.xlsx` carries the
long/short-term split; the web `.csv` also works), put the file in `config/`,
and import it. Name the momentum buys with `--satellite` so they are rotated on
rank:

```bash
PYTHONPATH=. .venv/bin/python import_kite_holdings.py --satellite CPPLUS SANSERA
```

- Quantities and average prices come from Kite; your `bucket`, `notes` and
  `purchase_date` are kept for every stock you still hold. The old file is
  backed up to `config/holdings.csv.bak`.
- **A new holding imported without `--satellite` has a blank bucket, and blank
  is not satellite** -- the framework only ever suggests core or legacy. The
  importer lists new holdings and reminds you.
- `--satellite` overrides any existing bucket, and warns about any symbol that
  is not in the Kite file.
- Optionally add `purchase_date` (YYYY-MM-DD) to the new rows in
  `config/holdings.csv`: Kite does not export it, and the trailing stop is
  measured from it. It is kept on later imports.
- A stock has one bucket. Buying more of a core holding keeps the whole
  position core.

Then re-run the advisor, so sizing, sector caps and the decision log include
the new positions.

## Momentum history

`record_momentum.py` saves the month's picks -- every stock in the top 15 on
either lookback, with ranks, scores, the price on the day and the NIFTY 500
level -- to `history/momentum_picks.xlsx`, one tab per month named like
`Oct 2026`. Each new month is a new tab at the end. It records once per month
(the first run of the month is kept; `--force` replaces that tab), and the
Saturday job runs it. Started October 2026.

The point is to measure later how the picks actually did against the backtest,
from the prices on the day they were suggested -- recomputing old months with
today's code would measure today's code instead. The file is meant for git;
what you held is deliberately not in it.

## Documentation

| | |
|---|---|
| [**Portfolio advisor**](docs/advisor.md) | Verdicts, cash constraint, concentration, tax, decision log |
| [**Dashboard**](docs/dashboard.md) | Two sections, badges, the two target frames |
| [**Strategy**](docs/strategy.md) | Momentum ranking, fundamentals, the lender rubric |
| [**Validation**](docs/validation.md) | What the backtests found, including what failed |
| [**Data**](docs/data.md) | Sources, five silent NSE traps, freshness |
| [**Automation**](docs/automation.md) | The Saturday job and its failure modes |
| [**Operations**](docs/operations.md) | Setup, watchlist, tuning, tests |
| [**F&O analysis**](docs/f_o/README.md) | PCR, OI walls, expected range -- and [whether it works](docs/f_o/validation.md); [option rule backtest](docs/f_o/backtest.md) |
| [**Code layout**](docs/layout.md) | Where things live |
| [ROADMAP](ROADMAP.md) | What is built, and what is still wrong with it |

## The idea in one table

| verdict | meaning |
|---|---|
| **B** Buy / Add | Below its intended weight, quality intact |
| **T** Trim | The position is too **big**, not bad — you keep it |
| **S** Exit | The **reason to own it** is gone — sell it all |
| **S!** Exit, urgent | A balance-sheet fact, not a judgement call |
| **H** Hold | No action, stated rather than left as silence |

Conflating trim and exit is how people average down into failures and take
profits on their winners. Holdings sit in one of three buckets — **core** (never
sold on rank), **satellite** (rotates on rank), **legacy** (red flags apply,
rank ignored; the safe default for anything bought before this existed).

## Results

| | 9-year CAGR |
|---|---|
| Momentum rotation, 9-1 (main) | **11.7%** |
| Momentum rotation, 12-1 (alongside) | 12.4% |
| NIFTY 500 index | 10.4% |
| Random-pick control | 4.0% |
| v1 signal rules *(retired)* | 1.3% |

After realistic Indian costs — STT, stamp duty, exchange fees, GST, slippage.
Momentum beat all 10 random controls drawn from the same universe, by 7.6
points on average (3.0 standard deviations), so the ranking carries real
information. **But it beats the index by only about 2 points a year**, with a
**−43% drawdown against the index's −37%**, and a Sharpe of 0.55. The rebalance
dates alone move the CAGR between 10.9% and 15.9% (12.5% on average); the
12-month lookback averages the same 12.5%, so neither lookback is proven
better and both are shown.

Removing survivorship bias cost 14 points of apparent return (30.8% → 16.9%,
measured with the old regime rule on) and dropped the random control from
17.2% to 5.0% — that collapse is the bias measured directly.

*Corrected 2026-10-04:* the earlier 14.5% was run with ETFs leaking into the
universe; see [validation](docs/validation.md). The earlier "8.4 standard
deviations" did not reproduce even with the ETFs left in (2.6 with 10 seeds).
The main ranking moved from 12 to 9 months the same day. Later that day the
16.9% headline was also withdrawn: it depended on holding half as many stocks
in RISK-OFF, a rule that did not reduce drawdowns and was switched off; see
[validation](docs/validation.md).

**Honest split: roughly 70% of this framework's value is monitoring and
discipline; perhaps 30% is momentum alpha.** Two ideas that sounded good and
failed their out-of-sample tests are recorded in
[validation](docs/validation.md) rather than quietly dropped.

## Scale

```
3.84M   daily price rows          175   tests, one per bug found
3,638   symbols, delisted included  489   ETFs excluded, with reasons
2,607   trading days                  0   orders it can place
```

Everything runs locally. Free public data throughout: no API key, no
subscription, no broker credentials.
