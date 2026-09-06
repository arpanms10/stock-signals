"""Core vs satellite classification.

The two buckets exist because they answer to different evidence, and mixing
them is how people talk themselves into selling their best long-term holdings
during a drawdown.

  CORE       Owned for the business, not the trend. Held THROUGH momentum
             signals -- a core name falling out of the ranking is not a sell.
             Sold only when the business itself deteriorates.
  SATELLITE  Owned for the trend. Rotates on rank, which is the whole point:
             the momentum backtest only works because losers are dropped.
  LEGACY     Bought before this framework existed, on reasoning it has no
             record of. Genuine red flags still apply -- a rising pledge is a
             rising pledge whoever bought the stock -- but it is NOT rotated on
             momentum rank. Selling a position because it fails a rule that was
             not used to buy it is applying that rule retroactively, and on a
             long-term book it produces a wall of exits that is an artefact of
             the framework rather than a finding about the holdings.

Getting this wrong in either direction is costly. Treat everything as
satellite and you churn quality compounders on noise -- which is exactly how
v1 turned an 85% rise in TCS into 1.3%. Treat everything as core and you never
sell anything, and the momentum edge disappears.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# What makes a holding core-like. None of these is about price direction --
# that is the satellite question.
CORE_QUALITY_FLOOR = 70.0        # a business you would hold through a bad year
CORE_MAX_VOLATILITY = 0.32       # annualised; above this it is a trade
CORE_MIN_LIQUIDITY_RANK = 150    # obscure names are not core holdings


def realised_vol(frame) -> float | None:
    """Annualised volatility from a price frame.

    Defined once and used by every caller. Two code paths computing this
    slightly differently produced two different core/satellite splits for the
    same portfolio -- 12 core in one place and 18 in another -- which is worse
    than either answer on its own.
    """
    if frame is None or len(frame) < 120 or "close" not in frame:
        return None
    r = frame["close"].pct_change().tail(252)
    sd = float(r.std())
    return sd * (252 ** 0.5) if sd == sd else None


def classify(symbol: str, quality_score: float | None, volatility: float | None,
             liquidity_rank: int | None, declared: str = "",
             default_unknown: str = "legacy") -> tuple[str, str]:
    """Return (bucket, reason). A declared bucket always wins.

    `default_unknown` is what a holding becomes when it is clearly not core.
    It defaults to "legacy" rather than "satellite" because a position already
    on the books was not bought on momentum, so it should not be sold on
    momentum without you saying so first.
    """
    if declared in ("core", "satellite", "legacy"):
        return declared, "set by you in config/holdings.csv"

    reasons = []
    if quality_score is None:
        return (default_unknown,
                "no quality score on file -- not enough to call it core; "
                "fill config/fundamentals.csv, or set the bucket yourself")

    if quality_score < CORE_QUALITY_FLOOR:
        return (default_unknown,
                f"quality {quality_score:.0f} is below {CORE_QUALITY_FLOOR:.0f} -- "
                f"not a business to hold through a bad year")
    reasons.append(f"quality {quality_score:.0f}")

    if volatility is not None and not np.isnan(volatility):
        if volatility > CORE_MAX_VOLATILITY:
            return (default_unknown,
                    f"volatility {100 * volatility:.0f}% is too high for a "
                    f"hold-through-anything position")
        reasons.append(f"volatility {100 * volatility:.0f}%")

    if liquidity_rank is not None and liquidity_rank > CORE_MIN_LIQUIDITY_RANK:
        return (default_unknown,
                f"liquidity rank {liquidity_rank} -- too thin to be a core holding")

    return "core", "; ".join(reasons) + " -- own the business, ignore the rank"


def suggest_split(holdings: dict, values: dict[str, float],
                  buckets: dict[str, str]) -> dict:
    """Current core/satellite split by value, with a view on the balance."""
    total = sum(values.values())
    if total <= 0:
        return {}
    core = sum(v for s, v in values.items() if buckets.get(s) == "core")
    legacy = sum(v for s, v in values.items() if buckets.get(s) == "legacy")
    sat = total - core - legacy
    core_pct = 100 * core / total
    if core_pct < 40:
        view = ("Satellite-heavy. Most of the book rides on rank, which means "
                "most of it can be wrong at once -- momentum drawdowns are "
                "correlated across holdings.")
    elif core_pct > 80:
        view = ("Almost entirely core. Nothing here rotates, so the momentum "
                "work in this framework is doing very little for you.")
    else:
        view = "A reasonable balance between businesses held and trends rented."
    if legacy > total * 0.25:
        view = (f"{100 * legacy / total:.0f}% of the book is unclassified. Those "
                f"holdings get red-flag checks but are not rotated on rank -- "
                f"decide which are core and which are satellite, in "
                f"config/holdings.csv, before the momentum side does anything "
                f"useful for you.")
    return {"core_value": core, "satellite_value": sat, "legacy_value": legacy,
            "core_pct": core_pct, "satellite_pct": 100 * sat / total,
            "legacy_pct": 100 * legacy / total, "view": view}
