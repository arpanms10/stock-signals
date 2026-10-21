"""The scheduled jobs, and how to install them on macOS, Linux and Windows.

One list of jobs, three ways to schedule them:

  macOS    launchd (~/Library/LaunchAgents). A run missed while the Mac was
           asleep happens when it wakes.
  Linux    cron (your user crontab). A missed run is skipped; the next one
           catches up, because every job only fetches what is missing.
  Windows  Task Scheduler (tasks under \\stock-signals\\). "Run as soon as
           possible after a missed start" is switched on.

Everything that builds a schedule entry is a pure function, so it is tested
without touching the machine; install() and remove() are the only parts that
change anything, and install_schedule.py / setup_project.py call them only
with your consent.
"""
from __future__ import annotations

import datetime as dt
import platform
import plistlib
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent
PREFIX = "local.stock-signals"           # launchd label prefix
TASK_FOLDER = "stock-signals"            # Windows Task Scheduler folder
CRON_MARK = "# stock-signals:"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
# Labels used before 2026-10-19, when the jobs were one person's. Removed on
# install so a job never runs twice.
LEGACY_SUFFIX = {"weekly": "weekly", "swing-daily": "daily",
                 "fno-intraday": "fno-intraday", "fno-awake": "fno-awake"}

DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
WEEKDAYS = DAYS[:5]
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT",
          "NOV", "DEC"]


@dataclass
class When:
    """kind: weekdays | weekly | monthly | quarterly | intraday."""
    kind: str
    hour: int = 0
    minute: int = 0
    day: str = "SAT"                     # weekly
    day_of_month: int = 1                # monthly, quarterly
    months: tuple[int, ...] = ()         # quarterly
    until: tuple[int, int] = (15, 30)    # intraday: every 30 min from hour:minute

    def describe(self) -> str:
        t = f"{self.hour:02d}:{self.minute:02d}"
        if self.kind == "weekdays":
            return f"Monday to Friday at {t}"
        if self.kind == "weekly":
            return f"every {dt.date(2024, 1, 1 + DAYS.index(self.day)):%A} at {t}"
        if self.kind == "monthly":
            return f"on day {self.day_of_month} of every month at {t}"
        if self.kind == "quarterly":
            names = ", ".join(f"{dt.date(2024, m, 1):%b}" for m in self.months)
            return f"on day {self.day_of_month} of {names} at {t}"
        return (f"Monday to Friday, every 30 minutes from {t} to "
                f"{self.until[0]:02d}:{self.until[1]:02d}")

    def intraday_times(self) -> list[tuple[int, int]]:
        out, h, m = [], self.hour, self.minute
        while (h, m) <= self.until:
            out.append((h, m))
            h, m = (h, m + 30) if m < 30 else (h + 1, m - 30)
        return out


@dataclass
class Job:
    name: str
    title: str
    script: str
    args: list[str]
    when: When
    what: str                         # plain words: what it does
    feeds: str                        # which pages need it
    recommended: bool = True
    platforms: tuple[str, ...] = ("macos", "linux", "windows")
    notes: list[str] = field(default_factory=list)


JOBS = {j.name: j for j in [
    Job("daily", "Daily, after the market closes", "run_swing_daily.py", [],
        When("weekdays", 19, 30),
        "Catches up share prices, records the day's swing scan and expected "
        "ranges, checks your watch list for stops and targets, and scores any "
        "new quarterly results.",
        "Today, Stocks, Swing, Quarterly results"),
    Job("weekly", "Weekly, Saturday morning", "run_weekly.py", [],
        When("weekly", 9, 30, day="SAT"),
        "Refreshes the full-market history, company fundamentals and the F&O "
        "history, re-measures the hit rates the pages quote, records the "
        "month's momentum picks, and writes a report to reports/.",
        "Market, Momentum, Analyze, F&O, Today"),
    Job("monthly", "Monthly, early in the month", "run_mf.py",
        ["--refresh", "--no-holdings"], When("monthly", 10, 0, day_of_month=5),
        "Adds the month-end NAV of every mutual fund (AMFI publishes them "
        "after month end).", "Mutual funds, Compare funds"),
    Job("quarterly", "Quarterly, after results season", "run_picker.py", ["--refresh"],
        When("quarterly", 10, 0, day_of_month=15, months=(2, 5, 8, 11)),
        "Fetches the quarter's filings for the Nifty 200 behind the GARP / QARP "
        "shortlists. Companies have 45 days to report, so mid-Feb, May, Aug "
        "and Nov catch the season.", "GARP / QARP"),
    Job("fno-intraday", "Every 30 minutes in market hours (optional)", "snapshot_fno.py",
        [], When("intraday", 9, 30, until=(15, 30)),
        "Saves option-chain snapshots so the F&O page can show how open "
        "interest moved during the day. Experimental; only useful if you read "
        "the F&O page.", "F&O (experimental)", recommended=False,
        notes=["The computer has to be awake in market hours. On macOS a companion "
               "job keeps it awake 09:20-15:35 on trading days."]),
]}


