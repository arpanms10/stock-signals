"""data/research.db: the platform's own records, kept rather than overwritten.

  audit_log         every change to your holdings file made through the app
                    (imports, with what changed and from which file)
  portfolio_snapshot / snapshot_position
                    the book as valued on a date: quantities, costs, the
                    price used and where it came from. Reviews compare these.
  listed_close      closing prices for series market.db does not keep
                    (REIT units RR, InvIT units IV), each with its date and
                    source

Local only (data/*.db is in .gitignore): it holds your positions.
Money is stored as TEXT holding a decimal string, so a value written is
exactly the value read back.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "research.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS portfolio_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at TEXT NOT NULL,
    price_date TEXT,
    label TEXT NOT NULL DEFAULT '',
    total_value TEXT NOT NULL,
    invested TEXT NOT NULL,
    summary TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshot_position (
    snapshot_id INTEGER NOT NULL REFERENCES portfolio_snapshot(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    kind TEXT NOT NULL,
    sleeve TEXT NOT NULL,
    bucket TEXT NOT NULL,
    quantity TEXT NOT NULL,
    avg_cost TEXT,
    price TEXT,
    price_date TEXT,
    price_source TEXT,
    market_value TEXT,
    sector TEXT,
    cap_band TEXT,
    PRIMARY KEY (snapshot_id, symbol)
);
CREATE TABLE IF NOT EXISTS listed_close (
    date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    series TEXT NOT NULL,
    close TEXT NOT NULL,
    isin TEXT,
    source TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    PRIMARY KEY (date, symbol, series)
);
CREATE INDEX IF NOT EXISTS idx_listed_close_symbol ON listed_close(symbol, series);
"""


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    return con


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _dec(v) -> str | None:
    if v is None:
        return None
    return str(v if isinstance(v, Decimal) else Decimal(str(v)))


def _json(obj) -> str:
    return json.dumps(obj, default=str, sort_keys=True)


# ------------------------------------------------------------------ audit

def log(con, action: str, target: str, detail: dict, actor: str = "user") -> int:
    cur = con.execute(
        "INSERT INTO audit_log (at, actor, action, target, detail) VALUES (?,?,?,?,?)",
        (_now(), actor, action, target, _json(detail)))
    con.commit()
    return cur.lastrowid


def audit_trail(con, limit: int = 50) -> list[dict]:
    rows = con.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
    return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]


# -------------------------------------------------------------- snapshots

def save_snapshot(con, positions: list[dict], summary: dict,
                  price_date: str | None, label: str = "") -> int:
    """positions: dicts with the snapshot_position columns (money as Decimal)."""
    total = sum((Decimal(str(p["market_value"])) for p in positions
                 if p.get("market_value") is not None), Decimal(0))
    invested = sum((Decimal(str(p["avg_cost"])) * Decimal(str(p["quantity"]))
                    for p in positions if p.get("avg_cost")), Decimal(0))
    cur = con.execute(
        "INSERT INTO portfolio_snapshot (taken_at, price_date, label, total_value, "
        "invested, summary) VALUES (?,?,?,?,?,?)",
        (_now(), price_date, label, _dec(total), _dec(invested), _json(summary)))
    sid = cur.lastrowid
    con.executemany(
        "INSERT INTO snapshot_position VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(sid, p["symbol"], p["kind"], p["sleeve"], p.get("bucket") or "",
          _dec(p["quantity"]), _dec(p.get("avg_cost")), _dec(p.get("price")),
          p.get("price_date"), p.get("price_source"), _dec(p.get("market_value")),
          p.get("sector"), p.get("cap_band")) for p in positions])
    con.commit()
    return sid


def snapshots(con) -> list[dict]:
    return [{**dict(r), "summary": json.loads(r["summary"])} for r in con.execute(
        "SELECT * FROM portfolio_snapshot ORDER BY id DESC")]


def snapshot_positions(con, snapshot_id: int) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT * FROM snapshot_position WHERE snapshot_id = ? ORDER BY symbol",
        (snapshot_id,))]


# ---------------------------------------------------------- listed closes

def save_listed_closes(con, date: str, rows: list[dict], source: str) -> int:
    now = _now()
    con.executemany(
        "INSERT OR REPLACE INTO listed_close VALUES (?,?,?,?,?,?,?)",
        [(date, r["symbol"], r["series"], _dec(r["close"]), r.get("isin"), source, now)
         for r in rows])
    con.commit()
    return len(rows)


def latest_listed_close(con, symbol: str, series: str) -> dict | None:
    """The two most recent closes: {'date','close','prev_close','source', ...}."""
    rows = con.execute(
        "SELECT * FROM listed_close WHERE symbol = ? AND series = ? "
        "ORDER BY date DESC LIMIT 2", (symbol, series)).fetchall()
    if not rows:
        return None
    out = dict(rows[0])
    out["close"] = Decimal(out["close"])
    out["prev_close"] = Decimal(rows[1]["close"]) if len(rows) > 1 else None
    return out


def listed_close_dates(con) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM listed_close ORDER BY date DESC")]
