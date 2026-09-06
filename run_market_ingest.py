"""Download the full-market bhavcopy history.

This is what makes the backtest survivorship-free: every stock that traded on
a day is recorded, including ones that later delisted. Incremental and safe to
re-run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import socket

from data import bhavcopy as bc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--db", default="data/market.db")
    args = ap.parse_args()

    # jugaad-data sets no request timeout; one stalled socket would hang this
    # for hours unattended.
    socket.setdefaulttimeout(45)

    today = dt.date.today()
    start = today - dt.timedelta(days=365 * args.years)
    con = bc.connect(args.db)
    have = len(bc.have_days(con))
    print(f"Ingesting {start} .. {today} ({have} days already held)")
    n = bc.ingest_range(con, start, today, verbose=False)
    days = len(bc.have_days(con))
    syms = con.execute("SELECT COUNT(DISTINCT symbol) FROM market").fetchone()[0]
    rows = con.execute("SELECT COUNT(*) FROM market").fetchone()[0]
    print(f"done: +{n:,} rows | {days} days | {syms:,} distinct symbols | "
          f"{rows:,} total rows")


if __name__ == "__main__":
    main()