# ------------------------------------------------------------------ helpers

def os_name() -> str:
    s = platform.system()
    return {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}.get(s, s.lower())


def python_path(root: Path = ROOT) -> Path:
    for p in (root / ".venv" / "bin" / "python", root / ".venv" / "Scripts" / "python.exe"):
        if p.exists():
            return p
    return Path(sys.executable)


def log_path(job: Job, root: Path = ROOT) -> Path:
    return root / "reports" / f"{job.name}.log"


def label(job_name: str) -> str:
    return f"{PREFIX}.{job_name}"


# ------------------------------------------------------------------- macOS

def plist(job: Job, root: Path = ROOT, extra: list[str] | None = None) -> dict:
    w = job.when
    if w.kind == "weekdays":
        cal = [{"Weekday": d, "Hour": w.hour, "Minute": w.minute} for d in range(1, 6)]
    elif w.kind == "weekly":
        cal = {"Weekday": (DAYS.index(w.day) + 1) % 7, "Hour": w.hour, "Minute": w.minute}
    elif w.kind == "monthly":
        cal = {"Day": w.day_of_month, "Hour": w.hour, "Minute": w.minute}
    elif w.kind == "quarterly":
        cal = [{"Month": m, "Day": w.day_of_month, "Hour": w.hour, "Minute": w.minute}
               for m in w.months]
    else:
        cal = [{"Weekday": d, "Hour": h, "Minute": m}
               for d in range(1, 6) for h, m in w.intraday_times()]
    log = str(log_path(job, root))
    return {
        "Label": label(job.name),
        "ProgramArguments": [str(python_path(root)), str(root / job.script),
                             *job.args, *(extra or [])],
        "WorkingDirectory": str(root),
        "EnvironmentVariables": {"PYTHONPATH": str(root),
                                 "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        "StartCalendarInterval": cal,
        "RunAtLoad": False,
        "StandardOutPath": log, "StandardErrorPath": log,
        "ProcessType": "Background", "LowPriorityIO": True,
    }


def awake_plist(root: Path = ROOT) -> dict:
    """Companion to fno-intraday on macOS: hold the Mac awake in market hours."""
    log = str(root / "reports" / "fno-awake.log")
    return {
        "Label": label("fno-awake"),
        "ProgramArguments": [str(python_path(root)), str(root / "keep_awake_fno.py")],
        "WorkingDirectory": str(root),
        "EnvironmentVariables": {"PYTHONPATH": str(root),
                                 "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        "StartCalendarInterval": [{"Weekday": d, "Hour": 9, "Minute": 20}
                                  for d in range(1, 6)],
        "RunAtLoad": False, "StandardOutPath": log, "StandardErrorPath": log,
        "ProcessType": "Background",
    }


def legacy_plists(folder: Path = LAUNCH_AGENTS) -> dict[str, Path]:
    """Old per-person agents (com.<name>.stock-signals.*) by new job name."""
    out = {}
    if not folder.exists():
        return out
    for p in folder.glob("*.stock-signals.*.plist"):
        if p.name.startswith(PREFIX + "."):
            continue
        suffix = p.stem.rsplit("stock-signals.", 1)[-1]
        if suffix in LEGACY_SUFFIX:
            out[LEGACY_SUFFIX[suffix]] = p
    return out


def _launchd_unload(p: Path) -> None:
    subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
    if p.exists():
        p.unlink()


def _launchd_load(name: str, payload: dict, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"{label(name)}.plist"
    p.write_bytes(plistlib.dumps(payload))
    subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
    r = subprocess.run(["launchctl", "load", str(p)], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"launchctl load failed for {name}: {r.stderr.strip()}")


# -------------------------------------------------------------------- Linux

def cron_lines(job: Job, root: Path = ROOT, extra: list[str] | None = None) -> list[str]:
    w = job.when
    cmd = (f"cd {_sh(root)} && PYTHONPATH={_sh(root)} {_sh(python_path(root))} "
           f"{_sh(root / job.script)} {' '.join(_sh(a) for a in [*job.args, *(extra or [])])}"
           ).rstrip() + f" >> {_sh(log_path(job, root))} 2>&1"
    if w.kind == "weekdays":
        specs = [f"{w.minute} {w.hour} * * 1-5"]
    elif w.kind == "weekly":
        specs = [f"{w.minute} {w.hour} * * {(DAYS.index(w.day) + 1) % 7}"]
    elif w.kind == "monthly":
        specs = [f"{w.minute} {w.hour} {w.day_of_month} * *"]
    elif w.kind == "quarterly":
        specs = [f"{w.minute} {w.hour} {w.day_of_month} {','.join(map(str, w.months))} *"]
    else:
        by_min: dict[int, list[int]] = {}
        for h, m in w.intraday_times():
            by_min.setdefault(m, []).append(h)
        specs = [f"{m} {','.join(map(str, hs))} * * 1-5" for m, hs in sorted(by_min.items())]
    return [f"{s} {cmd} {CRON_MARK}{job.name}" for s in specs]


def _sh(x) -> str:
    s = str(x)
    return s if s and all(c.isalnum() or c in "/._-=" for c in s) else "'" + s.replace("'", "'\\''") + "'"


def merge_crontab(current: str, name: str, lines: list[str] | None) -> str:
    """The crontab with `name`'s lines replaced by `lines` (None removes them)."""
    keep = [l for l in current.splitlines() if not l.rstrip().endswith(f"{CRON_MARK}{name}")]
    out = keep + (lines or [])
    return "\n".join(out).strip("\n") + "\n" if out else ""


def _crontab_read() -> str:
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def _crontab_write(text: str) -> None:
    r = subprocess.run(["crontab", "-"], input=text, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"crontab failed: {r.stderr.strip()}")


# ------------------------------------------------------------------ Windows

def windows_cmd(job: Job, root: Path = ROOT, extra: list[str] | None = None) -> str:
    """The .cmd wrapper a task runs: sets PYTHONPATH and appends to the log."""
    args = " ".join(f'"{a}"' if " " in a else a for a in [*job.args, *(extra or [])])
    return ("@echo off\r\n"
            f'cd /d "{root}"\r\n'
            f'set "PYTHONPATH={root}"\r\n'
            f'"{python_path(root)}" "{root / job.script}" {args} '
            f'>> "{log_path(job, root)}" 2>&1\r\n')


def task_xml(job: Job, wrapper: Path) -> str:
    """Task Scheduler XML. StartWhenAvailable runs a start missed while the PC
    was off as soon as it is back."""
    w = job.when
    start = f"2026-01-01T{w.hour:02d}:{w.minute:02d}:00"
    days = lambda ds: "".join(f"<{d}/>" for d in ds)  # noqa: E731
    full = {"MON": "Monday", "TUE": "Tuesday", "WED": "Wednesday", "THU": "Thursday",
            "FRI": "Friday", "SAT": "Saturday", "SUN": "Sunday"}
    rep = ""
    if w.kind in ("weekdays", "intraday"):
        sched = (f"<ScheduleByWeek><DaysOfWeek>{days(full[d] for d in WEEKDAYS)}"
                 "</DaysOfWeek><WeeksInterval>1</WeeksInterval></ScheduleByWeek>")
        if w.kind == "intraday":
            n = len(w.intraday_times())
            rep = (f"<Repetition><Interval>PT30M</Interval><Duration>PT{(n - 1) * 30 + 1}M"
                   "</Duration><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>")
    elif w.kind == "weekly":
        sched = (f"<ScheduleByWeek><DaysOfWeek><{full[w.day]}/></DaysOfWeek>"
                 "<WeeksInterval>1</WeeksInterval></ScheduleByWeek>")
    else:
        months = w.months or tuple(range(1, 13))
        names = "".join(f"<{dt.date(2024, m, 1):%B}/>" for m in months)
        sched = (f"<ScheduleByMonth><DaysOfMonth><Day>{w.day_of_month}</Day></DaysOfMonth>"
                 f"<Months>{names}</Months></ScheduleByMonth>")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>{escape(job.title)}: {escape(job.what)}</Description></RegistrationInfo>
  <Triggers><CalendarTrigger>{rep}<StartBoundary>{start}</StartBoundary><Enabled>true</Enabled>{sched}</CalendarTrigger></Triggers>
  <Settings>
    <StartWhenAvailable>true</StartWhenAvailable>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT4H</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
  </Settings>
  <Actions><Exec><Command>{escape(str(wrapper))}</Command><WorkingDirectory>{escape(str(wrapper.parent))}</WorkingDirectory></Exec></Actions>
</Task>
"""


def _task_name(name: str) -> str:
    return f"\\{TASK_FOLDER}\\{name}"


# --------------------------------------------------------------- operations

def supported(job: Job, osn: str | None = None) -> bool:
    return (osn or os_name()) in job.platforms


def install(name: str, root: Path = ROOT, extra: list[str] | None = None,
            when: When | None = None) -> str:
    """Install one job on this machine. Returns a one-line description."""
    job = JOBS[name]
    if when is not None:
        job = Job(**{**job.__dict__, "when": when})
    osn = os_name()
    if not supported(job, osn):
        raise RuntimeError(f"{name} is not supported on {osn}")
    (root / "reports").mkdir(exist_ok=True)
    if osn == "macos":
        old = legacy_plists().get(name)
        if old:
            _launchd_unload(old)
        _launchd_load(name, plist(job, root, extra), LAUNCH_AGENTS)
        if name == "fno-intraday":
            old = legacy_plists().get("fno-awake")
            if old:
                _launchd_unload(old)
            _launchd_load("fno-awake", awake_plist(root), LAUNCH_AGENTS)
    elif osn == "linux":
        _crontab_write(merge_crontab(_crontab_read(), name, cron_lines(job, root, extra)))
    elif osn == "windows":
        folder = root / "reports" / "tasks"
        folder.mkdir(parents=True, exist_ok=True)
        wrapper = folder / f"{name}.cmd"
        wrapper.write_text(windows_cmd(job, root, extra), encoding="utf-8")
        xml = folder / f"{name}.xml"
        xml.write_text(task_xml(job, wrapper), encoding="utf-16")
        r = subprocess.run(["schtasks", "/Create", "/TN", _task_name(name), "/XML",
                            str(xml), "/F"], capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(f"schtasks failed for {name}: {r.stderr.strip()}")
    else:
        raise RuntimeError(f"no scheduler support for {osn}")
    return f"{job.title}: {job.when.describe()}"


def remove(name: str) -> bool:
    """Remove one job. True if something was removed."""
    osn = os_name()
    if osn == "macos":
        found = False
        names = [name] + (["fno-awake"] if name == "fno-intraday" else [])
        for n in names:
            for p in (LAUNCH_AGENTS / f"{label(n)}.plist", legacy_plists().get(n)):
                if p is not None and p.exists():
                    _launchd_unload(p)
                    found = True
        return found
    if osn == "linux":
        cur = _crontab_read()
        if f"{CRON_MARK}{name}" not in cur:
            return False
        _crontab_write(merge_crontab(cur, name, None))
        return True
    if osn == "windows":
        r = subprocess.run(["schtasks", "/Delete", "/TN", _task_name(name), "/F"],
                           capture_output=True, text=True)
        return r.returncode == 0
    return False


def _job_root(name: str) -> Path | None:
    """The project folder an installed job runs in, or None if not installed."""
    osn = os_name()
    if osn == "macos":
        for p in (LAUNCH_AGENTS / f"{label(name)}.plist", legacy_plists().get(name)):
            if p is not None and p.exists():
                try:
                    return Path(plistlib.loads(p.read_bytes()).get("WorkingDirectory", ""))
                except Exception:
                    return Path("?")
        return None
    if osn == "linux":
        for line in _crontab_read().splitlines():
            if line.rstrip().endswith(f"{CRON_MARK}{name}") and " cd " in line:
                return Path(line.split(" cd ", 1)[1].split(" && ", 1)[0].strip("'"))
        return None
    if osn == "windows":
        r = subprocess.run(["schtasks", "/Query", "/TN", _task_name(name)],
                           capture_output=True, text=True)
        return ROOT if r.returncode == 0 else None
    return None


def installed(name: str, root: Path = ROOT) -> bool:
    """Installed for this copy of the project."""
    r = _job_root(name)
    return r is not None and r.resolve() == root.resolve()


def installed_elsewhere(name: str, root: Path = ROOT) -> Path | None:
    """The other folder a job with this name runs in, if any."""
    r = _job_root(name)
    return r if r is not None and r.resolve() != root.resolve() else None


def manual_instructions(job: Job, root: Path = ROOT) -> str:
    """For a system without a supported scheduler: what to run, and when."""
    return (f"{job.title} -- {job.when.describe()}:\n"
            f"    cd {root}\n"
            f"    PYTHONPATH=. {python_path(root)} {job.script} {' '.join(job.args)}".rstrip())

