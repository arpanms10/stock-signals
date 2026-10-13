"""Swing trading: scan the universe for setups, score them, plan the trade.

A different horizon from the momentum rotation. Momentum asks "which stocks
are strongest?" and holds them for months. A swing trade asks "which strong
stock is offering an entry right now?" and holds for days to a few weeks,
with a stop, targets and a time limit fixed before the trade starts.

Three layers, each one a gate for the next:

  1. Eligible   -- liquid, tradable volatility, in an uptrend (above a rising
                   200 DMA, 50 DMA above 200 DMA).
  2. Setup      -- something is happening today: a PULLBACK to the 20 EMA that
                   is turning back up, or a BREAKOUT from a tight base on
                   volume. No setup, no trade, whatever the score.
  3. Score      -- 0-100, orders the names that have a setup: trend, relative
                   strength against NIFTY 500, setup quality, volume and how
                   tight the stop can be.

The trade plan is mechanical so it can be backtested exactly as shown: a
buy-stop just above the signal day's high, a structure stop under the recent
swing low (kept between 1 and 3 ATRs), half booked at 1.5R with the stop moved
to entry, the rest at 3R or after 15 sessions. Every number is in
config/scoring.yaml under `swing:`.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from indicators import core
from scoring.timing import linmap

SETUPS = ("pullback", "breakout")


# ------------------------------------------------------------------ features

def features(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Swing columns for every bar of one stock.

    `df` is an indicator frame from indicators.core.compute_all (with
    `bench_close` when available). Computed for all dates, not just the last,
    so the backtest sees exactly what the live scan sees.
    """
    s = cfg["swing"]
    pb, bo = s["pullback"], s["breakout"]
    out = df.copy()
    c, h, l = out["close"], out["high"], out["low"]
    atr = out["atr14"].replace(0, np.nan)

    out["ema_pb"] = core.ema(c, pb["ma"])
    out["turnover_cr"] = (c * out["volume"]).rolling(20, min_periods=15).mean() / 1e7
    out["sma50_vs_200"] = 100 * (out["sma50"] / out["sma200"] - 1)

    if "bench_close" in out:
        b = out["bench_close"]
        ex3 = 100 * (c / c.shift(63) - 1) - 100 * (b / b.shift(63) - 1)
        ex6 = 100 * (c / c.shift(126) - 1) - 100 * (b / b.shift(126) - 1)
        out["rs_raw"] = 0.5 * ex3 + 0.5 * ex6
    else:
        out["rs_raw"] = np.nan

    out["uptrend"] = ((c > out["sma200"]) & (out["sma50"] > out["sma200"])
                      & (out["sma200_slope"] > 0))
    out["eligible"] = (out["uptrend"]
                       & (c >= s["min_price"])
                       & (out["turnover_cr"] >= s["min_turnover_cr"])
                       & out["atr_pct"].between(*s["atr_pct"]))

    # Pullback: the low came within touch_atr ATRs of the EMA recently, RSI
    # reset into its band, and today closed up, above the EMA. The close-up
    # condition is the turn -- buying the touch itself is catching a knife.
    near = (l - out["ema_pb"]) / atr
    touched = near.rolling(pb["touch_bars"], min_periods=1).min() <= pb["touch_atr"]
    out["pb_touch_atr"] = near.rolling(pb["touch_bars"], min_periods=1).min()
    out["setup_pullback"] = (touched & out["rsi14"].between(*pb["rsi"])
                             & (c > c.shift(1)) & (c > out["ema_pb"]))

    # Breakout: close above the prior N-session high, on volume, out of a base
    # no wider than max_base_pct, near the 52-week high (no overhead supply).
    prior_high = h.shift(1).rolling(bo["lookback"], min_periods=bo["lookback"]).max()
    prior_low = l.shift(1).rolling(bo["lookback"], min_periods=bo["lookback"]).min()
    out["base_pct"] = 100 * (prior_high / prior_low - 1)
    out["from_high_pct"] = 100 * (c / out["high_52w"] - 1)
    out["setup_breakout"] = ((c > prior_high) & (out["vol_ratio"] >= bo["volume_mult"])
                             & (out["base_pct"] <= bo["max_base_pct"])
                             & (out["from_high_pct"] >= -bo["near_high_pct"]))

    # Close location: where in the day's range it closed. 1 = at the high.
    rng = (h - l).replace(0, np.nan)
    out["close_loc"] = ((c - l) / rng).fillna(0.5)

    plan = trade_levels(out, cfg)
    for k, v in plan.items():
        out[k] = v
    return out


