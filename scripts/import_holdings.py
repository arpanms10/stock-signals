"""Turn a broker's holdings download into config/holdings.xlsx (or .csv).

Drop the .csv or .xlsx your broker gives you into config/ and run this. The
header row is found wherever it sits, so a client-details preamble is skipped,
and columns are matched by common names (book/portfolio.py COLUMN_ALIASES):
Symbol / Instrument / Scrip, Qty. / Quantity / Shares, Avg. cost / Average
Price / Buy Avg, Long Term Qty / Short Term Qty. For example:

    Zerodha Kite      .csv   Instrument, Qty., Avg. cost, ...
    Zerodha Console   .xlsx  a preamble, then Symbol, Quantity Available,
                             Quantity Long Term, Average Price

A file whose headers match none of these names is reported, not guessed at:
rename its headers, or fill config/holdings.xlsx by hand. REITs, InvITs, ETFs
and bonds are kept and labelled with their type (book/securities.py); only
company shares are scored and ranked.

You see a preview first -- what is new, removed and changed, and every issue
the validation found -- and the file is only written once you confirm
(--yes skips the question; --dry-run never writes). Each import is recorded in
data/research.db's audit log.

An existing holdings file is not thrown away: it is copied to holdings.*.bak,
and the bucket, notes and purchase_date you set by hand are carried over for
every symbol you still hold. Your broker knows your quantities; only you know why you
own something.

    PYTHONPATH=. .venv/bin/python scripts/import_holdings.py            # newest broker file in config/
    PYTHONPATH=. .venv/bin/python scripts/import_holdings.py path/to/holdings.xlsx
    PYTHONPATH=. .venv/bin/python scripts/import_holdings.py --trade CPPLUS SANSERA
    PYTHONPATH=. .venv/bin/python scripts/import_holdings.py --dry-run     # preview only
"""
from __future__ import annotations
import sys as _sys; from pathlib import Path as _P; _sys.path.insert(0, str(_P(__file__).resolve().parents[1]))  # run from anywhere

import argparse
import sqlite3
import sys
from pathlib import Path

from book import portfolio as pf
from book import holdings_import as hi
from book.holdings_import import (clean_symbol, convert, locate_header,  # noqa: F401
                                  mark_trade, read_grid, write_holdings)

CONFIG = Path(__file__).resolve().parents[1] / "config"
ROOT = CONFIG.parent

# Files in config/ that belong to the framework, never a broker export.
OWN_FILES = {"holdings.csv", "holdings.csv.bak", "holdings.example.csv",
             "holdings.xlsx", "holdings.xlsx.bak", "mf_holdings.csv",
             "mf_holdings.xlsx", "mf_holdings.example.csv",
             "fundamentals.csv", "fundamentals.example.csv", "watching.csv",
             "watching.xlsx", "watchlist.csv", "equities.csv", "etfs.csv"}


def find_export(folder: Path = CONFIG) -> Path | None:
    """The most recently modified broker file in config/."""
    candidates = [p for p in folder.iterdir()
                  if p.suffix.lower() in (".csv", ".xlsx", ".xlsm")
                  and p.name.lower() not in OWN_FILES
                  and not p.name.startswith(("~$", "."))]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    if candidates:
        return candidates[0]
    # Kite web names its download holdings.csv, so it may already be sitting
    # where the output goes. Convert it in place (the original goes to .bak).
    own = folder / "holdings.csv"
    if own.exists() and "kite" == _source_kind(own):
        return own
    return None


def _source_kind(path: Path) -> str:
    """'kite' if the file's header is Kite's rather than ours."""
    with path.open(encoding="utf-8-sig") as fh:
        for line in fh:
            if line.strip() and not line.lstrip().startswith("#"):
                return "ours" if _norm(line.split(",")[0]) == "symbol" else "kite"
    return "ours"


def market_context() -> hi.Context:
    db = ROOT / "data" / "market.db"
    if not db.exists():
        return hi.Context()
    con = sqlite3.connect(db)
    try:
        return hi.market_context(con)
    finally:
        con.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source", nargs="?", type=Path,
                    help="broker holdings file (.csv or .xlsx); default: newest in config/")
    ap.add_argument("--out", type=Path, default=None,
                    help="default: config/holdings.xlsx if you have one, else .csv")
    ap.add_argument("--trade", nargs="+", metavar="SYMBOL", default=[],
                    help="mark these holdings as trade (bought to trade, "
                         "rotated on rank); overrides any existing bucket")
    ap.add_argument("--dry-run", action="store_true", help="preview only; write nothing")
    ap.add_argument("--yes", action="store_true",
                    help="write without asking (after printing the preview)")
    args = ap.parse_args()

    src = args.source or find_export()
    if not src or not src.exists():
        sys.exit("No broker holdings file found. Put the .csv or .xlsx from your broker "
                 "into config/ and run again.")

    from book import userfiles as uf
    out: Path = args.out or uf.path_for("holdings")
    p = hi.preview(read_grid(src), pf.read_csv_holdings(out) if out.exists() else [],
                   market_context(), source=src.name, trade=args.trade)
    print("\n".join(hi.report(p)))
    if not p.ok:
        sys.exit("Not written: fix the errors above first.")
    if args.dry_run:
        print("Dry run: nothing written.")
        return
    if out.exists() and not p.has_changes:
        print("Nothing to write.")
        return
    if out.exists() and not args.yes:
        if not sys.stdin.isatty():
            sys.exit(f"Not written: {out.name} exists. Re-run with --yes to replace it "
                     "(the old file is kept as .bak).")
        if input(f"Replace {out.name} (the old file is kept as .bak)? [y/N] ").strip() \
                .lower() not in ("y", "yes"):
            print("Not written.")
            return
    from book import research_db as rdb
    rcon = rdb.connect()
    backup = hi.commit(p, out, rcon)
    print(f"Wrote {len(p.rows)} positions -> {out}"
          + (f"; previous file backed up to {backup.name}" if backup else ""))
    new_unmarked = [s for s in p.new if not any(r["symbol"] == s and r["bucket"] == "trade"
                                                for r in p.rows)]
    if new_unmarked and not args.trade:
        print("  a new holding with a blank bucket is NOT a trade -- if any of these "
              "were bought to trade, re-run with --trade " + " ".join(new_unmarked))
    if any(r["lt_quantity"] is None for r in p.rows):
        print("  note: this export has no long/short-term split; tax figures "
              "will fall back to purchase_date (Console's .xlsx includes it)")


if __name__ == "__main__":
    main()
