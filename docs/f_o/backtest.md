# Option rule backtest

What a simple, repeatable option rule would have done on NSE's history,
after costs, and the trade log to check it against.

[← F&O overview](README.md) · [← back to the README](../../README.md)

---

```bash
PYTHONPATH=. .venv/bin/python run_option_backtest.py --action buy --side PE --strike moves:-1
PYTHONPATH=. .venv/bin/python run_option_backtest.py --compare
PYTHONPATH=. .venv/bin/python run_option_backtest.py --action sell --side CE --strike moves:+1 \
    --universe stocks --sessions 5 --excel
PYTHONPATH=. .venv/bin/python run_option_backtest.py --universe NIFTY --cycle weekly --sessions 2
```

The "Check an option" panel answers "what are this contract's odds?". This
answers a different question: **if I had followed this rule every expiry,
what would have happened?** It still doesn't choose a strategy.

## A rule

| flag | values |
|---|---|
| `--action` | `buy` or `sell` |
| `--side` | `CE` (call) or `PE` (put) |
| `--strike` | `atm` · `moves:+1.0` (spot + 1 ATM straddle; negative is below) · `pct:-3` · `put_wall` · `call_wall` |
| `--sessions` | entry this many trading sessions before expiry: 20/10/5 (monthly) or 4/2/1 (weekly), the days the history stores chains |
| `--cycle` | `monthly` or `weekly` |
| `--universe` | `indices` · `stocks` · `all` · `NIFTY,BANKNIFTY` |
| `--start`, `--end` | limit the entry dates |
| `--slippage-pct` | per side, % of premium (default 1.0) |

`--compare` runs a standard grid: buy and sell, call and put, ATM and one
straddle out.

## How a trade is simulated
- **Entry** at that strike's closing price on the entry day, only if it
  traded that day. An untraded close is a carried or theoretical price.
- **Held to expiry,** settled at intrinsic value against the underlying's
  expiry-day close. Stock options are physically settled; the cash
  difference is the same.
