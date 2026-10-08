"""Store an intraday snapshot of the F&O basket's chains. Meant for launchd.

The change-in-OI PCR is the one F&O read the end-of-day history cannot test:
it is about what writers did during the session. These snapshots, taken every
half hour by the agent install_schedule.py --fno-intraday installs, build the
intraday history that run_fno_backtest.py tests once there is enough of it.

Does nothing outside 09:15-15:30 IST on a trading day. Light: no next-month
chain, no results lookup -- just the chain and futures, saved with NSE's own
timestamp so a run between NSE updates replaces rather than duplicates.

    PYTHONPATH=. .venv/bin/python snapshot_fno.py
    PYTHONPATH=. .venv/bin/python snapshot_fno.py --symbols NIFTY BANKNIFTY --force
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys


def in_session(now: dt.datetime, holidays) -> bool:
    return (now.weekday() < 5 and now.date() not in set(holidays)
            and dt.time(9, 15) <= now.time() <= dt.time(15, 30))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--symbols", nargs="+", default=None,
                    help="default: the record_fno.py basket")
    ap.add_argument("--force", action="store_true", help="run outside market hours")
    a = ap.parse_args(argv)

    import fno_report
    import record_fno
    from data.sources import nse_derivatives as nsed

    now = dt.datetime.now()
    try:
        hol = nsed.trading_holidays(now.year)
    except RuntimeError:
        hol = []
    if not a.force and not in_session(now, hol):
        print(f"{now:%Y-%m-%d %H:%M}: market closed, nothing to do.")
        return 0
    symbols = [s.upper() for s in (a.symbols or record_fno.default_symbols())]
    ok = 0
    for sym in symbols:
        try:
            fno_report.load(sym, save=True, with_context=False)
            ok += 1
        except (nsed.NotFnO, ValueError, RuntimeError) as exc:
            print(f"  {sym}: {exc}", file=sys.stderr)
    print(f"{now:%Y-%m-%d %H:%M}: {ok}/{len(symbols)} snapshots stored.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
