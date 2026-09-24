"""Weekly unattended run: refresh the data, then take a snapshot of the advice.

Scheduled for a weekend, deliberately. The market is closed, so Saturday's
data is Friday's close -- a stable snapshot that will not shift under you while
the report is being written, and one you can read before Monday's open.

Writes a dated report to reports/ and records the run in the decision log. If
anything fails it says so loudly, because the failure mode that matters here is
a job that quietly stops working and leaves you reading a report from a month
ago without noticing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"
STATE = ROOT / "data" / "last_weekly_run.txt"


def notify(title: str, message: str) -> None:
    """A macOS notification. Best effort -- never let this break the run."""
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification {message!r} with title {title!r}'],
            check=False, capture_output=True, timeout=10)
    except Exception:
        pass


def step(name: str, args: list[str], log, timeout: int = 3600) -> bool:
    """Run one script, streaming into the log. Returns True on success."""
    log.write(f"\n{'=' * 70}\n{name}\n{'=' * 70}\n")
    log.flush()
    started = dt.datetime.now()
    try:
        proc = subprocess.run(
            [sys.executable, *args], cwd=str(ROOT), timeout=timeout,
            capture_output=True, text=True,
            env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin:/usr/sbin"})
    except subprocess.TimeoutExpired:
        log.write(f"TIMED OUT after {timeout}s\n")
        return False
    log.write(proc.stdout or "")
    if proc.stderr:
        log.write("\n--- stderr ---\n" + proc.stderr)
    took = (dt.datetime.now() - started).seconds
    ok = proc.returncode == 0
    log.write(f"\n[{'ok' if ok else 'FAILED'} in {took}s]\n")
    log.flush()
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--portfolio", type=float, default=None,
                    help="total capital, for the core/satellite framing note")
    ap.add_argument("--skip-refresh", action="store_true",
                    help="report on cached data without fetching")
    args = ap.parse_args()

    REPORTS.mkdir(exist_ok=True)
    today = dt.date.today()
    report_path = REPORTS / f"{today:%Y-%m-%d}-weekly.txt"

    failures: list[str] = []
    with report_path.open("w") as log:
        log.write(f"Weekly run -- {today:%A %d %B %Y}\n")

        if not args.skip_refresh:
            # Prices first: the advisor is useless on stale ones, and the
            # fundamentals fetch is slower and less critical.
            if not step("Refresh prices", ["run_backfill.py", "--years", "10"],
                        log, timeout=5400):
                failures.append("price refresh")
            if not step("Refresh market data",
                        ["run_market_ingest.py", "--years", "10"], log,
                        timeout=5400):
                failures.append("market data")
            if not step("Refresh fundamentals", ["fetch_fundamentals.py"], log,
                        timeout=5400):
                failures.append("fundamentals")

        advisor = ["run_advisor.py"]
        if args.portfolio:
            advisor += ["--total-capital", str(args.portfolio)]
        if not step("Portfolio advisor", advisor, log, timeout=1800):
            failures.append("advisor")

    STATE.write_text(f"{dt.datetime.now().isoformat(timespec='seconds')}\n"
                     f"{'FAILED: ' + ', '.join(failures) if failures else 'ok'}\n")

    if failures:
        notify("Stock Signals — weekly run FAILED",
               f"{', '.join(failures)}. See {report_path.name}")
        print(f"FAILED: {', '.join(failures)}. Report: {report_path}")
        return 1

    notify("Stock Signals — weekly report ready", f"{report_path.name}")
    print(f"Done. Report: {report_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        REPORTS.mkdir(exist_ok=True)
        crash = REPORTS / f"{dt.date.today():%Y-%m-%d}-crash.txt"
        crash.write_text(traceback.format_exc())
        notify("Stock Signals — weekly run CRASHED", crash.name)
        raise
