"""Set up stock-signals on a new computer, step by step.

    python3 setup_project.py            # guided: asks before each step
    python3 setup_project.py --check    # what is set up, missing or stale

Run it with any Python 3.11 or newer; it creates its own environment. Safe to
run again at any time: nothing that exists is overwritten, and every data
download picks up where it stopped.

The steps:
  1. Check this computer: Python, free disk space, and whether NSE answers.
  2. Create a private Python environment (.venv) and install the packages.
  3. Create the folders and the empty databases.
  4. Create the files you fill in -- holdings, mutual funds, watch list -- as
     Excel workbooks, each with a "How to fill" sheet.
  5. Download market data: a quick starter set, everything (well over an
     hour), or the datasets you pick.
  6. Schedule the jobs that keep the data fresh (daily, weekly, monthly,
     quarterly), if you want them.
  7. Check everything and say how to open the dashboard.

Non-interactive: --yes takes the default answer everywhere; --data and --jobs
choose without asking (e.g. --data starter --jobs none).
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MIN_PYTHON = (3, 11)
MIN_FREE_GB = 2.0
WINDOWS = platform.system() == "Windows"
VENV_PY = ROOT / ".venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")


# ------------------------------------------------------------------ talking

class UI:
    def __init__(self, yes: bool = False):
        self.yes = yes

    def head(self, n: int, title: str) -> None:
        print(f"\n{'=' * 70}\n Step {n}. {title}\n{'=' * 70}")

    def say(self, text: str = "") -> None:
        print(text)

    def ok(self, text: str) -> None:
        print(f"  [ok]   {text}")

    def warn(self, text: str) -> None:
        print(f"  [!]    {text}")

    def ask(self, question: str, default: bool = True) -> bool:
        if self.yes or not sys.stdin.isatty():
            print(f"{question} -> {'yes' if default else 'no'} (default)")
            return default
        hint = "[Y/n]" if default else "[y/N]"
        while True:
            a = input(f"{question} {hint} ").strip().lower()
            if not a:
                return default
            if a in ("y", "yes", "n", "no"):
                return a.startswith("y")

    def choose(self, question: str, options: list[tuple[str, str]], default: str) -> str:
        print(question)
        for key, text in options:
            print(f"   {key:>10}  {text}")
        if self.yes or not sys.stdin.isatty():
            print(f"-> {default} (default)")
            return default
        keys = {k for k, _ in options}
        while True:
            a = input(f"Your choice [{default}]: ").strip().lower() or default
            if a in keys:
                return a
            print(f"Please type one of: {', '.join(sorted(keys))}")


# ------------------------------------------------------------ 1. preflight

def free_gb(path: Path = ROOT) -> float:
    return shutil.disk_usage(path).free / 1e9


NSE_PROBE = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"


def nse_reachable(timeout: float = 15.0) -> bool:
    """NSE's archive host, where the daily files come from. (The main site
    refuses scripted requests even when the downloads work.)"""
    req = urllib.request.Request(NSE_PROBE, headers={"User-Agent": "Mozilla/5.0",
                                                     "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def preflight(ui: UI) -> bool:
    ui.head(1, "Check this computer")
    v = sys.version_info
    if v[:2] < MIN_PYTHON:
        ui.warn(f"Python {v.major}.{v.minor} is too old; 3.11 or newer is needed.\n"
                "         Install it from https://www.python.org/downloads/ and run\n"
                "         this again with that Python, e.g.  python3.12 setup_project.py\n"
                "         (GETTING_STARTED.md has step-by-step help.)")
        return False
    ui.ok(f"Python {v.major}.{v.minor}.{v.micro} on {platform.system()}")
    gb = free_gb()
    if gb < MIN_FREE_GB:
        ui.warn(f"Only {gb:.1f} GB free. All the data takes about 1.5 GB; the starter "
                "set about 0.3 GB.")
    else:
        ui.ok(f"{gb:.0f} GB free (all the data needs about 1.5 GB)")
    if nse_reachable():
        ui.ok("NSE's download server answers from this connection")
    else:
        ui.warn("NSE's download server did not answer. NSE blocks some networks outside "
                "India and many cloud servers; downloads may fail. Setup will "
                "continue -- try the downloads again later or from another network.")
    return True


# ---------------------------------------------------------- 2. environment

REQUIRED_MODULES = ("pandas", "numpy", "openpyxl", "yaml", "streamlit",
                    "jugaad_data", "yfinance")


def in_venv() -> bool:
    """Running on .venv's interpreter? (sys.prefix, not the executable: a venv's
    python is a link to the same binary as the system one.)"""
    return Path(sys.prefix).resolve() == (ROOT / ".venv").resolve()


def relaunch(argv) -> int:
    """Continue this run on .venv's python, without repeating the checks."""
    env = {**os.environ, "STOCK_SIGNALS_PREFLIGHT_DONE": "1"}
    return subprocess.run([str(VENV_PY), __file__, *(argv if argv is not None
                                                     else sys.argv[1:])], env=env).returncode


