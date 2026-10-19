"""The Today ranges: built from the F&O history, logged forward, scored later.

run_range_backtest.py says how the bands WOULD have done. This file keeps the
record of how they actually do, from bands written down before the close they
are scored against existed.

  history/ranges/YYYY-MM.csv   one row per (price_date, symbol) for EVERY F&O
                               underlying -- not your holdings, which stay out
                               of git (see .gitignore) -- with both the
                               implied and the realised band for each horizon

Facts only, append-only: a price_date already recorded is left alone. The
outcome (the close h sessions later, inside or not) is not stored; score()
recomputes it from data/fo_history.db whenever it is read.
"""
from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import numpy as np
import pandas as pd

from strategy import ranges as rg

ROOT = Path(__file__).parent
STORE = ROOT / "history" / "ranges"
MAX_LOG_MOVE = math.log(1.6)      # same split filter as run_range_backtest.py
RESULTS_LOOKAHEAD_DAYS = 10
MARKET_CLOSE = "15:30:00"

COLUMNS = ["price_date", "recorded_at", "symbol", "kind", "spot", "sessions_left",
           "iv_used", "iv_from", "sigma_implied", "sigma_realised", "results_on"]
for _h in rg.HORIZONS:
    COLUMNS += [f"implied_low_{_h}", f"implied_high_{_h}",
                f"realised_low_{_h}", f"realised_high_{_h}"]


# --------------------------------------------------------------- building

def _results_ahead(con, day: str) -> dict[str, str]:
    end = (dt.date.fromisoformat(day) + dt.timedelta(days=RESULTS_LOOKAHEAD_DAYS)).isoformat()
    rows = con.execute("SELECT symbol, MIN(date) FROM fo_results WHERE date > ? AND date <= ? "
                       "GROUP BY symbol", (day, end)).fetchall()
    return dict(rows)


def build(con, day: str | None = None) -> pd.DataFrame:
    """Every F&O underlying's bands as of the close of `day` (default: the
    latest day in data/fo_history.db)."""
    day = day or con.execute("SELECT MAX(date) FROM fo_spot").fetchone()[0]
    if day is None:
        return pd.DataFrame(columns=COLUMNS)
    start = (dt.date.fromisoformat(day) - dt.timedelta(days=60)).isoformat()
    hist = pd.read_sql_query(
        "SELECT date, symbol, kind, spot, sessions_left, iv_straddle, iv_next "
        "FROM fo_spot WHERE date > ? AND date <= ? ORDER BY date", con, params=(start, day))
    hist = hist[hist["spot"] > 0]
    res = _results_ahead(con, day)
    rows = []
    for sym, g in hist.groupby("symbol"):
        last = g.iloc[-1]
        if last["date"] != day:
            continue
        sig_i, src = rg.implied_sigma(last["iv_straddle"], last["iv_next"],
                                      int(last["sessions_left"]) if pd.notna(last["sessions_left"]) else None)
        sig_r = realised(g["spot"])
        r = {"price_date": day, "symbol": sym, "kind": last["kind"],
             "spot": round(float(last["spot"]), 2),
             "sessions_left": last["sessions_left"],
             "iv_used": round(sig_i * 100 * math.sqrt(rg.SESSIONS_PER_YEAR), 2) if sig_i else None,
             "iv_from": src, "sigma_implied": sig_i, "sigma_realised": sig_r,
             "results_on": res.get(sym)}
        for h in rg.HORIZONS:
            for method, s in (("implied", sig_i), ("realised", sig_r)):
                lo, hi = rg.band(float(last["spot"]), s, h)
                r[f"{method}_low_{h}"] = None if lo is None else round(lo, 2)
                r[f"{method}_high_{h}"] = None if hi is None else round(hi, 2)
        rows.append(r)
    return pd.DataFrame(rows, columns=[c for c in COLUMNS if c != "recorded_at"])


def realised(closes: pd.Series) -> float | None:
    """rg.realised_sigma with the backtest's split filter: a >60% one-day
    "return" is a split or bonus, not volatility."""
    c = pd.Series(closes, dtype=float).dropna()
    c = c[c > 0]
    r = np.log(c).diff()
    r = r[r.abs() < MAX_LOG_MOVE].dropna()
    if len(r) < rg.REALISED_WINDOW - 2:
        return None
    s = float(r.iloc[-rg.REALISED_WINDOW:].std(ddof=1))
    return s if s > 0 else None


