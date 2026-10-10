"""Full-market daily bhavcopy: the survivorship-free universe.

Ranking today's NIFTY 200 members backwards over ten years is survivorship
bias in its purest form -- every company that was in the index in 2016 and
collapsed out of it is silently absent, so the backtest only ever picks from
survivors. That inflates any strategy, and momentum most of all, because the
names it would have bought and been destroyed by are the exact ones missing.

The bhavcopy is the fix. It is the day's actual trade record for every listed
stock, so a company that delisted in 2019 is present up to the day it stopped
trading and absent afterwards -- which is precisely point-in-time membership.

Two file formats, because NSE switched to UDiFF in July 2024. Both are
normalised to the same columns here so nothing downstream needs to know.
"""
from __future__ import annotations

import datetime as dt
import io
import sqlite3
import time
from pathlib import Path

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS market (
    date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    close REAL,
    volume REAL,
    turnover REAL,
    PRIMARY KEY (date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_market_date ON market(date);
CREATE INDEX IF NOT EXISTS idx_market_symbol ON market(symbol);
CREATE TABLE IF NOT EXISTS instruments (
    symbol TEXT PRIMARY KEY,
    isin TEXT
);
CREATE TABLE IF NOT EXISTS market_days (
    date TEXT PRIMARY KEY,
    rows INTEGER,
    fetched_at TEXT
);
-- Trade-for-trade series (BE, BZ). NSE moves a stock there for surveillance
-- and back again; it still trades and can still be sold. Kept apart from
-- `market` so nothing that ranks or builds a universe ever sees it -- only
-- the backtest reads it, to value and exit a holding that moved there.
CREATE TABLE IF NOT EXISTS market_t2t (
    date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    series TEXT,
    close REAL,
    volume REAL,
    turnover REAL,
    PRIMARY KEY (date, symbol)
);
CREATE TABLE IF NOT EXISTS t2t_days (
    date TEXT PRIMARY KEY,
    rows INTEGER
);
-- Splits and bonuses for the whole market, from NSE's corporate-actions
-- feed. `market` holds raw closes; the backtest divides prices before each
-- ex-date by `factor` so a 1:1 bonus is not read as a 50% crash.
CREATE TABLE IF NOT EXISTS market_actions (
    symbol TEXT NOT NULL,
    ex_date TEXT NOT NULL,
    factor REAL NOT NULL,
    kind TEXT,
    subject TEXT NOT NULL,
    PRIMARY KEY (symbol, ex_date, subject)
);
CREATE TABLE IF NOT EXISTS market_actions_synced (
    through TEXT
);
"""

T2T_SERIES = ("BE", "BZ")

UDIFF_SWITCH = dt.date(2024, 7, 8)


def connect(db_path: Path | str) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=30)
    con.executescript(SCHEMA)
    return con


def _isin_column(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([None] * len(df), index=df.index, dtype=object)
    return df[col].astype(str).str.strip().str.upper().replace({"NAN": None, "": None})


def _normalise_old(df: pd.DataFrame, series=("EQ",)) -> pd.DataFrame:
    df.columns = [c.strip().upper() for c in df.columns]
    df = df[df["SERIES"].astype(str).str.strip().isin(series)]
    out = pd.DataFrame({
        "symbol": df["SYMBOL"].astype(str).str.strip().str.upper(),
        "series": df["SERIES"].astype(str).str.strip(),
        "close": pd.to_numeric(df["CLOSE"], errors="coerce"),
        "volume": pd.to_numeric(df["TOTTRDQTY"], errors="coerce"),
        "turnover": pd.to_numeric(df.get("TOTTRDVAL"), errors="coerce"),
        "isin": _isin_column(df, "ISIN"),
    })
    return out


def _normalise_udiff(df: pd.DataFrame, series=("EQ",)) -> pd.DataFrame:
    df.columns = [c.strip() for c in df.columns]
    df = df[df["SctySrs"].astype(str).str.strip().isin(series)]
    out = pd.DataFrame({
        "symbol": df["TckrSymb"].astype(str).str.strip().str.upper(),
        "series": df["SctySrs"].astype(str).str.strip(),
        "close": pd.to_numeric(df.get("ClsPric"), errors="coerce"),
        "volume": pd.to_numeric(df.get("TtlTradgVol"), errors="coerce"),
        "turnover": pd.to_numeric(df.get("TtlTrfVal"), errors="coerce"),
        "isin": _isin_column(df, "ISIN"),
    })
    return out


EMPTY_COLS = ["symbol", "close", "volume", "turnover"]


def _is_missing(exc: Exception) -> bool:
    """NSE has no file in that format (vs a failed request)."""
    import zipfile
    resp = getattr(exc, "response", None)
    if resp is not None and getattr(resp, "status_code", None) == 404:
        return True
    # The UDiFF endpoint answers with a non-zip page for dates it lacks.
    return isinstance(exc, zipfile.BadZipFile)


def fetch_day(d: dt.date, series=("EQ",)) -> pd.DataFrame | None:
    """One trading day for the whole market.

    Empty frame: neither file format exists for the day (a holiday, or not
    published yet). None: a request FAILED -- network, throttling -- and the
    day must be retried, not recorded as empty. That confusion once left
    ~9% of trading days since 2016 recorded as "no data" and never refetched.

    Both formats are tried, the date's usual one first: UDiFF exists from
    January 2024 while the old archive has gaps in early 2024 (2024-06-12 is
    404 there but present in UDiFF).
    """
    from jugaad_data.nse import bhavcopy_raw, bhavcopy_udiff_raw

    order = ([(bhavcopy_udiff_raw, _normalise_udiff), (bhavcopy_raw, _normalise_old)]
             if d >= UDIFF_SWITCH else
             [(bhavcopy_raw, _normalise_old), (bhavcopy_udiff_raw, _normalise_udiff)])
    failed = False
    for fn, normalise in order:
        try:
            raw = fn(d)
        except Exception as exc:          # noqa: BLE001 -- jugaad raises many kinds
            failed = failed or not _is_missing(exc)
            continue
        if not raw:
            continue
        text = raw.decode(errors="ignore") if isinstance(raw, bytes) else raw
        try:
            df = pd.read_csv(io.StringIO(text))
        except Exception:
            failed = True
            continue
        if df.empty:
            continue
        out = normalise(df, series).dropna(subset=["symbol", "close"])
        out = out[out["close"] > 0].sort_values("series", key=lambda x: x != "EQ")
        return out.drop_duplicates(subset="symbol")
    return None if failed else pd.DataFrame(columns=EMPTY_COLS)


RECORD_EMPTY_AFTER_DAYS = 7


def keep_day(d: dt.date, df: pd.DataFrame | None) -> bool:
    """Store this day? A failed request: no, retry next run. No file in
    either format: record it as empty only once it is a week old -- a
    weekday that is still missing after a week is not coming; a recent one
    may just not be published yet."""
    if df is None:
        return False
    if df.empty:
        return (dt.date.today() - d).days > RECORD_EMPTY_AFTER_DAYS
    return True


def save_day(con, d: dt.date, df: pd.DataFrame) -> int:
    if df.empty:
        con.execute("INSERT OR REPLACE INTO market_days (date, rows, fetched_at)"
                    " VALUES (?,?,?)",
                    (d.isoformat(), 0, dt.datetime.now().isoformat(timespec="seconds")))
        con.commit()
        return 0
    rows = [(d.isoformat(), r.symbol, r.close, r.volume, r.turnover)
            for r in df.itertuples()]
    con.executemany("INSERT OR REPLACE INTO market (date, symbol, close, volume,"
                    " turnover) VALUES (?,?,?,?,?)", rows)
    # The ISIN rides along in every bhavcopy, and it is the only reliable way
    # to tell a company from an ETF (both trade in the EQ series). Discarding
    # it here is what once left the instrument map empty and let LIQUIDCASE
    # into the backtest.
    if "isin" in df.columns:
        record_isins(con, zip(df["symbol"], df["isin"]))
    con.execute("INSERT OR REPLACE INTO market_days (date, rows, fetched_at)"
                " VALUES (?,?,?)",
                (d.isoformat(), len(rows),
                 dt.datetime.now().isoformat(timespec="seconds")))
    con.commit()
    return len(rows)


# Indian ISINs discriminate cleanly: INE = company equity, INF = mutual fund
# or ETF. This matters more than it looks. ETFs are highly liquid, so they pass
# any turnover filter, and a liquid-fund ETF has near-zero volatility -- which
# sends risk-adjusted momentum (return / volatility) to near-infinity and puts
# a cash equivalent at the top of the ranking. LIQUIDCASE appeared in a
# backtest's holdings exactly this way.
EQUITY_ISIN_PREFIX = "INE"


class InstrumentMapMissing(RuntimeError):
    """The symbol -> ISIN map is empty, so equities cannot be told from ETFs."""


def record_isins(con, pairs) -> int:
    rows = [(str(sym).strip().upper(), str(isin).strip().upper())
            for sym, isin in pairs
            if sym and isin and str(isin).strip().lower() not in ("", "nan", "none")]
    if rows:
        con.executemany("INSERT OR REPLACE INTO instruments (symbol, isin) "
                        "VALUES (?,?)", rows)
    return len(rows)


def equity_symbols(con) -> set[str]:
    """Symbols known to be company equity. Empty set means the map is unbuilt."""
    return {r[0] for r in con.execute(
        "SELECT symbol FROM instruments WHERE isin LIKE ?",
        (EQUITY_ISIN_PREFIX + "%",))}


def non_equity_symbols(con) -> set[str]:
    """Symbols whose ISIN says fund/ETF, DVR or anything else not INE."""
    return {r[0] for r in con.execute(
        "SELECT symbol FROM instruments WHERE isin NOT LIKE ?",
        (EQUITY_ISIN_PREFIX + "%",))}


def require_equity_symbols(con) -> set[str]:
    """equity_symbols(), but refuse to proceed without a map.

    Anything that ranks must fail closed. An empty map used to mean "skip the
    filter", and with the map never built every ETF in the EQ series -- and a
    liquid-fund ETF with near-zero volatility above all -- competed with
    stocks. A symbol missing from a non-empty map is likewise treated as not
    equity: an unknown is excluded, never assumed.
    """
    eq = equity_symbols(con)
    if not eq:
        raise InstrumentMapMissing(
            "instrument map (symbol -> ISIN) is empty, so ETFs cannot be told "
            "from stocks. Run: PYTHONPATH=. .venv/bin/python run_market_ingest.py")
    return eq


def unmapped_symbols(con) -> set[str]:
    return {r[0] for r in con.execute(
        "SELECT DISTINCT m.symbol FROM market m "
        "LEFT JOIN instruments i ON i.symbol = m.symbol WHERE i.symbol IS NULL")}


def backfill_isins(con, max_days: int = 400, pause: float = 0.3,
                   verbose: bool = True) -> int:
    """Map every symbol in `market` to an ISIN, downloading as few days as possible.

    Greedy: repeatedly fetch the trading day on which the most still-unmapped
    symbols traded. A handful of recent days covers the live market; the long
    tail of delisted names needs one day each from their own era.
    """
    tried: set[str] = set()
    fetched = 0
    while fetched < max_days:
        if not unmapped_symbols(con):
            break
        skip = ("AND m.date NOT IN (" + ",".join("?" * len(tried)) + ") "
                if tried else "")
        row = con.execute(
            "SELECT m.date, COUNT(*) n FROM market m "
            "LEFT JOIN instruments i ON i.symbol = m.symbol "
            f"WHERE i.symbol IS NULL {skip}"
            "GROUP BY m.date ORDER BY n DESC, m.date DESC LIMIT 1",
            sorted(tried)).fetchone()
        if not row:
            break
        day = row[0]
        tried.add(day)
        df = fetch_day(dt.date.fromisoformat(day))
        fetched += 1
        if df is not None and not df.empty and "isin" in df.columns:
            got = record_isins(con, zip(df["symbol"], df["isin"]))
            con.commit()
            if verbose:
                print(f"  isin {day}: {got} symbols, "
                      f"{len(unmapped_symbols(con))} still unmapped", flush=True)
        time.sleep(pause)
    return len(unmapped_symbols(con))


def save_t2t_day(con, d: dt.date, df: pd.DataFrame) -> int:
    """Store a day's trade-for-trade rows and mark the day done."""
    t = df[df["series"].isin(T2T_SERIES)] if "series" in df.columns else df.iloc[0:0]
    rows = [(d.isoformat(), r.symbol, r.series, r.close, r.volume, r.turnover)
            for r in t.itertuples()]
    con.executemany("INSERT OR REPLACE INTO market_t2t (date, symbol, series, close,"
                    " volume, turnover) VALUES (?,?,?,?,?,?)", rows)
    if "isin" in t.columns:
        record_isins(con, zip(t["symbol"], t["isin"]))
    con.execute("INSERT OR REPLACE INTO t2t_days (date, rows) VALUES (?,?)",
                (d.isoformat(), len(rows)))
    con.commit()
    return len(rows)


def backfill_t2t(con, pause: float = 0.35, verbose: bool = True) -> int:
    """Fetch BE/BZ rows for trading days ingested before they were kept."""
    todo = [r[0] for r in con.execute(
        "SELECT date FROM market_days WHERE rows > 0 "
        "AND date NOT IN (SELECT date FROM t2t_days) ORDER BY date")]
    total, failed = 0, []
    for k, day in enumerate(todo, 1):
        df = fetch_day(dt.date.fromisoformat(day), T2T_SERIES)
        if df is None or df.empty:
            failed.append(day)       # retried next run; a trading day has rows
        else:
            total += save_t2t_day(con, dt.date.fromisoformat(day), df)
        if verbose and k % 100 == 0:
            print(f"  t2t {k}/{len(todo)} ({day}): {total:,} rows, "
                  f"{len(failed)} failed", flush=True)
        time.sleep(pause)
    if verbose:
        print(f"  t2t done: {total:,} rows over {len(todo) - len(failed)} days, "
              f"{len(failed)} to retry", flush=True)
    return total


def sync_actions(con, start: dt.date, end: dt.date, verbose: bool = True) -> int:
    """Fetch every split/bonus in [start, end], a year per request."""
    from . import corporate_actions as ca
    n, frm = 0, start
    while frm <= end:
        to = min(end, dt.date(frm.year, 12, 31))
        actions, _ = ca.fetch_all(frm, to)
        con.executemany(
            "INSERT OR REPLACE INTO market_actions (symbol, ex_date, factor, kind, subject) "
            "VALUES (?,?,?,?,?)",
            [(a.symbol, a.ex_date.isoformat(), a.factor, a.kind, a.subject) for a in actions])
        n += len(actions)
        if verbose:
            print(f"  actions {frm} .. {to}: {len(actions)}", flush=True)
        frm = to + dt.timedelta(days=1)
        time.sleep(0.5)
    con.execute("DELETE FROM market_actions_synced")
    con.execute("INSERT INTO market_actions_synced VALUES (?)", (end.isoformat(),))
    con.commit()
    return n


def actions_synced_through(con) -> dt.date | None:
    r = con.execute("SELECT through FROM market_actions_synced").fetchone()
    return dt.date.fromisoformat(r[0]) if r else None


def load_actions(con) -> pd.DataFrame:
    """One row per (symbol, ex_date), factors on the same day compounded
    (Bajaj Finance, Jun 2025: a bonus and a split on one ex-date)."""
    df = pd.read_sql_query("SELECT symbol, ex_date, factor FROM market_actions", con)
    if df.empty:
        return df
    return (df.groupby(["symbol", "ex_date"], as_index=False)["factor"].prod())


def symbol_aliases(con) -> dict[str, str]:
    """Old symbol -> current symbol, for companies that changed ticker.

    Linked through the ISIN, which survives a rename (ZOMATO -> ETERNAL,
    MOTHERSUMI -> MOTHERSON). Without this a rename reads as a delisting:
    the holding is force-sold and the new ticker has no history to rank on
    for a year. Two tickers that traded on the same day are never linked.
    """
    spans = con.execute(
        "SELECT i.isin, s.symbol, s.first, s.last FROM instruments i JOIN ("
        "  SELECT symbol, MIN(date) first, MAX(date) last FROM ("
        "    SELECT symbol, date FROM market UNION ALL "
        "    SELECT symbol, date FROM market_t2t) GROUP BY symbol"
        ") s ON s.symbol = i.symbol "
        "WHERE i.isin IN (SELECT isin FROM instruments GROUP BY isin "
        "HAVING COUNT(*) > 1) ORDER BY i.isin, s.first").fetchall()
    chains: dict[str, list[tuple[str, str, str]]] = {}
    for isin, sym, first, last in spans:
        chains.setdefault(isin, []).append((sym, first, last))
    alias: dict[str, str] = {}
    for links in chains.values():
        if any(b[1] <= a[2] for a, b in zip(links, links[1:])):
            continue                                     # overlapping: not a rename
        for sym, _, _ in links[:-1]:
            alias[sym] = links[-1][0]
    return alias


def have_days(con) -> set[str]:
    return {r[0] for r in con.execute("SELECT date FROM market_days")}


def ingest_range(con, start: dt.date, end: dt.date, pause: float = 0.35,
                 verbose: bool = True) -> int:
    """Download every trading day in a range. Skips days already held."""
    done = have_days(con)
    total, d, failed = 0, start, []
    while d <= end:
        if d.weekday() >= 5 or d.isoformat() in done:
            d += dt.timedelta(days=1)
            continue
        df = fetch_day(d, ("EQ",) + T2T_SERIES)
        if keep_day(d, df):
            eq = df[df["series"] == "EQ"] if "series" in df.columns else df
            n = save_day(con, d, eq)
            if not df.empty:
                save_t2t_day(con, d, df)
            total += n
            if verbose and n:
                print(f"  {d}: {n} stocks", flush=True)
        elif df is None:
            failed.append(d)
        time.sleep(pause)
        d += dt.timedelta(days=1)
    if failed and verbose:
        print(f"  {len(failed)} day(s) failed to download and will be retried next run: "
              + ", ".join(map(str, failed[:10])), flush=True)
    return total


def repair_false_empty_days(con, holidays: set[dt.date], verbose: bool = True) -> list[str]:
    """Forget 'no data' markers on weekdays that are not exchange holidays,
    so the next ingest refetches them. Only the marker row is removed --
    no price data exists for those days to lose."""
    rows = [r[0] for r in con.execute("SELECT date FROM market_days WHERE rows = 0")]
    suspect = [x for x in rows
               if dt.date.fromisoformat(x).weekday() < 5
               and dt.date.fromisoformat(x) not in holidays]
    con.executemany("DELETE FROM market_days WHERE date = ? AND rows = 0",
                    [(x,) for x in suspect])
    con.commit()
    if verbose:
        print(f"  cleared {len(suspect)} 'no data' markers on non-holiday weekdays")
    return suspect


def liquidity_ranks(con, on_date: dt.date) -> dict[str, int]:
    """Liquidity rank of EVERY eligible equity, 1 = most traded.

    Built from universe_on with no top-n cut. Ranking only the top 200 or 400
    left everything beyond it with no rank at all, and classify() read a
    missing rank as "no objection" -- so IRCTC, rank 562, was suggested as
    core against a top-150 requirement.
    """
    return {s: i + 1 for i, s in
            enumerate(universe_on(con, on_date, top_n=1_000_000))}


def universe_on(con, on_date: dt.date, top_n: int = 200,
                lookback_days: int = 60, min_days: int = 30,
                min_price: float = 0.0, min_turnover_cr: float = 0.0) -> list[str]:
    """The N most liquid stocks actually trading as of a date.

    Liquidity, not index membership, defines the universe -- and it is measured
    only from data up to `on_date`, so the selection could have been made on the
    day. `min_days` requires a stock to have actually traded through the window,
    which drops the illiquid and the freshly listed without needing a separate
    listing-date table.
    """
    # Turnover alone is not enough of a filter. Risk-adjusted momentum divides
    # return by volatility, so a thinly-held stock that doubles on a month of
    # promotion ranks above every real business -- and a price floor plus a
    # turnover floor is the cheapest guard against buying one.
    frm = (on_date - dt.timedelta(days=lookback_days)).isoformat()
    require_equity_symbols(con)
    # Equity-only inside the query, so ETFs crowding the top of the turnover
    # table cannot push real stocks out of the LIMIT.
    cur = con.execute(
        "SELECT m.symbol, AVG(m.turnover) t, COUNT(*) n, AVG(m.close) p "
        "FROM market m JOIN instruments i ON i.symbol = m.symbol "
        "WHERE m.date <= ? AND m.date > ? AND m.turnover IS NOT NULL "
        "AND i.isin LIKE ? "
        "GROUP BY m.symbol HAVING n >= ? AND p >= ? AND t >= ? "
        "ORDER BY t DESC LIMIT ?",
        (on_date.isoformat(), frm, EQUITY_ISIN_PREFIX + "%", min_days,
         min_price, min_turnover_cr * 1e7, top_n))
    return [r[0] for r in cur.fetchall()]
