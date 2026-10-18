"""Month-end NAVs for every Direct Growth scheme since 2013, in data/mf/mf.db.

One AMFI history request per month, covering the last week so a month that
ends on a holiday still has each scheme's last NAV. The latest NAVAll adds the
current month's point. Everything downstream works on month-end series, live
and backtest alike, so what the backtest tests is what the live view shows.

    schemes(code, name, amc, category, first_month, last_month)
    nav(code, month, date, nav)          one row per scheme per month

`last_month` is the last month a scheme had a NAV. A scheme that stopped
before the newest month was merged or closed; the backtest needs to know.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pandas as pd

from mf import amfi

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "mf" / "mf.db"
FIRST_MONTH = pd.Period("2013-01", "M")   # direct plans launched 1 Jan 2013

SCHEMA = """
CREATE TABLE IF NOT EXISTS schemes (
    code INTEGER PRIMARY KEY,
    name TEXT, amc TEXT, category TEXT,
    first_month TEXT, last_month TEXT
);
CREATE TABLE IF NOT EXISTS nav (
    code INTEGER NOT NULL,
    month TEXT NOT NULL,
    date TEXT NOT NULL,
    nav REAL NOT NULL,
    PRIMARY KEY (code, month)
);
CREATE TABLE IF NOT EXISTS fetched (month TEXT PRIMARY KEY, rows INTEGER, at TEXT);
"""


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def month_window(month: pd.Period) -> tuple[dt.date, dt.date]:
    end = month.end_time.date()
    return end - dt.timedelta(days=6), end


def save(con, df: pd.DataFrame) -> int:
    """Rows from amfi.parse -> nav (last NAV per month) and schemes."""
    if df.empty:
        return 0
    df = df.sort_values("date").copy()
    df["month"] = pd.to_datetime(df["date"]).dt.to_period("M").astype(str)
    last = df.groupby(["code", "month"], as_index=False).last()
    con.executemany(
        "INSERT OR REPLACE INTO nav(code, month, date, nav) VALUES (?,?,?,?)",
        [(int(r.code), r.month, str(r.date), float(r.nav)) for r in last.itertuples()])
    for r in last.groupby("code").last().itertuples():
        con.execute(
            """INSERT INTO schemes(code, name, amc, category, first_month, last_month)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(code) DO UPDATE SET
                 name = excluded.name, amc = excluded.amc,
                 -- the newest label wins, but never overwrite a category
                 -- with nothing
                 category = COALESCE(excluded.category, schemes.category),
                 first_month = MIN(schemes.first_month, excluded.first_month),
                 last_month = MAX(schemes.last_month, excluded.last_month)""",
            (int(r.Index), r.name, r.amc, r.category, r.month, r.month))
    con.commit()
    return len(last)


def months_missing(con, through: pd.Period) -> list[pd.Period]:
    have = {m for (m,) in con.execute("SELECT month FROM fetched")}
    out, m = [], FIRST_MONTH
    while m <= through:
        if str(m) not in have:
            out.append(m)
        m += 1
    return out


def refresh(con, today: dt.date | None = None, log=print) -> None:
    """Fetch every complete month not yet on file, then the latest NAVs."""
    today = today or dt.date.today()
    last_complete = pd.Period(today, "M") - 1
    missing = months_missing(con, last_complete)
    for i, m in enumerate(missing, 1):
        start, end = month_window(m)
        df = amfi.fetch_history(start, end)
        n = save(con, df)
        con.execute("INSERT OR REPLACE INTO fetched VALUES (?,?,?)",
                    (str(m), n, dt.datetime.now().isoformat(timespec="seconds")))
        con.commit()
        log(f"  {m}: {n} schemes ({i}/{len(missing)})")
    n = save(con, amfi.fetch_latest())
    log(f"  latest NAVs: {n} schemes")


def schemes(con) -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM schemes", con)


def panel(con, codes=None) -> pd.DataFrame:
    """Month x code NAV matrix, months as a PeriodIndex."""
    q = "SELECT code, month, nav FROM nav"
    if codes is not None:
        codes = [int(c) for c in codes]
        q += f" WHERE code IN ({','.join('?' * len(codes))})"
    df = pd.read_sql(q, con, params=codes)
    wide = df.pivot(index="month", columns="code", values="nav").sort_index()
    wide.index = pd.PeriodIndex(wide.index, freq="M")
    return wide


def last_dates(con) -> dict[int, str]:
    """Each scheme's newest NAV date, for the 'as of' line."""
    return dict(con.execute("SELECT code, MAX(date) FROM nav GROUP BY code"))
