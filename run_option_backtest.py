"""Backtest a simple option rule on the F&O bhavcopy history, after costs.

    PYTHONPATH=. .venv/bin/python run_option_backtest.py --action buy --side PE --strike moves:-1
    PYTHONPATH=. .venv/bin/python run_option_backtest.py --action sell --side CE --strike moves:+1 \\
        --sessions 5 --universe stocks --excel
    PYTHONPATH=. .venv/bin/python run_option_backtest.py --compare          # a standard grid
    PYTHONPATH=. .venv/bin/python run_option_backtest.py --universe NIFTY --cycle weekly --sessions 2

Strike rules: atm | moves:+1.0 (spot + 1 ATM straddle) | pct:-3 | put_wall | call_wall.
Entry: the day's close (only strikes that traded), 20/10/5 sessions before a
monthly expiry or 4/2/1 before a weekly one; held to expiry; one lot; costs
and slippage from backtest/costs.py OptionCostModel. Before printing any
metric, a random sample of trades is rebuilt from NSE's original files
(--no-reconcile to skip; it downloads a few files).

What a rule WOULD have done, not what to trade. Needs data/fo_history.db
(run_fno_backtest.py --ingest, and --ingest-old for 2019-2023 indices).
"""
from __future__ import annotations

import argparse
import sys

import pandas as pd

from backtest import option_backtest as ob
from backtest import reconcile as rc
from backtest import report as rp
from backtest.costs import OptionCostModel
from data import fo_bhavcopy as fb

GRID = [("buy", "CE", "atm"), ("buy", "PE", "atm"),
        ("buy", "CE", "moves:+1"), ("buy", "PE", "moves:-1"),
        ("sell", "CE", "moves:+1"), ("sell", "PE", "moves:-1"),
        ("sell", "CE", "atm"), ("sell", "PE", "atm")]


def _fmt(stats: dict) -> str:
    keys = ["trades", "win_rate_pct", "avg_win", "avg_loss", "profit_factor",
            "expectancy", "total", "max_drawdown", "return_to_max_dd",
            "longest_win_streak", "longest_loss_streak", "best", "worst"]
    return "\n".join(f"  {k:>20}: {stats.get(k)}" for k in keys if k in stats)


def one(con, rule, args, costs) -> dict:
    res = ob.run(con, rule, costs, args.start, args.end)
    t = res.trades
    print(f"\n=== {rule.name} ===")
    if t.empty:
        why = "; ".join(f"{k} ({v})" if v > 1 else k for k, v in res.skipped.items())
        print(f"  no trades: {why or 'nothing matched'}")
        return {}
    if not args.no_reconcile:
        print("  " + rc.summary(rc.options(t, k=args.reconcile)).replace("\n", "\n  "))
    st = rp.trade_stats(t["pnl"])
    print(f"  {t['entry_date'].min():%b %Y} .. {t['exit_date'].max():%b %Y}, "
          f"{t['symbol'].nunique()} underlyings, P&L in rupees per lot after costs")
    print(_fmt(st))
    if res.skipped:
        print(f"  skipped: " + ", ".join(f"{k} {v}" for k, v in res.skipped.items()))
    if t["lot_estimated"].any():
        print(f"  {int(t['lot_estimated'].sum())} pre-2024 trades use an estimated lot size "
              "(rupee amounts approximate; % of premium is exact)")
    by_kind = rp.split_stats(t, "kind")
    if len(by_kind) > 1:
        print("\n  by kind:\n" + by_kind[["kind", "trades", "win_rate_pct",
                                         "profit_factor", "total"]].to_string(index=False))
    months = rp.month_table(t.set_index("exit_date")["pnl"])
    print("\n  P&L by year and month (rupees per lot, summed by expiry month):")
    print(months.fillna("").to_string())
    if args.excel is not None:
        name = rule.name.replace(" ", "_").replace(":", "").replace("@", "")
        if args.excel and args.compare:
            # One workbook per rule: a single explicit path would be
            # overwritten by every rule in the grid.
            from pathlib import Path
            p = Path(args.excel)
            target = p.with_name(f"{p.stem}_{name}{p.suffix or '.xlsx'}")
        else:
            target = args.excel or rp.default_path(f"options_{name}")
        path = rp.export(target, {
            "Metrics": rp.stats_frame({"rule": rule.name, **st}),
            "Trades": t, "Year x Month": months,
            "By symbol": rp.split_stats(t, "symbol"),
            "By kind": by_kind,
        }, trades=t)
        print(f"\n  Excel: {path} (trades also as CSV beside it)")
    return {"rule": rule.name, **st}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--action", choices=["buy", "sell"], default="buy")
    ap.add_argument("--side", choices=["CE", "PE"], default="PE")
    ap.add_argument("--strike", default="atm")
    ap.add_argument("--sessions", type=int, default=10)
    ap.add_argument("--cycle", choices=["monthly", "weekly"], default="monthly")
    ap.add_argument("--universe", default="indices",
                    help="indices | stocks | all | comma-separated symbols")
    ap.add_argument("--start", default=None, help="YYYY-MM-DD, entry dates from")
    ap.add_argument("--end", default=None)
    ap.add_argument("--slippage-pct", type=float, default=None,
                    help="per side, %% of premium (default 1.0)")
    ap.add_argument("--compare", action="store_true",
                    help="run a standard grid of buy/sell, call/put, ATM/one-straddle rules")
    ap.add_argument("--excel", nargs="?", const="", default=None, metavar="PATH")
    ap.add_argument("--no-reconcile", action="store_true")
    ap.add_argument("--reconcile", type=int, default=4, metavar="N",
                    help="trades to rebuild from source (default 4)")
    a = ap.parse_args(argv)

    if not fb.DB_PATH.exists():
        print("No F&O history. Run: run_fno_backtest.py --ingest", file=sys.stderr)
        return 1
    con = fb.connect()
    costs = OptionCostModel()
    if a.slippage_pct is not None:
        costs.slippage_pct = a.slippage_pct
    universe = (a.universe if a.universe in ("indices", "stocks", "all")
                else [s.strip().upper() for s in a.universe.split(",")])
    try:
        ob.Rule(a.action, a.side, a.strike, a.sessions, a.cycle, universe).validate()
    except ValueError as exc:
        ap.error(str(exc))

    if a.compare:
        rows = []
        for action, side, strike in GRID:
            r = one(con, ob.Rule(action, side, strike, a.sessions, a.cycle, universe), a, costs)
            if r:
                rows.append(r)
        if rows:
            t = pd.DataFrame(rows)[["rule", "trades", "win_rate_pct", "profit_factor",
                                     "expectancy", "total", "max_drawdown"]]
            print("\n=== Comparison (rupees per lot, after costs) ===")
            print(t.to_string(index=False))
    else:
        one(con, ob.Rule(a.action, a.side, a.strike, a.sessions, a.cycle, universe), a, costs)
    print("\nWhat these rules WOULD have done, after costs -- not a recommendation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
