"""Record this month's picks for every forward strategy in the lab.

    PYTHONPATH=. .venv/bin/python scripts/record_lab.py            # record, then show
    PYTHONPATH=. .venv/bin/python scripts/record_lab.py --report   # show only
    PYTHONPATH=. .venv/bin/python scripts/record_lab.py --force    # redo this month

The first run of a month records it; later runs that month do nothing
(lab/forward.py). The Saturday job runs it after the market data refresh.
A strategy is forward when its status is "forward" in lab/strategies.py.
"""
from __future__ import annotations
import sys as _sys; from pathlib import Path as _P; _sys.path.insert(0, str(_P(__file__).resolve().parents[1]))  # run from anywhere

import argparse
import datetime as dt

import pandas as pd

from backtest import pit_engine as pit
from data import bhavcopy as bc
from data import store
from lab import forward, registry
from scoring import timing

ROOT = _P(__file__).resolve().parents[1]


def report(names: list[str], con, bench: pd.Series) -> None:
    recs = {n: forward.read(n) for n in names}
    syms = sorted({s for r in recs.values() for s in r["symbol"]})
    if not syms:
        print("No forward records yet.")
        return
    first = min(pd.Timestamp(r["price_date"].min()) for r in recs.values() if len(r))
    closes = pit.load_wide(con, first.date(), dt.date.today(), pit.symbol_aliases(con))
    for n in names:
        t = forward.score(n, closes, bench)
        if t.empty:
            continue
        done = t[~t["open"]]
        print(f"\n=== {n} ===")
        print(t.drop(columns="open").to_string(index=False))
        if len(done):
            print(f"  closed months: {len(done)}, average excess over NIFTY 500 "
                  f"{done['excess_pct'].mean():.2f} pts a month, beat it in "
                  f"{(done['excess_pct'] > 0).sum()} of {len(done)}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(ROOT / "data" / "market.db"))
    ap.add_argument("--report", action="store_true", help="show the records only")
    ap.add_argument("--force", action="store_true", help="replace this month's record")
    a = ap.parse_args(argv)
    con = bc.connect(a.db)
    base = timing.load_config()
    idx = store.load_index(store.connect(), base["signals"]["regime_index"])
    bench = idx.set_index(pd.to_datetime(idx["date"]))["close"]
    names = [s.name for s in registry.all_strategies() if s.status == "forward"]
    if not names:
        print("No strategy is marked forward in lab/strategies.py.")
        return 0
    if not a.report:
        on = dt.date.fromisoformat(sorted(bc.have_days(con))[-1])
        close = bench[bench.index <= pd.Timestamp(on)]
        for n in names:
            k = forward.record(registry.get(n), con, base, on, float(close.iloc[-1]),
                               force=a.force)
            print(f"{n}: " + (f"recorded {k} names at the {on} close" if k
                              else f"{on:%b %Y} already recorded"))
    report(names, con, bench)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
