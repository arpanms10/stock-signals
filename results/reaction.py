"""How the stock took the numbers.

The reaction day is the first session whose close reflects the filing: the
filing day itself when it was broadcast before the 15:30 close, otherwise the
next session. NSE timestamps every filing, and most results land after hours,
so this is usually the next morning's open.

Measured against the close before the reaction day:

  gap_pct         the open -- the market's first read
  day_pct         the reaction-day close
  day_atr         day_pct in multiples of the stock's own daily ATR, so a 4%
                  move in a sleepy utility and in a smallcap are not treated
                  as the same news
  day_excess_pct  day_pct less the NIFTY 500 on the same day
  d5_pct          five sessions on, and d5_excess_pct against the index

The verdict reads the filing; the reaction reads the market's expectations.
"Strong numbers, sold off" usually means the market expected even better --
which is exactly the analyst-consensus signal free data cannot give directly.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from indicators import core

MARKET_CLOSE = dt.time(15, 30)


def reaction_day(broadcast: dt.datetime, dates: list[dt.date]) -> dt.date | None:
    """First trading date whose close reflects a filing broadcast then."""
    d, after = broadcast.date(), broadcast.time() >= MARKET_CLOSE
    for x in dates:
        if x > d or (x == d and not after):
            return x
    return None


def measure(prices: pd.DataFrame, broadcast: dt.datetime,
            index: pd.DataFrame | None = None, atr_band: float = 1.0) -> dict:
    """Reaction fields for one filing; {} when prices do not reach it yet."""
    if prices is None or prices.empty or broadcast is None or pd.isna(broadcast):
        return {}
    p = prices.sort_values("date").reset_index(drop=True)
    dates = list(p["date"])
    d0 = reaction_day(pd.Timestamp(broadcast).to_pydatetime(), dates)
    if d0 is None:
        return {}
    i = dates.index(d0)
    if i == 0:
        return {}
    prev = float(p.at[i - 1, "close"])
    atr = core.atr_pct(p["high"], p["low"], p["close"]).iloc[i - 1]
    out: dict = {"reaction_date": d0,
                 "gap_pct": round(100 * (float(p.at[i, "open"]) / prev - 1), 2),
                 "day_pct": round(100 * (float(p.at[i, "close"]) / prev - 1), 2)}
    out["day_atr"] = round(out["day_pct"] / atr, 2) if atr and atr > 0 else None
    if i + 4 < len(p):
        out["d5_pct"] = round(100 * (float(p.at[i + 4, "close"]) / prev - 1), 2)

    if index is not None and not index.empty:
        ix = index.set_index("date")["close"]

        def idx_ret(a: dt.date, b: dt.date):
            if a in ix.index and b in ix.index:
                return 100 * (ix[b] / ix[a] - 1)
            return None
        r = idx_ret(dates[i - 1], d0)
        if r is not None:
            out["day_excess_pct"] = round(out["day_pct"] - r, 2)
        if "d5_pct" in out:
            r5 = idx_ret(dates[i - 1], dates[i + 4])
            if r5 is not None:
                out["d5_excess_pct"] = round(out["d5_pct"] - r5, 2)

    z = out.get("day_atr")
    out["reaction"] = ("muted" if z is None or abs(z) < atr_band
                       else "rallied" if z > 0 else "sold off")
    return out


def read(verdict: str, reaction: str | None) -> str | None:
    """A plain reading of the verdict against the reaction, when they disagree."""
    if not isinstance(reaction, str):
        return None
    if verdict == "strong" and reaction == "sold off":
        return "good numbers, sold off -- the market expected more"
    if verdict == "weak" and reaction == "rallied":
        return "weak numbers, bought anyway -- feared worse, or looking past it"
    return None
