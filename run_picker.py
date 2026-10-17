"""GARP and QARP stock picks from NSE filings, and their backtest.

GARP: profit growing fast and lastingly, at a P/E not far above that growth
(PEG). QARP: a business that earns well, steadily and in cash on little debt,
bought when it is not near its own most expensive. Both are ranked within the
Nifty 200 on fundamentals computed only from what NSE had published on the
day -- see picker/ and docs/picker.md.

The picks are a shortlist to research, not orders. Read the backtest before
trusting either list.

    PYTHONPATH=. .venv/bin/python run_picker.py --refresh       # fetch new filings (minutes)
    PYTHONPATH=. .venv/bin/python run_picker.py                 # today's picks
    PYTHONPATH=. .venv/bin/python run_picker.py --as-of 2025-06-05
    PYTHONPATH=. .venv/bin/python run_picker.py --backtest      # -> config/picker_calibration.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd

from picker import engine as pe
from picker import score as ps
from results import store

ROOT = Path(__file__).resolve().parent
CALIBRATION = ROOT / "config" / "picker_calibration.json"
PERIODS = store.DIR / "picker_backtest_periods.csv"
PICKS = store.DIR / "picker_backtest_picks.csv"


def _f(v, fmt="{:.1f}", none="-"):
    return none if v is None or v != v else fmt.format(v)


def held_symbols() -> set[str]:
    try:
        import portfolio as pf
        return {h["tradingsymbol"] for h in pf.load_holdings()}
    except Exception:
        return set()


def table(top: pd.DataFrame, method: str, held: set[str]) -> str:
    if top.empty:
        return "  nothing passes the gates"
    lines = []
    for i, (_, r) in enumerate(top.iterrows(), 1):
        mark = " *held" if r["symbol"] in held else ""
        if method == "garp":
            detail = (f"growth {_f(r['growth'], '{:+.0f}%')}  core P/E {_f(r['core_pe'])}  "
                      f"PEG {_f(r['peg'], '{:.2f}')}  top line {_f(r['topline_growth'], '{:+.0f}%')}")
        else:
            detail = (f"quality {_f(r['quality'], '{:.0f}')}  ROE {_f(r.get('roe'), '{:.0f}%')}  "
                      f"P/E {_f(r['pe'])} ({_f(r['pe_hist_pct'], '{:.0%}', 'no')} of own range, "
                      f"{_f(r['pe_vs_sector'], '{:.1f}x', 'no')} sector)")
        lines.append(f"  {i:>2}. {r['symbol']:<12} {r[f'{method}_score']:>5.1f}  "
                     f"{detail}  [{r.get('sector') or r['kind']}]{mark}")
    return "\n".join(lines)


def live(as_of: dt.date, cfg: dict) -> int:
    from data import store as price_store
    q = store.quarters(pe.UNIVERSE)
    if q.empty:
        print("No universe filings on file. Run with --refresh first.", file=sys.stderr)
        return 1
    u = pe.universe(cfg["universe"])
    q = q[q["symbol"].isin(set(u["symbol"]))]
    con = price_store.connect()
    scored = pe.evaluate(q, as_of, pe.Prices(con), dict(zip(u["symbol"], u["sector"])), cfg)
    held = held_symbols()
    n = cfg["top_n"]
    garp, qarp = ps.picks(scored, "garp", n), ps.picks(scored, "qarp", n)
    both = sorted(set(garp["symbol"]) & set(qarp["symbol"]))
    print(f"GARP / QARP PICKS -- {as_of:%a %d %b %Y}  ({len(scored)} of "
          f"{cfg['universe']} scored; {int(scored['garp_ok'].sum())} pass GARP, "
          f"{int(scored['qarp_ok'].sum())} pass QARP)")
    print("Fundamentals from NSE filings published by this date. A shortlist to "
          "research, not orders.\n")
    print(f"GARP -- growth at a reasonable price (top {n})")
    print(table(garp, "garp", held))
    print(f"\nQARP -- quality at a reasonable price (top {n})")
    print(table(qarp, "qarp", held))
    if both:
        print(f"\nOn both lists: {', '.join(both)}")
    if CALIBRATION.exists():
        cal = json.loads(CALIBRATION.read_text())
        print(f"\nBacktest ({cal['from']} to {cal['to']}): " + "; ".join(
            f"{m.upper()} {cal[m]['excess_mean']:+.1f}%/period vs universe "
            f"(t={cal[m]['excess_t']})" for m in ("garp", "qarp") if m in cal))
    else:
        print("\nNo backtest yet: run --backtest before trusting either list.")
    return 0


def backtest(cfg: dict) -> int:
    from data import store as price_store
    from scoring import timing
    q = store.quarters(pe.UNIVERSE)
    if q.empty:
        print("No universe filings on file. Run with --refresh first.", file=sys.stderr)
        return 1
    u = pe.universe(cfg["universe"])
    q = q[q["symbol"].isin(set(u["symbol"]))]
    con = price_store.connect()
    index = price_store.load_index(con, timing.load_config()["signals"]["regime_index"])
    # Start once most of the universe has eight quarters visible (TTM growth).
    first = sorted(q["to_date"].unique())
    start = first[min(8, len(first) - 1)] + dt.timedelta(days=60)
    end = price_store.load_prices(con, "RELIANCE")["date"].max()
    res = pe.backtest(q, dict(zip(u["symbol"], u["sector"])), cfg, con, start, end, index)
    periods = res["periods"]
    if periods.empty:
        print("No periods to test.", file=sys.stderr)
        return 1
    store.DIR.mkdir(parents=True, exist_ok=True)
    periods.to_csv(PERIODS, index=False)
    res["picks"].to_csv(PICKS, index=False)
    summary = pe.summarise(periods)
    summary.update({"tested_on": dt.date.today().isoformat(), "universe": cfg["universe"],
                    "top_n": cfg["top_n"],
                    "note": "today's index members (survivorship); balance-sheet "
                            "quality only from 2025 filings"})
    CALIBRATION.write_text(json.dumps(summary, indent=1, default=str))
    print(f"\nBACKTEST {summary['from']} to {summary['to']}, {summary['periods']} periods, "
          f"top {cfg['top_n']}, equal weight, {cfg['round_trip_cost_pct']}% round trip")
    for m in ("garp", "qarp", "universe", "index"):
        s = summary.get(m)
        if not s:
            continue
        line = f"  {m.upper():<9} CAGR {s['cagr']:+.1f}%  total {s['total']:+.1f}%  " \
               f"worst period {s['worst_period']:+.1f}%"
        if "excess_mean" in s:
            line += (f"  | vs universe {s['excess_mean']:+.2f}%/period, t={s['excess_t']}, "
                     f"beat it {s['beat_universe_pct']:.0f}% of periods, "
                     f"{s['avg_names']} names")
        print(line)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--refresh", action="store_true", help="fetch new filings for the universe")
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--as-of", type=dt.date.fromisoformat, default=None)
    ap.add_argument("--quarters", type=int, default=24)
    args = ap.parse_args(argv)
    cfg = pe.load_config()
    if args.refresh:
        pe.refresh(list(pe.universe(cfg["universe"])["symbol"]), args.quarters)
    if args.backtest:
        return backtest(cfg)
    if args.as_of is None:
        from data import store as price_store
        args.as_of = price_store.load_prices(price_store.connect(), "RELIANCE")["date"].max()
    return live(args.as_of, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
