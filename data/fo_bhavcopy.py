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
from strategy import fno
from strategy.fno import sessions_to

DB_PATH = Path(__file__).resolve().parent / "fo_history.db"
URL = ("https://nsearchives.nseindia.com/content/fo/"
       "BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip")
FIRST_DAY = dt.date(2024, 1, 1)
OFFSETS = (20, 10, 5)          # sessions before a MONTHLY expiry at which chains are kept
WEEKLY_OFFSETS = (4, 2, 1)     # sessions before a WEEKLY index expiry
RISK_FREE_PCT = 6.5            # for backing IV out of closes; small effect at these tenors
WINDOW_PCT = 15.0              # strikes kept within this % of spot

SCHEMA = """
CREATE TABLE IF NOT EXISTS fo_days (
    date TEXT PRIMARY KEY,
    rows INTEGER,
    fetched_at TEXT
);
-- One row per underlying per day. fut_high/low are the near-monthly
-- future's; spot high/low are estimated from them minus the closing basis
-- (the file has no underlying high/low). iv_* are annualised %, from closes:
-- iv_straddle from the ATM straddle (fno.straddle_iv), wings by inverting
-- Black-Scholes at spot -/+ one straddle, iv_next from the next monthly.
CREATE TABLE IF NOT EXISTS fo_spot (
    date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    kind TEXT,
    spot REAL,
    near_expiry TEXT,
    lot INTEGER,
    fut_close REAL, fut_high REAL, fut_low REAL,
    spot_high REAL, spot_low REAL,
    days_left INTEGER, sessions_left INTEGER,
    straddle REAL, iv_straddle REAL, iv_put_wing REAL, iv_call_wing REAL,
    next_expiry TEXT, iv_next REAL,
    src TEXT,
    PRIMARY KEY (date, symbol)
);
CREATE TABLE IF NOT EXISTS fo_chain (
    date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    expiry TEXT NOT NULL,
    cycle TEXT,                -- 'monthly' | 'weekly'
    sessions INTEGER,
    spot REAL,
    strike REAL NOT NULL,
    ce_oi REAL, ce_chg_oi REAL, ce_vol REAL, ce_ltp REAL,
    pe_oi REAL, pe_chg_oi REAL, pe_vol REAL, pe_ltp REAL,
    PRIMARY KEY (date, symbol, expiry, strike)
);
CREATE INDEX IF NOT EXISTS idx_fo_spot_symbol ON fo_spot(symbol, date);
-- Daily index OHLC (niftyindices, via jugaad-data): the spot, and the true
-- high/low, for the pre-2024 old-format history, which carries neither.
-- Announced quarterly-results dates (data/sources/nse_events.py), and which
-- months have been fetched.
CREATE TABLE IF NOT EXISTS fo_results (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    purpose TEXT,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS fo_results_months (month TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS fo_index_ohlc (
    date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    high REAL, low REAL, close REAL,
    PRIMARY KEY (date, symbol)
);
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
        "high": pd.to_numeric(df["HghPric"], errors="coerce"),
        "low": pd.to_numeric(df["LwPric"], errors="coerce"),
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


def _atm(ce: dict, pe: dict, spot: float):
    """(strike, call close, put close) at the strike nearest spot where BOTH
    legs traded, or None. ce/pe: strike -> close, traded strikes only."""
    both = [k for k in ce if k in pe]
    if not both:
        return None
    k = min(both, key=lambda x: abs(x - spot))
    # A "nearest traded" strike far from spot is not an ATM straddle.
    if abs(k - spot) > 0.05 * spot:
        return None
    return float(k), float(ce[k]), float(pe[k])


def _traded(o: pd.DataFrame) -> tuple[dict, dict]:
    t = o[o["vol"] > 0]
    ce, pe = t[t["opt"] == "CE"], t[t["opt"] == "PE"]
    return (dict(zip(ce["strike"].to_numpy(float), ce["close"].to_numpy(float))),
            dict(zip(pe["strike"].to_numpy(float), pe["close"].to_numpy(float))))


def _nearest(d: dict, level: float, keep) -> tuple | None:
    ks = [k for k in d if keep(k)]
    if not ks:
        return None
    k = min(ks, key=lambda x: abs(x - level))
    return k, d[k]


def features(norm: pd.DataFrame, day: dt.date, holidays=()) -> pd.DataFrame:
    """One row per underlying: spot, the near-monthly future's range, the
    straddle and the IV measures behind IV percentile, skew and term
    structure. Wing IVs are solved in one vectorised batch."""
    near = near_monthly(norm, day)
    futs = norm[(norm["instr"] == "fut") & (norm["expiry"] >= day)]
    fut_by = {k: v.sort_values("expiry") for k, v in futs.groupby("symbol")}
    opts = norm[(norm["instr"] == "opt") & (norm["expiry"] >= day)]
    opt_by = {k: _traded(v) for k, v in opts.groupby(["symbol", "expiry"])}
    spot_by = norm.groupby("symbol")["spot"].median()
    kind_by = norm.groupby("symbol")["kind"].first()
    rows, wings = [], []          # wings: (row index, column, S, K, T, price, call)
    for sym, e1 in near.items():
        spot = float(spot_by.get(sym, np.nan))
        if not spot > 0:
            continue
        f = fut_by[sym]
        f1 = f[f["expiry"] == e1]
        exps = list(f["expiry"])
        e2 = exps[1] if len(exps) > 1 else None
        fc = float(f1["close"].iloc[0]) if len(f1) else np.nan
        fh = float(f1["high"].iloc[0]) if len(f1) else np.nan
        fl = float(f1["low"].iloc[0]) if len(f1) else np.nan
        basis = fc - spot
        days = (e1 - day).days
        ce, pe = opt_by.get((sym, e1), ({}, {}))
        atm = _atm(ce, pe, spot)
        strad = atm[1] + atm[2] if atm else None
        i = len(rows)
        if strad:
            T = fno.year_frac(days)
            p = _nearest(pe, spot - strad, lambda k: k < spot)
            c = _nearest(ce, spot + strad, lambda k: k > spot)
            if p:
                wings.append((i, "iv_put_wing", spot, p[0], T, p[1], False))
            if c:
                wings.append((i, "iv_call_wing", spot, c[0], T, c[1], True))
        iv2 = None
        if e2 is not None:
            atm2 = _atm(*opt_by.get((sym, e2), ({}, {})), spot)
            if atm2:
                iv2 = fno.straddle_iv(spot, atm2[1] + atm2[2], (e2 - day).days)
        rows.append({
            "symbol": sym, "kind": kind_by[sym], "spot": spot,
            "near_expiry": e1, "lot": f1["lot"].iloc[0] if len(f1) else np.nan,
            "fut_close": fc, "fut_high": fh, "fut_low": fl,
            # Underlying high/low estimated as the future's minus the closing
            # basis. Clipped so it never sits inside the close.
            "spot_high": max(fh - basis, spot) if np.isfinite(fh) else np.nan,
            "spot_low": min(fl - basis, spot) if np.isfinite(fl) else np.nan,
            "days_left": days, "sessions_left": sessions_to(day, e1, holidays),
            "straddle": strad, "iv_straddle": fno.straddle_iv(spot, strad, days),
            "iv_put_wing": None, "iv_call_wing": None,
            "next_expiry": e2, "iv_next": iv2,
        })
    if wings:
        w = np.array([x[2:6] for x in wings], dtype=float)
        calls = np.array([x[6] for x in wings])
        iv = fno.implied_vol(w[:, 3], w[:, 0], w[:, 1], w[:, 2], RISK_FREE_PCT / 100, calls)
        for (i, col, *_), v in zip(wings, iv):
            rows[i][col] = None if np.isnan(v) else 100 * float(v)
    return pd.DataFrame(rows)


def spots(norm: pd.DataFrame, day: dt.date) -> pd.DataFrame:
    """Lightweight: symbol, kind, spot, near monthly expiry, lot."""
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

def fetch_day(d: dt.date) -> pd.DataFrame | None:
    """The day's file, normalised.

    Empty frame: NSE has no file for the day (404) -- a holiday, or not
    published yet. None: the request FAILED (network, throttling, a page
    instead of a zip). The two must not be confused: recording a failure as
    "no data" leaves a permanent hole, because later runs skip days held."""
    try:
        r = nse.session().get(URL.format(d=d), timeout=60)
    except Exception:
        return None
    if r.status_code == 404:
        return pd.DataFrame()
    if r.status_code != 200 or r.content[:2] != b"PK":
        return None
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        raw = pd.read_csv(z.open(z.namelist()[0]), low_memory=False)
    return normalise(raw)


CHAIN_COLS = ["strike", "ce_oi", "ce_chg_oi", "ce_vol", "ce_ltp",
              "pe_oi", "pe_chg_oi", "pe_vol", "pe_ltp"]


SPOT_COLS = ["date", "symbol", "kind", "spot", "near_expiry", "lot", "fut_close",
             "fut_high", "fut_low", "spot_high", "spot_low", "days_left",
             "sessions_left", "straddle", "iv_straddle", "iv_put_wing",
             "iv_call_wing", "next_expiry", "iv_next", "src"]


def _val(x):
    if x is None:
        return None
    if isinstance(x, dt.date):
        return x.isoformat()
    if isinstance(x, (float, np.floating)) and np.isnan(x):
        return None
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return float(x)
    return x


def _store_chain(con, iso, sym, expiry, cycle, k, spot, ch) -> None:
    rows = [(iso, sym, expiry.isoformat(), cycle, k, float(spot))
            + tuple(None if pd.isna(x) else float(x) for x in t)
            for t in ch[CHAIN_COLS].itertuples(index=False, name=None)]
    con.executemany(
        f"INSERT OR REPLACE INTO fo_chain VALUES ({','.join('?' * 15)})", rows)


def save_day(con, d: dt.date, norm: pd.DataFrame,
             offsets: tuple[int, ...] = OFFSETS, holidays=(),
             weekly_offsets: tuple[int, ...] = WEEKLY_OFFSETS,
             src: str = "udiff") -> tuple[int, int]:
    """Store the day's features, and chains at the offset days: before each
    monthly expiry, and before each weekly index expiry."""
    iso = d.isoformat()
    if norm.empty:
        con.execute("INSERT OR REPLACE INTO fo_days VALUES (?,?,?)",
                    (iso, 0, dt.datetime.now().isoformat(timespec="seconds")))
        con.commit()
        return 0, 0
    ft = features(norm, d, holidays)
    ft["date"], ft["src"] = iso, src
    con.executemany(
        f"INSERT OR REPLACE INTO fo_spot ({','.join(SPOT_COLS)}) "
        f"VALUES ({','.join('?' * len(SPOT_COLS))})",
        [tuple(_val(r[c]) for c in SPOT_COLS) for r in ft.to_dict("records")])
    n_chain = 0
    for r in ft.itertuples():
        k = int(r.sessions_left)
        if k in offsets:
            ch = chain_of(norm, r.symbol, r.near_expiry)
            if not ch.empty:
                _store_chain(con, iso, r.symbol, r.near_expiry, "monthly", k, r.spot, ch)
                n_chain += 1
        if r.kind != "index":
            continue
        # Weekly: the nearest option expiry, when it is not the monthly.
        o = norm[(norm["symbol"] == r.symbol) & (norm["instr"] == "opt")
                 & (norm["expiry"] >= d)]
        if o.empty:
            continue
        w = o["expiry"].min()
        if w == r.near_expiry:
            continue
        kw = sessions_to(d, w, holidays)
        if kw in weekly_offsets:
            ch = chain_of(norm, r.symbol, w)
            if not ch.empty:
                _store_chain(con, iso, r.symbol, w, "weekly", kw, r.spot, ch)
                n_chain += 1
    con.execute("INSERT OR REPLACE INTO fo_days VALUES (?,?,?)",
                (iso, len(ft), dt.datetime.now().isoformat(timespec="seconds")))
    con.commit()
    return len(ft), n_chain


def ingest_range(con, start: dt.date, end: dt.date, pause: float = 0.35,
                 verbose: bool = True) -> int:
    """Every weekday in the range not already held. Safe to re-run."""
    from data.sources.nse_derivatives import holidays_between
    done = {r[0] for r in con.execute("SELECT date FROM fo_days")}
    start = max(start, FIRST_DAY)
    # Expiries can sit up to a few months past `end`; holidays out to then.
    hol = holidays_between(start, end + dt.timedelta(days=120))
    total, d, failed = 0, start, []
    while d <= end:
        if d.weekday() < 5 and d.isoformat() not in done and d not in hol:
            norm = fetch_day(d)
            if _keep(d, norm):
                n, c = save_day(con, d, norm, holidays=hol)
                total += n
                if verbose and n:
                    print(f"  {d}: {n} underlyings, {c} chains kept", flush=True)
            elif norm is None:
                failed.append(d)
            time.sleep(pause)
        d += dt.timedelta(days=1)
    if failed and verbose:
        print(f"  {len(failed)} day(s) failed to download and will be retried next run: "
              + ", ".join(map(str, failed[:10])), flush=True)
    return total


RECORD_EMPTY_AFTER_DAYS = 7


def _keep(d: dt.date, norm) -> bool:
    """Store this day? A failed request: no, retry next run. NSE has no file:
    record it as empty only once it is a week old -- a weekday file that is
    missing for a week is not coming; a recent one may just not be up yet."""
    if norm is None:
        return False
    if norm.empty:
        return (dt.date.today() - d).days > RECORD_EMPTY_AFTER_DAYS
    return True


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
    return pd.read_sql_query("SELECT * FROM fo_spot", con)


# ------------------------------------------------- pre-2024, indices only
# The old foDDMMMYYYYbhav.csv has no underlying price and no lot size, so it
# is used for indices only, with spot from the index's own daily history.
# OI stays in shares (no lot to divide by): every OI read is a ratio or an
# argmax within one chain, so the unit does not matter, but these chains are
# not comparable in absolute OI with the post-2024 ones.

OLD_URL = ("https://nsearchives.nseindia.com/content/historical/DERIVATIVES/"
           "{d:%Y}/{mon}/fo{d:%d}{mon}{d:%Y}bhav.csv.zip")
OLD_FIRST_DAY = dt.date(2019, 1, 1)
INDEX_NAMES = {"NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK",
               "FINNIFTY": "NIFTY FINANCIAL SERVICES",
               "MIDCPNIFTY": "NIFTY MIDCAP SELECT", "NIFTYNXT50": "NIFTY NEXT 50"}


def ingest_index_ohlc(con, start: dt.date, end: dt.date) -> int:
    """Daily OHLC for every F&O index, a year per request."""
    from jugaad_data.nse import index_df
    n = 0
    for sym, name in INDEX_NAMES.items():
        y = start
        while y <= end:
            hi = min(dt.date(y.year, 12, 31), end)
            try:
                df = index_df(symbol=name, from_date=y, to_date=hi)
            except Exception:
                df = pd.DataFrame()
            if len(df):
                rows = [(pd.Timestamp(r.HistoricalDate).date().isoformat(), sym,
                         _val(float(r.HIGH)), _val(float(r.LOW)), _val(float(r.CLOSE)))
                        for r in df.itertuples()]
                con.executemany("INSERT OR REPLACE INTO fo_index_ohlc VALUES (?,?,?,?,?)", rows)
                n += len(rows)
            con.commit()
            y = dt.date(y.year + 1, 1, 1)
            time.sleep(0.5)
    return n


def normalise_old(raw: pd.DataFrame, closes: dict[str, float]) -> pd.DataFrame:
    """Old-format F&O bhavcopy -> the normalise() shape, indices only.
    `closes`: symbol -> that day's index close (the spot)."""
    df = raw[raw["INSTRUMENT"].isin(["OPTIDX", "FUTIDX"])
             & raw["SYMBOL"].str.strip().isin(closes)].copy()
    sym = df["SYMBOL"].str.strip()
    return pd.DataFrame({
        "symbol": sym, "kind": "index",
        "instr": np.where(df["INSTRUMENT"] == "FUTIDX", "fut", "opt"),
        "expiry": pd.to_datetime(df["EXPIRY_DT"], format="%d-%b-%Y").dt.date,
        "strike": pd.to_numeric(df["STRIKE_PR"], errors="coerce"),
        "opt": df["OPTION_TYP"].where(df["OPTION_TYP"].isin(["CE", "PE"])),
        "close": pd.to_numeric(df["CLOSE"], errors="coerce"),
        "high": pd.to_numeric(df["HIGH"], errors="coerce"),
        "low": pd.to_numeric(df["LOW"], errors="coerce"),
        "oi": pd.to_numeric(df["OPEN_INT"], errors="coerce").fillna(0),
        "chg_oi": pd.to_numeric(df["CHG_IN_OI"], errors="coerce").fillna(0),
        "vol": pd.to_numeric(df["CONTRACTS"], errors="coerce").fillna(0),
        "spot": sym.map(closes).astype(float),
        "lot": np.nan,
    }).reset_index(drop=True)


