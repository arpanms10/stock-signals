"""NSE index membership and the equity instrument master.

Used to seed the watchlist and to validate symbols on entry -- a typo like
RELAINCE otherwise produces no data silently for as long as it takes you to
notice, which could be a month.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import nse_client as nse

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"


def _index_list(url: str, filename: str) -> pd.DataFrame:
    df = nse.fetch_csv(url, cache_path=CACHE_DIR / filename, max_age_days=7)
    df = df.rename(columns={"Company Name": "name", "Industry": "sector",
                            "Symbol": "symbol", "ISIN Code": "isin"})
    df["symbol"] = df["symbol"].str.strip().str.upper()
    df["name"] = df["name"].str.strip()
    df["sector"] = df["sector"].fillna("").str.strip()
    return df[["symbol", "name", "sector", "isin"]]


def nifty50() -> pd.DataFrame:
    return _index_list(nse.NIFTY50_LIST_URL, "ind_nifty50list.csv")


def nifty200() -> pd.DataFrame:
    """Momentum needs dispersion to rank across; 50 names is too few."""
    return _index_list(nse.NIFTY200_LIST_URL, "ind_nifty200list.csv")


def nifty500() -> pd.DataFrame:
    return _index_list(nse.NIFTY500_LIST_URL, "ind_nifty500list.csv")


def equity_master() -> pd.DataFrame:
    """Every symbol listed on NSE's equity segment."""
    df = nse.fetch_csv(nse.EQUITY_LIST_URL, cache_path=CACHE_DIR / "EQUITY_L.csv",
                       max_age_days=7)
    df = df.rename(columns={"SYMBOL": "symbol", "NAME OF COMPANY": "name",
                            "SERIES": "series"})
    df["symbol"] = df["symbol"].str.strip().str.upper()
    return df


def valid_symbols() -> set[str]:
    try:
        return set(equity_master()["symbol"])
    except Exception:
        return set()  # offline: skip validation rather than block the user


def validate(symbols: list[str]) -> tuple[list[str], list[str]]:
    """Split symbols into (known, unknown). Unknown is empty when offline."""
    known_all = valid_symbols()
    if not known_all:
        return list(symbols), []
    known, unknown = [], []
    for s in symbols:
        (known if s.strip().upper() in known_all else unknown).append(s.strip().upper())
    return known, unknown
