"""Historical F&O chains from NSE's daily F&O bhavcopy, for the validation.

The live option chain is a snapshot with no history, so measuring whether the
OI range ever worked needs another source. The F&O bhavcopy is the day's
end-of-day record of every futures and options contract: OI, change in OI,
volume, close, and -- in the UDiFF format -- the underlying's close and lot.

Coverage: the UDiFF file exists from January 2024 (404 before that). The older
`foDDMMMYYYYbhav.csv` goes back further but has no underlying price, and the
local databases hold no NIFTY 50 / BANK NIFTY history to supply it, so it is
not used. See docs/f_o/validation.md for what that does to the index sample.

What is kept, so the history stays small (~200 underlyings x every day would
be millions of strike rows):

  fo_spot    every day: each underlying's close and its near monthly expiry.
             The outcome (close on expiry day) and the path come from here.
  fo_chain   the near-monthly chain, but only on days a fixed number of
             sessions before expiry (OFFSETS) -- the moments the validation
             asks "what did OI say then?"

Traps:
  * OI here is in SHARES; the live API reports CONTRACTS. Divided by the
    lot size here so a stored chain reads like a live one. (PCR, walls and
    max pain are ratios or argmaxes within one chain, so they are unaffected
    either way -- but the numbers should not change meaning between sources.)
  * Prices are UNADJUSTED. A split or bonus inside a cycle moves both spot
    and strikes; the validation drops cycles whose spot moved implausibly
    (see run_fno_backtest.py) rather than guessing a factor.
  * A close of an option that did not trade is a carried / theoretical
    price, so it becomes NaN, exactly as an untraded LTP does live.
  * Sessions are counted as weekdays; an exchange holiday can shift an
    offset day by one, or skip it for that cycle. Harmless for a validation
    of this kind, and it keeps ingestion single-pass.
"""
from __future__ import annotations

import datetime as dt
import io
import sqlite3
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from data.sources import nse_client as nse
from strategy.fno import sessions_to

DB_PATH = Path(__file__).resolve().parent / "fo_history.db"
URL = ("https://nsearchives.nseindia.com/content/fo/"
       "BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip")
FIRST_DAY = dt.date(2024, 1, 1)
OFFSETS = (20, 10, 5)          # sessions before expiry at which chains are kept
WINDOW_PCT = 15.0              # strikes kept within this % of spot

SCHEMA = """
CREATE TABLE IF NOT EXISTS fo_days (
    date TEXT PRIMARY KEY,
    rows INTEGER,
    fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS fo_spot (
    date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    kind TEXT,
    spot REAL,
    near_expiry TEXT,
    lot INTEGER,
    PRIMARY KEY (date, symbol)
);
CREATE TABLE IF NOT EXISTS fo_chain (
    date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    expiry TEXT NOT NULL,
    sessions INTEGER,
    spot REAL,
    strike REAL NOT NULL,
    ce_oi REAL, ce_chg_oi REAL, ce_vol REAL, ce_ltp REAL,
    pe_oi REAL, pe_chg_oi REAL, pe_vol REAL, pe_ltp REAL,
    PRIMARY KEY (date, symbol, expiry, strike)
);
CREATE INDEX IF NOT EXISTS idx_fo_spot_symbol ON fo_spot(symbol, date);
"""

KIND = {"IDO": "index", "IDF": "index", "STO": "equity", "STF": "equity"}


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


# ----------------------------------------------------------------- parsing

def normalise(raw: pd.DataFrame) -> pd.DataFrame:
    """UDiFF F&O bhavcopy -> one row per contract, tidy names."""
    df = raw[raw["FinInstrmTp"].isin(KIND)].copy()
    out = pd.DataFrame({
        "symbol": df["TckrSymb"].str.strip(),
        "kind": df["FinInstrmTp"].map(KIND),
        "instr": np.where(df["FinInstrmTp"].str.endswith("F"), "fut", "opt"),
        "expiry": pd.to_datetime(df["XpryDt"]).dt.date,
        "strike": pd.to_numeric(df["StrkPric"], errors="coerce"),
        "opt": df["OptnTp"].where(df["OptnTp"].isin(["CE", "PE"])),
        "close": pd.to_numeric(df["ClsPric"], errors="coerce"),
        "oi": pd.to_numeric(df["OpnIntrst"], errors="coerce").fillna(0),
        "chg_oi": pd.to_numeric(df["ChngInOpnIntrst"], errors="coerce").fillna(0),
        "vol": pd.to_numeric(df["TtlTradgVol"], errors="coerce").fillna(0),
        "spot": pd.to_numeric(df["UndrlygPric"], errors="coerce"),
        "lot": pd.to_numeric(df["NewBrdLotQty"], errors="coerce"),
    })
    return out.reset_index(drop=True)


def near_monthly(norm: pd.DataFrame, day: dt.date) -> pd.Series:
    """symbol -> nearest monthly expiry on or after `day`.

    Futures only exist on monthly expiries, so the futures rows ARE the
    monthly calendar -- no date arithmetic, and holiday-shifted expiries come
    out right.
    """
    fut = norm[(norm["instr"] == "fut") & (norm["expiry"] >= day)]
    return fut.groupby("symbol")["expiry"].min()


def spots(norm: pd.DataFrame, day: dt.date) -> pd.DataFrame:
    near = near_monthly(norm, day)
    first = norm.groupby("symbol").agg(kind=("kind", "first"), spot=("spot", "median"))
    lot = (norm[norm["instr"] == "fut"].merge(near.rename("e"), left_on="symbol",
                                              right_index=True)
           .query("expiry == e").groupby("symbol")["lot"].first())
    out = first.join(near.rename("near_expiry")).join(lot)
    return out.dropna(subset=["spot", "near_expiry"]).reset_index()