def trade_levels(f: pd.DataFrame, cfg: dict) -> dict[str, pd.Series]:
    """Entry trigger, stop and targets for a signal on each bar."""
    s = cfg["swing"]
    atr = f["atr14"]
    entry = f["high"] * (1 + s["entry_buffer_pct"] / 100)
    structure = (f["low"].rolling(s["stop_lookback"], min_periods=1).min()
                 - s["stop_buffer_atr"] * atr)
    lo, hi = s["stop_atr"]
    # Too close and noise takes you out; too far and the position is tiny.
    stop = structure.clip(lower=entry - hi * atr, upper=entry - lo * atr)
    risk = entry - stop
    return {"entry": entry, "stop": stop, "risk": risk,
            "stop_pct": 100 * risk / entry,
            "t1": entry + s["t1_r"] * risk, "t2": entry + s["t2_r"] * risk}


# --------------------------------------------------------------------- score

def setup_quality(f: pd.DataFrame, cfg: dict) -> pd.Series:
    """0-100 for how clean today's setup is; NaN where there is none."""
    pb = cfg["swing"]["pullback"]
    bo = cfg["swing"]["breakout"]
    pull = (linmap(f["pb_touch_atr"].clip(lower=0), pb["touch_atr"], 0.0) * 0.4
            + linmap(f["rsi14"], pb["rsi"][1], pb["rsi"][0]) * 0.3
            + linmap(f["close_loc"], 0.3, 1.0) * 0.3)
    brk = (linmap(f["vol_ratio"], bo["volume_mult"], 3.0) * 0.4
           + linmap(f["base_pct"], bo["max_base_pct"], 5.0) * 0.3
           + linmap(f["from_high_pct"], -bo["near_high_pct"], 0.0) * 0.3)
    q = pd.Series(np.nan, index=f.index)
    q = q.mask(f["setup_pullback"], pull)
    q = q.mask(f["setup_breakout"], np.fmax(q.fillna(-1), brk))
    return q


