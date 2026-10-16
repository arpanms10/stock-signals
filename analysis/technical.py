"""The Technical Analyst: what is the chart doing, and where are the levels?

Rule-based, on the same indicators and scores the rest of the framework uses
(indicators.core, scoring.timing, strategy.swing), so a stock reads the same
here as on the Market and Swing tabs.

It describes the chart; it does not forecast it. The swing score did not rank
stocks in the backtest, and nothing here has been shown to predict returns
either. What it is good for: knowing whether you would be buying into an
uptrend or a falling knife, how stretched the price is, and where the nearby
support, resistance and a sensible stop sit.

Prices come from data/signals.db. A stock that is not there yet -- anything
outside the NIFTY 500 and your holdings -- is fetched from NSE on demand (a few
seconds per year of history) and stored like any other.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from scoring import timing
from strategy import swing as sw

HISTORY_YEARS = 3         # enough for a warm 200 DMA and a year of levels
MIN_BARS = 220


@dataclass
class Level:
    price: float
    kind: str                 # support / resistance
    source: str               # "swing high ×3", "200 DMA", "52-week high"
    pct_away: float


@dataclass
class TechnicalReport:
    symbol: str
    stance: str                          # Uptrend / Downtrend / Sideways / Not enough data
    qualifier: str = ""                  # "strong", "stretched", "losing momentum", ...
    summary: str = ""
    as_of: str = ""
    close: float | None = None
    score: float | None = None           # the framework's 0-100 timing score
    buckets: dict = field(default_factory=dict)
    signals: list[tuple[str, str, str]] = field(default_factory=list)  # (area, reading, tone)
    positives: list[str] = field(default_factory=list)
    negatives: list[str] = field(default_factory=list)
    returns: list[dict] = field(default_factory=list)   # period, stock, benchmark, excess
    supports: list[Level] = field(default_factory=list)
    resistances: list[Level] = field(default_factory=list)
    setup: dict | None = None            # today's swing setup with its trade plan
    chart: pd.DataFrame | None = None


# --------------------------------------------------------------------- data

def load_frame(symbol: str, cfg: dict | None = None, fetch: bool = True,
               con=None) -> pd.DataFrame:
    """Scored indicator frame, fetching missing or stale bars first."""
    from data import ingest, store
    from scoring import pipeline

    cfg = cfg or timing.load_config()
    con = con or store.connect()
    if fetch:
        last = store.last_date(con, "stock", symbol)
        bench_last = store.last_date(con, "index", cfg["signals"]["regime_index"])
        # Behind the benchmark means behind the market: top it up. Fetching is
        # gap-only, so a stock already current costs nothing.
        if last is None or (bench_last and last < bench_last):
            try:
                ingest.backfill_symbol(con, symbol, years=HISTORY_YEARS, verbose=False)
            except Exception:
                pass
    return pipeline.scored_frame(con, symbol, cfg)


# ------------------------------------------------------------------- levels

def pivots(high: pd.Series, low: pd.Series, window: int = 5) -> tuple[pd.Series, pd.Series]:
    """Swing highs and lows: a bar that is the extreme of `window` bars each side."""
    span = 2 * window + 1
    hi = high[high == high.rolling(span, center=True, min_periods=span).max()]
    lo = low[low == low.rolling(span, center=True, min_periods=span).min()]
    return hi, lo


def cluster(prices: list[float], tolerance_pct: float) -> list[tuple[float, int]]:
    """Group prices within `tolerance_pct` of each other: (mean, touches)."""
    groups: list[list[float]] = []
    for p in sorted(prices):
        if groups and p <= groups[-1][0] * (1 + tolerance_pct / 100):
            groups[-1].append(p)
        else:
            groups.append([p])
    return [(float(np.mean(g)), len(g)) for g in groups]


def levels(f: pd.DataFrame, lookback: int = 250, max_each: int = 3) -> tuple[list[Level], list[Level]]:
    """Nearest support below and resistance above today's close.

    From swing pivots over the last year, clustered (a level touched three
    times is stronger than one touched once), plus the moving averages and
    the 52-week extremes that traders watch anyway.
    """
    x = f.tail(lookback)
    close = float(x["close"].iloc[-1])
    atr_pct = float(x["atr_pct"].iloc[-1]) if "atr_pct" in x and pd.notna(x["atr_pct"].iloc[-1]) else 2.0
    # Cluster width scales with how much the stock normally moves.
    tol = float(np.clip(atr_pct * 0.6, 0.75, 4.0))
    hi, lo = pivots(x["high"], x["low"])
    cands: list[tuple[float, str]] = []
    for p, n in cluster(list(hi) + list(lo), tol):
        cands.append((p, f"swing {'pivot' if n == 1 else f'pivots ×{n}'}"))
    last = x.iloc[-1]
    for col, name in (("sma50", "50 DMA"), ("sma200", "200 DMA")):
        if pd.notna(last.get(col)):
            cands.append((float(last[col]), name))
    yr_hi, yr_lo = float(x["high"].max()), float(x["low"].min())
    cands += [(yr_hi, "52-week high"), (yr_lo, "52-week low")]

    def mk(p, src, kind):
        return Level(round(p, 2), kind, src, 100 * (p / close - 1))

    sup = sorted((mk(p, s, "support") for p, s in cands if p < close * 0.998),
                 key=lambda l: -l.price)
    res = sorted((mk(p, s, "resistance") for p, s in cands if p > close * 1.002),
                 key=lambda l: l.price)
    return _dedupe(sup, tol)[:max_each], _dedupe(res, tol)[:max_each]


def _dedupe(lv: list[Level], tol: float) -> list[Level]:
    """Merge levels that sit on top of each other, keeping both names."""
    out: list[Level] = []
    for l in lv:
        if out and abs(l.price / out[-1].price - 1) * 100 <= tol / 2:
            if l.source not in out[-1].source:
                out[-1].source += f" + {l.source}"
            continue
        out.append(l)
    return out


# ----------------------------------------------------------------- readings

def _ret(s: pd.Series, bars: int) -> float | None:
    if len(s) <= bars or pd.isna(s.iloc[-1 - bars]) or s.iloc[-1 - bars] == 0:
        return None
    return float(100 * (s.iloc[-1] / s.iloc[-1 - bars] - 1))


def returns_table(f: pd.DataFrame) -> list[dict]:
    out = []
    for label, bars in (("1 month", 21), ("3 months", 63), ("6 months", 126), ("1 year", 250)):
        r = _ret(f["close"], bars)
        b = _ret(f["bench_close"], bars) if "bench_close" in f else None
        out.append({"period": label, "stock_pct": r, "benchmark_pct": b,
                    "excess_pct": None if r is None or b is None else r - b})
    return out


def trend_state(row: pd.Series) -> str:
    c, s50, s200, slope = row["close"], row["sma50"], row["sma200"], row["sma200_slope"]
    if any(pd.isna(v) for v in (c, s50, s200, slope)):
        return "Not enough data"
    if c > s200 and s50 > s200 and slope > 0:
        return "Uptrend"
    if c < s200 and s50 < s200 and slope < 0:
        return "Downtrend"
    return "Sideways"


def read(f: pd.DataFrame) -> tuple[list[tuple[str, str, str]], list[str], list[str], str]:
    """Indicator readings as (area, reading, tone), plus positives, negatives
    and a qualifier for the trend."""
    r, p = f.iloc[-1], f.iloc[-2]
    sig: list[tuple[str, str, str]] = []
    pos: list[str] = []
    neg: list[str] = []
    c = r["close"]

    # Trend structure
    for col, name in (("sma50", "50 DMA"), ("sma200", "200 DMA")):
        if pd.notna(r[col]):
            d = 100 * (c / r[col] - 1)
            sig.append(("Trend", f"{abs(d):.1f}% {'above' if d >= 0 else 'below'} the {name}",
                        "pos" if d >= 0 else "neg"))
    if pd.notna(r["sma50"]) and pd.notna(r["sma200"]):
        golden = r["sma50"] > r["sma200"]
        crossed = (p["sma50"] > p["sma200"]) != golden
        txt = ("50 DMA above 200 DMA" if golden else "50 DMA below 200 DMA")
        if crossed:
            txt += f" — {'golden' if golden else 'death'} cross today"
        sig.append(("Trend", txt, "pos" if golden else "neg"))
    if pd.notna(r["sma200_slope"]):
        sl = r["sma200_slope"]
        sig.append(("Trend", f"200 DMA {'rising' if sl > 0 else 'falling'} ({sl:+.1f}% a month)",
                    "pos" if sl > 0 else "neg"))
    if pd.notna(r["adx14"]):
        a = r["adx14"]
        strength = "strong trend" if a >= 25 else "weak trend / range" if a < 20 else "moderate trend"
        side = "buyers" if r["plus_di"] > r["minus_di"] else "sellers"
        sig.append(("Trend", f"ADX {a:.0f}: {strength}, {side} in control",
                    "pos" if side == "buyers" and a >= 20 else "neg" if side == "sellers" and a >= 20 else "neutral"))

    # Momentum
    rsi = r["rsi14"]
    if pd.notna(rsi):
        zone = ("overbought" if rsi >= 70 else "strong" if rsi >= 55 else
                "neutral" if rsi >= 45 else "weak" if rsi > 30 else "oversold")
        sig.append(("Momentum", f"RSI {rsi:.0f} ({zone})",
                    "pos" if 55 <= rsi < 70 else "neg" if rsi <= 45 else "neutral"))
    if pd.notna(r["macd_hist"]):
        above = r["macd"] > r["macd_signal"]
        turned = (p["macd"] > p["macd_signal"]) != above
        rising = r["macd_hist"] > p["macd_hist"]
        txt = f"MACD {'above' if above else 'below'} signal"
        txt += (" — crossed today" if turned else
                f", histogram {'rising' if rising else 'falling'}")
        sig.append(("Momentum", txt, "pos" if above else "neg"))

    # Volatility
    if pd.notna(r["atr_pct"]):
        sig.append(("Volatility", f"ATR {r['atr_pct']:.1f}% of price a day "
                    f"(₹{r['atr14']:.2f})", "neutral"))
    bw = f["bb_width"].tail(250).dropna()
    if len(bw) > 50 and pd.notna(r["bb_width"]):
        pct = float((bw < r["bb_width"]).mean() * 100)
        if pct <= 20:
            sig.append(("Volatility", f"Bollinger squeeze: bands narrower than "
                        f"{100 - pct:.0f}% of the past year — a big move often follows",
                        "neutral"))

    # Volume
    if pd.notna(r["vol_ratio"]):
        sig.append(("Volume", f"today {r['vol_ratio']:.1f}× its 20-day average",
                    "neutral"))
    if pd.notna(r["ud_vol_ratio"]):
        u = r["ud_vol_ratio"]
        sig.append(("Volume", f"up-day / down-day volume {u:.2f} over 20 days "
                    f"({'accumulation' if u > 1.1 else 'distribution' if u < 0.9 else 'balanced'})",
                    "pos" if u > 1.1 else "neg" if u < 0.9 else "neutral"))
    if "delivery_pct" in f and f["delivery_pct"].tail(20).notna().sum() >= 10:
        d20 = f["delivery_pct"].tail(20).mean()
        d_prior = f["delivery_pct"].iloc[-120:-20].mean()
        if pd.notna(d_prior):
            sig.append(("Volume", f"delivery {d20:.0f}% of volume (20-day) vs "
                        f"{d_prior:.0f}% before", "pos" if d20 > d_prior + 3 else
                        "neg" if d20 < d_prior - 3 else "neutral"))

    # 52-week position
    if pd.notna(r["high_52w"]) and pd.notna(r["low_52w"]):
        from_hi = 100 * (c / r["high_52w"] - 1)
        sig.append(("Range", f"{abs(from_hi):.1f}% below the 52-week high"
                    if from_hi < -0.5 else "at a 52-week high",
                    "pos" if from_hi > -10 else "neg" if from_hi < -30 else "neutral"))

    for area, text, tone in sig:
        if tone == "pos":
            pos.append(f"{area}: {text}")
        elif tone == "neg":
            neg.append(f"{area}: {text}")

    # One qualifier for the headline, most important first.
    stretch = 100 * (c / r["sma50"] - 1) if pd.notna(r["sma50"]) else 0
    state = trend_state(r)
    q = ""
    if state == "Uptrend":
        if (pd.notna(rsi) and rsi >= 70) or stretch > 15:
            q = "stretched"
        elif pd.notna(r["macd"]) and r["macd"] < r["macd_signal"] and c < r["sma50"]:
            q = "losing momentum"
        elif pd.notna(r["adx14"]) and r["adx14"] >= 25:
            q = "strong"
    elif state == "Downtrend":
        if pd.notna(rsi) and rsi <= 30:
            q = "oversold"
        elif pd.notna(r["macd"]) and r["macd"] > r["macd_signal"] and c > r["sma50"]:
            q = "trying to turn"
    elif state == "Sideways":
        if pd.notna(r["sma200"]):
            q = "above the 200 DMA" if c > r["sma200"] else "below the 200 DMA"
    return sig, pos, neg, q


def swing_setup(f: pd.DataFrame, cfg: dict) -> dict | None:
    """Today's pullback or breakout setup, if any, with its trade plan."""
    if len(f) < MIN_BARS:
        return None
    x = sw.features(f, cfg).iloc[-1]
    name = sw.setup_name(x)
    if not name:
        return None
    return {"setup": name, "eligible": bool(x["eligible"]),
            "entry": float(x["entry"]), "stop": float(x["stop"]),
            "stop_pct": float(x["stop_pct"]), "t1": float(x["t1"]), "t2": float(x["t2"]),
            "note": "passes the Swing tab's filters" if x["eligible"] else
                    "the Swing tab would not suggest it: " + _why_not(x, cfg)}