def chain_of(norm: pd.DataFrame, symbol: str, expiry: dt.date,
             window_pct: float = WINDOW_PCT) -> pd.DataFrame:
    """One symbol-expiry as a live-shaped chain (see nse_derivatives.parse_chain),
    OI in contracts, untraded closes NaN, no bid/ask or IV (not in the file)."""
    o = norm[(norm["symbol"] == symbol) & (norm["expiry"] == expiry)
             & (norm["instr"] == "opt")]
    if o.empty:
        return pd.DataFrame()
    spot = float(o["spot"].median())
    lot = o["lot"].where(o["lot"] > 0).fillna(1)
    o = o.assign(oi=o["oi"] / lot, chg_oi=o["chg_oi"] / lot,
                 ltp=o["close"].where(o["vol"] > 0))
    wide = o.pivot_table(index="strike", columns="opt",
                         values=["oi", "chg_oi", "vol", "ltp"], aggfunc="first")
    wide.columns = [f"{side.lower()}_{field}" for field, side in wide.columns]
    for side in ("ce", "pe"):
        for f in ("oi", "chg_oi", "vol"):
            col = f"{side}_{f}"
            wide[col] = wide[col].fillna(0) if col in wide else 0.0
        if f"{side}_ltp" not in wide:
            wide[f"{side}_ltp"] = np.nan
        for f in ("iv", "bid", "ask"):
            wide[f"{side}_{f}"] = np.nan
    wide = wide.reset_index()
    lo, hi = spot * (1 - window_pct / 100), spot * (1 + window_pct / 100)
    return wide[(wide["strike"] >= lo) & (wide["strike"] <= hi)].reset_index(drop=True)


# ---------------------------------------------------------------- fetching

def fetch_day(d: dt.date) -> pd.DataFrame:
    """The day's file, normalised. Empty on a holiday (NSE answers 404)."""
    try:
        r = nse.session().get(URL.format(d=d), timeout=60)
    except Exception:
        return pd.DataFrame()
    if r.status_code != 200 or r.content[:2] != b"PK":
        return pd.DataFrame()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        raw = pd.read_csv(z.open(z.namelist()[0]), low_memory=False)
    return normalise(raw)


CHAIN_COLS = ["strike", "ce_oi", "ce_chg_oi", "ce_vol", "ce_ltp",
              "pe_oi", "pe_chg_oi", "pe_vol", "pe_ltp"]


def save_day(con, d: dt.date, norm: pd.DataFrame,
             offsets: tuple[int, ...] = OFFSETS) -> tuple[int, int]:
    """Store the day's spots, and chains for symbols at an offset day."""
    iso = d.isoformat()
    if norm.empty:
        con.execute("INSERT OR REPLACE INTO fo_days VALUES (?,?,?)",
                    (iso, 0, dt.datetime.now().isoformat(timespec="seconds")))
        con.commit()
        return 0, 0
    sp = spots(norm, d)
    con.executemany(
        "INSERT OR REPLACE INTO fo_spot VALUES (?,?,?,?,?,?)",
        [(iso, r.symbol, r.kind, float(r.spot), r.near_expiry.isoformat(),
          None if pd.isna(r.lot) else int(r.lot)) for r in sp.itertuples()])
    n_chain = 0
    for r in sp.itertuples():
        k = sessions_to(d, r.near_expiry)
        if k not in offsets:
            continue
        ch = chain_of(norm, r.symbol, r.near_expiry)
        if ch.empty:
            continue
        rows = [(iso, r.symbol, r.near_expiry.isoformat(), k, float(r.spot))
                + tuple(None if pd.isna(x) else float(x) for x in t)
                for t in ch[CHAIN_COLS].itertuples(index=False, name=None)]
        con.executemany(
            f"INSERT OR REPLACE INTO fo_chain VALUES ({','.join('?' * 14)})", rows)
        n_chain += 1
    con.execute("INSERT OR REPLACE INTO fo_days VALUES (?,?,?)",
                (iso, len(sp), dt.datetime.now().isoformat(timespec="seconds")))
    con.commit()
    return len(sp), n_chain


def ingest_range(con, start: dt.date, end: dt.date, pause: float = 0.35,
                 verbose: bool = True) -> int:
    """Every weekday in the range not already held. Safe to re-run."""
    done = {r[0] for r in con.execute("SELECT date FROM fo_days")}
    total, d = 0, max(start, FIRST_DAY)
    while d <= end:
        if d.weekday() < 5 and d.isoformat() not in done:
            n, c = save_day(con, d, fetch_day(d))
            total += n
            if verbose and n:
                print(f"  {d}: {n} underlyings, {c} chains kept", flush=True)
            time.sleep(pause)
        d += dt.timedelta(days=1)
    return total


# ----------------------------------------------------------------- reading

def load_chain(con, date: str, symbol: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT * FROM fo_chain WHERE date=? AND symbol=? ORDER BY strike",
        con, params=(date, symbol))
    for side in ("ce", "pe"):
        for f in ("iv", "bid", "ask"):
            df[f"{side}_{f}"] = np.nan
    return df


def observations(con) -> pd.DataFrame:
    """Every (date, symbol, expiry, sessions, spot) with a stored chain."""
    return pd.read_sql_query(
        "SELECT DISTINCT date, symbol, expiry, sessions, spot FROM fo_chain "
        "ORDER BY date, symbol", con)


def spot_history(con) -> pd.DataFrame:
    return pd.read_sql_query("SELECT date, symbol, kind, spot FROM fo_spot", con)
