"""Risk monitoring for a held book.

The v1 exit engine, pointed at holdings instead of trades. That engine's
failure was in *driving trades* -- cutting winners on 50 DMA noise. As alerts
on a buy-and-hold book it is doing something quite different and much more
defensible: making sure a 40% drawdown never arrives unnoticed.

Every output here is a REVIEW PROMPT, not an instruction. The distinction is
the whole point. v1 proved that acting mechanically on these signals loses
money; being told that something has changed, and deciding for yourself, is
the part that has value.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from signals.engine import initial_stop, targets


def holding_status(sym: str, frame: pd.DataFrame, holding: dict,
                   cfg: dict) -> dict:
    """Current risk picture for one holding."""
    if frame.empty:
        return {"symbol": sym, "error": "no price data"}
    row = frame.iloc[-1]
    close = float(row["close"])
    avg = float(holding.get("average_price") or holding.get("avg_price") or 0)
    qty = float(holding.get("quantity") or 0)
    atr = float(row.get("atr14") or 0)

    stop = initial_stop(avg, atr, cfg) if avg else None
    r = cfg["risk"]
    pnl_pct = 100 * (close / avg - 1) if avg else np.nan

    # Trailing stop from the highest close since entry, using a bounded
    # lookback when the purchase date is unknown -- conservative rather than
    # invented.
    entry_date = holding.get("purchase_date")
    try:
        since = dt.date.fromisoformat(str(entry_date)[:10]) if entry_date else None
    except ValueError:
        since = None
    if since is None:
        since = frame["date"].iloc[-1] - dt.timedelta(days=r["trail_lookback_days"])
    window = frame[frame["date"] >= since]
    peak = float(window["close"].max()) if not window.empty else close

    trailing = None
    if avg and pnl_pct >= r["trail_activate_pct"] and atr:
        cand = round(peak - r["trail_atr_mult"] * atr, 2)
        if stop is None or cand > stop:
            trailing = cand

    active_stop = trailing or stop
    # Targets computed from what you PAID. Correct for judging the position you
    # hold, and actively misleading if shown next to a decision to buy more:
    # a stock that has run past its entry targets would display targets below
    # the current price, reading as "sell lower than you buy".
    t1 = t2 = None
    t1_hit = t2_hit = False
    if avg and stop and cfg.get("targets", {}).get("enabled"):
        t1, t2 = targets(avg, stop, cfg)
        t1_hit, t2_hit = close >= t1, close >= t2

    # Targets for buying at TODAY's price, which is the only frame that makes
    # sense for an add. Kept separate rather than overwriting the entry
    # targets -- both are true, they just answer different questions.
    add_stop = add_t1 = add_t2 = None
    if atr and cfg.get("targets", {}).get("enabled"):
        add_stop = initial_stop(close, atr, cfg)
        add_t1, add_t2 = targets(close, add_stop, cfg)

    # A stop computed from a peak the price has since fallen well below is not
    # a level you can act on -- it is a level that would already have fired.
    # Presenting 3172 as "your stop" while the stock trades at 2304 invites
    # setting an order that executes instantly. Say what actually happened.
    alerts = []
    breached = bool(active_stop and close <= active_stop)
    stale = bool(breached and active_stop > close * 1.05)
    if breached:
        kind = "trailing" if trailing else "initial"
        if stale:
            alerts.append(f"its {kind} stop ({active_stop:.2f}) sits well above "
                          f"today's {close:.2f} -- this level was breached some "
                          f"time ago, not today. Position is {pnl_pct:+.1f}%")
        else:
            alerts.append(f"below its {kind} stop of {active_stop:.2f} "
                          f"({pnl_pct:+.1f}% on the position) -- worth a review")
    score = row.get("timing_score")
    if not pd.isna(score) and score < cfg["signals"]["exit_score"]:
        alerts.append(f"timing score has fallen to {score:.0f}; the trend that "
                      f"justified holding it may be over")
    sma50, sma200 = row.get("sma50"), row.get("sma200")
    if not pd.isna(sma50) and not pd.isna(sma200) and sma50 < sma200:
        alerts.append("50 DMA is below the 200 DMA -- long-term trend is down")
    drawdown = 100 * (close / peak - 1) if peak else 0.0
    if drawdown < -25:
        alerts.append(f"{drawdown:.0f}% below its peak since you bought it")

    return {"symbol": sym, "close": close, "avg_price": avg, "qty": qty,
            "pnl_pct": pnl_pct, "value": qty * close, "stop": active_stop,
            "trailing": trailing is not None, "peak": peak,
            "stop_breached": breached, "stop_stale": stale,
            "t1_hit": t1_hit, "t2_hit": t2_hit,
            "add_stop": add_stop, "add_t1": add_t1, "add_t2": add_t2,
            "drawdown_from_peak": drawdown, "t1": t1, "t2": t2,
            "score": None if pd.isna(score) else float(score), "alerts": alerts}


def portfolio_risk(statuses: list[dict], cfg: dict) -> list[str]:
    """Book-level observations."""
    live = [s for s in statuses if "error" not in s and s.get("value")]
    if not live:
        return []
    total = sum(s["value"] for s in live)
    notes = []
    losers = [s for s in live if s["pnl_pct"] < 0]
    if losers:
        worst = min(losers, key=lambda s: s["pnl_pct"])
        notes.append(f"{len(losers)} of {len(live)} holdings are underwater; "
                     f"worst is {worst['symbol']} at {worst['pnl_pct']:+.1f}%")
    breached = [s for s in live if s["stop"] and s["close"] <= s["stop"]]
    if breached:
        notes.append(f"{len(breached)} holding(s) below their stop: "
                     f"{', '.join(s['symbol'] for s in breached)}")
    # Only holdings still ABOVE their stop have distance left to lose. Summing
    # max(...,0) across breached ones silently reported 0% risk precisely when
    # the book was in the worst shape -- the reading was backwards.
    intact = [s for s in live if s["stop"] and not s.get("stop_breached")]
    at_risk = sum(s["value"] - s["stop"] * s["qty"] for s in intact)
    if intact:
        notes.append(f"capital at risk if every intact stop triggered today: "
                     f"{at_risk:,.0f} ({100 * at_risk / total:.1f}% of the book, "
                     f"across {len(intact)} of {len(live)} holdings)")
    if len(intact) < len(live):
        notes.append(f"{len(live) - len(intact)} holding(s) are already below "
                     f"their stop and carry no defined downside limit")
    return notes


def regime_note(bench: pd.DataFrame, cfg: dict) -> str:
    """Market state, and how long it has been that way."""
    n = cfg["signals"]["regime_sma"]
    if bench.empty or len(bench) < n:
        return "market regime: unknown (not enough index history)"
    close = bench["close"].reset_index(drop=True)
    sma = close.rolling(n, min_periods=n).mean()
    above = close > sma
    state = "RISK-ON" if bool(above.iloc[-1]) else "RISK-OFF"
    flips = above != above.shift(1)
    last_flip = flips[flips].index.max()
    days = len(above) - 1 - last_flip if last_flip is not None and not pd.isna(last_flip) else len(above)
    pct = 100 * (float(close.iloc[-1]) / float(sma.iloc[-1]) - 1)
    return (f"market regime: {state} for {int(days)} sessions "
            f"(index is {pct:+.1f}% vs its {n} DMA)")
