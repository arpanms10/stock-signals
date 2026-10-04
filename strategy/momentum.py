"""Cross-sectional momentum ranking.

The strategy class change that this whole roadmap turns on. v1 tried to time
entries and exits on individual stocks and lost because it was out of the
market 70% of the time. Cross-sectional momentum never asks "should I be in
this stock?" -- it asks "which stocks are strongest right now?" and holds the
top slice, always. It rotates rather than exits, so the cost of absence that
sank v1 does not arise.

Definition follows the standard academic one, which is also what NSE's own
NIFTY200 Momentum 30 index uses in spirit:

    12-1 momentum = return over the last 12 months, EXCLUDING the most recent
    month.

The skipped month is not a detail. Short-horizon returns reverse (the
one-month reversal effect), so including the latest month actively works
against you -- you buy what just spiked and it mean-reverts.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

TRADING_DAYS_MONTH = 21


def momentum_score(close: pd.Series, lookback_months: int = 12,
                   skip_months: int = 1) -> pd.Series:
    """Return over `lookback` months, excluding the most recent `skip`."""
    lb = lookback_months * TRADING_DAYS_MONTH
    sk = skip_months * TRADING_DAYS_MONTH
    past = close.shift(lb)
    recent = close.shift(sk)
    return 100 * (recent / past - 1)


def trailing_return(close: pd.Series, months: int) -> float | None:
    """Plain price return over the last `months`, ending today, in percent.

    For display. It is the number you can check against your broker, unlike
    the 9-1 / 12-1 figures the ranking uses, which deliberately ignore the
    latest month.
    """
    n = months * TRADING_DAYS_MONTH
    c = close.dropna()
    if len(c) <= n or not c.iloc[-1 - n]:
        return None
    return float(100 * (c.iloc[-1] / c.iloc[-1 - n] - 1))


def realised_volatility(close: pd.Series, n: int = 252) -> pd.Series:
    """Annualised standard deviation of daily returns."""
    return close.pct_change().rolling(n, min_periods=n // 2).std() * np.sqrt(252)


def risk_adjusted_momentum(close: pd.Series, lookback_months: int = 12,
                           skip_months: int = 1, vol_window: int = 252) -> pd.Series:
    """Momentum divided by volatility.

    A calm 30% gain and a violent 30% gain are not the same signal: the calm
    one is far more likely to persist. Scaling by volatility is the single
    best-evidenced refinement to plain momentum, and it also stops the ranking
    filling up with whatever happens to be most volatile.
    """
    mom = momentum_score(close, lookback_months, skip_months)
    vol = realised_volatility(close, vol_window)
    return mom / vol.replace(0, np.nan)


def build_panel(frames: dict[str, pd.DataFrame], cfg: dict) -> pd.DataFrame:
    """One row per (date, symbol) with the momentum inputs attached."""
    m = cfg.get("momentum_strategy", {})
    rows = []
    for sym, f in frames.items():
        if f.empty or len(f) < 260:
            continue
        d = f[["date", "close"]].copy()
        d["symbol"] = sym
        d["mom"] = momentum_score(d["close"], m.get("lookback_months", 12),
                                  m.get("skip_months", 1))
        d["vol"] = realised_volatility(d["close"], m.get("vol_window", 252))
        d["ram"] = d["mom"] / d["vol"].replace(0, np.nan)
        d["sma200"] = d["close"].rolling(200, min_periods=200).mean()
        rows.append(d)
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)


def rank_on(panel: pd.DataFrame, on_date, cfg: dict,
            eligible: set[str] | None = None) -> pd.DataFrame:
    """Rank the universe as of one date. Returns best-first.

    `eligible` is the set of ISIN-confirmed equities (bhavcopy.
    require_equity_symbols). Live callers pass it because their panels also
    carry holdings, and a held ETF must never compete with stocks: its
    near-zero volatility puts it at the top of a risk-adjusted ranking.
    """
    m = cfg.get("momentum_strategy", {})
    col = "ram" if m.get("risk_adjusted", True) else "mom"
    snap = panel[panel["date"] == on_date].copy()
    if eligible is not None:
        snap = snap[snap["symbol"].isin(eligible)]
    snap = snap[snap[col].notna()]
    if snap.empty:
        return snap
    # A stock below its own 200 DMA is not in an uptrend whatever its 12-month
    # number says -- this is what keeps falling knives out of the top slice.
    if m.get("require_above_200dma", True):
        snap = snap[snap["close"] > snap["sma200"]]
    snap = snap.sort_values(col, ascending=False).reset_index(drop=True)
    snap["rank"] = snap.index + 1
    return snap


def portfolio_vol(returns: pd.Series, window: int = 60) -> float | None:
    """Annualised realised volatility of a return series."""
    r = returns.dropna().tail(window)
    if len(r) < max(window // 2, 20):
        return None
    sd = float(r.std())
    return sd * np.sqrt(252) if sd == sd and sd > 0 else None


def exposure_for_vol(realised: float | None, cfg: dict) -> tuple[float, str]:
    """Fraction of capital to deploy so the book runs at its target vol.

    The insight volatility targeting rests on: momentum's worst drawdowns
    arrive when volatility has already risen, and volatility is far more
    persistent than return. Scaling exposure down as vol rises therefore cuts
    the drawdown without needing to predict anything.

    Capped at fully invested -- this framework does not borrow -- and floored
    well above zero, because a portfolio that goes to cash on every vol spike
    misses the rebound that usually follows.
    """
    m = cfg.get("momentum_strategy", {})
    if not m.get("vol_target") or not realised:
        return 1.0, "volatility targeting off -- fully invested"
    target = m.get("vol_target_pct", 15.0) / 100
    raw = target / realised
    lo, hi = m.get("vol_min_exposure", 0.3), m.get("vol_max_leverage", 1.0)
    exposure = max(lo, min(hi, raw))
    note = (f"realised vol {100 * realised:.0f}% against a {100 * target:.0f}% "
            f"target -> {100 * exposure:.0f}% invested")
    if raw > hi:
        note += " (capped at fully invested; no borrowing)"
    elif raw < lo:
        note += f" (floored at {100 * lo:.0f}%; a vol spike is not a reason "
        note += "to hold only cash)"
    return exposure, note


def rebalance_dates(dates: list[dt.date], freq_days: int = 21) -> list[dt.date]:
    """Month-end-ish rebalance points.

    Monthly is the standard cadence: weekly churns through costs, quarterly
    lets losers linger too long.
    """
    if not dates:
        return []
    out, last = [dates[0]], dates[0]
    for d in dates[1:]:
        if (d - last).days >= freq_days * 7 / 5:
            out.append(d)
            last = d
    return out


def lookbacks(cfg: dict) -> list[int]:
    """Primary lookback first, then the comparison one if configured."""
    m = cfg.get("momentum_strategy", {})
    out = [m.get("lookback_months", 12)]
    alt = m.get("compare_lookback_months")
    if alt and alt not in out:
        out.append(alt)
    return out


def with_lookback(cfg: dict, months: int) -> dict:
    return {**cfg, "momentum_strategy": {**cfg.get("momentum_strategy", {}),
                                         "lookback_months": months}}


def common_top(rankings: dict[int, pd.DataFrame], n: int) -> list[str]:
    """Names in the top `n` of every ranking, best average rank first.

    Agreement between two lookbacks is the cheap robustness check: a name that
    is strong on both is less likely to be an artefact of one window.
    """
    tops = [dict(zip(r["symbol"].head(n), r["rank"].head(n)))
            for r in rankings.values() if not r.empty]
    if len(tops) < len(rankings) or not tops:
        return []
    both = set.intersection(*(set(t) for t in tops))
    return sorted(both, key=lambda s: (sum(t[s] for t in tops), s))


def target_holdings(ranked: pd.DataFrame, current: set[str], cfg: dict) -> list[str]:
    """Which names to hold after this rebalance.

    Hysteresis: a stock enters on reaching the top `enter_rank` and is only
    dropped once it falls past `exit_rank`. Without the gap, names hovering at
    the boundary get bought and sold every month and the costs eat the edge.
    """
    m = cfg.get("momentum_strategy", {})
    n_hold = m.get("n_hold", 15)
    enter_rank = m.get("enter_rank", n_hold)
    exit_rank = m.get("exit_rank", n_hold * 2)

    by_rank = dict(zip(ranked["symbol"], ranked["rank"]))
    keep = [s for s in current if by_rank.get(s, 10**6) <= exit_rank]
    keep.sort(key=lambda s: by_rank[s])

    for sym in ranked["symbol"]:
        if len(keep) >= n_hold:
            break
        if sym not in keep and by_rank[sym] <= enter_rank:
            keep.append(sym)
    return keep[:n_hold]
