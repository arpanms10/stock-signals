"""Download the full-market bhavcopy history.

This is what makes the backtest survivorship-free: every stock that traded on
a day is recorded, including ones that later delisted. Incremental and safe to
re-run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import socket

import instruments as ins
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

    # New days carry their ISINs; this catches symbols from days ingested
    # before that, so the equity filter never runs against a partial map.
    missing = len(bc.unmapped_symbols(con))
    if missing:
        print(f"Mapping {missing:,} symbols without an ISIN")
        left = bc.backfill_isins(con, verbose=False)
        if left:
            print(f"  !! {left:,} symbols still unmapped -- they are treated "
                  f"as non-equity and never ranked")
    # Splits and bonuses: the full history once, then a rolling window that
    # also catches ex-dates announced ahead.
    through = bc.actions_synced_through(con)
    frm = start if through is None else through - dt.timedelta(days=45)
    print(f"Syncing splits/bonuses from {frm}")
    bc.sync_actions(con, frm, today + dt.timedelta(days=60), verbose=through is None)

    # Trade-for-trade (BE/BZ) prices, for days ingested before they were kept.
    left = con.execute("SELECT COUNT(*) FROM market_days WHERE rows > 0 AND date "
                       "NOT IN (SELECT date FROM t2t_days)").fetchone()[0]
    if left:
        print(f"Fetching trade-for-trade rows for {left:,} days")
        bc.backfill_t2t(con, verbose=left > 50)
    snap = ins.snapshot(con)
    print(f"instruments: {snap['equities']:,} equities, {snap['funds']:,} "
          f"excluded (config/etfs.csv), {snap['funds_active']:,} still trading")


if __name__ == "__main__":
    main()
