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

cp config/holdings.example.csv config/holdings.csv    # a Kite export works as-is

PYTHONPATH=. .venv/bin/python run_market_ingest.py --years 10   # ~70 min, once
PYTHONPATH=. .venv/bin/python run_backfill.py --universe nifty200 --years 10
PYTHONPATH=. .venv/bin/python fetch_fundamentals.py

.venv/bin/python run_ui.py                            # dashboard on :8501
```

Optional — a report every Saturday, on this machine:

```bash
.venv/bin/python install_schedule.py --day saturday --portfolio 2000000
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
| `set_buckets.py` | Pre-fill core / satellite / legacy |

Scripts need `PYTHONPATH=.`; the launchers above run fine via `.venv/bin/python`.
Full flags in [operations](docs/operations.md).

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
| Momentum rotation | **14.5%** |
| NIFTY 500 index | 11.3% |
| Random-pick control | 1.8% |
| v1 signal rules *(retired)* | 1.3% |

After realistic Indian costs — STT, stamp duty, exchange fees, GST, slippage.
Momentum sits 8.4 standard deviations above a random control drawn from the same
universe, so the ranking carries real information. But the edge is 3.2 points
with a **−44% drawdown against the index's −38%**, and a Sharpe of 0.64.

Removing survivorship bias cost 14 points of apparent return (28.7% → 14.5%) and
dropped the random control from 17.7% to 1.8% — that collapse is the bias
measured directly.

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
