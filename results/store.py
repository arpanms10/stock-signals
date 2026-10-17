"""Scorecards and the quarterly lines behind them, on disk.

  data/results/scorecards.csv   one row per (symbol, quarter end): the card,
                                plus the price reaction once prices exist
  data/results/quarters.csv     one row per (symbol, quarter end): the parsed
                                filing, for trend charts and the backtest

Both live outside git, deliberately. The numbers are public, but which
symbols are on file is not: holdings are on the coverage list, and the
.gitignore keeps holdings out of the repo.

A revised filing (new XBRL URL for a quarter already on file) replaces the
old row; re-scoring the same filing is skipped.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

DIR = Path(__file__).resolve().parents[1] / "data" / "results"
CARDS = DIR / "scorecards.csv"
QUARTERS = DIR / "quarters.csv"
KEY = ["symbol", "to_date"]


def _read(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if "to_date" in df:
        df["to_date"] = pd.to_datetime(df["to_date"]).dt.date
    for col in ("broadcast",):
        if col in df:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    for col in ("reaction_date",):
        if col in df:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
    return df


def _upsert(path: Path, rows: list[dict] | pd.DataFrame) -> pd.DataFrame:
    new = pd.DataFrame(rows)
    if new.empty:
        return _read(path)
    old = _read(path)
    if not old.empty:
        keys = set(map(tuple, new[KEY].astype(str).values))
        old = old[[tuple(k) not in keys for k in old[KEY].astype(str).values]]
        new = pd.concat([old, new], ignore_index=True)
    new = new.sort_values(["symbol", "to_date"]).reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    new.to_csv(path, index=False)
    return new


def cards(path: Path = CARDS) -> pd.DataFrame:
    return _read(path)


def quarters(path: Path = QUARTERS) -> pd.DataFrame:
    return _read(path)


def save_cards(rows, path: Path = CARDS) -> pd.DataFrame:
    return _upsert(path, rows)


def save_quarters(rows, path: Path = QUARTERS) -> pd.DataFrame:
    return _upsert(path, rows)


def scored(df: pd.DataFrame, symbol: str, to_date: dt.date, xbrl: str) -> bool:
    """True when this exact filing is already scored."""
    if df.empty:
        return False
    hit = df[(df["symbol"] == symbol) & (df["to_date"] == to_date)]
    return not hit.empty and str(hit.iloc[-1].get("xbrl")) == str(xbrl)


def latest(df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Each symbol's newest scorecard."""
    df = cards() if df is None else df
    if df.empty:
        return df
    return (df.sort_values("to_date").groupby("symbol", as_index=False).tail(1)
            .reset_index(drop=True))
