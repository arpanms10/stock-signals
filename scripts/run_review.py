"""Portfolio review: monitoring events and what changed (Phase 8; docs/monitoring.md).

Fetches NSE announcements for your company holdings, turns announcements,
shareholding and results into dated events (thesis check / watch / info),
saves today's scorecards and a review snapshot, compares it with the last
review, and writes data/reviews/review-<date>.md.

    PYTHONPATH=. .venv/bin/python scripts/run_review.py              # full review
    PYTHONPATH=. .venv/bin/python scripts/run_review.py --offline    # no NSE fetch
    PYTHONPATH=. .venv/bin/python scripts/run_review.py --events     # list open events

Run scripts/refresh_holding_data.py first so shareholding and results are current.
"""
from __future__ import annotations
import sys as _sys; from pathlib import Path as _P; _sys.path.insert(0, str(_P(__file__).resolve().parents[1]))  # run from anywhere

import argparse
import sys

from book import monitoring as bm
from strategy import monitor as mon


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="don't fetch announcements")
    ap.add_argument("--no-save-scores", action="store_true",
                    help="don't store today's scorecards")
    ap.add_argument("--events", action="store_true", help="list open events and stop")
    a = ap.parse_args()
    if a.events:
        con = bm.connect()
        for e in bm.events(con, status="open", severities=(mon.THESIS, mon.WATCH)):
            print(f"{mon.LABEL[e['severity']]:12} {e['date']}  {e['symbol']:12} {e['title']}")
        con.close()
        return 0
    r = bm.run(fetch=not a.offline, save_scores=not a.no_save_scores)
    n = mon.summarise([mon.Event(**{k: e[k] for k in ("key", "symbol", "date", "kind",
                                                       "severity", "title", "detail",
                                                       "source", "link")})
                       for e in r["events"] if e["status"] == "open"])
    print(f"review {r['as_of']}" + (f" vs {r['previous']}" if r["previous"] else " (first)")
          + f": {r['new_announcements']} new announcements, {r['new_events']} new events; "
          f"open: {n[mon.THESIS]} thesis checks, {n[mon.WATCH]} watch; "
          f"{len(r['changes'])} changes")
    for p in r["problems"]:
        print("  problem:", p)
    print("wrote", r["report"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