def fetch_day_old(d: dt.date, closes: dict[str, float]) -> pd.DataFrame | None:
    """As fetch_day: empty on 404, None when the request failed."""
    url = OLD_URL.format(d=d, mon=d.strftime("%b").upper())
    try:
        r = nse.session().get(url, timeout=60)
    except Exception:
        return None
    if r.status_code == 404:
        return pd.DataFrame()
    if r.status_code != 200 or r.content[:2] != b"PK":
        return None
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        raw = pd.read_csv(z.open(z.namelist()[0]), low_memory=False)
    return normalise_old(raw, closes)


def ingest_old_range(con, start: dt.date, end: dt.date, pause: float = 0.35,
                     verbose: bool = True) -> int:
    """Index chains from the old format. Trading days are the index's own,
    so holidays need no calendar: a weekday with no index close is one."""
    start = max(start, OLD_FIRST_DAY)
    end = min(end, FIRST_DAY - dt.timedelta(days=1))
    ohlc = pd.read_sql_query("SELECT * FROM fo_index_ohlc WHERE date BETWEEN ? AND ?",
                             con, params=(start.isoformat(),
                                          (end + dt.timedelta(days=120)).isoformat()))
    if ohlc.empty:
        raise RuntimeError("no index OHLC -- run ingest_index_ohlc first")
    days = sorted({dt.date.fromisoformat(x) for x in ohlc["date"]})
    span = pd.bdate_range(days[0], days[-1]).date
    hol = sorted(set(span) - set(days))
    by_day = {d: g.set_index("symbol") for d, g in ohlc.groupby("date")}
    done = {r[0] for r in con.execute("SELECT date FROM fo_days")}
    total = 0
    for d in days:
        if d > end or d.isoformat() in done:
            continue
        o = by_day[d.isoformat()]
        closes = o["close"].dropna().to_dict()
        norm = fetch_day_old(d, closes)
        if not _keep(d, norm):
            if verbose:
                print(f"  {d}: download failed, will retry next run", flush=True)
            continue
        n, c = save_day(con, d, norm, holidays=hol, src="old")
        if n:
            # The index's real high/low, not one estimated from the future.
            for sym in o.index:
                con.execute("UPDATE fo_spot SET spot_high=?, spot_low=? "
                            "WHERE date=? AND symbol=?",
                            (_val(o.loc[sym, "high"]), _val(o.loc[sym, "low"]),
                             d.isoformat(), sym))
            con.commit()
        total += n
        if verbose and n:
            print(f"  {d}: {n} indices, {c} chains kept", flush=True)
        time.sleep(pause)
    return total


def ingest_results(con, start: dt.date, end: dt.date) -> int:
    """Results dates month by month; months already fetched are skipped,
    except the latest two, which keep gaining announcements."""
    from data.sources import nse_events
    done = {r[0] for r in con.execute("SELECT month FROM fo_results_months")}
    n, m = 0, start.replace(day=1)
    recent = (dt.date.today().replace(day=1) - dt.timedelta(days=32)).replace(day=1)
    while m <= end:
        key = m.strftime("%Y-%m")
        nxt = (m + dt.timedelta(days=32)).replace(day=1)
        if key not in done or m >= recent:
            df = nse_events.fetch(m, nxt - dt.timedelta(days=1))
            con.executemany("INSERT OR REPLACE INTO fo_results VALUES (?,?,?)",
                            [(r.symbol, r.date.isoformat(), r.purpose) for r in df.itertuples()])
            con.execute("INSERT OR REPLACE INTO fo_results_months VALUES (?)", (key,))
            con.commit()
            n += len(df)
        m = nxt
    return n
