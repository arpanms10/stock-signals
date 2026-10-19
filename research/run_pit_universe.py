"""Point-in-time universe for the results and picker backtests.

Builds the 200 largest NSE companies on every picker rebalance date and
every results quarter end, from the full-market history in data/market.db,
and fetches the filings of past members that are not in today's Nifty 200
-- see results/pit_universe.py.

    PYTHONPATH=. .venv/bin/python research/run_pit_universe.py --fetch    # filings (hours, first time)
    PYTHONPATH=. .venv/bin/python research/run_pit_universe.py            # rebuild the member list

Then run the backtests on it:

    PYTHONPATH=. .venv/bin/python research/run_results_backtest.py --report-only --universe pit
    PYTHONPATH=. .venv/bin/python scripts/run_picker.py --backtest --universe pit
"""
from __future__ import annotations
import sys as _sys; from pathlib import Path as _P; _sys.path.insert(0, str(_P(__file__).resolve().parents[1]))  # run from anywhere

import argparse
import datetime as dt
import time

import pandas as pd

from results import filings as rf
from results import pit_universe as pu
from results import store

START = dt.date(2020, 3, 31)


def fetch(symbols: list[str], quarters: int, log=print) -> list[dict]:
    """Filings for each symbol into universe_quarters.csv; gaps returned."""
    rows, gaps = [], []
    for i, sym in enumerate(symbols, 1):
        try:
            hist = rf.history(sym, quarters)
        except Exception as exc:
            gaps.append({"symbol": sym, "reason": f"filing list failed: {exc}"[:200]})
            log(f"[{i}/{len(symbols)}] {sym}: failed ({exc})")
            continue
        if not hist:
            gaps.append({"symbol": sym, "reason": "no XBRL filings listed on NSE"})
        rows.extend(hist)
        log(f"[{i}/{len(symbols)}] {sym}: {len(hist)} quarters")
        if i % 10 == 0:
            store.save_quarters(rows, store.UNIVERSE)
            rows = []
        time.sleep(0.2)
    store.save_quarters(rows, store.UNIVERSE)
    return gaps


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fetch", action="store_true",
                    help="fetch filings for pool names not yet on file")
    ap.add_argument("--quarters", type=int, default=24)
    ap.add_argument("--pool", type=int, default=pu.POOL)
    ap.add_argument("--size", type=int, default=pu.SIZE)
    ap.add_argument("--limit", type=int, default=None, help="first N to fetch (testing)")
    args = ap.parse_args(argv)

    from data import bhavcopy as bc
    from data.sources import universe as uni
    from picker import engine as pe
    con = bc.connect(pu.MARKET_DB)
    end = dt.date.fromisoformat(max(bc.have_days(con)))
    tagged = pu.dates(pe.load_config(), START, end)
    kinds: dict[dt.date, str] = {}
    for d, k in tagged:
        kinds[d] = "both" if d in kinds and kinds[d] != k else k
    pools = pu.pools(con, sorted(kinds), args.pool)
    pooled = sorted({s for p in pools.values() for s in p})
    index_now = set(uni.nifty200()["symbol"])
    print(f"{len(kinds)} dates {min(kinds)} .. {max(kinds)}; pool {args.pool} by "
          f"1-year turnover; {len(pooled)} names ever pooled, "
          f"{len(set(pooled) - index_now)} not in today's Nifty 200")

    q = store.quarters(store.UNIVERSE)
    have = set(q["symbol"]) if not q.empty else set()
    old_gaps = pd.read_csv(pu.GAPS) if pu.GAPS.exists() else pd.DataFrame(
        columns=["symbol", "reason"])
    if args.fetch:
        todo = [s for s in pooled if s not in have and s not in set(old_gaps["symbol"])]
        todo = todo[: args.limit] if args.limit else todo
        print(f"fetching {len(todo)} symbols x {args.quarters} quarters")
        gaps = pd.DataFrame(fetch(todo, args.quarters), columns=["symbol", "reason"])
        old_gaps = pd.concat([old_gaps, gaps]).drop_duplicates("symbol", keep="last")
        old_gaps.to_csv(pu.GAPS, index=False)
        q = store.quarters(store.UNIVERSE)

    df = pu.members(pools, q, con, args.size, kinds, index_now)
    df.to_csv(pu.MEMBERS, index=False)
    per = df.groupby("date").agg(n=("symbol", "size"),
                                 by_mcap=("basis", lambda b: int(b.str.startswith("mcap").sum())),
                                 now_in_index=("in_index_now", "sum"))
    print(per.to_string())
    ever = pu.ever(df)
    missing = sorted(ever - set(q["symbol"]))
    print(f"\n{len(ever)} names ever members, {len(ever - index_now)} not in today's "
          f"Nifty 200; {len(missing)} with no filings on file"
          + (f": {', '.join(missing[:40])}" + (" ..." if len(missing) > 40 else "")
             if missing else ""))
    print(f"wrote {pu.MEMBERS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
