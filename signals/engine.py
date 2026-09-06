"""Signal rules.

Written as pure functions over a single bar plus position state, with no
database and no network, for one specific reason: the backtest must run the
*same* code as the nightly job. If live and backtest logic diverge even
slightly, the backtest stops being evidence about the thing you actually run.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from signals.state import Position, Signal

# ---------------------------------------------------------------- regime


def regime_ok(bench: pd.DataFrame, on_date, cfg: dict) -> bool:
    """True when the benchmark is above its own long moving average.

    Blocks new entries in a falling market. Exits are never blocked -- you
    always want to be able to get out.
    """
    n = cfg["signals"]["regime_sma"]
    b = bench[bench["date"] <= on_date]
    if len(b) < n:
        return False
    return float(b["close"].iloc[-1]) > float(b["close"].tail(n).mean())


# ---------------------------------------------------------------- entries


def check_entry(row: pd.Series, prev: pd.Series, cfg: dict) -> tuple[str, str] | None:
    """Return (rule, reason) for the first entry rule that fires, else None."""
    s = cfg["signals"]
    score, prev_score = row.get("timing_score"), prev.get("timing_score")
    close, sma200, sma50 = row.get("close"), row.get("sma200"), row.get("sma50")
    if pd.isna(score) or pd.isna(sma200):
        return None

    # 1. Trend entry -- score crosses up through the threshold in an uptrend.
    if (not pd.isna(prev_score) and prev_score < s["buy_score"] <= score
            and row.get("sma200_slope", 0) > 0 and close > sma200):
        return ("trend_entry",
                f"timing score crossed {s['buy_score']:.0f} "
                f"({prev_score:.0f}->{score:.0f}) with a rising 200 DMA")

    # 2. Pullback entry -- dip inside an intact uptrend, then a reclaim.
    lo, hi = s["pullback_rsi"]
    if (close > sma200 and row.get("sma200_slope", 0) > 0 and not pd.isna(sma50)):
        dipped = not pd.isna(prev.get("rsi14")) and lo <= prev["rsi14"] <= hi
        touched = not pd.isna(prev.get("low")) and prev["low"] <= prev.get("sma50", 0) * 1.02
        reclaimed = close > sma50 and prev.get("close", 0) <= prev.get("sma50", 0) * 1.02
        if dipped and touched and reclaimed:
            return ("pullback_entry",
                    f"pulled back to the 50 DMA (RSI {prev['rsi14']:.0f}) inside an "
                    f"uptrend, then closed back above it")

    # 3. Breakout entry -- new high confirmed by volume.
    prior_high = prev.get("high_52w")
    if (not pd.isna(prior_high) and close > prior_high
            and row.get("vol_ratio", 0) >= s["breakout_volume_mult"]
            and close > sma200):
        return ("breakout_entry",
                f"closed above its {s['breakout_lookback']}-bar high on "
                f"{row['vol_ratio']:.1f}x average volume")
    return None


def entry_levels(row: pd.Series, cfg: dict) -> dict:
    """The price at which each entry rule would actually fire.

    This is NOT a recommended price -- nothing here endorses buying. It is the
    arithmetic inverse of check_entry(): given today's indicators, the level at
    which each rule's condition becomes true. Useful for setting a price alert
    so you are not watching a screen; useless as a target to chase.

    Returns the blocking reason instead when no rule can fire at any price,
    which is itself the more useful answer: a stock below a falling 200 DMA has
    no valid entry, and saying so is better than quoting a number.
    """
    s = cfg["signals"]
    close, sma50, sma200 = row.get("close"), row.get("sma50"), row.get("sma200")
    slope, atr = row.get("sma200_slope"), row.get("atr14")
    out = {"blocked": None, "levels": []}

    if pd.isna(close) or pd.isna(sma200):
        out["blocked"] = "not enough history"
        return out
    # Both entry rules require close > 200 DMA, so nothing can fire below it.
    if close < sma200:
        out["blocked"] = f"below its 200 DMA ({sma200:.2f}) -- no entry rule can fire"
        return out

    # Breakout: needs a close above the prior N-bar high, on 1.5x volume.
    hi = row.get("high_52w")
    if not pd.isna(hi):
        out["levels"].append({
            "rule": "breakout",
            "price": float(hi),
            "note": f"a close above {hi:.2f} on >={s['breakout_volume_mult']}x "
                    f"average volume",
        })

    # Pullback: needs a dip to the 50 DMA in an intact uptrend, then a reclaim.
    if not pd.isna(sma50) and not pd.isna(slope) and slope > 0:
        out["levels"].append({
            "rule": "pullback",
            "price": float(sma50),
            "note": f"a dip to the 50 DMA ({sma50:.2f}) with RSI "
                    f"{s['pullback_rsi'][0]:.0f}-{s['pullback_rsi'][1]:.0f}, "
                    f"then a close back above it",
        })
    elif not pd.isna(slope) and slope <= 0:
        out["blocked"] = ("200 DMA is flat or falling, so the pullback rule is "
                          "disabled; only a breakout could fire")

    # Attach the stop and targets that would apply at each level.
    for lv in out["levels"]:
        stop = initial_stop(lv["price"], float(atr or 0), cfg)
        lv["stop"] = stop
        if cfg.get("targets", {}).get("enabled"):
            lv["t1"], lv["t2"] = targets(lv["price"], stop, cfg)
        lv["pct_away"] = 100 * (lv["price"] / float(close) - 1)
    return out


# ---------------------------------------------------------------- exits


def initial_stop(entry_price: float, atr: float, cfg: dict) -> float:
    """Whichever is tighter: N x ATR, or a hard percentage floor."""
    r = cfg["risk"]
    atr_stop = entry_price - r["stop_atr_mult"] * (atr or 0)
    pct_stop = entry_price * (1 - r["stop_max_loss_pct"] / 100)
    return round(max(atr_stop, pct_stop), 2)


def targets(entry_price: float, stop_price: float, cfg: dict) -> tuple[float, float]:
    """T1 and T2 as multiples of R, where R = entry - stop.

    Using R rather than a fixed percentage means the targets scale with the
    risk actually being taken: a tight stop implies nearer targets, a wide one
    implies further targets, and the reward-to-risk ratio stays constant across
    calm and volatile stocks alike.
    """
    t = cfg.get("targets", {})
    r = max(entry_price - stop_price, 0.01)
    return (round(entry_price + t.get("t1_r", 1.5) * r, 2),
            round(entry_price + t.get("t2_r", 3.0) * r, 2))


def update_trailing(pos: Position, row: pd.Series, cfg: dict) -> Position:
    """Ratchet the stop upward once the position is far enough in profit.

    The stop only ever rises. A trailing stop that could fall would hand back
    gains it had already locked in.
    """
    r = cfg["risk"]
    close, atr = row.get("close"), row.get("atr14")
    if pd.isna(close):
        return pos
    pos.highest_close = max(pos.highest_close, float(close))
    if pos.pnl_pct(close) >= r["trail_activate_pct"] and not pd.isna(atr):
        candidate = pos.highest_close - r["trail_atr_mult"] * float(atr)
        if candidate > pos.stop_price:
            pos.stop_price = round(candidate, 2)
            pos.trailing = True
    return pos


def check_exit(row: pd.Series, prev: pd.Series, pos: Position,
               cfg: dict) -> tuple[str, str] | None:
    """Return (rule, reason) for the first exit rule that fires, else None."""
    s, close = cfg["signals"], row.get("close")
    if pd.isna(close):
        return None

    if pos.stop_price and close <= pos.stop_price:
        kind = "trailing stop" if pos.trailing else "hard stop"
        return ("stop_hit",
                f"closed at {close:.2f}, at or below the {kind} of "
                f"{pos.stop_price:.2f} ({pos.pnl_pct(close):+.1f}% on the position)")

    # Which moving average defines "the trend has broken" is the single biggest
    # lever on how long a position is held: the 50 DMA cuts on ordinary noise,
    # the 200 DMA only on a real regime change.
    break_n = s.get("trend_break_sma", 50)
    break_col = f"sma{break_n}"
    break_ma, sma200 = row.get(break_col), row.get("sma200")
    if (not pd.isna(break_ma) and close < break_ma
            and not pd.isna(prev.get("close")) and not pd.isna(prev.get(break_col))
            and prev["close"] < prev[break_col]):
        return ("trend_break",
                f"second consecutive close below the {break_n} DMA ({break_ma:.2f})")

    sma50 = row.get("sma50")
    if (not pd.isna(sma50) and not pd.isna(sma200) and sma50 < sma200
            and not pd.isna(prev.get("sma50")) and prev["sma50"] >= prev.get("sma200", 0)
            and s.get("use_death_cross", True)):
        return ("death_cross", "50 DMA crossed below the 200 DMA")

    score = row.get("timing_score")
    if (s.get("exit_score") is not None and not pd.isna(score)
            and score < s["exit_score"]):
        return ("score_collapse",
                f"timing score fell to {score:.0f}, below {s['exit_score']:.0f}")
    return None


def check_trim(row: pd.Series, prev: pd.Series, cfg: dict) -> tuple[str, str] | None:
    """Overbought then cooling -- book part of the gain, keep the position."""
    s = cfg["signals"]
    rsi, prsi = row.get("rsi14"), prev.get("rsi14")
    if pd.isna(rsi) or pd.isna(prsi):
        return None
    if prsi > s["trim_rsi_high"] and rsi <= s["trim_rsi_reset"]:
        return ("overbought_cooling",
                f"RSI peaked above {s['trim_rsi_high']:.0f} and has turned down "
                f"({prsi:.0f}->{rsi:.0f})")
    return None


# ---------------------------------------------------------------- sizing


def position_size(price: float, stop: float, portfolio_value: float,
                  cfg: dict) -> tuple[int, str]:
    """Quantity such that being stopped out costs at most risk_per_trade_pct.

    This is the rule that makes a bad trade survivable by construction rather
    than by luck, so it is also capped by max_position_pct -- a very tight stop
    would otherwise justify an enormous position.
    """
    r = cfg["risk"]
    risk_per_share = max(price - stop, 0.01)
    budget = portfolio_value * r["risk_per_trade_pct"] / 100
    qty_by_risk = budget / risk_per_share
    qty_by_cap = portfolio_value * r["max_position_pct"] / 100 / price
    qty = int(max(0, min(qty_by_risk, qty_by_cap)))
    binding = "1% risk cap" if qty_by_risk <= qty_by_cap else \
        f"{r['max_position_pct']:.0f}% position cap"
    return qty, binding


def in_cooldown(last: dt.date | None, on_date, cfg: dict) -> bool:
    if last is None:
        return False
    on_date = on_date if isinstance(on_date, dt.date) else pd.Timestamp(on_date).date()
    return (on_date - last).days < cfg["signals"]["cooldown_days"]
