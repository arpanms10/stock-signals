"""Add a `bucket` column to holdings.csv, pre-filled with the framework's view.

Editing a word is a much smaller ask than constructing a column, so this writes
the current classification in and leaves you to change what you disagree with.
Every other column in the file is preserved byte-for-byte -- a broker export
carries figures this framework does not use, and losing them would be rude.
"""
from __future__ import annotations

import csv
import shutil
from pathlib import Path

import fundamentals as fu
import portfolio as pf
from data import bhavcopy as bc
from data import store
from data.sources import universe as uni
from scoring import pipeline, timing
from strategy import buckets as bk

HOLDINGS = Path("config/holdings.csv")


def main() -> None:
    rows_in = list(csv.DictReader(
        l for l in HOLDINGS.open(encoding="utf-8-sig")
        if not l.lstrip().startswith("#")))
    if not rows_in:
        print("No holdings found.")
        return
    fields = list(rows_in[0].keys())
    sym_col = next((f for f in fields
                    if (f or "").strip().lower() in
                    ("instrument", "symbol", "tradingsymbol")), fields[0])

    cfg = timing.load_config()
    con = store.connect()
    holdings = {h["tradingsymbol"]: h for h in pf.read_csv_holdings()}
    sectors = {}
    try:
        u = uni.nifty500()
        sectors = dict(zip(u["symbol"], u["sector"]))
    except Exception:
        pass
    qual = fu.score_all(sorted(holdings), sectors)

    mkt = bc.connect("data/market.db")
    import datetime as dt
    today = dt.date.fromisoformat(sorted(bc.have_days(mkt))[-1])
    liq = {s: i + 1 for i, s in enumerate(bc.universe_on(mkt, today, top_n=400))}

    suggestions: dict[str, tuple[str, str]] = {}
    for sym in holdings:
        f = pipeline.scored_frame(con, sym, cfg)
        vol = bk.realised_vol(f)
        q = qual.get(sym)
        suggestions[sym] = bk.classify(sym, q.score if q else None, vol,
                                       liq.get(sym))

    if "bucket" not in [f.strip().lower() for f in fields]:
        fields.append("bucket")
    shutil.copy(HOLDINGS, HOLDINGS.with_suffix(".csv.bak"))

    with HOLDINGS.open("w", newline="", encoding="utf-8") as fh:
        fh.write("# bucket: core | satellite | legacy\n"
                 "#   core      never sold on momentum rank; only a genuine red\n"
                 "#             flag (pledge, interest cover, quality collapse)\n"
                 "#             triggers an exit.\n"
                 "#   satellite rotated on rank -- falls out of the band, it is\n"
                 "#             sold. The only bucket momentum acts on.\n"
                 "#   legacy    red flags apply, rank ignored. The safe default.\n"
                 "# Values below are this framework's suggestion. Change freely.\n")
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows_in:
            sym = str(r.get(sym_col, "")).strip().strip('"').upper()
            if sym in suggestions and not (r.get("bucket") or "").strip():
                r["bucket"] = suggestions[sym][0]
            w.writerow({f: r.get(f, "") for f in fields})

    counts: dict[str, int] = {}
    for b, _ in suggestions.values():
        counts[b] = counts.get(b, 0) + 1
    print(f"Added `bucket` to {HOLDINGS} (backup at {HOLDINGS}.bak)")
    print("  suggested:", ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    print("\nThe ones worth your judgement are the legacy rows -- the framework")
    print("will not act on any of them until you decide.")


if __name__ == "__main__":
    main()
