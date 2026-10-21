# Option rule backtest

What a simple, repeatable option rule would have done on NSE's history,
after costs, and the trade log to check it against.

[← F&O overview](README.md) · [← back to the README](../../README.md)

---

```bash
PYTHONPATH=. .venv/bin/python research/run_option_backtest.py --action buy --side PE --strike moves:-1
PYTHONPATH=. .venv/bin/python research/run_option_backtest.py --compare
PYTHONPATH=. .venv/bin/python research/run_option_backtest.py --action sell --side CE --strike moves:+1 \
    --universe stocks --sessions 5 --excel
PYTHONPATH=. .venv/bin/python research/run_option_backtest.py --universe NIFTY --cycle weekly --sessions 2
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
- **Held to expiry,** valued at intrinsic value against the underlying's
  expiry-day close, and closed the way it would be in practice:
  - **index options** are cash-settled; an in-the-money long pays exercise
    STT of 0.125% of intrinsic value;
  - **stock options** are physically settled since 2019, so holding an
    in-the-money one into expiry means delivering or taking the shares.
    Traders square off instead, so an in-the-money stock option is sold
    (long) or bought back (short) on expiry day at intrinsic value, with
    normal charges and slippage. The trade log's `closed` column says which
    happened.
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

## Reading the results

- **Judge a rule by period, not by its total.** Option rules swing with the
  market regime: a rule that sells puts wins small and often in calm markets
  and can lose heavily in a sell-off, when the matching buying rule does well.
  Use `--start` / `--end` to split the history.
- **Sellers win small and often, and lose big and rarely.** Look at the worst
  trade and the max drawdown, not just the win rate.
- **Per lot, not per rupee of margin.** A short option needs a lot of margin
  per lot, so return on capital is much lower than the per-lot totals
  suggest, and a gap through the strike can cost many months of premium.
- **Stocks: use the average per trade.** The totals add up one lot in every
  stock every month, which isn't a portfolio anyone holds. Short stock options
  expiring on the same day also lose together in a broad fall, and one stock's
  gap can be many times the median trade.
- Stock history starts in 2024, so it is a short sample.

What a rule would have done after costs, not what to trade.

## Also: trade logs for the equity backtests

The same reporting runs on the equity backtests:

```bash
PYTHONPATH=. .venv/bin/python research/run_backtest.py --export
PYTHONPATH=. .venv/bin/python research/run_pit_momentum.py --export
```

- **`research/run_backtest.py`** (the signal engine) prints the trade statistics
  above, equity drawdown stats and a monthly return table. It first checks
  a sample of fills: entry and exit on real trading sessions, each filled
  at that session's open ± slippage. `--export` writes the trade log
  (Excel and CSV), and splits by symbol and by exit rule.
- **`research/run_pit_momentum.py`** (the rotation strategy) has no discrete trades.
  Its record is the equity curve, the monthly return table, strategy vs
  benchmark by year, and the holdings at every rebalance, all in the
  `--export` workbook.

---

See also: [validation](validation.md) · [F&O data](data.md) · the
equity-side [validation](../validation.md).
