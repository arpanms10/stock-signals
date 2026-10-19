"""Daily after-close job: refresh prices, then record the swing scan.

The forward record (swing_store.py) is only worth having if it has no gaps:
a record kept on the days someone happened to open the dashboard is a
record of those days, not of the strategy. So this runs on its own, every
weekday evening (install_schedule.py --swing-daily).

  1. Catch prices up for the Nifty 500 (a superset of the Nifty 200) from
     NSE's daily market file -- seconds when only a day or two is missing.
  2. Record the scan for each universe in `swing.record_universes`, which
     also rebuilds history/swing_picks.xlsx.
  3. Check the watch list (config/watching.csv) against the new prices:
     stops hit and targets reached are dated there (watching.py).
  4. Score any new quarterly results for the stocks you follow, and measure
     the price reaction to the ones already scored (run_results.py).
  5. Catch the F&O history up and record the day's ranges for every F&O
     underlying -- the forward log behind Today's ranges (record_ranges.py).

Idempotent: a holiday, or a second run the same evening, finds the price
date already recorded and writes nothing. Weekends are skipped outright.
The new rows are left in the working tree for you to commit; the job never
touches git.

    PYTHONPATH=. .venv/bin/python run_swing_daily.py
    PYTHONPATH=. .venv/bin/python run_swing_daily.py --skip-refresh   # record only

A log per run goes to reports/swing_daily.log.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

from run_weekly import ROOT, REPORTS, notify, step


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--skip-refresh", action="store_true",
                    help="record on the prices already on file")
    ap.add_argument("--force", action="store_true", help="run on a weekend too")
    args = ap.parse_args(argv)

    now = dt.datetime.now()
    if now.weekday() >= 5 and not args.force:
        print(f"{now:%Y-%m-%d %H:%M}: weekend, nothing to record.")
        return 0

    from scoring import timing
    universes = timing.load_config()["swing"].get("record_universes", ["nifty200"])

    REPORTS.mkdir(exist_ok=True)
    failures = []
    with (REPORTS / "swing_daily.log").open("a") as log:
        log.write(f"\n\n##### Swing daily run -- {now:%A %d %B %Y %H:%M}\n")
        if not args.skip_refresh:
            # --years only bounds a symbol with no history at all; the daily
            # path fetches just the missing days.
            if not step("Refresh Nifty 500 prices",
                        ["run_backfill.py", "--universe", "nifty500", "--years", "10"],
                        log, timeout=3600):
                failures.append("price refresh")
        for u in universes:
            if not step(f"Record swing scan ({u})", ["record_swing.py", "--universe", u],
                        log, timeout=1800):
                failures.append(f"record {u}")
        if not step("Check watch list for stops and targets", ["watching.py"],
                    log, timeout=600):
            failures.append("watch list check")
        if not step("Score new quarterly results", ["run_results.py"],
                    log, timeout=1800):
            failures.append("results")
        if not step("Record Today ranges", ["record_ranges.py"], log, timeout=1800):
            failures.append("ranges record")
    if failures:
        notify("Stock signals: swing record FAILED", ", ".join(failures))
        print(f"failed: {', '.join(failures)} -- see reports/swing_daily.log",
              file=sys.stderr)
        return 1
    print(f"{now:%Y-%m-%d %H:%M}: recorded {', '.join(universes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
