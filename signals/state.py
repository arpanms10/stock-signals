"""Position and alert state.

Trailing stops are path-dependent -- they depend on the highest close since you
entered -- so this state has to survive restarts. Recomputing it from scratch
each run would work only until the first time the process died mid-month.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    qty REAL NOT NULL,
    entry_price REAL NOT NULL,
    entry_date TEXT NOT NULL,
    highest_close REAL,
    stop_price REAL,
    trailing INTEGER DEFAULT 0,
    source TEXT,
    t1 REAL DEFAULT 0,
    t2 REAL DEFAULT 0,
    t1_booked INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS signal_log (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    action TEXT NOT NULL,
    rule TEXT,
    price REAL,
    score REAL,
    stop_price REAL,
    qty REAL,
    reason TEXT,
    PRIMARY KEY (symbol, date, action, rule)
);
"""


@dataclass
class Position:
    symbol: str
    qty: float
    entry_price: float
    entry_date: dt.date
    highest_close: float = 0.0
    stop_price: float = 0.0
    trailing: bool = False
    source: str = "manual"
    t1: float = 0.0
    t2: float = 0.0
    t1_booked: bool = False

    def __post_init__(self):
        if not self.highest_close:
            self.highest_close = self.entry_price

    def pnl_pct(self, price: float) -> float:
        return 100 * (price / self.entry_price - 1)


@dataclass
class Signal:
    symbol: str
    date: dt.date
    action: str          # BUY | SELL | TRIM
    rule: str
    price: float
    score: float | None = None
    stop_price: float | None = None
    qty: float | None = None
    reason: str = ""
    components: str = ""
    t1: float | None = None
    t2: float | None = None

    def line(self) -> str:
        bits = [f"{self.action} {self.symbol} @ {self.price:.2f}"]
        if self.stop_price:
            bits.append(f"stop {self.stop_price:.2f}")
        if self.qty:
            bits.append(f"qty {self.qty:.0f}")
        if self.score is not None:
            bits.append(f"score {self.score:.0f}")
        out = "  ".join(bits) + f"\n    {self.rule}: {self.reason}"
        if self.t1 and self.t2 and self.stop_price:
            r = self.price - self.stop_price
            book = f"{self.qty * 0.5:.0f}" if self.qty else "half"
            out += (f"\n    R = {r:.2f}/share  |  "
                    f"T1 {self.t1:.2f} (+{100*(self.t1/self.price-1):.1f}%, sell {book}) "
                    f"-> then move stop to {self.price:.2f} breakeven  |  "
                    f"T2 {self.t2:.2f} (+{100*(self.t2/self.price-1):.1f}%, sell rest)")
        return out


# Columns added after the first release. CREATE TABLE IF NOT EXISTS silently
# does nothing on an existing table, so a database created before a column was
# introduced never gains it -- and the failure only shows up at runtime, on a
# real user's machine, never in tests that build a fresh database each time.
MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "positions": [
        ("t1", "REAL DEFAULT 0"),
        ("t2", "REAL DEFAULT 0"),
        ("t1_booked", "INTEGER DEFAULT 0"),
    ],
}


def _migrate(con) -> list[str]:
    """Add any columns missing from an older database. Idempotent."""
    applied = []
    for table, columns in MIGRATIONS.items():
        existing = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue                      # table not created yet; SCHEMA handles it
        for name, decl in columns:
            if name not in existing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                applied.append(f"{table}.{name}")
    if applied:
        con.commit()
    return applied


def init(con) -> None:
    con.executescript(SCHEMA)
    _migrate(con)
    con.commit()


def load_positions(con) -> dict[str, Position]:
    init(con)
    cur = con.execute("SELECT symbol, qty, entry_price, entry_date, highest_close,"
                      " stop_price, trailing, source, t1, t2, t1_booked FROM positions")
    return {r[0]: Position(r[0], r[1], r[2], dt.date.fromisoformat(r[3]),
                           r[4] or 0.0, r[5] or 0.0, bool(r[6]), r[7] or "manual",
                           r[8] or 0.0, r[9] or 0.0, bool(r[10]))
            for r in cur.fetchall()}


def save_position(con, p: Position) -> None:
    init(con)
    con.execute("INSERT OR REPLACE INTO positions (symbol, qty, entry_price,"
                " entry_date, highest_close, stop_price, trailing, source,"
                " t1, t2, t1_booked) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (p.symbol, p.qty, p.entry_price, p.entry_date.isoformat(),
                 p.highest_close, p.stop_price, int(p.trailing), p.source,
                 p.t1, p.t2, int(p.t1_booked)))
    con.commit()


def close_position(con, symbol: str) -> None:
    con.execute("DELETE FROM positions WHERE symbol=?", (symbol,))
    con.commit()


def log_signal(con, s: Signal) -> None:
    init(con)
    con.execute("INSERT OR REPLACE INTO signal_log (symbol, date, action, rule,"
                " price, score, stop_price, qty, reason) VALUES (?,?,?,?,?,?,?,?,?)",
                (s.symbol, s.date.isoformat(), s.action, s.rule, s.price,
                 s.score, s.stop_price, s.qty, s.reason))
    con.commit()


def last_signal_date(con, symbol: str, action: str | None = None) -> dt.date | None:
    init(con)
    if action:
        cur = con.execute("SELECT MAX(date) FROM signal_log WHERE symbol=? AND action=?",
                          (symbol, action))
    else:
        cur = con.execute("SELECT MAX(date) FROM signal_log WHERE symbol=?", (symbol,))
    row = cur.fetchone()[0]
    return dt.date.fromisoformat(row) if row else None
