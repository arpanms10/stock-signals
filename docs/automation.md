# Automation

The Saturday job, and the failure modes it guards against. All five jobs
(daily, weekly, monthly, quarterly, fno-intraday) are listed in the README's
[Scheduled jobs](../README.md#scheduled-jobs).

[← back to the README](../README.md)

---

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
