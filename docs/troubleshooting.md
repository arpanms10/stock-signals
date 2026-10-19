# Troubleshooting

The problems people actually hit, and what to do. Start with the check: it
says what's set up, missing or stale.

```bash
python3 setup_project.py --check
```

[← back to the README](../README.md) · [Getting started](../GETTING_STARTED.md)

---

## Installing

**"Python 3.9 is too old" (or 3.10).** Install a newer Python from
[python.org](https://www.python.org/downloads/) and run the setup with it:
`python3.12 setup_project.py`. On a Mac the built-in 3.9 stays; you just use
the new one.

**"python3: command not found" on Windows.** Use `py setup_project.py`. If
that isn't found either, reinstall Python and tick *Add python.exe to PATH*.

**Package install fails.** Usually a network hiccup: run the setup again. If it
mentions a compiler or "wheel", your Python is newer than the packages
support yet. Install Python 3.12 and use that.

**"Both holdings.xlsx and holdings.csv are in config/".** Keep one. The
`.xlsx` is used while both exist.

## Downloading data

**"NSE's download server did not answer".** NSE blocks many networks outside
India, most cloud servers and some VPNs. Turn off the VPN, or try another
connection (a phone hotspot works). Then run the setup again; it continues
where it stopped.

**A download stops or fails part-way.** Run the same command again. Every
download skips what it already has, so nothing is fetched twice. Days NSE
didn't publish (holidays) are normal and skipped.

**It's slow.** The full-market history is about 2,500 daily files from NSE,
fetched politely one at a time; 60–90 minutes is normal. Use the starter set
(two years) to get going, and let the weekly job extend it.

**Price fetching suddenly fails everywhere.** NSE changed its website and the
`jugaad-data` package needs an update:
`.venv/bin/python -m pip install -U jugaad-data`
(Windows: `.venv\Scripts\python -m pip install -U jugaad-data`).

## The dashboard

**"The market data isn't loaded yet".** That page ranks the whole market.
Download it: `python3 setup_project.py --data market-recent` (15–25 min), or
press *Update full-market data* on the Data and jobs page.

**A page says data is stale (amber or red chip).** Click the chip, top
right. It opens Data and jobs, which shows each dataset's age with a button to
refresh it, or *Refresh everything stale*.

**My holdings don't show up.** Check `config/holdings.xlsx`: symbols must be
NSE symbols in capitals (`HDFCBANK`, not "HDFC Bank"), quantity must be a
number, and the data must be on the first sheet (not on *How to fill*).
`setup_project.py --check` says how many rows it reads.

**A mutual fund isn't recognised.** The page lists close matches. Use the
fund's AMFI scheme code in the `fund` column to be exact.

**The browser didn't open.** Go to `http://localhost:8501` yourself. If that
port is busy, the terminal shows the address it used instead.

## Scheduled jobs

**Did my jobs run?** `.venv/bin/python install_schedule.py --status` lists
them. Each job writes to `reports/<job>.log`, and the weekly job also writes
a report in `reports/`.

**They didn't run.** Jobs run only while the computer is on.
- **Mac and Windows:** a missed run happens when the computer wakes.
- **Linux:** cron skips missed runs, and the next one catches up.

Either way nothing is lost, because each run fetches whatever is missing.

**"runs another copy".** A job installed from a different folder of this
project. Installing it here moves it to this copy, so only one copy's data
stays fresh.

**Remove them all.** `.venv/bin/python install_schedule.py --remove all`.

## Updating

**`git pull` complains about files in `history/`.** The daily and weekly jobs
add records there (the forward logs). Keep yours and take the update:
`git stash`, then `git pull`, then `git stash pop`. If you don't need the
records in git, download the ZIP instead.

## Still stuck

Open the project in Claude Code and type `/setup`, or open an issue on GitHub
with the output of `python3 setup_project.py --check`. Leave out anything from
your `config` folder: it's your portfolio.
