"""Backtest the cross-sectional momentum strategy."""
from __future__ import annotations

import argparse
import datetime as dt

import numpy as np
import pandas as pd

import watchlist as wl
from backtest import portfolio_engine as pe
from backtest.costs import CostModel
from data import quality, store
from data.sources import universe as uni
from scoring import pipeline, timing


def load_frames(con, cfg, symbols, start=None, end=None):
    cuts = quality.report(con, symbols)
    frames = {}
    for s in symbols:
        f = pipeline.scored_frame(con, s, cfg, usable_from=cuts.get(s))
        if f.empty:
            continue
        if start:
            f = f[f["date"] >= start]
        if end:
            f = f[f["date"] <= end]
        if len(f) > 300:
            frames[s] = f.reset_index(drop=True)
    return frames


def equal_weight_hold(frames) -> float:
    """Equal-weight buy-and-hold of the same names -- the bar that matters."""
    rets, spans = [], []
    for f in frames.values():
        rets.append(f["close"].iloc[-1] / f["close"].iloc[0])
        spans.append((f["date"].iloc[-1] - f["date"].iloc[0]).days / 365.25)
    if not rets:
        return float("nan")
    return 100 * (float(np.mean(rets)) ** (1 / max(float(np.mean(spans)), 1e-9)) - 1)


def show(title, m):
    print(f"\n=== {title} ===")
    for k, v in m.items():
        print(f"  {k:>28}: {v}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=1_000_000)
    ap.add_argument("--universe", default=None,
                    help="nifty50 | nifty200 | nifty500 (default: watchlist)")
    ap.add_argument("--exclude-universe", default=None,
                    help="drop these names, e.g. --universe nifty200 "
                         "--exclude-universe nifty50 for an out-of-sample run")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--shuffle", type=int, default=0,
                    help="number of random-pick control runs")
    args = ap.parse_args()

    con = store.connect()
    cfg = timing.load_config()
    if args.universe:
        symbols = list(getattr(uni, args.universe)()["symbol"])
    else:
        symbols = wl.all_symbols()
    if args.exclude_universe:
        drop = set(getattr(uni, args.exclude_universe)()["symbol"])
        symbols = [s for s in symbols if s not in drop]

    start = dt.date.fromisoformat(args.start) if args.start else None
    end = dt.date.fromisoformat(args.end) if args.end else None
    frames = load_frames(con, cfg, symbols, start, end)
    print(f"Momentum backtest: {len(frames)} of {len(symbols)} symbols have usable history")
    if len(frames) < 20:
        print("  !! Fewer than 20 names. Cross-sectional ranking needs a wide")
        print("  !! universe to rank across; this result is not meaningful.")

    bench = store.load_index(con, cfg["signals"]["regime_index"])
    res = pe.run(frames, bench, cfg, args.capital, CostModel())
    show("Momentum rotation", res.metrics)

    bh = equal_weight_hold(frames)
    print(f"\n  {'equal-weight buy & hold':>28}: {bh:.2f}%   <- the bar to beat")
    print(f"  {'momentum':>28}: {res.metrics.get('cagr_pct')}%")

    if args.shuffle:
        print("\n=== Random-pick controls (same count, dates and costs) ===")
        vals = []
        for seed in range(1, args.shuffle + 1):
            m = pe.run(frames, bench, cfg, args.capital, CostModel(),
                       shuffle_seed=seed).metrics
            vals.append(m.get("cagr_pct", 0.0))
            print(f"  seed {seed}: {m.get('cagr_pct'):>7.2f}%")
        print(f"\n  control mean {np.mean(vals):.2f}%  sd {np.std(vals):.2f}  "
              f"range {min(vals):.2f}..{max(vals):.2f}")
        beat = sum(1 for v in vals if res.metrics.get("cagr_pct", 0) > v)
        print(f"  momentum beats {beat}/{len(vals)} controls")
        print("\n  Ranking must beat random selection from the same eligible")
        print("  pool. If it does not, the rank carries no information.")

    if res.holdings_log:
        print(f"\n  latest holdings ({res.holdings_log[-1][0]}):")
        print("   ", ", ".join(res.holdings_log[-1][1]))


if __name__ == "__main__":
    main()