- **Costs and slippage per order** (`backtest/costs.py`, `OptionCostModel`,
  Zerodha's F&O schedule): ₹20 brokerage, exchange and SEBI fees, GST,
  stamp duty on buys, and STT of 0.1% of premium on sells. A long option
  that expires in the money also pays exercise STT of 0.125% of its
  intrinsic value. Slippage is 1% of premium per side (at least one tick),
  because the history has closes, not quotes.
- **One lot per trade.** P&L is in rupees per lot, and also per unit and
  as a % of premium. Pre-2024 index trades use the earliest known lot size,
  because the old file has none, so their rupee figures are approximate.
- **Skipped,** with counts reported: cycles with a split or bonus, strikes
  that didn't trade, and targets with no traded strike near them.

## What it reports
- **Automatic check first.** A random sample of trades is rebuilt from
  NSE's original files, re-downloaded: the option's close, that it traded,
  the expiry-day close, and the P&L recomputed from those numbers alone.
  Any mismatch is printed as a failure before any metric.
- **Statistics:** trades, win rate, average win and loss, profit factor,
  expectancy, total, max drawdown on cumulative P&L, return / max drawdown,
  longest winning and losing streaks, best and worst trade
  (`backtest/report.py`).
- **Year × month P&L table,** and results split by index/stock (and by
  symbol in Excel).
- **`--excel`:** a workbook with Metrics, Trades, Year x Month, By symbol
  and By kind, plus the trade log as CSV beside it.

## First results (indices, monthly, entry 10 sessions out, 2019 – Sep 2026)

From `--compare`, rupees per lot after costs. The automatic check passed for
every rule.

| rule | trades | win rate | profit factor | total | max drawdown |
|---|---|---|---|---|---|
| buy CE ATM | 298 | 34.6% | 0.91 | −2.42 L | 10.47 L |
| buy PE ATM | 293 | 32.1% | 0.93 | −1.65 L | 4.94 L |
| buy CE 1 straddle above | 247 | 17.4% | 0.94 | −0.49 L | 3.49 L |
| buy PE 1 straddle below | 248 | 19.0% | 0.71 | −2.50 L | 3.95 L |
| sell CE 1 straddle above | 247 | 82.6% | 1.02 | +0.15 L | 4.31 L |
| sell PE 1 straddle below | 248 | 81.0% | 1.34 | +2.15 L | 1.91 L |
| sell CE ATM | 298 | 65.1% | 1.05 | +1.27 L | 10.21 L |
| sell PE ATM | 293 | 67.9% | 1.03 | +0.67 L | 4.58 L |

**Buying lost after costs in every case, and selling made money in every
case, but that is not a free lunch.** The same split by period as the
validation:

| | 2019–21 | 2022–23 | 2024–26 |
|---|---|---|---|
| sell PE 1 straddle below: profit factor (total) | 1.32 (+0.40 L) | **0.51 (−1.23 L)** | 2.19 (+2.98 L) |
| buy PE 1 straddle below: profit factor (total) | 0.72 (−0.48 L) | **1.87 (+1.16 L)** | 0.44 (−3.18 L) |

- **Regime decides it.** The seller's best rule lost heavily through the
  2022–23 sell-offs, exactly when the buyer's made money. Its worst single
  trade was −₹43,818 per lot (NIFTY, February 2022).
- **Sellers win small and often, and lose big and rarely.** That's the
  same "straddle slightly overprices the move" seen in the
  [validation](validation.md), collected one premium at a time, with the
  tail risk attached.
- **Per lot, not per rupee of margin.** A short index option needs
  roughly a lakh or more of margin per lot, depending on the strike and
  the day. Return on capital is much lower than the totals suggest, and a
  gap through the strike can cost many months of premium.
- **The headline totals mix three regimes.** Judge any rule by period,
  as above, not by its total.

What these rules would have done after costs, not what to trade.

## Stocks (monthly, entry 10 sessions out, Jan 2024 – Sep 2026)

From `--compare --universe stocks`. That's about 6,500 trades per rule
across ~200 stocks, and the automatic check passed for every rule. Use the
**average per trade**: the totals add up one lot in every stock every month,
which isn't a portfolio anyone holds.

| rule | trades | win rate | profit factor | average per trade (₹/lot) |
|---|---|---|---|---|
| buy CE ATM | 6,612 | 32.1% | 0.88 | −1,283 |
| buy PE ATM | 6,625 | 33.1% | 0.79 | −2,074 |
| buy CE 1 straddle above | 6,450 | 15.7% | 0.73 | −1,435 |
| buy PE 1 straddle below | 6,444 | 14.2% | 0.61 | −1,790 |
| sell CE 1 straddle above | 6,450 | 84.2% | 1.29 | +1,149 |
| sell PE 1 straddle below | 6,444 | 85.5% | 1.53 | +1,522 |
| sell CE ATM | 6,612 | 67.3% | 1.08 | +760 |
| sell PE ATM | 6,625 | 66.5% | 1.19 | +1,583 |

The same pattern as indices, more strongly: buying lost after costs and
selling made money, in every rule.

| profit factor by year | 2024 | 2025 | 2026 |
|---|---|---|---|
| sell PE 1 straddle below | 1.46 | 1.79 | 1.38 |
| buy PE 1 straddle below | 0.65 | 0.52 | 0.68 |
| sell CE 1 straddle above | 1.29 | 1.11 | 1.62 |

- **Consistent across all three years, but they're only three years.**
  Stock history starts in 2024, so the sample has no stretch like 2022–23,
  when the same put-selling rule lost heavily on indices.
- **Losses arrive together.** About 200 short options expire on the same
  day, so a broad fall hits them all at once. For selling the put one
  straddle below, 27% of expiries lost on average across stocks; the worst
  were Dec-2024 (−₹8,267 per stock-lot) and Oct-2024 (−₹8,084).
- **The single-stock tail is severe.** The worst short put lost **₹2.18
  lakh on one lot** (IEX), about 50× the median trade (+₹4,345). The worst
  short call lost ₹1.69 lakh (INDIACEM). A bought option can't lose more
  than its premium: the worst bought put lost ₹28,036.
- **Margin.** Short stock options tie up a lot of margin per lot, so
  return on capital is far below these per-lot figures.
- **Skips:** 214 cycles had no traded ATM straddle to measure "one
  straddle" from, 16 had a split or bonus inside, and up to 61 had no traded
  strike near the target. All are counted in the output.

Selling won small and often; a single stock's gap or a market-wide fall took
back months of premium. What these rules would have done after costs, not
what to trade.

## Also: trade logs for the equity backtests

The same reporting now runs on the existing backtests:

```bash
PYTHONPATH=. .venv/bin/python run_backtest.py --export
PYTHONPATH=. .venv/bin/python run_pit_momentum.py --export
```

- **`run_backtest.py`** (the signal engine) prints the trade statistics
  above, equity drawdown stats and a monthly return table. It first checks
  a sample of fills: entry and exit on real trading sessions, each filled
  at that session's open ± slippage. `--export` writes the trade log
  (Excel and CSV), and splits by symbol and by exit rule.
- **`run_pit_momentum.py`** (the rotation strategy) has no discrete trades.
  Its record is the equity curve, the monthly return table, strategy vs
  benchmark by year, and the holdings at every rebalance, all in the
  `--export` workbook.

---

See also: [validation](validation.md) · [F&O data](data.md) · the
equity-side [validation](../validation.md).
