"""Market statistics per holding, each a provenance row like the financials.

From data already on disk, nothing fetched:

  signals.db   split-adjusted daily prices and the NIFTY 500 index: 52-week
               high and low, average volume, 1-year volatility, beta, price
               returns over 1, 3 and 5 years
  market.db    the raw (unadjusted) close, which is what a share costs today
  filings      shares outstanding (paid-up capital / face value, latest
               filing) -> market capitalisation = shares x raw close
  shareholding promoter % (NSE) -> non-promoter holding, a stand-in for
               free float (it also counts locked-in and strategic holders,
               so it overstates the float; labelled low confidence)

Historical, all of it: what happened, not what will.
"""
from __future__ import annotations

import math
from decimal import Decimal

import pandas as pd

from data.financials import CALC, CR, HIGH, LOW, MEDIUM, NA, PCT, row

SIGNALS = "NSE prices (signals.db, split-adjusted)"
MARKET = "NSE bhavcopy (market.db)"
SESSIONS_1Y = 250


def from_prices(symbol: str, px: pd.DataFrame, index: pd.DataFrame | None) -> list[dict]:
    """px: date-indexed frame with close, high, low, volume (adjusted)."""
    if px is None or px.empty:
        return []
    px = px.sort_index()
    last = px.index[-1]
    end = last.date() if hasattr(last, "date") else last
    out = []
    add = lambda m, v, unit, note="", conf=HIGH, method="calculated": out.append(  # noqa: E731
        row(symbol, m, "point", end, v, unit, SIGNALS, method=method, confidence=conf,
            note=note))
    yr = px.tail(SESSIONS_1Y)
    add("close_adjusted", float(px["close"].iloc[-1]), "₹", method="reported")
    if len(px) > 1:
        add("prev_close_adjusted", float(px["close"].iloc[-2]), "₹", method="reported")
    hi = yr["high"] if "high" in yr and yr["high"].notna().any() else yr["close"]
    lo = yr["low"] if "low" in yr and yr["low"].notna().any() else yr["close"]
    n = len(yr)
    note = f"{n} sessions to {end}" + ("" if n >= SESSIONS_1Y else ": less than a year")
    add("high_52w", float(hi.max()), "₹", note)
    add("low_52w", float(lo.min()), "₹", note)
    if "volume" in px:
        for k in (20, 60):
            v = px["volume"].tail(k)
            if v.notna().sum() >= k // 2:
                add(f"avg_volume_{k}d", float(v.mean()), "shares", f"mean of {k} sessions")
    rets = px["close"].pct_change().dropna()
    r1 = rets.tail(SESSIONS_1Y)
    if len(r1) >= 120:
        add("volatility_1y_pct", float(r1.std() * math.sqrt(252) * 100), PCT,
            "annualised std. dev. of daily returns")
    if index is not None and not index.empty and len(r1) >= 120:
        ir = index["close"].sort_index().pct_change().dropna()
        both = pd.concat([r1, ir], axis=1, join="inner").dropna()
        if len(both) >= 120 and both.iloc[:, 1].var() > 0:
            beta = both.iloc[:, 0].cov(both.iloc[:, 1]) / both.iloc[:, 1].var()
            add("beta_1y", float(beta), "x", f"vs NIFTY 500, {len(both)} daily returns")
    for years in (1, 3, 5):
        start = last - pd.DateOffset(years=years)
        hist = px["close"][px.index <= start]
        if hist.empty:
            continue
        r = float(px["close"].iloc[-1] / hist.iloc[-1] - 1) * 100
        if years > 1:
            r = ((1 + r / 100) ** (1 / years) - 1) * 100
            add(f"return_{years}y_cagr_pct", r, PCT, "price only, dividends excluded")
        else:
            add("return_1y_pct", r, PCT, "price only, dividends excluded")
    return out


def market_cap(symbol: str, raw_close: float | None, close_date: str | None,
               shares: dict | None) -> list[dict]:
    """shares: the latest shares_cr metric row (from the filings)."""
    if raw_close is None or not shares or shares.get("value") is None:
        return [row(symbol, "market_cap", "point", close_date or "", None, CR, CALC,
                    method="calculated", confidence=HIGH,
                    note="needs a close and a filed share count")]
    mcap = Decimal(str(raw_close)) * shares["value"]
    return [row(symbol, "market_cap", "point", close_date, mcap, CR, CALC,
                method="calculated", confidence=HIGH,
                note=f"close {raw_close} ({MARKET}) x {shares['value']:.4f} cr shares "
                     f"(filing for {shares['period_end']})")]


def iso_date(v) -> str:
    """'30-SEP-2026' (NSE's shareholding format) -> '2026-09-30'."""
    import datetime as dt
    s = str(v or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y", "%d %b %Y"):
        try:
            return dt.datetime.strptime(s.title() if "%b" in fmt else s, fmt).date().isoformat()
        except ValueError:
            continue
    return s


def free_float(symbol: str, promoter: list[dict]) -> list[dict]:
    """promoter: NSE shareholding history rows with date and promoter_pct."""
    rows = [p for p in promoter if p.get("promoter_pct") is not None]
    if not rows:
        return []
    rows = [{**p, "date": iso_date(p.get("date"))} for p in rows]
    latest = rows[0]
    out = [row(symbol, "promoter_pct", "instant", latest["date"], latest["promoter_pct"],
               PCT, "NSE shareholding pattern", confidence=HIGH),
           row(symbol, "non_promoter_pct", "instant", latest["date"],
               100 - float(latest["promoter_pct"]), PCT, CALC, method="calculated",
               confidence=LOW, note="100 - promoter %: a stand-in for free float "
                                    "(includes locked-in and strategic holders)")]
    if len(rows) > 1:
        prev = rows[-1]
        out.append(row(symbol, "promoter_change_pct", "instant", latest["date"],
                       float(latest["promoter_pct"]) - float(prev["promoter_pct"]), PCT,
                       CALC, method="calculated", confidence=HIGH,
                       note=f"change since {prev['date']}"))
    return out


def dividend_yield(symbol: str, dps: dict | None, price: float | None,
                   price_date: str | None) -> list[dict]:
    if not dps or dps.get("value") is None or not price:
        return []
    y = float(dps["value"]) / price * 100
    return [row(symbol, "dividend_yield_pct", "point", price_date, y, PCT, CALC,
                method="calculated", confidence=MEDIUM,
                note=f"trailing 12-month payouts ₹{dps['value']:.2f} (Yahoo) / "
                     f"close ₹{price}")]


def not_applicable(symbol: str, metric: str, end: str, why: str) -> dict:
    return row(symbol, metric, "point", end, None, "", CALC, method="calculated",
               status=NA, confidence=HIGH, note=why)


def to_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)