def score(f: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Component scores and the blended swing score.

    `f` must already carry `rs_pct`, the cross-sectional percentile of
    relative strength on its date (score_panel adds it). Relative strength is
    ranked against the rest of the universe on the same day rather than
    mapped to a fixed range, because what counts as strong moves with the
    market.
    """
    s = cfg["swing"]
    tr = s["trend_ranges"]
    out = pd.DataFrame(index=f.index)
    out["sc_trend"] = (linmap(f["sma50_vs_200"], *tr["sma50_vs_200_pct"]) * 0.4
                       + linmap(f["sma200_slope"], *tr["slope_200_pct"]) * 0.3
                       + linmap(f["adx14"], *tr["adx"]) * 0.3)
    out["sc_rs"] = f["rs_pct"]
    out["sc_setup"] = setup_quality(f, cfg)
    out["sc_volume"] = linmap(f["ud_vol_ratio"], *s["volume_ud_ratio"])
    out["sc_risk"] = linmap(f["stop_pct"], *s["stop_pct_range"])
    w = s["weights"]
    parts = {"trend": "sc_trend", "relative_strength": "sc_rs", "setup": "sc_setup",
             "volume": "sc_volume", "risk": "sc_risk"}
    # A name with no setup scores 0 on that component rather than having its
    # weight redistributed: otherwise a stock doing nothing could outscore a
    # real setup on trend alone.
    total = sum(w[k] * out[c].fillna(0) for k, c in parts.items())
    out["score"] = total / sum(w.values())
    return out


def score_panel(panel: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Add rs_pct and the scores to a (date, symbol) panel of feature rows.

    Percentiles are taken among eligible names only: a stock is ranked
    against the ones it could actually be traded instead of.
    """
    p = panel.copy()
    elig = p["eligible"] & p["rs_raw"].notna()
    p["rs_pct"] = np.nan
    p.loc[elig, "rs_pct"] = (p.loc[elig].groupby("date")["rs_raw"]
                             .rank(pct=True) * 100)
    return p.join(score(p, cfg))


def setup_name(row) -> str:
    if row.get("setup_breakout"):
        return "breakout"
    if row.get("setup_pullback"):
        return "pullback"
    return ""


def build_panel(frames: dict[str, pd.DataFrame], cfg: dict,
                start=None) -> pd.DataFrame:
    """Feature rows for every (date, symbol), scored. `start` trims the
    output (features are computed on full history first, so the 200 DMA is
    warm on the first kept date)."""
    rows = []
    for sym, f in frames.items():
        if f.empty or len(f) < 220:
            continue
        x = features(f, cfg)
        x["symbol"] = sym
        x["bar"] = np.arange(len(x))       # position in the frame, for simulate
        if start is not None:
            x = x[x["date"] >= start]
        rows.append(x)
    if not rows:
        return pd.DataFrame()
    return score_panel(pd.concat(rows, ignore_index=True), cfg)


def scan(panel: pd.DataFrame, on_date, cfg: dict,
         results: dict[str, list] | None = None) -> pd.DataFrame:
    """Every eligible name on one date, best first.

    Names with an active setup come first, ordered by score; eligible names
    without one follow (the "full list" -- in an uptrend, nothing to do yet).
    `results` maps symbol -> upcoming results dates; names reporting within
    `results_within_days` are kept but marked, and never suggested.
    """
    s = cfg["swing"]
    t = panel[(panel["date"] == on_date) & panel["eligible"]].copy()
    if t.empty:
        return t
    t["setup"] = [setup_name(r) for r in t[["setup_breakout", "setup_pullback"]]
                  .to_dict("records")]
    days = s.get("results_within_days", 0)
    t["results_on"] = None
    if results and days:
        horizon = pd.Timestamp(on_date) + pd.Timedelta(days=days)
        for i, sym in zip(t.index, t["symbol"]):
            soon = [d for d in results.get(sym, [])
                    if pd.Timestamp(on_date) <= pd.Timestamp(d) <= horizon]
            if soon:
                t.at[i, "results_on"] = min(soon)
    t["suggest"] = (t["setup"] != "") & t["results_on"].isna()
    t = t.sort_values(["suggest", "score"], ascending=[False, False])
    t["rank"] = range(1, len(t) + 1)
    return t.reset_index(drop=True)


# ------------------------------------------------------------------- regime

@dataclass
class Regime:
    index_above_200: bool | None
    index_vs_200_pct: float | None
    breadth_pct: float | None
    state: str                       # favourable | mixed | unfavourable | unknown
    note: str


def regime(bench: pd.DataFrame, panel_today: pd.DataFrame, cfg: dict) -> Regime:
    """Market backdrop for swing longs: trend of the index, and breadth.

    Breadth is the share of the universe above its own 50 DMA. An index can
    sit above its 200 DMA on a handful of heavyweights while most stocks are
    falling; long swing setups fail far more often in that tape.
    """
    s = cfg["swing"]
    above = pct = None
    if bench is not None and len(bench) >= 200:
        c = bench["close"].reset_index(drop=True)
        sma = c.rolling(200).mean().iloc[-1]
        above = bool(c.iloc[-1] > sma)
        pct = float(100 * (c.iloc[-1] / sma - 1))
    breadth = None
    t = panel_today[panel_today["sma50"].notna()]
    if len(t):
        breadth = float(100 * (t["close"] > t["sma50"]).mean())
    if above is None or breadth is None:
        return Regime(above, pct, breadth, "unknown", "not enough index history")
    ok_b = breadth >= s["breadth_min_pct"]
    state = ("favourable" if above and ok_b else
             "unfavourable" if not above and not ok_b else "mixed")
    note = (f"NIFTY 500 {pct:+.1f}% vs its 200 DMA; {breadth:.0f}% of the "
            f"universe above its 50 DMA (needs {s['breadth_min_pct']:.0f}%)")
    return Regime(above, pct, breadth, state, note)


def regime_series(bench: pd.DataFrame, panel: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """The regime on every date, for the backtest."""
    b = bench[["date", "close"]].copy()
    b["idx_above"] = b["close"] > b["close"].rolling(200).mean()
    br = (panel[panel["sma50"].notna()]
          .assign(a=lambda d: d["close"] > d["sma50"])
          .groupby("date")["a"].mean() * 100).rename("breadth")
    out = b[["date", "idx_above"]].merge(br.reset_index(), on="date", how="right")
    ok_b = out["breadth"] >= cfg["swing"]["breadth_min_pct"]
    above = out["idx_above"].fillna(False).astype(bool)
    out["regime"] = np.where(above & ok_b, "favourable",
                             np.where(~above & ~ok_b, "unfavourable", "mixed"))
    return out[["date", "regime", "breadth"]]


# --------------------------------------------------------------------- sizing

def position_size(entry: float, stop: float, capital: float, cfg: dict) -> dict:
    """Shares so a stop-out costs risk_per_trade_pct of capital, capped."""
    s = cfg["swing"]
    risk = entry - stop
    if not capital or risk <= 0 or entry <= 0:
        return {"qty": 0, "value": 0.0, "risk_amt": 0.0, "pct_of_capital": 0.0,
                "capped": False}
    by_risk = capital * s["risk_per_trade_pct"] / 100 / risk
    by_cap = capital * s["max_position_pct"] / 100 / entry
    qty = int(max(0, min(by_risk, by_cap)))
    return {"qty": qty, "value": qty * entry, "risk_amt": qty * risk,
            "pct_of_capital": 100 * qty * entry / capital,
            "capped": by_cap < by_risk}


# ----------------------------------------------------------------- simulation

@dataclass
class Trade:
    signal_idx: int
    entry_idx: int
    exit_idx: int
    entry: float
    stop: float
    r_gross: float                   # result in R, before costs
    r_net: float                     # after round-trip costs
    ret_pct: float                   # net return on the position, percent
    exit_reason: str                 # stop | t2 | time | t1+stop | t1+time | end
    sessions: int


def simulate(o, h, l, c, i: int, entry: float, stop: float, cfg: dict) -> Trade | None:
    """Play out a signal on bar `i` with the configured plan.

    Conservative wherever the daily bar is ambiguous: a gap through the stop
    fills at the open, and a bar touching both stop and target counts as the
    stop. Returns None if the buy-stop never triggers.
    """
    s = cfg["swing"]
    n = len(c)
    risk = entry - stop
    if risk <= 0:
        return None
    fill = None
    for j in range(i + 1, min(i + 1 + s["entry_valid_sessions"], n)):
        if h[j] >= entry:
            fill, k = max(o[j], entry), j
            break
    if fill is None:
        return None
    # Levels are fixed from the plan; a gap-up fill above the trigger just
    # earns less per R. Stop at or above the fill (a huge gap) means no trade.
    if fill <= stop:
        return None
    t1, t2 = entry + s["t1_r"] * risk, entry + s["t2_r"] * risk
    book = s["t1_book_pct"] / 100
    cur_stop, half_done, realised = stop, False, 0.0
    last = min(k + s["max_hold_sessions"], n - 1)
    reason = "end"
    exit_px = c[last]
    for j in range(k, last + 1):
        # On the entry bar only the part of the range after the fill counts;
        # treat the low as possible (conservative), the high only beyond fill.
        lo = l[j]
        if j > k and o[j] <= cur_stop:                     # gap through stop
            exit_px, reason = o[j], ("t1+stop" if half_done else "stop")
            last = j
            break
        if lo <= cur_stop:
            exit_px, reason = cur_stop, ("t1+stop" if half_done else "stop")
            last = j
            break
        if not half_done and h[j] >= t1:
            realised += book * (max(t1, o[j] if j > k else t1) - fill)
            half_done, cur_stop = True, max(cur_stop, fill)
        if h[j] >= t2:
            exit_px, reason = max(t2, o[j]) if j > k else t2, "t2"
            last = j
            break
    else:
        # Ran the full holding period, or ran out of data first ("end": the
        # trade is still open, valued at the last close).
        timed = last - k >= s["max_hold_sessions"]
        reason = ("t1+" if half_done else "") + ("time" if timed else "end")
    rest = (1 - book) if half_done else 1.0
    pnl = realised + rest * (exit_px - fill)
    cost = fill * s["round_trip_cost_pct"] / 100
    r_unit = fill - stop
    return Trade(i, k, last, fill, stop, pnl / r_unit, (pnl - cost) / r_unit,
                 100 * (pnl - cost) / fill, reason, last - k + 1)
