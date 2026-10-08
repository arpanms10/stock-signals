"""Momentum backtest on a survivorship-free, point-in-time universe."""
from __future__ import annotations

import argparse
import datetime as dt

import numpy as np

from backtest import pit_engine as pit
from backtest.costs import CostModel
from data import bhavcopy as bc
from data import store
from scoring import timing


def show(title, m):
    print(f"\n=== {title} ===")
    for k, v in m.items():
        print(f"  {k:>28}: {v}")


def _report(res, args) -> None:
    """A rotation strategy has no discrete trades to log: its record is the
    equity curve and what it held at each rebalance."""
    import pandas as pd
    from backtest import report as rp
    if res.equity is None or len(res.equity) < 2:
        return
    show("Equity curve", rp.equity_stats(res.equity))
    months = rp.month_table(res.equity, "compound")
    bench_m = rp.month_table(res.benchmark, "compound") if len(res.benchmark) else None
    print("\n=== Monthly return, % ===")
    print(months.fillna("").to_string())
    if bench_m is not None and len(bench_m):
        yr = pd.DataFrame({"strategy": months["Total"], "benchmark": bench_m["Total"]})
        yr["difference"] = yr["strategy"] - yr["benchmark"]
        print("\n=== By year, % (strategy vs benchmark) ===")
        print(yr.round(2).to_string())
    if args.export is not None:
        holdings = pd.DataFrame([{"rebalance": d, "n": len(h), "holdings": ", ".join(h)}
                                 for d, h in res.holdings_log])
        path = rp.export(args.export or rp.default_path("momentum"), {
            "Metrics": rp.stats_frame({**res.metrics, **rp.equity_stats(res.equity)}),
            "Monthly return %": months,
            "Benchmark monthly %": bench_m,
            "Holdings log": holdings,
            "Equity": pd.DataFrame({"strategy": res.equity, "benchmark": res.benchmark})
            .rename_axis("date"),
        })
        print(f"\nExcel: {path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=1_000_000)
    ap.add_argument("--db", default="data/market.db")
    ap.add_argument("--universe-size", type=int, default=200)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--shuffle", type=int, default=0)
    ap.add_argument("--skip-months", type=int, default=None,
                    help="override momentum_strategy.skip_months (0 = plain return)")
    ap.add_argument("--lookback-months", type=int, default=None,
                    help="override momentum_strategy.lookback_months")
    ap.add_argument("--score", choices=["ram", "nse_blend"], default=None,
                    help="ram = 12m momentum / vol (default); nse_blend = "
                         "Nifty200 Momentum 30 style 6m+12m z-score blend")
    ap.add_argument("--rebalance-days", type=int, default=None,
                    help="override momentum_strategy.rebalance_days (126 = semi-annual)")
    ap.add_argument("--selection", choices=["primary", "overlap"], default=None,
                    help="overlap = hold only names top-band on BOTH the primary "
                         "and compare_lookback_months rankings")
    ap.add_argument("--compare-lookback-months", type=int, default=None,
                    help="override momentum_strategy.compare_lookback_months")
    ap.add_argument("--risk-off-scale", type=float, default=None,
                    help="override momentum_strategy.risk_off_scale "
                         "(1.0 = ignore the market regime)")
    ap.add_argument("--vol-target", action="store_true",
                    help="scale exposure to a target portfolio volatility")
    ap.add_argument("--export", nargs="?", const="", default=None, metavar="PATH",
                    help="write the equity curve, monthly returns and holdings log to Excel")
    args = ap.parse_args()

    con = bc.connect(args.db)
    cfg = timing.load_config()
    if args.vol_target:
        cfg = dict(cfg)
        cfg["momentum_strategy"] = {**cfg["momentum_strategy"], "vol_target": True}
    for key in ("skip_months", "lookback_months", "score", "rebalance_days",
                "selection", "compare_lookback_months", "risk_off_scale"):
        if getattr(args, key) is not None:
            cfg = dict(cfg)
            cfg["momentum_strategy"] = {**cfg["momentum_strategy"],
                                        key: getattr(args, key)}
    ms = cfg["momentum_strategy"]
    print(f"Momentum:    {ms['lookback_months']}-month lookback, "
          f"skipping the latest {ms['skip_months']}, score {ms.get('score', 'ram')}, "
          f"rebalance every {ms['rebalance_days']} sessions, "
          f"risk-off scale {ms.get('risk_off_scale', 1.0)}")
    if ms.get("selection") == "overlap":
        print(f"Selection:   overlap -- top {ms.get('enter_rank', ms['n_hold'])} on "
              f"BOTH {ms['lookback_months']}- and "
              f"{ms.get('compare_lookback_months')}-month rankings")
    days = sorted(bc.have_days(con))
    if len(days) < 400:
        print(f"Only {len(days)} market days ingested. Run run_market_ingest.py first.")
        return

    first = dt.date.fromisoformat(days[0])
    last = dt.date.fromisoformat(days[-1])
    # Momentum needs ~14 months of warm-up before it can rank.
    start = dt.date.fromisoformat(args.start) if args.start else first + dt.timedelta(days=430)
    end = dt.date.fromisoformat(args.end) if args.end else last
    print(f"Market data: {first} .. {last} ({len(days)} days)")
    print(f"Backtest:    {start} .. {end}   universe = top {args.universe_size} by liquidity, "
          f"rebuilt at every rebalance")

    bench = store.load_index(store.connect(), cfg["signals"]["regime_index"])
    res = pit.run(con, cfg, start, end, bench, args.capital, CostModel(),
                  args.universe_size)
    show("Momentum, point-in-time universe (no survivorship bias)", res.metrics)
    _report(res, args)

    if args.shuffle:
        print("\n=== Random-pick controls (same universe, dates and costs) ===")
        vals = []
        for seed in range(1, args.shuffle + 1):
            m = pit.run(con, cfg, start, end, bench, args.capital, CostModel(),
                        args.universe_size, shuffle_seed=seed).metrics
            vals.append(m.get("cagr_pct", 0.0))
            print(f"  seed {seed}: {m.get('cagr_pct'):>7.2f}%")
        mean, sd = float(np.mean(vals)), float(np.std(vals))
        print(f"\n  control mean {mean:.2f}%  sd {sd:.2f}  "
              f"range {min(vals):.2f}..{max(vals):.2f}")
        edge = res.metrics.get("cagr_pct", 0) - mean
        z = edge / sd if sd else 0
        print(f"  momentum beats {sum(1 for v in vals if res.metrics.get('cagr_pct',0)>v)}"
              f"/{len(vals)} controls, edge {edge:+.2f}pp ({z:.1f} sd)")
        print("\n  The controls draw from the same point-in-time universe, so")
        print("  they share every bias the strategy has. The gap between them")
        print("  is the part that is actually about ranking.")

    if res.holdings_log:
        counts = [len(h) for _, h in res.holdings_log]
        print(f"\n  names held per rebalance: avg {np.mean(counts):.1f}, "
              f"min {min(counts)}, max {max(counts)}")
        print(f"\n  latest holdings ({res.holdings_log[-1][0]}):")
        print("   ", ", ".join(res.holdings_log[-1][1]))


if __name__ == "__main__":
    main()
