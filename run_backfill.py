"""Backfill price history for the whole watchlist. Safe to re-run: incremental."""
from __future__ import annotations

import argparse
import socket

import watchlist as wl
from data import ingest, store


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--universe", default=None,
                    help="nifty50 | nifty200 | nifty500 -- backfill an index instead")
    args = ap.parse_args()

    # jugaad-data does not set a request timeout, so a single stalled socket
    # would hang an unattended nightly job indefinitely.
    socket.setdefaulttimeout(45)

    con = store.connect()
    if args.universe:
        from data.sources import universe as uni
        symbols = list(getattr(uni, args.universe)()["symbol"])
    else:
        symbols = args.symbols or wl.all_symbols()
    print(f"Backfilling {len(symbols)} symbols, {args.years}y")
    ingest.backfill(con, symbols, years=args.years)
    print("done")


if __name__ == "__main__":
    main()
