"""A record of what the advisor said, and what happened next.

The backtest says momentum works. The backtest also said 28.7% before
survivorship bias was removed. Nothing in this repo currently records what the
advisor actually recommended on a given day, so live behaviour can never be
compared against the history it was validated on.

This is the cheapest possible fix: every run appends its verdicts with the date
and price. After a few months you can ask what a recommendation was worth --
not in theory, but on your book.

It records advice, never trades. Whether you acted is a separate column you
fill in yourself, because the framework has no way to know and should not guess.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).parent / "data" / "signals.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_log (
    run_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    bucket TEXT,
    urgency TEXT,
    price REAL NOT NULL,
    qty REAL,
    pct_of_book REAL,
    pnl_pct REAL,
    quality REAL,
    rank INTEGER,
    timing_score REAL,
    stop REAL,
    price_date TEXT,
    reason TEXT,
    acted TEXT DEFAULT '',
    PRIMARY KEY (run_date, symbol, action)
);
CREATE INDEX IF NOT EXISTS idx_log_symbol ON decision_log(symbol);
CREATE INDEX IF NOT EXISTS idx_log_date ON decision_log(run_date);
"""


# CREATE TABLE IF NOT EXISTS is a no-op on an existing table, so a column added
# after the first release never appears without this.
MIGRATIONS = [("decision_log", "price_date", "TEXT")]


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(Path(db_path))
    con.executescript(SCHEMA)
    for table, col, decl in MIGRATIONS:
        existing = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if existing and col not in existing:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    con.commit()
    return con


def record(con, rows: list[dict], run_date: dt.date | None = None,
           price_date: dt.date | None = None) -> int:
    """Append one run's verdicts. Idempotent -- re-running a day overwrites it.

    `run_date` is when the call was made; `price_date` is the session the price
    comes from. On a weekend run those differ -- a Sunday report quotes Friday's
    close -- and labelling the price with the run date implies a Sunday price
    that does not exist.
    """
    run_date = run_date or dt.date.today()
    payload = []
    for r in rows:
        if r.get("action") in (None, "", "HOLD"):
            continue          # only decisions worth reviewing later
        payload.append((
            run_date.isoformat(), r["symbol"], r["action"], r.get("bucket"),
            r.get("urgency"), float(r.get("price") or 0),
            float(r.get("qty_action") or 0), float(r.get("pct_of_book") or 0),
            float(r.get("pnl_pct") or 0),
            r.get("quality"), r.get("rank"), r.get("timing_score"),
            r.get("stop"),
            (price_date or run_date).isoformat(),
            "; ".join(r.get("reasons", []))[:600],
        ))
    if not payload:
        return 0
    con.executemany(
        "INSERT OR REPLACE INTO decision_log (run_date, symbol, action, bucket,"
        " urgency, price, qty, pct_of_book, pnl_pct, quality, rank,"
        " timing_score, stop, price_date, reason)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", payload)
    con.commit()
    return len(payload)


def record_candidates(con, market_rows: list[dict], top_n: int = 15,
                      run_date: dt.date | None = None,
                      price_date: dt.date | None = None) -> int:
    """Log the top-ranked names you do NOT own.

    The advisor's decisions cover holdings only, so a market-side candidate --
    the thing the ranking is actually asserting -- was never recorded. Without
    this you can check whether the framework manages a portfolio well, but not
    whether its ranking picks anything.

    Logged as WATCH_RANK so it never mixes with advice about your own book.
    """
    run_date = run_date or dt.date.today()
    payload = []
    for r in market_rows[:top_n]:
        if r.get("held"):
            continue
        payload.append((
            run_date.isoformat(), r["symbol"], "WATCH_RANK", "market", "normal",
            float(r.get("price") or 0), 0.0, 0.0, 0.0,
            r.get("quality"), r.get("rank"), r.get("timing_score"), None,
            (price_date or run_date).isoformat(),
            f"rank {r.get('rank')} | {r.get('lookback', 12)}-1 momentum "
            f"{r.get('momentum', 0):.0f}% "
            f"| vol {r.get('vol', 0):.0f}%"[:600],
        ))
    if not payload:
        return 0
    con.executemany(
        "INSERT OR REPLACE INTO decision_log (run_date, symbol, action, bucket,"
        " urgency, price, qty, pct_of_book, pnl_pct, quality, rank,"
        " timing_score, stop, price_date, reason)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", payload)
    con.commit()
    return len(payload)