def _why_not(x: pd.Series, cfg: dict) -> str:
    s = cfg["swing"]
    if not x["uptrend"]:
        return "not in an uptrend, so the pattern is more likely a bounce in a fall"
    if x["close"] < s["min_price"]:
        return f"price below ₹{s['min_price']:.0f}"
    if not (x["turnover_cr"] >= s["min_turnover_cr"]):
        return f"trades less than ₹{s['min_turnover_cr']:.0f} cr a day"
    return "daily range outside {:.0f}-{:.0f}% of price".format(*s["atr_pct"])


# ------------------------------------------------------------------ analyse

def analyse(symbol: str, f: pd.DataFrame, cfg: dict | None = None) -> TechnicalReport:
    """Report from a scored frame. Pure: no network."""
    cfg = cfg or timing.load_config()
    if f is None or f.empty or len(f) < 60:
        return TechnicalReport(symbol, "Not enough data",
                               summary="Fewer than three months of prices — too "
                                       "little history to read a trend.")
    r = f.iloc[-1]
    state = trend_state(r)
    sig, pos, neg, qual = read(f)
    sup, res = levels(f)
    rets = returns_table(f)
    setup = swing_setup(f, cfg)

    buckets = {b.replace("_", " "): (None if pd.isna(r.get("volume_score" if b == "volume" else b))
                                     else float(r.get("volume_score" if b == "volume" else b)))
               for b in timing.BUCKETS}
    score = None if pd.isna(r.get("timing_score")) else float(r["timing_score"])

    if state == "Not enough data":
        summary = ("Under a year of history, so the 200 DMA is not formed yet; "
                   "shorter-term readings below.")
    else:
        head = {"Uptrend": "In an uptrend", "Downtrend": "In a downtrend",
                "Sideways": "No clear trend"}[state]
        summary = head + (f", {qual}" if qual else "") + "."
        yr = next((x for x in rets if x["period"] == "6 months"), None)
        if yr and yr["excess_pct"] is not None:
            summary += (f" {'Outperformed' if yr['excess_pct'] >= 0 else 'Underperformed'} "
                        f"the NIFTY 500 by {abs(yr['excess_pct']):.0f} points over 6 months.")
        if sup:
            summary += f" Nearest support ₹{sup[0].price:,.2f} ({sup[0].pct_away:+.1f}%)"
            summary += (f", resistance ₹{res[0].price:,.2f} ({res[0].pct_away:+.1f}%)."
                        if res else ", no resistance overhead in the past year.")

    chart = f.tail(250)[["date", "close", "sma50", "sma200", "volume"]].copy()
    chart["date"] = pd.to_datetime(chart["date"])
    return TechnicalReport(
        symbol, state, qual, summary, str(r["date"]), float(r["close"]), score,
        buckets, sig, pos, neg, rets, sup, res, setup, chart)


def analyse_symbol(symbol: str, fetch: bool = True) -> TechnicalReport:
    cfg = timing.load_config()
    return analyse(symbol, load_frame(symbol, cfg, fetch=fetch), cfg)
