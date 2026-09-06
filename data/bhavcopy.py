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
"""

UDIFF_SWITCH = dt.date(2024, 7, 8)


def connect(db_path: Path | str) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    return con


def _normalise_old(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.strip().upper() for c in df.columns]
    df = df[df["SERIES"].astype(str).str.strip() == "EQ"]
    out = pd.DataFrame({
        "symbol": df["SYMBOL"].astype(str).str.strip().str.upper(),
        "close": pd.to_numeric(df["CLOSE"], errors="coerce"),
        "volume": pd.to_numeric(df["TOTTRDQTY"], errors="coerce"),
        "turnover": pd.to_numeric(df.get("TOTTRDVAL"), errors="coerce"),
    })
    return out


def _normalise_udiff(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.strip() for c in df.columns]
    df = df[df["SctySrs"].astype(str).str.strip() == "EQ"]
    out = pd.DataFrame({
        "symbol": df["TckrSymb"].astype(str).str.strip().str.upper(),
        "close": pd.to_numeric(df.get("ClsPric"), errors="coerce"),
        "volume": pd.to_numeric(df.get("TtlTradgVol"), errors="coerce"),
        "turnover": pd.to_numeric(df.get("TtlTrfVal"), errors="coerce"),
    })
    return out


def fetch_day(d: dt.date) -> pd.DataFrame:
    """One trading day for the whole market. Empty frame on holidays."""
    from jugaad_data.nse import bhavcopy_raw, bhavcopy_udiff_raw

    fn = bhavcopy_udiff_raw if d >= UDIFF_SWITCH else bhavcopy_raw
    try:
        raw = fn(d)
    except Exception:
        return pd.DataFrame(columns=["symbol", "close", "volume", "turnover"])
    if not raw:
        return pd.DataFrame(columns=["symbol", "close", "volume", "turnover"])
    text = raw.decode(errors="ignore") if isinstance(raw, bytes) else raw
    try:
        df = pd.read_csv(io.StringIO(text))
    except Exception:
        return pd.DataFrame(columns=["symbol", "close", "volume", "turnover"])
    if df.empty:
        return df
    out = (_normalise_udiff(df) if d >= UDIFF_SWITCH else _normalise_old(df))
    out = out.dropna(subset=["symbol", "close"])
    return out[out["close"] > 0].drop_duplicates(subset="symbol")


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


def build_instrument_map(con, dates: list[dt.date], verbose: bool = True) -> int:
    """Symbol -> ISIN, sampled across history.

    Sampled rather than collected during ingestion because the mapping is
    stable, so a few dozen days spread over the period cover essentially every
    symbol that ever traded -- far cheaper than re-downloading everything.
    """
    from jugaad_data.nse import bhavcopy_raw, bhavcopy_udiff_raw
    import io as _io

    seen: dict[str, str] = {}
    for d in dates:
        fn = bhavcopy_udiff_raw if d >= UDIFF_SWITCH else bhavcopy_raw
        try:
            raw = fn(d)
            text = raw.decode(errors="ignore") if isinstance(raw, bytes) else raw
            df = pd.read_csv(_io.StringIO(text))
        except Exception:
            continue
        df.columns = [c.strip() for c in df.columns]
        sym_col = "TckrSymb" if "TckrSymb" in df.columns else "SYMBOL"
        ser_col = "SctySrs" if "SctySrs" in df.columns else "SERIES"
        if "ISIN" not in df.columns or sym_col not in df.columns:
            continue
        df = df[df[ser_col].astype(str).str.strip() == "EQ"]
        for sym, isin in zip(df[sym_col].astype(str).str.strip().str.upper(),
                             df["ISIN"].astype(str).str.strip()):
            if sym and isin and isin.lower() != "nan":
                seen.setdefault(sym, isin)
        time.sleep(0.3)
    if seen:
        con.executemany("INSERT OR REPLACE INTO instruments (symbol, isin) VALUES (?,?)",
                        list(seen.items()))
        con.commit()
    if verbose:
        eq = sum(1 for v in seen.values() if v.startswith(EQUITY_ISIN_PREFIX))
        print(f"  instrument map: {len(seen)} symbols, {eq} equities, "
              f"{len(seen) - eq} funds/ETFs")
    return len(seen)


def equity_symbols(con) -> set[str]:
    """Symbols known to be company equity. Empty set means the map is unbuilt."""
    return {r[0] for r in con.execute(
        "SELECT symbol FROM instruments WHERE isin LIKE ?",
        (EQUITY_ISIN_PREFIX + "%",))}


def have_days(con) -> set[str]:
    return {r[0] for r in con.execute("SELECT date FROM market_days")}


def ingest_range(con, start: dt.date, end: dt.date, pause: float = 0.35,
                 verbose: bool = True) -> int:
    """Download every trading day in a range. Skips days already held."""
    done = have_days(con)
    total, d = 0, start
    while d <= end:
        if d.weekday() >= 5 or d.isoformat() in done:
            d += dt.timedelta(days=1)
            continue
        n = save_day(con, d, fetch_day(d))
        total += n
        if verbose and n:
            print(f"  {d}: {n} stocks", flush=True)
        time.sleep(pause)
        d += dt.timedelta(days=1)
    return total


def universe_on(con, on_date: dt.date, top_n: int = 200,
                lookback_days: int = 60, min_days: int = 30) -> list[str]:
    """The N most liquid stocks actually trading as of a date.

    Liquidity, not index membership, defines the universe -- and it is measured
    only from data up to `on_date`, so the selection could have been made on the
    day. `min_days` requires a stock to have actually traded through the window,
    which drops the illiquid and the freshly listed without needing a separate
    listing-date table.
    """
    frm = (on_date - dt.timedelta(days=lookback_days)).isoformat()
    cur = con.execute(
        "SELECT symbol, AVG(turnover) t, COUNT(*) n FROM market "
        "WHERE date <= ? AND date > ? AND turnover IS NOT NULL "
        "GROUP BY symbol HAVING n >= ? ORDER BY t DESC LIMIT ?",
        (on_date.isoformat(), frm, min_days, top_n * 2))
    syms = [r[0] for r in cur.fetchall()]
    equities = equity_symbols(con)
    if equities:
        syms = [s for s in syms if s in equities]
    return syms[:top_n]
