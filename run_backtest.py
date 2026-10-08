"""Backtest the strategy, then try hard to disprove it."""
from __future__ import annotations

import argparse

import watchlist as wl
from backtest import engine as bt
from backtest.costs import CostModel
from data import quality, store
from scoring import pipeline, timing


def show(title: str, m: dict) -> None:
    print(f"\n=== {title} ===")
    for k, v in m.items():
        print(f"  {k:>28}: {v}")


def _report(res, args) -> None:
    """Trade-level statistics and the year x month tables; Excel on --export."""
    import pandas as pd
    from backtest import report as rp
    closed = [t for t in res.trades if t.exit_price is not None]
    if not closed:
        return
    t = pd.DataFrame([{
        "symbol": x.symbol, "entry_date": pd.Timestamp(x.entry_date),
        "exit_date": pd.Timestamp(x.exit_date), "entry_price": x.entry_price,
        "exit_price": x.exit_price, "qty": x.qty, "entry_rule": x.entry_rule,
        "exit_rule": x.exit_rule, "partial": x.scale_out, "costs": round(x.costs, 2),
        "pnl": round(x.pnl, 2), "return_pct": round(x.return_pct, 2)}
        for x in closed]).sort_values("exit_date").reset_index(drop=True)
    st = rp.trade_stats(t["pnl"])
    show("Trades (rupees, after costs)", st)
    show("Equity curve", rp.equity_stats(res.equity))
    months = rp.month_table(res.equity, "compound")
    print("\n=== Monthly return, % (equity curve) ===")
    print(months.fillna("").to_string())
    if args.export is not None:
        path = rp.export(args.export or rp.default_path("trades"), {
            "Metrics": rp.stats_frame({**res.metrics, **st, **rp.equity_stats(res.equity)}),
            "Trades": t, "Monthly return %": months,
            "P&L by month": rp.month_table(t.set_index("exit_date")["pnl"]),
            "By symbol": rp.split_stats(t, "symbol"),
            "By exit rule": rp.split_stats(t, "exit_rule"),
            "Equity": res.equity.rename("equity").to_frame().rename_axis("date"),
        }, trades=t)
        print(f"\nExcel: {path} (trades also as CSV beside it)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=1_000_000)
    ap.add_argument("--start", type=str, default=None, help="YYYY-MM-DD")
    ap.add_argument("--end", type=str, default=None)
    ap.add_argument("--shuffle", action="store_true",
                    help="run the randomised-entry control")
    ap.add_argument("--symbols", nargs="*", default=None,
                    help="restrict to these symbols (few names = noisy result)")
    ap.add_argument("--per-symbol", action="store_true",
                    help="per-stock table vs buy-and-hold of that same stock")
    ap.add_argument("--export", nargs="?", const="", default=None, metavar="PATH",
                    help="write the trade log, metrics and year x month tables to Excel "
                         "(default reports/backtests/trades_<time>.xlsx)")
    ap.add_argument("--no-reconcile", action="store_true",
                    help="skip checking sampled fills against the price data")
    args = ap.parse_args()

    con = store.connect()
    cfg = timing.load_config()
    # all_symbols, not active_symbols: excluding retired names would erase past
    # losers from history and flatter every number below.
    symbols = [x.upper() for x in args.symbols] if args.symbols else wl.all_symbols()
    bench = store.load_index(con, cfg["signals"]["regime_index"])

    cuts = quality.report(con, symbols)

    frames = {}
    for s in symbols:
        f = pipeline.scored_frame(con, s, cfg, usable_from=cuts.get(s))
        if f.empty:
            continue
        if args.start:
            f = f[f["date"] >= __import__("datetime").date.fromisoformat(args.start)]
        if args.end:
            f = f[f["date"] <= __import__("datetime").date.fromisoformat(args.end)]
        if len(f) > 250:
            frames[s] = f.reset_index(drop=True)
    print(f"Backtesting {len(frames)} symbols")

    costs = CostModel()
    res = bt.run(frames, bench, cfg, args.capital, costs)
    if not args.no_reconcile:
        from backtest import reconcile as rc
        print("\n" + rc.summary(rc.equity_trades(res.trades, frames, costs, k=8)))
    show("Strategy", res.metrics)
    _report(res, args)

    n_trades = res.metrics.get("trades", 0)
    if n_trades < 30:
        print(f"\n  !! Only {n_trades} closed trades. That is far too few to")
        print("  !! conclude anything -- at this sample size the result is mostly")
        print("  !! luck, and would swing wildly on a different date range.")

    if args.per_symbol:
        per = {}
        for t in res.trades:
            if t.exit_price is None:
                continue
            d = per.setdefault(t.symbol, {"n": 0, "pnl": 0.0, "wins": 0})
            d["n"] += 1
            d["pnl"] += t.pnl
            d["wins"] += 1 if t.pnl > 0 else 0
        print("\n=== Per stock: strategy vs simply holding that stock ===")
        print(f"  {'symbol':<12} {'trades':>6} {'hit%':>6} {'strategy P&L':>14} "
              f"{'buy&hold %':>11} {'strategy %':>11}")
        for sym in sorted(per, key=lambda x: -per[x]["pnl"]):
            f = frames.get(sym)
            if f is None or f.empty:
                continue
            bh = 100 * (f["close"].iloc[-1] / f["close"].iloc[0] - 1)
            d = per[sym]
            # Strategy return measured against the capital actually committed.
            committed = sum(t.entry_price * t.qty for t in res.trades
                            if t.symbol == sym and t.exit_price is not None)
            strat = 100 * d["pnl"] / committed if committed else 0.0
            print(f"  {sym:<12} {d['n']:>6} {100*d['wins']/d['n']:>5.0f}% "
                  f"{d['pnl']:>14,.0f} {bh:>10.1f}% {strat:>10.1f}%")
        print("\n  'buy&hold %' is the whole-period return of holding that one")
        print("  stock. 'strategy %' is profit as a percent of the capital the")
        print("  strategy actually put into it. Beating buy&hold is the bar.")

    if args.shuffle:
        ctrl = bt.run(frames, bench, cfg, args.capital, CostModel(), shuffle_seed=42)
        show("Shuffled entries (control)", ctrl.metrics)
        print("\n  A strategy with real edge should beat its own shuffled control.")
        print(f"  strategy CAGR {res.metrics.get('cagr_pct')}% vs "
              f"shuffled {ctrl.metrics.get('cagr_pct')}%")


if __name__ == "__main__":
    main()