def missing_modules(python: Path) -> list[str]:
    code = ("import importlib.util,sys;"
            f"print(','.join(m for m in {REQUIRED_MODULES!r} "
            "if importlib.util.find_spec(m) is None))")
    r = subprocess.run([str(python), "-c", code], capture_output=True, text=True)
    out = r.stdout.strip()
    return [m for m in out.split(",") if m] if r.returncode == 0 else list(REQUIRED_MODULES)


def environment(ui: UI) -> bool:
    """Make sure .venv exists with the packages. True when ready."""
    ui.head(2, "Python environment and packages")
    if not VENV_PY.exists():
        ui.say("The project keeps its packages in a private folder, .venv, so it\n"
               "does not change anything else on this computer. Creating it and\n"
               "installing the packages takes 2-5 minutes and about 600 MB.")
        if not ui.ask("Create .venv and install the packages now?"):
            ui.warn("Skipped. Create it yourself with:\n"
                    f"         {sys.executable} -m venv .venv\n"
                    f"         {VENV_PY} -m pip install -r requirements.txt")
            return False
        subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], check=True)
    missing = missing_modules(VENV_PY)
    if missing:
        ui.say(f"Installing packages ({', '.join(missing)} missing) ...")
        r = subprocess.run([str(VENV_PY), "-m", "pip", "install", "-q", "-r",
                            str(ROOT / "requirements.txt")])
        if r.returncode:
            ui.warn("Package install failed. The message above says why; fix it and "
                    "run this again.")
            return False
        missing = missing_modules(VENV_PY)
        if missing:
            ui.warn(f"Still missing after install: {', '.join(missing)}")
            return False
    ui.ok(f"Packages installed in {VENV_PY.parent.parent.name}/")
    return True


# ----------------------------------------------------- 3. folders and DBs

FOLDERS = ["config", "data", "data/cache", "data/mf", "reports", "history"]


def folders_and_databases(ui: UI) -> None:
    ui.head(3, "Folders and empty databases")
    for f in FOLDERS:
        (ROOT / f).mkdir(parents=True, exist_ok=True)
    ui.ok("folders: " + ", ".join(FOLDERS))
    import decision_log
    from data import bhavcopy as bc
    from data import fo_bhavcopy as fb
    from data import store
    from mf import navdb
    dbs = [("data/signals.db", "prices, indices, the advisor's decision log",
            lambda: (store.connect(), decision_log.connect())),
           ("data/market.db", "every NSE share, every day (the full market)",
            lambda: bc.connect(str(ROOT / "data" / "market.db"))),
           ("data/fo_history.db", "futures and options history",
            lambda: fb.connect()),
           ("data/mf/mf.db", "mutual fund NAVs", lambda: navdb.connect())]
    for name, what, make in dbs:
        existed = (ROOT / name).exists()
        cons = make()
        for c in cons if isinstance(cons, tuple) else (cons,):
            c.close()
        ui.ok(f"{name:20} {'already there' if existed else 'created'} -- {what}")


# ------------------------------------------------------------- 4. templates

def templates(ui: UI, config: Path | None = None) -> list[Path]:
    ui.head(4, "Files you fill in")
    import userfiles as uf
    config = config or ROOT / "config"
    made = []
    for spec in uf.SPECS.values():
        have = uf.find(spec.stem, config)
        if have is not None:
            ui.ok(f"config/{have.name} already there -- left as it is")
            continue
        p = uf.template(spec, config)
        made.append(p)
        ui.ok(f"config/{p.name} created -- {spec.title.lower()}")
    for w in uf.conflicts(config):
        ui.warn(w)
    ui.say("\nEach workbook has a 'How to fill' sheet. Fill them in Excel, Numbers\n"
           "or LibreOffice whenever you like; the app reads them on its next load.\n"
           "They stay on this computer: config/ files with your positions are in\n"
           ".gitignore and are never uploaded, even if you share the project.\n"
           "Shortcut for holdings: download them from your broker and run\n"
           "  import_kite_holdings.py   (it fills config/holdings.xlsx for you).")
    return made


