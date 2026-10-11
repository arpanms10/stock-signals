"""Parag Parikh Flexi Cap Fund: monthly portfolio comparison.

Downloads any month-end disclosure not already in data/mf/ppfas_raw/, parses
every file, and writes:

    data/mf/ppfas_flexi_cap_holdings.csv   every line of every month (tracked)
    reports/mf/PPFAS_Flexi_Cap_Portfolio_Analysis.xlsx   the readable workbook

PPFAS publishes about 7-10 days after month-end; running earlier just finds
the newest month missing and reports on the months it has. Raw files are kept
as downloaded so a parser fix can be re-run over the full history.

    PYTHONPATH=. .venv/bin/python run_mf_ppfas.py                 # last 13 month-ends
    PYTHONPATH=. .venv/bin/python run_mf_ppfas.py --months 25     # two years
    PYTHONPATH=. .venv/bin/python run_mf_ppfas.py --no-download   # offline re-run
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from mf import changes as ch
from mf import ppfas
from mf import report

ROOT = Path(__file__).parent
HOLDINGS_CSV = ROOT / "data" / "mf" / "ppfas_flexi_cap_holdings.csv"
REPORT = ROOT / "reports" / "mf" / "PPFAS_Flexi_Cap_Portfolio_Analysis.xlsx"


def recent_month_ends(n: int, today: dt.date) -> list[dt.date]:
    y, m = today.year, today.month
    out = []
    for _ in range(n + 1):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(ppfas.month_end(y, m))
    return sorted(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--months", type=int, default=13,
                    help="month-end snapshots to compare (13 = a full year of changes)")
    ap.add_argument("--no-download", action="store_true")
    ap.add_argument("--out", type=Path, default=REPORT)
    args = ap.parse_args()

    # One extra candidate month: if last month is not published yet, the
    # window still holds `--months` snapshots.
    wanted = recent_month_ends(args.months, dt.date.today())
    if not args.no_download:
        for d in wanted:
            path = ppfas.download(d)
            print(f"  {d:%b %Y}: {path.name if path else 'not published yet'}")

    by_month = {}
    for p in ppfas.raw_files():
        d = ppfas.parse(p)
        by_month[d.month_end] = d
        for w in d.warnings:
            print(f"  WARNING {w}")
    discs = [by_month[m] for m in sorted(by_month) if m in set(wanted)][-args.months:]
    if len(discs) < 2:
        raise SystemExit("Need at least two monthly files to compare.")

    h = ch.holdings_table(discs)
    summary = ch.fund_summary(discs)
    known = ch.known_actions(ROOT / "data" / "signals.db", ROOT / "config" / "equities.csv")
    splits = ch.detect_splits(h, known)
    changes = ch.monthly_changes(h, summary, splits)
    journeys = ch.stock_journeys(h, changes, splits)
    sectors = ch.sector_weights(h)

    HOLDINGS_CSV.parent.mkdir(parents=True, exist_ok=True)
    all_h = ch.holdings_table([by_month[m] for m in sorted(by_month)])
    all_h.drop(columns=["price"]).to_csv(HOLDINGS_CSV, index=False)
    out = report.build(args.out, discs, h, summary, changes, journeys, splits, sectors)

    print(f"\n{len(discs)} months: {ch.month_label(discs[0].month_end)} to "
          f"{ch.month_label(discs[-1].month_end)}")
    for _, r in splits.iterrows():
        print(f"  split/bonus: {r['name']} {r['factor']:g}:1 in "
              f"{ch.month_label(r['month_end'])} ({r['source']})")
    print(f"Holdings: {HOLDINGS_CSV.relative_to(ROOT)}")
    print(f"Report:   {out}")


if __name__ == "__main__":
    main()
