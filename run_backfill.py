"""Bring price history up to date. Safe to re-run: incremental.

With no arguments it refreshes every symbol that matters:
  * the watchlist (config/watchlist.csv),
  * your holdings (config/holdings.csv, or the Kite snapshot),
  * every symbol already in the price table -- whatever was once loaded
    (e.g. with --universe nifty500) is kept current.

It exits with an error, instead of printing "done", if that adds up to
nothing to refresh. Until 2026-10-08 it read the watchlist alone, so a
missing config/watchlist.csv made the dashboard's "Refresh prices" button
refresh 0 symbols and report success while prices went stale.
"""
from __future__ import annotations

import argparse
import socket
import sys

import watchlist as wl
from data import ingest, store


def default_symbols(con) -> tuple[list[str], dict[str, int]]:
    """Watchlist + holdings + everything already tracked, and where each came from."""
    import portfolio
    watch = set(wl.all_symbols())
    try:
        held = {str(h.get("tradingsymbol") or h.get("symbol") or "").strip().upper()
                for h in portfolio.load_holdings()}
    except Exception as exc:         # noqa: BLE001 -- a bad holdings file must not block prices
        print(f"warning: could not read holdings ({exc})", file=sys.stderr)
        held = set()
    held.discard("")
    tracked = {r[0] for r in con.execute("SELECT DISTINCT symbol FROM prices")}
    symbols = sorted(watch | held | tracked)
    return symbols, {"watchlist": len(watch), "holdings": len(held),
                     "already tracked": len(tracked)}


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
    elif args.symbols:
        symbols = args.symbols
    else:
        symbols, sources = default_symbols(con)
        print("Symbols: " + ", ".join(f"{k} {v}" for k, v in sources.items())
              + f" -> {len(symbols)} unique")
        if not symbols:
            print("Nothing to refresh: no watchlist, no holdings, and no prices yet. "
                  "Run with --universe nifty500 once, or import your holdings.",
                  file=sys.stderr)
            sys.exit(1)
    print(f"Backfilling {len(symbols)} symbols, {args.years}y")
    ingest.backfill(con, symbols, years=args.years)
    print("done")


if __name__ == "__main__":
    main()