# ----------------------------------------------------------------- 5. data

@dataclass
class Dataset:
    key: str
    title: str
    what: str
    pages: str
    minutes: str
    commands: list[list[str]]
    starter: bool = False


DATASETS = [
    Dataset("market", "Full-market history, 10 years",
            "Every NSE share on every trading day since 2016, including companies "
            "that later delisted. Ranks the market and powers every backtest.",
            "Stocks, Today, Momentum, Market overview, Swing", "60-90",
            [["run_market_ingest.py", "--years", "10"]]),
    Dataset("market-recent", "Full-market history, last 2 years (quick start)",
            "The same, but only two years: enough for the live pages. Run 'market' "
            "later to extend it to 10 years for the backtests.",
            "Stocks, Today, Momentum, Market overview", "15-25",
            [["run_market_ingest.py", "--years", "2"]], starter=True),
    Dataset("prices", "Share prices for the Nifty 500, your holdings and the index",
            "Daily prices behind the trade plans, stops and the market regime.",
            "Stocks, Today, Swing, Analyze", "10-20",
            [["run_backfill.py", "--universe", "nifty500", "--years", "10"],
             ["run_backfill.py", "--years", "10"]], starter=True),
    Dataset("fundamentals", "Company fundamentals for what you follow",
            "Quarterly results, ratios and promoter holding from NSE filings and "
            "Yahoo, for the quality scores.",
            "Stocks, Analyze, GARP / QARP", "5-20",
            [["fetch_fundamentals.py"]], starter=True),
    Dataset("mf", "Mutual fund NAVs",
            "Month-end NAVs of every direct-growth fund, to compare each fund "
            "with its category.", "Mutual funds, Compare funds", "about 25",
            [["run_mf.py", "--refresh", "--no-holdings"]], starter=True),
    Dataset("fno", "Futures and options history (2024 onwards)",
            "Daily option prices for Nifty and ~200 stocks; behind Today's expected "
            "ranges and the F&O page. Then measures how often the ranges held.",
            "Today, F&O", "20-40",
            [["run_fno_backtest.py", "--ingest"], ["run_range_backtest.py"]]),
    Dataset("fno-old", "Index option history 2019-2023 (optional)",
            "Older index-option history, for a longer validation of the F&O ranges.",
            "F&O (validation only)", "about 60",
            [["run_fno_backtest.py", "--ingest-old"]]),
    Dataset("results", "Quarterly results for what you follow",
            "Scores new results filings for your holdings and watch list.",
            "Quarterly results, Today", "2-10", [["run_results.py"]]),
    Dataset("picker", "Nifty 200 filings for GARP / QARP",
            "Six years of quarterly filings for the Nifty 200.",
            "GARP / QARP", "20-40", [["run_picker.py", "--refresh"]]),
    Dataset("evidence", "Backtests behind the evidence lines",
            "Re-runs the swing and mutual fund backtests so each page can say how "
            "well its signals held up. Needs the market and fund data first.",
            "Swing, Mutual funds", "5-15",
            [["run_swing.py", "--backtest"], ["run_mf.py", "--backtest"]]),
]
BY_KEY = {d.key: d for d in DATASETS}
ORDER = [d.key for d in DATASETS]


def pick_datasets(ui: UI, choice: str | None) -> list[Dataset]:
    if choice is None:
        ui.say("Each dataset, roughly how long the first download takes, and what it is for:\n")
        for d in DATASETS:
            star = " (starter)" if d.starter else ""
            ui.say(f"  {d.key:14} {d.minutes:>7} min  {d.title}{star}\n"
                   f"  {'':14} {'':>7}      {d.what}\n"
                   f"  {'':14} {'':>7}      Used by: {d.pages}")
        starter_min = "about 1-1.5 hours"
        ui.say("\nEverything takes well over 2 hours on a typical connection. Downloads\n"
               "can be stopped (Ctrl+C) and resumed by running setup again; the\n"
               "scheduled jobs also fill gaps on their own.")
        choice = ui.choose("\nWhat should be downloaded now?",
                           [("starter", f"the starter set ({starter_min}): live pages work"),
                            ("all", "everything (2-4 hours)"),
                            ("pick", "choose datasets one by one"),
                            ("none", "nothing now")], "starter")
    if choice == "none":
        return []
    if choice == "starter":
        return [d for d in DATASETS if d.starter]
    if choice == "all":
        return [d for d in DATASETS if d.key != "market-recent"]
    if choice == "pick":
        return [d for d in DATASETS
                if ui.ask(f"  {d.title} ({d.minutes} min)?", default=d.starter)]
    keys = [k.strip() for k in choice.split(",") if k.strip()]
    bad = [k for k in keys if k not in BY_KEY]
    if bad:
        raise SystemExit(f"unknown dataset(s): {', '.join(bad)}. "
                         f"Choose from: {', '.join(ORDER)}")
    return sorted((BY_KEY[k] for k in keys), key=lambda d: ORDER.index(d.key))


