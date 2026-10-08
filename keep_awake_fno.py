"""Hold the Mac awake through market hours, so the F&O snapshots run.

Started by launchd at 09:20 on weekdays (install_schedule.py --fno-intraday).
On an NSE trading day it runs macOS's own `caffeinate -i` until 15:35 -- an
assertion against idle sleep held by this process, not a change to system
settings: it ends when the process does, and nothing is left behind.

What it cannot do: wake a Mac that is already asleep (that needs a scheduled
wake: `sudo pmset repeat wakeorpoweron MTWRF 09:20:00`, which you run
yourself), or keep a closed-lid laptop on battery awake.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

END = dt.time(15, 35)


def seconds_until_close(now: dt.datetime) -> int:
    end = dt.datetime.combine(now.date(), END)
    return max(0, int((end - now).total_seconds()))


def main() -> int:
    now = dt.datetime.now()
    if now.weekday() >= 5:
        print(f"{now:%Y-%m-%d %H:%M}: weekend, not holding awake.")
        return 0
    try:
        from data.sources import nse_derivatives as nsed
        if now.date() in set(nsed.trading_holidays(now.year)):
            print(f"{now:%Y-%m-%d %H:%M}: NSE holiday, not holding awake.")
            return 0
    except Exception as exc:      # noqa: BLE001 -- unknown calendar: stay awake
        print(f"holiday check failed ({exc}); holding awake anyway", file=sys.stderr)
    secs = seconds_until_close(now)
    if secs == 0:
        print(f"{now:%Y-%m-%d %H:%M}: market already closed.")
        return 0
    print(f"{now:%Y-%m-%d %H:%M}: holding awake for {secs // 60} min (until {END:%H:%M}).",
          flush=True)
    os.execvp("caffeinate", ["caffeinate", "-i", "-t", str(secs)])


if __name__ == "__main__":
    raise SystemExit(main())
