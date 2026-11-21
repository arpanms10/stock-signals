"""Score every company share you hold on the 100-point scorecard.

Descriptive: summarises each company's filed record against the conventions
in config/scorecard.yaml. Not backtested; it ranks and predicts nothing.
Needs holding data first (scripts/refresh_holding_data.py).

    PYTHONPATH=. .venv/bin/python scripts/score_holdings.py            # print
    PYTHONPATH=. .venv/bin/python scripts/score_holdings.py --save     # + dated copy in research.db
    PYTHONPATH=. .venv/bin/python scripts/score_holdings.py --export data/results/scorecards.json
    PYTHONPATH=. .venv/bin/python scripts/score_holdings.py --fill-in  # create the research input files
"""
from __future__ import annotations
import sys as _sys; from pathlib import Path as _P; _sys.path.insert(0, str(_P(__file__).resolve().parents[1]))  # run from anywhere

import argparse

from book import scorecards as bs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--save", action="store_true", help="store today's cards in research.db")
    ap.add_argument("--export", metavar="PATH", help="write every card as JSON")
    ap.add_argument("--fill-in", action="store_true",
                    help="create / extend config/research_inputs and config/sector_outlook")
    args = ap.parse_args()
    cards, notes = bs.build(save=args.save)
    for n in notes:
        print(n)
    print(f"{'holding':12} {'model':16} {'score':>5} {'assessed':>8}  tier / role")
    for c in sorted(cards, key=lambda c: c.symbol):
        s = "—" if c.score is None else f"{c.score:.0f}"
        flags = f"  [{'; '.join(f.text for f in c.flags)}]" if c.flags else ""
        print(f"{c.symbol:12} {c.model[:16]:16} {s:>5} {c.coverage_pct:>7.0f}%  "
              f"{c.tier_label} / {c.role}{flags}")
    if args.export:
        print("wrote", bs.export_json(cards, args.export))
    if args.fill_in:
        for line in bs.fill_in_files(cards) or ["fill-in files already list every holding"]:
            print(line)
    if args.save:
        print(f"saved {len(cards)} scorecards to research.db")
    print("Descriptive, not backtested: explains each holding, ranks nothing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