def run_script(args: list[str]) -> int:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    return subprocess.run([sys.executable, *args], cwd=str(ROOT), env=env).returncode


def download(ui: UI, choice: str | None) -> dict[str, str]:
    ui.head(5, "Market data")
    chosen = pick_datasets(ui, choice)
    results: dict[str, str] = {}
    for i, d in enumerate(chosen, 1):
        ui.say(f"\n--- [{i}/{len(chosen)}] {d.title} (about {d.minutes} min) ---")
        start = time.time()
        status = "done"
        for cmd in d.commands:
            try:
                code = run_script(cmd)
            except KeyboardInterrupt:
                ui.warn("Stopped. Run setup again to resume; nothing is lost.")
                results[d.key] = "stopped"
                return results
            if code:
                status = f"failed ({' '.join(cmd)} exit {code})"
                break
        mins = (time.time() - start) / 60
        results[d.key] = status
        (ui.ok if status == "done" else ui.warn)(f"{d.title}: {status} in {mins:.0f} min")
    return results


# ----------------------------------------------------------------- 6. jobs

def schedule(ui: UI, choice: str | None) -> list[str]:
    ui.head(6, "Jobs that keep the data fresh")
    import jobs
    osn = jobs.os_name()
    ui.say("These run on their own and only fetch what is missing. They run while\n"
           "this computer is on; a run missed while it was off happens at the next\n"
           f"chance{'' if osn != 'linux' else ' (on Linux, at the next scheduled time)'}.\n")
    for j in jobs.JOBS.values():
        tag = "recommended" if j.recommended else "optional"
        ui.say(f"  {j.name:13} {j.when.describe()} ({tag})\n"
               f"  {'':13} {j.what}\n  {'':13} Used by: {j.feeds}")
    if choice is None:
        choice = ui.choose("\nInstall which jobs?",
                           [("recommended", "daily, weekly, monthly and quarterly"),
                            ("pick", "choose one by one"),
                            ("none", "none for now (install later with install_schedule.py)")],
                           "recommended")
    if choice == "none":
        return []
    if choice == "recommended":
        names = [n for n, j in jobs.JOBS.items() if j.recommended]
    elif choice == "pick":
        names = [n for n, j in jobs.JOBS.items()
                 if ui.ask(f"  Install {n} ({j.when.describe()})?", default=j.recommended)]
    else:
        names = [n.strip() for n in choice.split(",") if n.strip()]
    done = []
    for n in names:
        job = jobs.JOBS[n]
        if not jobs.supported(job, osn):
            ui.warn(f"{n}: not available on {osn}. Run it yourself:\n"
                    + jobs.manual_instructions(job))
            continue
        try:
            ui.ok("installed " + jobs.install(n, ROOT))
            done.append(n)
        except Exception as exc:          # noqa: BLE001 -- say what to do instead
            ui.warn(f"{n}: could not install ({exc}). Run it yourself:\n"
                    + jobs.manual_instructions(job))
    if done:
        ui.say(f"\nCheck them: python install_schedule.py --status\n"
               f"Remove one: python install_schedule.py --remove <name>\n"
               f"Logs go to {ROOT / 'reports'}")
    return done


# ---------------------------------------------------------------- 7. check

def _count(db: Path, sql: str):
    import sqlite3
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(db)
        v = con.execute(sql).fetchone()
        con.close()
        return v
    except Exception:
        return None


