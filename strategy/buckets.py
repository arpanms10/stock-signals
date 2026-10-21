"""Core / satellite classification, plus trade for positions bought to trade.

The buckets exist because they answer to different evidence, and mixing
them is how people talk themselves into selling their best long-term holdings
during a drawdown.

  CORE       Owned for the business, for a decade or more. Held THROUGH
             momentum signals -- a core name falling out of the ranking is not
             a sell. Sold only when the business itself deteriorates.
  SATELLITE  Owned for one to ten years, still as an investment, not a trade.
             Red flags, the quality floor and sizing apply, but it is NOT sold
             on momentum rank: falling out of the ranking asks you to review
             the thesis, it does not exit. The default for anything unset.
  TRADE      Bought to trade. Rotates on rank and halves below its stop: the
             momentum backtest only works because losers are dropped. Only
             what you mark bucket=trade in config/holdings.csv is treated so.

"legacy" (the old name for "bought before this framework, not rotated") is
read as satellite, which now carries exactly those rules.

Getting this wrong in either direction is costly. Treat everything as a
trade and you churn quality compounders on noise -- which is exactly how
v1 turned an 85% rise in TCS into 1.3%. Treat everything as core and you never
sell anything, and the momentum edge disappears.
"""
from __future__ import annotations

import numpy as np

BUCKETS = ("core", "satellite", "trade")
ALIASES = {"legacy": "satellite"}

# What makes a holding core-like. None of these is about price direction --
# that is the trade question.
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
             default_unknown: str = "satellite") -> tuple[str, str]:
    """Return (bucket, reason). A declared bucket always wins.

    `default_unknown` is what a holding becomes when it is clearly not core.
    It is satellite, never trade: a position on the books was not
    necessarily bought on momentum, so it is not sold on momentum unless you
    mark it bucket=trade yourself.
    """
    declared = (declared or "").strip().lower()
    if declared in ALIASES:
        return (ALIASES[declared], f"'{declared}' in config/holdings.csv is read as "
                                   f"{ALIASES[declared]}; rename it there")
    if declared in BUCKETS:
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

    # Core needs evidence on every test, as it already does for quality: an
    # unknown volatility or liquidity is not a pass. Treating "no data" as
    # "no objection" made illiquid and price-less holdings look like core.
    if volatility is None or np.isnan(volatility):
        return (default_unknown,
                "no price history to measure volatility -- not enough to call "
                "it core; set the bucket yourself")
    if volatility > CORE_MAX_VOLATILITY:
        return (default_unknown,
                f"volatility {100 * volatility:.0f}% is too high for a "
                f"hold-through-anything position")
    reasons.append(f"volatility {100 * volatility:.0f}%")

    if liquidity_rank is None:
        return (default_unknown,
                "not in the liquidity ranking (too few recent trading days or "
                "below the price floor) -- too thin to be a core holding")
    if liquidity_rank > CORE_MIN_LIQUIDITY_RANK:
        return (default_unknown,
                f"liquidity rank {liquidity_rank} -- too thin to be a core holding")

    return "core", "; ".join(reasons) + " -- own the business, ignore the rank"


def suggest_split(holdings: dict, values: dict[str, float],
                  buckets: dict[str, str]) -> dict:
    """Current core / satellite / trade split by value, with a view on it."""
    total = sum(values.values())
    if total <= 0:
        return {}
    core = sum(v for s, v in values.items() if buckets.get(s) == "core")
    trade = sum(v for s, v in values.items() if buckets.get(s) == "trade")
    sat = total - core - trade
    core_pct = 100 * core / total
    if core_pct < 40:
        view = ("Core is under 40% of the book. Satellites are held for years "
                "but not for decades -- most of the book is a medium-term view.")
    else:
        view = "Most of the book is owned for the long term."
    if trade > total * 0.25:
        view = (f"{100 * trade / total:.0f}% of the book is marked trade. That "
                f"part rotates on rank and its drawdowns are correlated -- "
                f"momentum losers tend to fall together.")
    return {"core_value": core, "satellite_value": sat, "trade_value": trade,
            "core_pct": core_pct, "satellite_pct": 100 * sat / total,
            "trade_pct": 100 * trade / total, "view": view}
