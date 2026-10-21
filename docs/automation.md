# Automation

The scheduled jobs that keep the data fresh, and the failure modes the
Saturday job guards against.

[← back to the README](../README.md)

---

## Scheduled jobs

Five jobs, defined once in `ops/jobs.py` and installed with each system's own
scheduler: launchd on macOS, cron on Linux, Task Scheduler on Windows. Install
them yourself, or let `setup_project.py` ask. Each changes what the computer
does on its own; none touches git or your broker.

| Job | When | Runs | Feeds |
|---|---|---|---|
| **daily** | Weekdays 19:30 | `scripts/run_swing_daily.py`: catch up prices, record the swing scans and Today's ranges, date stops and targets in the watch list, score new results | Today, Stocks, Swing, Quarterly results |
| **weekly** | Saturday 09:30 | `scripts/run_weekly.py`: full-market history, fundamentals, F&O history and validation, momentum and F&O records, range calibration, a report in `reports/` | Market, Momentum, Analyze, F&O, Today |
| **monthly** | 5th, 10:00 | `scripts/run_mf.py --refresh`: month-end fund NAVs | Mutual funds, Compare funds |
| **quarterly** | 15 Feb/May/Aug/Nov, 10:00 | `scripts/run_picker.py --refresh`: Nifty 200 filings | GARP / QARP |
| fno-intraday *(optional)* | Weekdays 09:30–15:30, half-hourly | `scripts/snapshot_fno.py`, plus a keep-awake agent on macOS | F&O |

```bash
PYTHONPATH=. .venv/bin/python scripts/install_schedule.py --list                    # what each does
PYTHONPATH=. .venv/bin/python scripts/install_schedule.py --install recommended     # daily, weekly, monthly, quarterly
PYTHONPATH=. .venv/bin/python scripts/install_schedule.py --status
PYTHONPATH=. .venv/bin/python scripts/install_schedule.py --remove daily            # or: --remove all
```

- **Missed runs.** macOS and Windows run a job missed while the computer was
  off or asleep when it wakes. Linux cron skips it, and the next run catches
  up. Every job fetches only what is missing, so a late or repeated run is
  harmless.
- **Logs.** Each job writes `reports/<job>.log`. The weekly job also writes a
  dated report, and a failure raises a notification on macOS.
- **Options.** `--portfolio 2000000` gives the weekly report your total
  capital. `swing.record_universes` in `config/scoring.yaml` picks the
  universes the daily job records.
- **Removing a job** leaves its records (`history/`), workbooks and logs in
  place. Delete those by hand only if you want the history gone; it can't be
  rebuilt.

## The Saturday job

```bash
.venv/bin/python scripts/install_schedule.py --install weekly --portfolio 2000000
.venv/bin/python scripts/install_schedule.py --status
.venv/bin/python scripts/install_schedule.py --remove weekly
```

Installs a job (launchd on macOS, cron on Linux, Task Scheduler on Windows;
see `ops/jobs.py`) that runs `scripts/run_weekly.py`: refresh prices, market
data and fundamentals, then the advisor, writing a dated report to `reports/`.
Everything runs locally; nothing goes to a server.

Saturday because the market is closed -- the data is Friday's close, a stable
snapshot that will not shift while the report is written, and one you read
before Monday's open.

**On macOS, launchd rather than cron**: if the Mac is asleep at the scheduled
time the job runs when it next wakes. Cron would skip the week silently, which
on a laptop that is shut at weekends is the normal case rather than the
exception. Windows tasks are created with "run as soon as possible after a
missed start" for the same reason. Linux uses cron, so there a missed week is
caught up by the next run (every step fetches only what is missing).

The failure this guards against is a scheduled job that quietly stops working
and leaves you reading a month-old report without noticing. So a macOS
notification fires on failure and on crash naming what broke,
`data/last_weekly_run.txt` records the outcome, every step has a timeout, and
the dashboard shows a red banner when data ages.

The full refresh takes 60-90 minutes at low I/O priority.

---

See also: [data](data.md) for how staleness is detected, [advisor](advisor.md) for what the weekly report contains.
