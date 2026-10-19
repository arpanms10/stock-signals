"""Install, check or remove the scheduled jobs (macOS, Linux, Windows).

    python install_schedule.py --list                      # what each job does
    python install_schedule.py --status                    # what is installed
    python install_schedule.py --install daily weekly      # install these
    python install_schedule.py --install recommended       # daily, weekly, monthly, quarterly
    python install_schedule.py --remove daily              # remove one
    python install_schedule.py --remove all

The jobs and the three schedulers (launchd, cron, Task Scheduler) are in
jobs.py. This changes what your computer does on its own, so it only ever
runs when you ask; setup_project.py asks before calling it.

Older forms still work: no arguments installs the weekly job
(--day/--hour/--minute/--portfolio), --swing-daily the daily one,
--fno-intraday the market-hours snapshots; each with --remove or --status.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import jobs

ROOT = Path(__file__).resolve().parent


def show_list() -> None:
    osn = jobs.os_name()
    for j in jobs.JOBS.values():
        tag = "recommended" if j.recommended else "optional"
        print(f"{j.name:13} {j.title} ({tag})")
        print(f"{'':13} When:  {j.when.describe()}")
        print(f"{'':13} Does:  {j.what}")
        print(f"{'':13} Feeds: {j.feeds}")
        for n in j.notes:
            print(f"{'':13} Note:  {n}")
        if not jobs.supported(j, osn):
            print(f"{'':13} Not available on {osn}.")
        print()


def show_status() -> None:
    state = ROOT / "data" / "last_weekly_run.txt"
    for j in jobs.JOBS.values():
        print(f"{j.name:13} {'installed' if jobs.installed(j.name) else 'not installed'}"
              f"   ({j.when.describe()})")
    if state.exists():
        print("\nlast weekly run:\n  " + state.read_text().strip().replace("\n", "\n  "))
    print(f"\nLogs: {ROOT / 'reports'}")


def names(arg: list[str]) -> list[str]:
    if arg == ["recommended"]:
        return [n for n, j in jobs.JOBS.items() if j.recommended]
    if arg == ["all"]:
        return list(jobs.JOBS)
    bad = [a for a in arg if a not in jobs.JOBS]
    if bad:
        raise SystemExit(f"unknown job(s): {', '.join(bad)}. "
                         f"Choose from: {', '.join(jobs.JOBS)}, recommended, all")
    return arg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--list", action="store_true", help="describe every job")
    ap.add_argument("--status", action="store_true", help="what is installed")
    ap.add_argument("--install", nargs="+", metavar="JOB")
    ap.add_argument("--remove", nargs="*", metavar="JOB",
                    help="jobs to remove (with an old-style flag: that job)")
    ap.add_argument("--portfolio", type=float, default=None,
                    help="your total capital, for the weekly report's framing note")
    # Older forms.
    ap.add_argument("--swing-daily", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--fno-intraday", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--day", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--hour", type=int, default=None, help=argparse.SUPPRESS)
    ap.add_argument("--minute", type=int, default=None, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)

    if a.list:
        show_list()
        return 0
    legacy = "daily" if a.swing_daily else "fno-intraday" if a.fno_intraday else None
    if a.status:
        if legacy:
            print(f"{legacy}: {'installed' if jobs.installed(legacy) else 'not installed'}")
        else:
            show_status()
        return 0
    if a.remove is not None:
        targets = names(a.remove) if a.remove else [legacy or "weekly"]
        for n in targets:
            print(f"{n}: {'removed' if jobs.remove(n) else 'was not installed'}")
        return 0

    targets = names(a.install) if a.install else [legacy or "weekly"]
    extra_weekly = ["--portfolio", str(a.portfolio)] if a.portfolio else []
    failed = False
    for n in targets:
        job = jobs.JOBS[n]
        when = None
        if a.hour is not None or a.minute is not None or a.day:
            w = job.when
            when = jobs.When(w.kind, w.hour if a.hour is None else a.hour,
                             w.minute if a.minute is None else a.minute,
                             (a.day or w.day)[:3].upper(), w.day_of_month, w.months, w.until)
        if not jobs.supported(job):
            print(f"{n}: not available on {jobs.os_name()}. Run it yourself:\n"
                  + jobs.manual_instructions(job))
            continue
        try:
            print("Installed  " + jobs.install(n, ROOT, extra_weekly if n == "weekly" else None,
                                               when))
        except RuntimeError as exc:
            failed = True
            print(f"{n}: {exc}", file=sys.stderr)
            print("Run it yourself instead:\n" + jobs.manual_instructions(job))
    print(f"\nCheck:  python install_schedule.py --status\n"
          f"Remove: python install_schedule.py --remove <job>\nLogs:   {ROOT / 'reports'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
