"""Portfolio construction: target weights, drift, and concentration.

Everything here is about *your* book rather than a backtest, and everything is
a suggestion. Nothing in this module places an order.

The core/satellite framing matters and is stated in the output rather than
assumed: the evidence from v1 is that an index fund beat every rule set tested.
This framework governs a deliberately sized slice, not the whole portfolio, and
the report says so every time so the framing cannot quietly erode.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class Allocation:
    symbol: str
    target_pct: float
    current_pct: float
    drift_pct: float
    action: str          # BUY | SELL | HOLD
    shares_delta: float
    value_delta: float
    reason: str = ""


def equal_weights(symbols: list[str]) -> dict[str, float]:
    if not symbols:
        return {}
    w = 100.0 / len(symbols)
    return {s: w for s in symbols}


def volatility_parity_weights(symbols: list[str],
                              vols: dict[str, float]) -> dict[str, float]:
    """Size inversely to volatility so each position contributes similar risk.

    Equal *rupee* weight is not equal *risk* weight: a 45%-volatility smallcap
    and a 15%-volatility largecap at the same rupee size mean the smallcap
    drives three times the portfolio's movement.
    """
    inv = {s: 1.0 / vols[s] for s in symbols
           if s in vols and vols[s] and not np.isnan(vols[s]) and vols[s] > 0}
    if not inv:
        return equal_weights(symbols)
    total = sum(inv.values())
    return {s: 100.0 * v / total for s, v in inv.items()}


def build_plan(targets: dict[str, float], holdings: dict[str, dict],
               prices: dict[str, float], satellite_value: float,
               drift_tolerance: float = 5.0) -> list[Allocation]:
    """Compare the book against its targets and suggest the trades to close
    the gap. `holdings` maps symbol -> {"quantity", "average_price"}."""
    current_value = {s: h.get("quantity", 0) * prices.get(s, 0.0)
                     for s, h in holdings.items()}
    total = satellite_value or sum(current_value.values())
    if total <= 0:
        return []

    out: list[Allocation] = []
    for sym in sorted(set(targets) | set(holdings)):
        tgt = targets.get(sym, 0.0)
        cur = 100 * current_value.get(sym, 0.0) / total
        drift = cur - tgt
        px = prices.get(sym, 0.0)
        value_delta = (tgt - cur) / 100 * total
        shares_delta = value_delta / px if px else 0.0

        if tgt == 0 and cur > 0:
            action, reason = "SELL", "no longer in the target holdings"
        elif cur == 0 and tgt > 0:
            action, reason = "BUY", "new entry to the target holdings"
        elif abs(drift) > drift_tolerance:
            action = "BUY" if drift < 0 else "SELL"
            reason = f"drifted {drift:+.1f}% from its {tgt:.1f}% target"
        else:
            action, reason = "HOLD", f"within {drift_tolerance:.0f}% of target"
            shares_delta = value_delta = 0.0
        out.append(Allocation(sym, tgt, cur, drift, action,
                              shares_delta, value_delta, reason))
    return out


def concentration_report(holdings: dict[str, dict], prices: dict[str, float],
                         sectors: dict[str, str], cfg: dict) -> list[str]:
    """Single-stock and sector limits, plus correlation clusters.

    The sector check exists because three IT stocks are one bet, not three --
    a portfolio can look diversified across ten names and still be a
    concentrated wager on one part of the economy.
    """
    r = cfg["risk"]
    values = {s: h.get("quantity", 0) * prices.get(s, 0.0)
              for s, h in holdings.items()}
    total = sum(values.values())
    if total <= 0:
        return []

    warnings = []
    for sym, val in sorted(values.items(), key=lambda kv: -kv[1]):
        pct = 100 * val / total
        if pct > r["max_position_pct"]:
            warnings.append(f"{sym} is {pct:.1f}% of the book, above the "
                            f"{r['max_position_pct']:.0f}% single-stock cap")

    by_sector: dict[str, float] = {}
    for sym, val in values.items():
        by_sector[sectors.get(sym, "Unknown")] = \
            by_sector.get(sectors.get(sym, "Unknown"), 0.0) + val
    for sec, val in sorted(by_sector.items(), key=lambda kv: -kv[1]):
        pct = 100 * val / total
        if pct > r["max_sector_pct"]:
            names = [s for s in values if sectors.get(s, "Unknown") == sec]
            warnings.append(f"{sec} is {pct:.1f}% of the book, above the "
                            f"{r['max_sector_pct']:.0f}% sector cap "
                            f"({', '.join(sorted(names))} are one bet, not "
                            f"{len(names)})")
    return warnings


def correlation_clusters(frames: dict[str, pd.DataFrame], symbols: list[str],
                         threshold: float = 0.75, window: int = 250) -> list[str]:
    """Pairs of holdings that move together closely enough to be one position."""
    rets = {}
    for s in symbols:
        f = frames.get(s)
        if f is None or len(f) < window:
            continue
        rets[s] = f["close"].tail(window).pct_change().reset_index(drop=True)
    if len(rets) < 2:
        return []
    corr = pd.DataFrame(rets).corr()
    out = []
    seen = set()
    for a in corr.columns:
        for b in corr.columns:
            if a >= b or (a, b) in seen:
                continue
            seen.add((a, b))
            c = corr.loc[a, b]
            if not np.isnan(c) and c >= threshold:
                out.append(f"{a} and {b} move together (correlation {c:.2f}) "
                           f"-- closer to one position than two")
    return out


def core_satellite_note(satellite_value: float, total_value: float) -> str:
    """State the framing on every report so it cannot quietly erode."""
    if total_value <= 0:
        return ""
    pct = 100 * satellite_value / total_value
    return (f"This framework governs {pct:.0f}% of your capital "
            f"({satellite_value:,.0f} of {total_value:,.0f}). The remaining "
            f"{100 - pct:.0f}% is assumed to be index exposure. Every backtest "
            f"run here says an index fund beat these rules -- so the satellite "
            f"is meant to stay a satellite.")
