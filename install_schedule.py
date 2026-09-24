"""Install (or remove) the weekly launchd agent.

launchd rather than cron: it survives reboots, it runs the job when the machine
next wakes if it was asleep at the scheduled time, and it is what macOS
actually supports. cron still works but Apple has been deprecating it for
years.

Saturday morning by default. The market is closed, so the data is Friday's
close -- stable, and read before Monday's open.

This writes a file into ~/Library/LaunchAgents and loads it. Run it yourself
rather than having it done for you: it is a change to what your machine does
on its own.
"""
from __future__ import annotations

import argparse
import plistlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LABEL = "com.arpanmuk.stock-signals.weekly"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"

WEEKDAYS = {"sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3,
            "thursday": 4, "friday": 5, "saturday": 6}


def build(day: str, hour: int, minute: int, portfolio: float | None) -> dict:
    args = [str(ROOT / ".venv" / "bin" / "python"), str(ROOT / "run_weekly.py")]
    if portfolio:
        args += ["--portfolio", str(portfolio)]
    return {
        "Label": LABEL,
        "ProgramArguments": args,
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": {"PYTHONPATH": str(ROOT),
                                 "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        "StartCalendarInterval": {"Weekday": WEEKDAYS[day],
                                  "Hour": hour, "Minute": minute},
        # If the Mac was asleep at the scheduled time, run when it wakes.
        # Without this a laptop that is shut on Saturday simply skips the week,
        # silently, which is the failure this whole job exists to avoid.
        "RunAtLoad": False,
        "StandardOutPath": str(ROOT / "reports" / "launchd.out.log"),
        "StandardErrorPath": str(ROOT / "reports" / "launchd.err.log"),
        "ProcessType": "Background",
        "LowPriorityIO": True,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="saturday", choices=sorted(WEEKDAYS))
    ap.add_argument("--hour", type=int, default=9)
    ap.add_argument("--minute", type=int, default=30)
    ap.add_argument("--portfolio", type=float, default=None)
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.status:
        r = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
        line = [l for l in r.stdout.splitlines() if LABEL in l]
        print(f"installed at {PLIST}" if PLIST.exists() else "not installed")
        print(f"loaded: {line[0] if line else 'no'}")
        state = ROOT / "data" / "last_weekly_run.txt"
        if state.exists():
            print(f"last run:\n  " + state.read_text().strip().replace("\n", "\n  "))
        return

    if args.remove:
        subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
        if PLIST.exists():
            PLIST.unlink()
        print(f"removed {LABEL}")
        return

    (ROOT / "reports").mkdir(exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    PLIST.write_bytes(plistlib.dumps(build(args.day, args.hour, args.minute,
                                           args.portfolio)))
    subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
    r = subprocess.run(["launchctl", "load", str(PLIST)], capture_output=True,
                       text=True)
    if r.returncode:
        print(f"load failed: {r.stderr.strip()}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Installed: {args.day.title()} at {args.hour:02d}:{args.minute:02d}")
    print(f"  plist   {PLIST}")
    print(f"  reports {ROOT / 'reports'}")
    print("\nCheck it with:  python install_schedule.py --status")
    print("Remove it with: python install_schedule.py --remove")


if __name__ == "__main__":
    main()
