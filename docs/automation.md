# Automation

The Saturday launchd job, and the failure modes it guards against.

[← back to the README](../README.md)

---

```bash
.venv/bin/python install_schedule.py --day saturday --hour 9 --minute 30 \
    --portfolio 2000000
.venv/bin/python install_schedule.py --status
.venv/bin/python install_schedule.py --remove
```

Installs a **launchd** agent that runs `run_weekly.py`: refresh prices, market
data and fundamentals, then the advisor, writing a dated report to `reports/`.
Everything runs locally on this Mac; nothing goes to a server.

Saturday because the market is closed -- the data is Friday's close, a stable
snapshot that will not shift while the report is written, and one you read
before Monday's open.

**launchd rather than cron**: if the Mac is asleep at the scheduled time the job
runs when it next wakes. Cron would skip the week silently, which on a laptop
that is shut at weekends is the normal case rather than the exception.

The failure this guards against is a scheduled job that quietly stops working
and leaves you reading a month-old report without noticing. So a macOS
notification fires on failure and on crash naming what broke,
`data/last_weekly_run.txt` records the outcome, every step has a timeout, and
the dashboard shows a red banner when data ages.

The full refresh takes 60-90 minutes at low I/O priority.

---

See also: [data](data.md) for how staleness is detected, [advisor](advisor.md) for what the weekly report contains.