# ---------------------------------------------------------------- the log

def log(root: Path = STORE) -> pd.DataFrame:
    files = sorted(Path(root).glob("*.csv"))
    if not files:
        return pd.DataFrame(columns=COLUMNS)
    return pd.concat([pd.read_csv(f) for f in files], ignore_index=True)


def recorded_dates(root: Path = STORE) -> set[str]:
    df = log(root)
    return set(df["price_date"].astype(str)) if not df.empty else set()


def record(df: pd.DataFrame, root: Path = STORE, now: dt.datetime | None = None) -> int:
    """Append one price_date's rows; a date already on file is left alone."""
    if df.empty:
        return 0
    day = str(df["price_date"].iloc[0])
    if day in recorded_dates(root):
        return 0
    df = df.copy()
    df["recorded_at"] = (now or dt.datetime.now()).isoformat(timespec="seconds")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{day[:7]}.csv"
    df = df[COLUMNS]
    for c in ("sigma_implied", "sigma_realised"):
        df[c] = df[c].astype(float).round(6)
    df.to_csv(path, mode="a", header=not path.exists(), index=False)
    return len(df)


# ---------------------------------------------------------------- scoring

def score(entries: pd.DataFrame, con) -> pd.DataFrame:
    """One row per (price_date, symbol, horizon, method) whose close is in:
    the band, the close h sessions later, inside or not, and whether a
    results date fell inside the window. Bands recorded after the close they
    are scored on are left out."""
    if entries.empty:
        return pd.DataFrame()
    days = [r[0] for r in con.execute("SELECT DISTINCT date FROM fo_spot ORDER BY date")]
    pos = {d: i for i, d in enumerate(days)}
    first = str(entries["price_date"].min())
    closes = pd.read_sql_query("SELECT date, symbol, spot FROM fo_spot WHERE date >= ?",
                               con, params=(first,))
    close = {(d, s): v for d, s, v in closes.itertuples(index=False)}
    out = []
    for r in entries.itertuples(index=False):
        i = pos.get(str(r.price_date))
        if i is None:
            continue
        rec = str(r.recorded_at)
        for h in rg.HORIZONS:
            if i + h >= len(days):
                continue
            end = days[i + h]
            # Only a band written down before its close counts: one recorded
            # after the end date's close was recorded knowing the answer.
            if rec >= f"{end}T{MARKET_CLOSE}":
                continue
            c = close.get((end, r.symbol))
            if c is None or not r.spot or abs(math.log(c / r.spot)) >= MAX_LOG_MOVE:
                continue
            res = (isinstance(r.results_on, str) and str(r.price_date) < r.results_on <= end)
            for method in ("implied", "realised"):
                lo, hi = getattr(r, f"{method}_low_{h}"), getattr(r, f"{method}_high_{h}")
                ok = rg.inside(lo, hi, c)
                if ok is None:
                    continue
                out.append({"price_date": r.price_date, "symbol": r.symbol, "kind": r.kind,
                            "horizon": h, "method": method, "end": end, "close": c,
                            "low": lo, "high": hi, "inside": ok, "results": res})
    return pd.DataFrame(out)


def summary(scored: pd.DataFrame, cal: dict | None = None) -> pd.DataFrame:
    """Hit rate by method, kind and horizon, next to the backtest's."""
    if scored.empty:
        return pd.DataFrame()
    rows = []
    groups = [(k, g) for k, g in scored.groupby(["method", "kind", "horizon"])]
    groups += [((m, "NIFTY", h), g) for (m, h), g in
               scored[scored["symbol"] == "NIFTY"].groupby(["method", "horizon"])]
    for (m, k, h), g in groups:
        rows.append({"method": m, "group": k, "horizon": h,
                     "days": g["price_date"].nunique(), "n": len(g),
                     "hit_pct": round(100 * g["inside"].mean(), 1),
                     "backtest_pct": rg.hit_rate(cal, m, "index" if k == "NIFTY" else k, h,
                                                 symbol=k)})
    return pd.DataFrame(rows)
