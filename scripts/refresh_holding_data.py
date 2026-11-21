"""Refresh financial and market data for your holdings, with provenance.

NSE filings first, Yahoo as a cross-checked secondary source; ratios, market
stats and market cap calculated; everything stored in data/research.db with
its source, period and retrieval time (data/enrich.py). A failed source keeps
its previous rows and is logged.

    PYTHONPATH=. .venv/bin/python scripts/refresh_holding_data.py
    PYTHONPATH=. .venv/bin/python scripts/refresh_holding_data.py --symbols TCS INFY
    PYTHONPATH=. .venv/bin/python scripts/refresh_holding_data.py --max-age-days 6
"""
from __future__ import annotations
import sys as _sys; from pathlib import Path as _P; _sys.path.insert(0, str(_P(__file__).resolve().parents[1]))  # run from anywhere

import argparse
import sys
import time

from book import portfolio as pf
from data import enrich


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--symbols", nargs="+", default=None, help="only these holdings")
    ap.add_argument("--max-age-days", type=float, default=0,
                    help="skip holdings refreshed more recently than this")
    args = ap.parse_args()
    holdings = pf.read_csv_holdings()
    if not holdings:
        print("No holdings file with positions found.")
        return 1
    t = time.time()
    done = enrich.refresh(holdings, args.symbols, args.max_age_days)
    mism = [m for d in done for m in d.get("mismatches", [])]
    print(f"\nRefreshed {len(done)} holding(s) in {time.time() - t:.0f}s.")
    if mism:
        print(f"NSE and Yahoo disagree by more than 3% on {len(mism)} figure(s):")
        for m in mism:
            print(f"  {m}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
