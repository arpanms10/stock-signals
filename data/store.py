"""SQLite cache for prices and corporate actions.

Design decision worth stating: we store *raw* prices and corporate actions
separately, and apply the adjustment at read time. Storing pre-adjusted prices
would look simpler, but the moment a new split is announced every previously
stored row becomes silently wrong and there is no way to tell from the data.
Raw + adjust-on-read is always correct at the cost of a cheap multiply.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "signals.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    prev_close REAL, vwap REAL,
    volume REAL, trades REAL, delivery_qty REAL, delivery_pct REAL,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS index_prices (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS corporate_actions (
    symbol TEXT NOT NULL,
    ex_date TEXT NOT NULL,
    factor REAL NOT NULL,
    kind TEXT,
    subject TEXT,
    PRIMARY KEY (symbol, ex_date, subject)
);
CREATE TABLE IF NOT EXISTS fetch_log (
    scope TEXT NOT NULL,
    symbol TEXT NOT NULL,
    last_date TEXT,
    fetched_at TEXT,
    PRIMARY KEY (scope, symbol)
);
"""

PRICE_COLS = ["symbol", "date", "open", "high", "low", "close", "prev_close",
              "vwap", "volume", "trades", "delivery_qty", "delivery_pct"]
INDEX_COLS = ["symbol", "date", "open", "high", "low", "close"]


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=30)
    # WAL lets the dashboard (and you, poking at the DB) read while the nightly
    # job is writing. The default journal mode locks readers out entirely, which
    # shows up as the dashboard hanging exactly when data is being refreshed.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    con.executescript(SCHEMA)
    return con


def _upsert(con, table: str, df: pd.DataFrame, cols: list[str]) -> int:
    if df.empty:
        return 0
    frame = df.copy()
    for c in cols:
        if c not in frame.columns:
            frame[c] = None
    frame = frame[cols]
    frame["date"] = frame["date"].astype(str)
    placeholders = ",".join("?" * len(cols))
    con.executemany(
        f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({placeholders})",
        frame.itertuples(index=False, name=None),
    )
    con.commit()
    return len(frame)


def save_prices(con, df: pd.DataFrame) -> int:
    return _upsert(con, "prices", df, PRICE_COLS)


def save_index_prices(con, df: pd.DataFrame) -> int:
    return _upsert(con, "index_prices", df, INDEX_COLS)


def save_actions(con, actions) -> int:
    rows = [(a.symbol, a.ex_date.isoformat(), a.factor, a.kind, a.subject)
            for a in actions]
    if not rows:
        return 0
    con.executemany(
        "INSERT OR REPLACE INTO corporate_actions "
        "(symbol, ex_date, factor, kind, subject) VALUES (?,?,?,?,?)", rows)
    con.commit()
    return len(rows)


def load_actions(con, symbol: str):
    from .corporate_actions import Action
    cur = con.execute(
        "SELECT symbol, ex_date, factor, kind, subject FROM corporate_actions "
        "WHERE symbol=? ORDER BY ex_date", (symbol.upper(),))
    return [Action(r[0], dt.date.fromisoformat(r[1]), r[2], r[3], r[4])
            for r in cur.fetchall()]


def load_prices(con, symbol: str, adjusted: bool = True) -> pd.DataFrame:
    """Price history for one symbol. Adjusted for splits/bonuses by default."""
    df = pd.read_sql_query(
        "SELECT * FROM prices WHERE symbol=? ORDER BY date", con, params=(symbol.upper(),))
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"]).dt.date
    if adjusted:
        from .corporate_actions import adjust
        df = adjust(df, load_actions(con, symbol))
    return df.reset_index(drop=True)


def load_index(con, symbol: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT * FROM index_prices WHERE symbol=? ORDER BY date", con,
        params=(symbol,))
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def last_date(con, scope: str, symbol: str) -> dt.date | None:
    table = "index_prices" if scope == "index" else "prices"
    cur = con.execute(f"SELECT MAX(date) FROM {table} WHERE symbol=?", (symbol,))
    row = cur.fetchone()[0]
    return dt.date.fromisoformat(row) if row else None


def first_date(con, scope: str, symbol: str) -> dt.date | None:
    table = "index_prices" if scope == "index" else "prices"
    row = con.execute(f"SELECT MIN(date) FROM {table} WHERE symbol=?", (symbol,)).fetchone()[0]
    return dt.date.fromisoformat(row) if row else None


def mark_fetched(con, scope: str, symbol: str, last: dt.date | None) -> None:
    con.execute(
        "INSERT OR REPLACE INTO fetch_log (scope, symbol, last_date, fetched_at) "
        "VALUES (?,?,?,?)",
        (scope, symbol, last.isoformat() if last else None,
         dt.datetime.now().isoformat(timespec="seconds")))
    con.commit()


def fetched_at(con, scope: str, symbol: str) -> dt.datetime | None:
    cur = con.execute("SELECT fetched_at FROM fetch_log WHERE scope=? AND symbol=?",
                      (scope, symbol))
    row = cur.fetchone()
    return dt.datetime.fromisoformat(row[0]) if row and row[0] else None