def check(ui: UI) -> list[str]:
    """What is set up, missing or stale. Returns the next steps."""
    ui.head(7, "Check")
    nxt = []
    if not VENV_PY.exists() or missing_modules(VENV_PY):
        ui.warn("packages: not installed -- run setup_project.py")
        return ["Run python3 setup_project.py"]
    ui.ok("packages installed")
    import userfiles as uf
    for spec in uf.SPECS.values():
        p = uf.find(spec.stem)
        if p is None:
            ui.warn(f"{spec.stem}: no file -- run setup_project.py to create it")
            continue
        _, rows = uf.read(p)
        n = len([r for r in rows if any(v for v in r.values())])
        (ui.ok if n else ui.warn)(f"{p.relative_to(ROOT)}: {n} row(s)"
                                  + ("" if n else " -- fill it in when you are ready"))
        if not n and spec.stem == "holdings":
            nxt.append("Fill in config/holdings.xlsx, or run import_kite_holdings.py "
                       "on a broker download")
    for w in uf.conflicts():
        ui.warn(w)
    data = [("Full market", ROOT / "data" / "market.db",
             "SELECT MIN(date), MAX(date), COUNT(DISTINCT date) FROM market", "market"),
            ("Share prices", ROOT / "data" / "signals.db",
             "SELECT MIN(date), MAX(date), COUNT(DISTINCT symbol) FROM prices", "prices"),
            ("F&O history", ROOT / "data" / "fo_history.db",
             "SELECT MIN(date), MAX(date), COUNT(*) FROM fo_days", "fno"),
            ("Fund NAVs", ROOT / "data" / "mf" / "mf.db",
             "SELECT MIN(month), MAX(month), COUNT(*) FROM nav", "mf")]
    for name, db, sql, key in data:
        v = _count(db, sql)
        if not v or not v[1]:
            ui.warn(f"{name:13} empty -- python3 setup_project.py --data {key}")
            nxt.append(f"Download {name}: python3 setup_project.py --data {key}")
        else:
            ui.ok(f"{name:13} {v[0]} to {v[1]}")
    fund = ROOT / "config" / "fundamentals.csv"
    (ui.ok if fund.exists() else ui.warn)(
        "Fundamentals  " + ("on file" if fund.exists()
                            else "none yet -- python3 setup_project.py --data fundamentals"))
    import jobs
    on = [n for n in jobs.JOBS if jobs.installed(n, ROOT)]
    (ui.ok if on else ui.warn)("Scheduled jobs: " + (", ".join(on) if on else
                                                     "none -- python install_schedule.py --list"))
    for n in jobs.JOBS:
        other = jobs.installed_elsewhere(n, ROOT)
        if other:
            ui.warn(f"the {n} job runs another copy of this project ({other}); "
                    "installing it here would move it to this copy")
    run = f"{VENV_PY.relative_to(ROOT)} run_ui.py"
    nxt.append(f"Open the dashboard: {run}   (then http://localhost:8501)")
    return nxt


# ----------------------------------------------------------------- driver

def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)     # keep order when piped
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="only report what is set up")
    ap.add_argument("--yes", action="store_true", help="take the default answer everywhere")
    ap.add_argument("--data", default=None,
                    help="starter | all | none | pick | a comma list, e.g. prices,mf")
    ap.add_argument("--jobs", default=None,
                    help="recommended | none | pick | a comma list, e.g. daily,weekly")
    a = ap.parse_args(argv)
    ui = UI(yes=a.yes)

    # Everything after step 2 needs the packages, so it runs inside .venv.
    if VENV_PY.exists() and not in_venv() and not missing_modules(VENV_PY):
        return relaunch(argv)
    print("stock-signals setup -- decision support for NSE investors, not advice.")
    if a.check:
        if not in_venv():
            print("Packages are not installed yet: run  python3 setup_project.py")
            return 1
        for line in check(ui):
            print(f"  next: {line}")
        return 0

    if not os.environ.get("STOCK_SIGNALS_PREFLIGHT_DONE") and not preflight(ui):
        return 1
    if not in_venv():
        if not environment(ui):
            return 1
        return relaunch(argv)
    ui.head(2, "Python environment and packages")
    ui.ok(f"running inside {VENV_PY.parent.parent.name}/ with the packages installed")
    folders_and_databases(ui)
    templates(ui)
    results = download(ui, a.data)
    schedule(ui, a.jobs)
    nxt = check(ui)
    failed = [k for k, v in results.items() if v != "done"]
    print(f"\n{'=' * 70}\n Done.{' Some downloads did not finish: ' + ', '.join(failed) if failed else ''}")
    print(" Next:")
    for line in nxt:
        print(f"   - {line}")
    print("\n New here? GETTING_STARTED.md walks through the dashboard in plain words.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
