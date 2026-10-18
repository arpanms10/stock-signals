"""Mutual funds: judge a fund against its category and say EXIT / TRIM / ADD / WATCH / HOLD.

    PYTHONPATH=. .venv/bin/python run_mf.py --refresh                 # NAV history (first run ~25 min)
    PYTHONPATH=. .venv/bin/python run_mf.py                           # your book (config/mf_holdings.csv or .xlsx)
    PYTHONPATH=. .venv/bin/python run_mf.py "parag parikh flexi" "hdfc flexi cap"
    PYTHONPATH=. .venv/bin/python run_mf.py "quant small cap" "nippon small cap" --compare
    PYTHONPATH=. .venv/bin/python run_mf.py --excel                   # workbook in reports/mf/
    PYTHONPATH=. .venv/bin/python run_mf.py --backtest                # what each fund state was worth

Funds are judged against their SEBI category on month-end NAVs of Direct
Growth plans: rank, excess over the category median, consistency, downside.
The rules are in config/mf_rules.yaml. Decision support, not advice; nothing
here places an order.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import pandas as pd

from mf import engine, navdb, verdict

ROOT = Path(__file__).parent
REPORT = ROOT / "reports" / "mf" / "MF_Evaluation.xlsx"


def _fmt(v, key: str = "") -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    if isinstance(v, str):
        return v
    if key.startswith(("Rank", "Beat")):
        return f"{v:.0%}"
    if key in ("History (months)", "Peers"):
        return f"{int(v)}"
    if key == "Downside capture 3y":
        return f"{v:.2f}"
    return f"{v * 100:+.1f}%" if "vs" in key else f"{v * 100:.1f}%"


def print_decisions(ev: engine.Evaluation) -> None:
    print(f"\nAs of {ev.as_of} (month-end NAVs; newest {max(ev.nav_date.values())})\n")
    order = {verdict.EXIT: 0, verdict.TRIM: 1, verdict.WATCH: 2, verdict.ADD: 3,
             verdict.HOLD: 4, verdict.AVOID: 5, verdict.NEUTRAL: 6, verdict.NOT_JUDGED: 7}
    for d in sorted(ev.decisions, key=lambda d: (d.weight is None, order.get(d.action, 9))):
        held = f"{d.weight:.0f}% of book" if d.weight is not None else "not held"
        target = f" -> {d.target:.0f}%" if d.target is not None and d.weight is not None else ""
        print(f"{d.action:<10} {d.name}")
        print(f"{'':10} {d.category or '?'} | {d.state} | {held}{target}")
        for r in d.reasons:
            print(f"{'':10} - {r}")
        for c in d.cautions:
            print(f"{'':10} ! {c}")
        if d.alternatives:
            print(f"{'':10} in the same category: {'; '.join(d.alternatives)}")
        print()


def print_compare(ev: engine.Evaluation, codes: list[int]) -> None:
    t = engine.compare_table(ev, codes)
    t = t.apply(lambda col: [_fmt(v, k) for k, v in col.items()])
    with pd.option_context("display.width", 200, "display.max_colwidth", 28):
        print(t.to_string())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("funds", nargs="*", help="fund names or AMFI codes to evaluate")
    ap.add_argument("--refresh", action="store_true", help="download missing NAV months first")
    ap.add_argument("--compare", action="store_true", help="side-by-side metrics table")
    ap.add_argument("--no-holdings", action="store_true", help="ignore config/mf_holdings.csv / .xlsx")
    ap.add_argument("--excel", nargs="?", const=REPORT, type=Path, help="write a workbook")
    ap.add_argument("--backtest", action="store_true",
                    help="test the fund states on history (survivorship-free)")
    args = ap.parse_args()

    con = navdb.connect()
    if args.refresh:
        navdb.refresh(con)
    if args.backtest:
        from mf import backtest
        backtest.main(con)
        return
    if not args.funds and args.no_holdings:
        if args.refresh:
            return
        raise SystemExit("Name some funds, or drop --no-holdings.")

    ev = engine.evaluate(args.funds, use_holdings=not args.no_holdings, con=con)
    for q, cands in ev.unresolved:
        print(f"Could not pin down '{q}'. Closest:")
        for code, name, score in cands:
            print(f"   {code:>7}  {name}")
        print("   Use the AMFI code, or more of the name.\n")
    if ev.book is not None:
        for w in ev.book.warnings:
            print(f"Holdings: {w}")
    elif not args.funds:
        raise SystemExit("No config/mf_holdings.csv or .xlsx (see config/mf_holdings.example.csv); "
                         "or name funds to evaluate.")
    print_decisions(ev)
    if args.compare:
        codes = [d.code for d in ev.decisions if d.code is not None]
        print_compare(ev, codes)
    if args.excel:
        from mf import evaluation_report
        out = evaluation_report.build(args.excel, ev)
        print(f"\nWorkbook: {out}")
    from mf.backtest import calibration_line
    print("\n" + (calibration_line() or "No backtest yet: run_mf.py --backtest."))
    print("Decision support, not advice.")


if __name__ == "__main__":
    main()
