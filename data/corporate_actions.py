"""Split and bonus adjustment.

NSE serves raw traded prices. A 1:1 bonus halves the quoted price overnight,
which an unadjusted RSI reads as a 50% crash and an unadjusted moving average
reads as a death cross -- so every indicator downstream would fire false exits
on the healthiest stocks. This module makes the price series continuous.

Ratios follow Indian market convention:
  "Bonus a:b"  -> a new shares for every b held  -> price factor (a+b)/b
  "Face Value Split From Rs X To Rs Y"           -> price factor X/Y
A single announcement can carry both (Bajaj Finance, Sep 2016), and two separate
announcements can share one ex-date (Bajaj Finance, Jun 2025); both compound.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

import pandas as pd

from .sources import nse_client as nse

CA_URL = "https://www.nseindia.com/api/corporates-corporateActions"

_BONUS_RE = re.compile(r"bonus\s+(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", re.I)
_SPLIT_RE = re.compile(
    r"split.*?from\s+(?:rs|re)\.?\s*(\d+(?:\.\d+)?)"
    r".*?to\s+(?:rs|re)\.?\s*(\d+(?:\.\d+)?)",
    re.I | re.S,
)
_RIGHTS_RE = re.compile(r"rights\s+\d+\s*:\s*\d+", re.I)


@dataclass
class Action:
    symbol: str
    ex_date: dt.date
    factor: float          # divide pre-ex-date prices by this
    kind: str              # "bonus", "split", "bonus+split"
    subject: str


def parse_factor(subject: str) -> tuple[float, str]:
    """Return (price factor, kind) for one announcement. 1.0 means no adjustment."""
    factor, kinds = 1.0, []
    m = _BONUS_RE.search(subject)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        if b > 0:
            factor *= (a + b) / b
            kinds.append("bonus")
    m = _SPLIT_RE.search(subject)
    if m:
        old_fv, new_fv = float(m.group(1)), float(m.group(2))
        if new_fv > 0:
            factor *= old_fv / new_fv
            kinds.append("split")
    return factor, "+".join(kinds)


def _parse_ex_date(raw: str) -> dt.date | None:
    raw = (raw or "").strip()
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def fetch(symbol: str, from_date: dt.date, to_date: dt.date) -> list[Action]:
    """Corporate actions for one symbol that require a price adjustment."""
    params = {
        "index": "equities",
        "symbol": symbol.upper(),
        "from_date": from_date.strftime("%d-%m-%Y"),
        "to_date": to_date.strftime("%d-%m-%Y"),
    }
    rows = nse.get(CA_URL, params=params).json()
    actions: list[Action] = []
    for r in rows:
        subject = (r.get("subject") or "").strip()
        ex = _parse_ex_date(r.get("exDate", ""))
        if ex is None:
            continue
        factor, kind = parse_factor(subject)
        if factor != 1.0:
            actions.append(Action(symbol.upper(), ex, factor, kind, subject))
    return sorted(actions, key=lambda a: a.ex_date)


def find_rights(symbol: str, from_date: dt.date, to_date: dt.date) -> list[str]:
    """Rights issues also move price but need the subscription price to adjust
    properly. We surface them for your judgement rather than guess."""
    params = {"index": "equities", "symbol": symbol.upper(),
              "from_date": from_date.strftime("%d-%m-%Y"),
              "to_date": to_date.strftime("%d-%m-%Y")}
    rows = nse.get(CA_URL, params=params).json()
    return [f"{r.get('exDate')}: {(r.get('subject') or '').strip()}"
            for r in rows if _RIGHTS_RE.search(r.get("subject") or "")]


def adjust(df: pd.DataFrame, actions: list[Action]) -> pd.DataFrame:
    """Back-adjust an OHLCV frame so the series is continuous.

    Prices before an ex-date are divided by the cumulative factor of every
    action on or after it; volumes are multiplied by the same factor so that
    traded value stays comparable across the break.
    """
    if df.empty or not actions:
        return df
    out = df.copy()
    dates = pd.to_datetime(out["date"]).dt.date

    # Cumulative factor applying to each row: product of all later actions.
    cum = pd.Series(1.0, index=out.index)
    for a in actions:
        cum *= pd.Series((dates < a.ex_date).astype(float) * (a.factor - 1) + 1,
                         index=out.index)

    for col in ("open", "high", "low", "close", "vwap", "prev_close"):
        if col in out.columns:
            out[col] = out[col] / cum
    for col in ("volume", "delivery_qty"):
        if col in out.columns:
            out[col] = out[col] * cum
    out["adj_factor"] = cum
    return out
