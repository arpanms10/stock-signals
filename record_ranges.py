"""Forward log of the Today ranges: record each day's bands, score the old ones.

    PYTHONPATH=. .venv/bin/python record_ranges.py               # fetch, record, score
    PYTHONPATH=. .venv/bin/python record_ranges.py --no-ingest   # record what is on file

1. Catch data/fo_history.db up with NSE's F&O bhavcopy (only the missing
   days; the Saturday job's full refresh is still what fills the rest), and
   the results calendar for the weeks ahead.
2. Record the bands for every F&O underlying as of each close not yet in
   history/ranges/ -- at most the last CATCHUP sessions, so a Mac asleep for
   a day leaves no hole, while nothing from long ago is written with code
   that did not exist then. A new log starts with the latest close only. A
   band recorded after its close was known is kept but never scored.
3. Score every recorded band whose close is now in, and print the hit rates
   next to the backtest's (config/range_calibration.json).

The weekday job (run_swing_daily.py) runs this after the close. The CSVs are
left in the working tree for you to commit; the job never touches git.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json

import ranges_store as rs
from data import fo_bhavcopy as fb
from run_range_backtest import CALIBRATION_PATH

CATCHUP = 5


def load_calibration() -> dict | None:
    try:
        return json.loads(CALIBRATION_PATH.read_text())
    except (OSError, ValueError):
        return None


def ingest(con, today: dt.date) -> None:
    last = con.execute("SELECT MAX(date) FROM fo_days").fetchone()[0]
    start = dt.date.fromisoformat(last) + dt.timedelta(days=1) if last else fb.FIRST_DAY
    if start <= today:
        print(f"F&O bhavcopy {start} .. {today}")
        fb.ingest_range(con, start, today)
    fb.ingest_results(con, today - dt.timedelta(days=31), today + dt.timedelta(days=45))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-ingest", action="store_true", help="skip the NSE download")
    a = ap.parse_args(argv)
    today = dt.date.today()
    con = fb.connect()
    if not a.no_ingest:
        try:
            ingest(con, today)
        except Exception as exc:          # noqa: BLE001 -- record what is on file anyway
            print(f"Download failed ({exc}); recording from the data on file.")

    days = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM fo_spot ORDER BY date DESC LIMIT ?", (CATCHUP,))]
    held = rs.recorded_dates()
    if not held:
        days = days[:1]           # a new log starts today, not with a backfill
    for day in sorted(set(days) - held):
        n = rs.record(rs.build(con, day))
        print(f"Recorded {day}: {n} underlyings")
    if not set(days) - held:
        print(f"Already recorded through {max(days) if days else 'nothing'}.")

    scored = rs.score(rs.log(), con)
    summ = rs.summary(scored, load_calibration())
    if summ.empty:
        print("Nothing scored yet: a band is scored once the close it covers is in.")
    else:
        print("\nForward log, share of closes inside the band:")
        print(summ.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
