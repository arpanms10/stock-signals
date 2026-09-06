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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=1_000_000)
    ap.add_argument("--db", default="data/market.db")
    ap.add_argument("--universe-size", type=int, default=200)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--shuffle", type=int, default=0)
    args = ap.parse_args()

    con = bc.connect(args.db)
    cfg = timing.load_config()
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
        print(f"\n  latest holdings ({res.holdings_log[-1][0]}):")
        print("   ", ", ".join(res.holdings_log[-1][1]))


if __name__ == "__main__":
    main()