def history(con, symbol: str | None = None, since: dt.date | None = None) -> pd.DataFrame:
    q = "SELECT * FROM decision_log WHERE 1=1"
    params: list = []
    if symbol:
        q += " AND symbol = ?"
        params.append(symbol.upper())
    if since:
        q += " AND run_date >= ?"
        params.append(since.isoformat())
    return pd.read_sql_query(q + " ORDER BY run_date DESC, symbol", con,
                             params=params)


def score_past_advice(con, prices_now: dict[str, float],
                      min_age_days: int = 21) -> pd.DataFrame:
    """What each past recommendation would have been worth.

    Only scores advice old enough to have played out -- judging a call made
    three days ago tells you about noise, not about the rule that produced it.

    `outcome_pct` is signed so that positive always means the advice was right:
    a price rise after a BUY and a price fall after a SELL both score positive.
    Without that flip the two would cancel out in any average.
    """
    df = history(con)
    if df.empty:
        return df
    today = dt.date.today()
    rows = []
    for r in df.itertuples():
        age = (today - dt.date.fromisoformat(r.run_date)).days
        if age < min_age_days:
            continue
        now = prices_now.get(r.symbol)
        if not now or not r.price:
            continue
        move = 100 * (now / r.price - 1)
        # EXIT is a directional call: you left because the thesis broke, so a
        # subsequent fall vindicates it. TRIM is NOT -- you sold because the
        # position was oversized, with no view on direction. Its outcome is
        # therefore opportunity cost, not a right-or-wrong verdict: a run of
        # negative TRIM scores says the sizing cap may be too tight, not that
        # the rule misread the stock.
        bullish = r.action in ("BUY", "ADD", "WATCH_RANK")
        rows.append({
            "run_date": r.run_date, "symbol": r.symbol, "action": r.action,
            "bucket": r.bucket, "days": age, "price_then": r.price,
            "price_now": now, "move_pct": round(move, 2),
            "outcome_pct": round(move if bullish else -move, 2),
            "kind": ("ranking" if r.action == "WATCH_RANK"
                     else "directional" if r.action in ("BUY", "ADD", "EXIT")
                     else "sizing"),
            "acted": r.acted,
        })
    return pd.DataFrame(rows)


def summary(scored: pd.DataFrame) -> dict:
    """Hit rate and average outcome per action type.

    Reported with the sample size attached, because a hit rate over eleven
    calls is a number about luck.
    """
    if scored.empty:
        return {}
    out: dict = {}
    for action, grp in scored.groupby("action"):
        out[action] = {
            "n": len(grp),
            "hit_rate_pct": round(100 * (grp["outcome_pct"] > 0).mean(), 1),
            "avg_outcome_pct": round(grp["outcome_pct"].mean(), 2),
            "median_days": int(grp["days"].median()),
        }
    # The headline number covers directional calls only. Averaging sizing
    # trims into it would report a rising market as a failure of judgement.
    directional = scored[scored["kind"] == "directional"]
    out["_overall"] = {
        "n": len(directional),
        "hit_rate_pct": round(100 * (directional["outcome_pct"] > 0).mean(), 1)
        if len(directional) else 0.0,
        "avg_outcome_pct": round(directional["outcome_pct"].mean(), 2)
        if len(directional) else 0.0,
        "enough_to_judge": len(directional) >= 30,
        "note": "directional calls only (BUY/ADD/EXIT). Trims are sizing "
                "decisions and are scored separately as opportunity cost.",
    }
    return out
