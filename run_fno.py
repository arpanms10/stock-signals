"""F&O analysis: PCR, OI support and resistance, expected range to expiry.

    PYTHONPATH=. .venv/bin/python run_fno.py NIFTY
    PYTHONPATH=. .venv/bin/python run_fno.py NIFTY BANKNIFTY RELIANCE --excel
    PYTHONPATH=. .venv/bin/python run_fno.py RELIANCE --expiry 2026-11-23
    PYTHONPATH=. .venv/bin/python run_fno.py NIFTY --excel reports/nifty.xlsx

Defaults to the nearest MONTHLY expiry -- NIFTY's weeklies are skipped unless
named with --expiry. Each run saves a snapshot of the chain to the local
database (--no-save to skip), which is what the dashboard's intraday PCR
history reads.

Decision support, not investment advice.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import fno_report
from data.sources.nse_derivatives import NotFnO


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("symbols", nargs="+", help="NIFTY, BANKNIFTY, RELIANCE, ...")
    ap.add_argument("--expiry", type=dt.date.fromisoformat, default=None,
                    help="YYYY-MM-DD; default: nearest monthly expiry")
    ap.add_argument("--excel", nargs="?", const="", default=None, metavar="PATH",
                    help="also write an Excel report (default reports/fno/fno_<time>.xlsx)")
    ap.add_argument("--no-save", action="store_true",
                    help="do not store a snapshot of the chain")
    a = ap.parse_args(argv)

    results, failed = [], 0
    for sym in a.symbols:
        try:
            res = fno_report.load(sym, a.expiry, save=not a.no_save)
        except (NotFnO, ValueError, RuntimeError) as exc:
            print(f"{sym.upper()}: {exc}\n", file=sys.stderr)
            failed += 1
            continue
        results.append(res)
        print(fno_report.text(res.view))
        print()

    if a.excel is not None and results:
        path = fno_report.write_excel(results, Path(a.excel) if a.excel else None)
        print(f"Excel report: {path}")
    print("Decision support, not investment advice.")
    return 1 if failed and not results else 0


if __name__ == "__main__":
    sys.exit(main())
