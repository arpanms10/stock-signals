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


# ------------------------------------------------- financial metrics (P2)
#
# One row per value. Every value keeps where it came from and how it was
# made, so a figure on screen can always be traced:
#
#   period      quarter | half | fy | ttm | instant | point (a market stat)
#   method      reported (as filed / as the provider gives it) | calculated
#               (from other rows, named in `note`) | estimated
#   status      ok | missing (looked for, not found) | not_applicable
#               (e.g. debt/equity for a bank). value is NULL unless ok, so
#               a missing number is never read as zero.
#   confidence  high (exchange filing, or two sources agree) | medium (one
#               secondary source) | low (approximation, stated in note)

METRIC_SCHEMA = """
CREATE TABLE IF NOT EXISTS financial_metric (
    symbol TEXT NOT NULL,
    metric TEXT NOT NULL,
    period TEXT NOT NULL,
    period_end TEXT NOT NULL,
    source TEXT NOT NULL,
    value TEXT,
    unit TEXT NOT NULL,
    basis TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ok',
    confidence TEXT NOT NULL,
    ref TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    retrieved_at TEXT NOT NULL,
    PRIMARY KEY (symbol, metric, period, period_end, source)
);
CREATE INDEX IF NOT EXISTS idx_metric_symbol ON financial_metric(symbol, metric);
CREATE TABLE IF NOT EXISTS retrieval_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    dataset TEXT NOT NULL,
    status TEXT NOT NULL,
    rows INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_retrieval_symbol ON retrieval_log(symbol, dataset, id);
"""

METRIC_COLS = ("symbol", "metric", "period", "period_end", "source", "value", "unit",
               "basis", "method", "status", "confidence", "ref", "note",
               "retrieved_at")


def _ensure_metrics(con) -> None:
    con.executescript(METRIC_SCHEMA)


def save_metrics(con, rows: list[dict], replace_source: tuple[str, str] | None = None) -> int:
    """Upsert metric rows. replace_source=(symbol, source) first deletes that
    source's rows for the symbol, so a value the provider no longer reports
    does not linger as if current."""
    _ensure_metrics(con)
    if replace_source:
        con.execute("DELETE FROM financial_metric WHERE symbol = ? AND source = ?",
                    replace_source)
    now = _now()
    con.executemany(
        f"INSERT OR REPLACE INTO financial_metric ({','.join(METRIC_COLS)}) "
        f"VALUES ({','.join('?' * len(METRIC_COLS))})",
        [tuple(_dec(r.get("value")) if c == "value" else
               (r.get(c) or (now if c == "retrieved_at" else
                             "ok" if c == "status" else ""))
               for c in METRIC_COLS) for r in rows])
    con.commit()
    return len(rows)


def metrics(con, symbol: str, metric: str | None = None) -> list[dict]:
    _ensure_metrics(con)
    q, args = "SELECT * FROM financial_metric WHERE symbol = ?", [symbol]
    if metric:
        q += " AND metric = ?"
        args.append(metric)
    out = []
    for r in con.execute(q + " ORDER BY metric, period, period_end", args):
        d = dict(r)
        d["value"] = Decimal(d["value"]) if d["value"] is not None else None
        out.append(d)
    return out


def log_retrieval(con, symbol: str, source: str, dataset: str, status: str,
                  rows: int = 0, message: str = "") -> None:
    _ensure_metrics(con)
    con.execute("INSERT INTO retrieval_log (at, symbol, source, dataset, status, rows, "
                "message) VALUES (?,?,?,?,?,?,?)",
                (_now(), symbol, source, dataset, status, rows, message[:500]))
    con.commit()


def last_retrievals(con, symbol: str | None = None) -> list[dict]:
    """The most recent attempt per (symbol, source, dataset)."""
    _ensure_metrics(con)
    q = ("SELECT r.* FROM retrieval_log r JOIN (SELECT MAX(id) id FROM retrieval_log "
         "GROUP BY symbol, source, dataset) m ON r.id = m.id")
    args = []
    if symbol:
        q += " WHERE r.symbol = ?"
        args.append(symbol)
    return [dict(r) for r in con.execute(q + " ORDER BY r.symbol, r.dataset", args)]


# ------------------------------------------------------------------ scorecards

SCORECARD_SCHEMA = """
CREATE TABLE IF NOT EXISTS scorecard (
    symbol       TEXT NOT NULL,
    as_of        TEXT NOT NULL,
    model        TEXT NOT NULL DEFAULT '',
    score        REAL,
    earned       REAL,
    available    REAL,
    coverage_pct REAL,
    tier         TEXT,
    raw_tier     TEXT,
    role         TEXT,
    flags        TEXT NOT NULL DEFAULT '[]',
    detail       TEXT NOT NULL,           -- the whole card as JSON
    config_hash  TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (symbol, as_of)
);
"""


def save_scorecard(con, summary: dict, detail: dict, config_hash: str = "") -> None:
    """One card per symbol per day; a re-run the same day replaces it."""
    con.executescript(SCORECARD_SCHEMA)
    con.execute("INSERT OR REPLACE INTO scorecard VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (summary["symbol"], summary["as_of"], summary.get("model", ""),
                 summary.get("score"), summary.get("earned"), summary.get("available"),
                 summary.get("coverage_pct"), summary.get("tier"), summary.get("raw_tier"),
                 summary.get("role"), _json(summary.get("flags", [])), _json(detail),
                 config_hash))
    con.commit()


def scorecard_history(con, symbol: str | None = None) -> list[dict]:
    con.executescript(SCORECARD_SCHEMA)
    q = "SELECT * FROM scorecard" + (" WHERE symbol = ?" if symbol else "") + \
        " ORDER BY symbol, as_of"
    return [dict(r) for r in con.execute(q, (symbol,) if symbol else ())]
