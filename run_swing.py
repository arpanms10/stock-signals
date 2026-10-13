"""Swing trade suggestions for the Nifty 200 / 500, and the backtest behind them.

    PYTHONPATH=. .venv/bin/python run_swing.py                      # top 10, Nifty 200
    PYTHONPATH=. .venv/bin/python run_swing.py --universe nifty500 --all
    PYTHONPATH=. .venv/bin/python run_swing.py --capital 1000000    # with share counts
    PYTHONPATH=. .venv/bin/python run_swing.py --backtest           # writes config/swing_calibration.json

The backtest plays out every signal since --start with the plan the
suggestions show, after costs, and asks whether the score adds anything over
the setups alone. The Swing tab reads its summary.

Decision support, not investment advice.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json

import pandas as pd

import swing_report as sr
from backtest import swing_backtest as sb
from data import store
from scoring import timing
from strategy import swing as sw


def show_scan(v: sr.SwingView, cfg: dict, capital: float, show_all: bool) -> None:
    print(f"\nSwing scan -- {v.universe.upper()}, data through {v.as_of}")
    if v.regime:
        print(f"  regime: {v.regime.state.upper()} -- {v.regime.note}")
        if v.regime.state != "favourable":
            print("  !! Long swing setups fail more often in this backdrop; "
                  "see the backtest's regime split.")
    if not v.results_checked:
        print("  (results calendar unavailable -- the results-week filter was skipped)")
    t = sr.suggestion_rows(v.table, capital, cfg)
    if t.empty:
        print("  nothing eligible")
        return
    n_sug = int(t["suggest"].sum())
    print(f"  {len(t)} eligible (uptrend, liquid), {n_sug} with an active setup\n")
    if cfg["swing"].get("regime_gate") and v.regime and v.regime.state != "favourable":
        print("  regime_gate is on and the regime is not favourable: no suggestions.")
        t = t[~t["suggest"]]
    rows = t if show_all else t[t["suggest"]].head(cfg["swing"]["top_n"])
    cols = ["rank", "symbol", "setup", "score", "close", "entry", "stop",
            "stop_pct", "t1", "t2", "status"]
    if capital:
        cols += ["qty", "position"]
    out = rows[cols].copy()
    out["score"] = out["score"].round(0)
    for c in ("close", "entry", "stop", "t1", "t2", "position"):
        if c in out:
            out[c] = out[c].round(1)
    out["stop_pct"] = out["stop_pct"].round(1)
    print(out.to_string(index=False))
    print("\n  entry = buy-stop for the next "
          f"{cfg['swing']['entry_valid_sessions']} sessions; T1 books "
          f"{cfg['swing']['t1_book_pct']:.0f}% and moves the stop to entry; "
          f"exit the rest at T2 or after {cfg['swing']['max_hold_sessions']} sessions.")


def show_backtest(res: dict) -> None:
    print(f"\n=== Swing backtest: {res['universe']}, {res['from']} to {res['to']} ===")
    print(f"  plan: {res['plan']}")
    print("\n  Each layer against the one below (average R after costs):")
    for k, g in res["groups"].items():
        print(f"  {k:>14}: {g}")
    print("\n  Score quintiles (setup trades):")
    for b in res["score_bands"]:
        print(f"    {b}")
    print("\n  By regime:")
    for k, g in res["regimes"].items():
        print(f"    {k:>13}: {g}")
    if res.get("results"):
        print(f"\n  Results within {res['results']['days']} days (from {res['results']['from']}):")
        for k in ("results soon", "no results"):
            print(f"    {k:>13}: {res['results'][k]}")
    print("\n  By year (avg R):")
    for y in res["years"]:
        print(f"    {y}")
    print(f"\n  Portfolio (top {res['top_n']} daily into 8 slots): {res['portfolio']}")
    print(f"  NIFTY 500 over the same span: {res['benchmark_cagr_pct']}% a year")
    print("\n  Universe is today's index list applied backwards (survivorship): "
          "trust the comparisons between rows more than any absolute number.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--universe", choices=sr.UNIVERSES, default=None)
    ap.add_argument("--all", action="store_true", help="every eligible name, not just the top")
    ap.add_argument("--capital", type=float, default=0.0,
                    help="size positions for this much capital")
    ap.add_argument("--no-results", action="store_true",
                    help="skip the NSE results-calendar lookup")
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--start", default="2018-01-01", help="backtest start")
    args = ap.parse_args()

    cfg = timing.load_config()
    universe = args.universe or cfg["swing"]["universe"]
    if not args.backtest:
        v = sr.load(universe, cfg, check_results=not args.no_results, verbose=True)
        show_scan(v, cfg, args.capital, args.all)
        return

    con = store.connect()
    symbols, _ = sr.universe_symbols(universe)
    frames = sr.load_frames(con, cfg, symbols, verbose=False)
    print(f"Backtest: {len(frames)} of {len(symbols)} symbols priced; building signals...")
    panel = sw.build_panel(frames, cfg, start=dt.date.fromisoformat(args.start))
    bench = store.load_index(con, cfg["signals"]["regime_index"])
    res = sb.run(panel, frames, bench, cfg, universe, sr.historical_results())
    show_backtest(res)
    sr.CALIBRATION_PATH.write_text(json.dumps(res, indent=1, default=str))
    print(f"\n  wrote {sr.CALIBRATION_PATH.relative_to(sr.ROOT)} (the Swing tab reads it)")


if __name__ == "__main__":
    main()
