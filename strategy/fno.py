"""F&O analysis: PCR, support/resistance from open interest, expected range.

Pure functions over a chain DataFrame (see data/sources/nse_derivatives.py:
one row per strike, ce_* and pe_* columns). No I/O, so the backtest in
run_fno_backtest.py runs exactly this code on historical bhavcopy chains.

What open interest is evidence of: where option WRITERS have put money. A
large put OI below spot means writers are paid as long as price stays above
that strike, which is why it is read as support; large call OI above spot as
resistance.

What it is NOT evidence of, measured in docs/f_o/validation.md on ~14,000
stock and 360 index observations since January 2024: where price ends up.
The walls held exactly as often as any level the same distance from spot,
PCR did not predict direction, and max pain was a worse guess than "no
change". So the walls, PCR and max pain are reported as positioning, and the
range is spot +/- the ATM straddle -- the market's own price for the move --
labelled with how often it has actually held.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

DEFAULTS = {
    "wall_window_pct": 10.0,
    "wall_min_share_pct": 5.0,
    # Index PCR: the conventional reading.
    "pcr_bearish_below": 0.7,
    "pcr_bullish_above": 1.2,
    "pcr_stretched_above": 1.6,
    # Stock PCR sits far lower (median 0.63), so index thresholds would call
    # ~70% of stocks bearish. These are its 20th/80th/97th percentiles.
    "equity_pcr_bearish_below": 0.5,
    "equity_pcr_bullish_above": 0.8,
    "equity_pcr_stretched_above": 1.05,
    # Share of expiry closes inside spot +/- straddle, by sessions out.
    "range_hit_pct": {"index": {5: 49, 10: 56, 20: 58},
                      "equity": {5: 61, 10: 63, 20: 54}},
    "near_expiry_sessions": 2,
    "risk_free_pct": 6.5,
    "stale_minutes": 30,
}


def settings(cfg: dict | None = None) -> dict:
    return {**DEFAULTS, **((cfg or {}).get("fno") or {})}


def range_hit_pct(kind: str, sessions: int, s: dict) -> float | None:
    """How often the expiry close landed inside +/- straddle, for the nearest
    measured offset (5, 10 or 20 sessions out)."""
    table = (s.get("range_hit_pct") or {}).get(kind) or {}
    if not table:
        return None
    k = min(table, key=lambda x: abs(int(x) - sessions))
    return float(table[k])


# ------------------------------------------------------------------- PCR

def _ratio(num: float, den: float) -> float | None:
    return float(num / den) if den > 0 and num >= 0 else None


def pcr(strikes: pd.DataFrame) -> dict:
    """Put/call ratios on OI, today's change in OI, and volume.

    The change-in-OI ratio is only defined when both sides ADDED open
    interest; if either side shed it (net unwinding), a ratio of two numbers
    with different signs means nothing, so it is None and the raw changes are
    reported instead.
    """
    ce_chg, pe_chg = strikes["ce_chg_oi"].sum(), strikes["pe_chg_oi"].sum()
    return {
        "oi": _ratio(strikes["pe_oi"].sum(), strikes["ce_oi"].sum()),
        "chg_oi": _ratio(pe_chg, ce_chg) if ce_chg > 0 and pe_chg > 0 else None,
        "volume": _ratio(strikes["pe_vol"].sum(), strikes["ce_vol"].sum()),
        "ce_oi": float(strikes["ce_oi"].sum()),
        "pe_oi": float(strikes["pe_oi"].sum()),
        "ce_chg_oi": float(ce_chg),
        "pe_chg_oi": float(pe_chg),
    }


def pcr_bias(value: float | None, s: dict, kind: str = "index") -> str:
    """Positioning label. Stocks are read against stock norms, not index ones.
    Describes where writers lean -- validation found no directional edge."""
    if value is None or math.isnan(value):
        return "n/a"
    pre = "equity_" if kind == "equity" else ""
    if value > s[pre + "pcr_stretched_above"]:
        return "stretched"
    if value > s[pre + "pcr_bullish_above"]:
        return "bullish"
    if value < s[pre + "pcr_bearish_below"]:
        return "bearish"
    return "neutral"


# --------------------------------------------------------------- max pain

def max_pain(strikes: pd.DataFrame) -> float | None:
    """Expiry price at which option holders, in total, collect the least.

    For each candidate settlement K: calls pay oi * max(K - strike, 0) and puts
    pay oi * max(strike - K, 0). Max pain is the K minimising the sum.
    """
    k = strikes["strike"].to_numpy(float)
    ce, pe = strikes["ce_oi"].to_numpy(float), strikes["pe_oi"].to_numpy(float)
    if ce.sum() + pe.sum() == 0:
        return None
    settle = k[:, None]
    payout = (ce * np.maximum(settle - k, 0)).sum(1) + (pe * np.maximum(k - settle, 0)).sum(1)
    return float(k[int(np.argmin(payout))])


# ------------------------------------------------------------------ walls

@dataclass
class Wall:
    strike: float
    oi: float
    chg_oi: float
    share_pct: float          # of this side's OI within the search window

    @property
    def chg_pct(self) -> float | None:
        prev = self.oi - self.chg_oi
        return 100 * self.chg_oi / prev if prev > 0 else None


def walls(strikes: pd.DataFrame, spot: float, side: str, s: dict,
          n: int = 2) -> list[Wall]:
    """Strongest strikes by OI on one side of spot.

    side='pe' -> support: puts strictly below spot.
    side='ce' -> resistance: calls strictly above spot.
    Each side only looks out of the money: a call written below spot is
    already in the money -- a hedge or a covered position, not a ceiling --
    and likewise a put above spot. Limited to wall_window_pct of spot,
    where stock chains thin out into strikes nobody trades, and a strike must
    hold wall_min_share_pct of the window's OI to count.
    """
    lo, hi = spot * (1 - s["wall_window_pct"] / 100), spot * (1 + s["wall_window_pct"] / 100)
    if side == "pe":
        win = strikes[(strikes["strike"] < spot) & (strikes["strike"] >= lo)]
    elif side == "ce":
        win = strikes[(strikes["strike"] > spot) & (strikes["strike"] <= hi)]
    else:
        raise ValueError(side)
    oi = win[f"{side}_oi"]
    total = oi.sum()
    if total <= 0:
        return []
    top = win.assign(_oi=oi).sort_values(["_oi", "strike"],
                                         ascending=[False, side == "ce"])
    out = []
    for _, r in top.head(n).iterrows():
        share = 100 * r["_oi"] / total
        if share < s["wall_min_share_pct"]:
            break
        out.append(Wall(float(r["strike"]), float(r["_oi"]),
                        float(r[f"{side}_chg_oi"]), float(share)))
    return out


# --------------------------------------------------------- expected move

def atm_strike(strikes: pd.DataFrame, spot: float) -> float:
    k = strikes["strike"].to_numpy(float)
    return float(k[int(np.argmin(np.abs(k - spot)))])


def _mid(row: pd.Series, side: str) -> float:
    bid, ask, ltp = row[f"{side}_bid"], row[f"{side}_ask"], row[f"{side}_ltp"]
    if pd.notna(bid) and pd.notna(ask) and ask >= bid:
        return float((bid + ask) / 2)
    return float(ltp) if pd.notna(ltp) else float("nan")


def straddle(strikes: pd.DataFrame, spot: float) -> float | None:
    """ATM call + put premium: the market's price for a move either way.

    Uses the bid/ask mid where quoted -- last traded price can be hours old on
    a thin stock chain -- and falls back to LTP.
    """
    row = strikes.set_index("strike").loc[atm_strike(strikes, spot)]
    v = _mid(row, "ce") + _mid(row, "pe")
    return None if math.isnan(v) else v


def atm_iv(strikes: pd.DataFrame, spot: float) -> float | None:
    row = strikes.set_index("strike").loc[atm_strike(strikes, spot)]
    ivs = [v for v in (row["ce_iv"], row["pe_iv"]) if pd.notna(v)]
    return float(np.mean(ivs)) if ivs else None


def iv_move(spot: float, iv_pct: float | None, days: int) -> float | None:
    """One standard deviation to expiry, from IV over calendar days."""
    if iv_pct is None or days < 0:
        return None
    return spot * iv_pct / 100 * math.sqrt(max(days, 0.5) / 365)


def sessions_to(today: dt.date, expiry: dt.date, holidays=()) -> int:
    """Trading sessions after today up to and including expiry: weekdays,
    minus NSE's F&O holidays when given (nse_derivatives.trading_holidays)."""
    return int(np.busday_count(today + dt.timedelta(days=1), expiry + dt.timedelta(days=1),
                               holidays=[h for h in holidays]))


# ---------------------------------------------------------------- futures

BUILDUP = {
    (True, True): "long buildup",       # price up, OI up: new longs
    (False, True): "short buildup",     # price down, OI up: new shorts
    (True, False): "short covering",    # price up, OI down: shorts exiting
    (False, False): "long unwinding",   # price down, OI down: longs exiting
}


def buildup(price_chg: float, oi_chg: float) -> str | None:
    if price_chg == 0 or oi_chg == 0 or pd.isna(price_chg) or pd.isna(oi_chg):
        return None
    return BUILDUP[(price_chg > 0, oi_chg > 0)]


@dataclass
class FuturesView:
    expiry: dt.date
    price: float
    basis: float                 # futures - spot
    basis_ann_pct: float | None  # annualised, as a cost-of-carry check
    oi: float
    chg_oi: float
    price_chg: float
    buildup: str | None


def futures_view(fut: pd.DataFrame, spot: float, expiry: dt.date,
                 today: dt.date) -> FuturesView | None:
    hit = fut[fut["expiry"] == expiry] if len(fut) else fut
    if hit is None or hit.empty:
        return None
    r = hit.iloc[0]
    days = (expiry - today).days
    basis = float(r["ltp"] - spot)
    chg = float(r["ltp"] - r["prev_close"]) if pd.notna(r["prev_close"]) else float("nan")
    return FuturesView(
        expiry=expiry, price=float(r["ltp"]), basis=basis,
        basis_ann_pct=100 * basis / spot * 365 / days if days > 0 else None,
        oi=float(r["oi"]), chg_oi=float(r["chg_oi"]), price_chg=chg,
        buildup=buildup(chg, float(r["chg_oi"])),
    )


# ------------------------------------------------------------- the view

@dataclass
class FnoView:
    symbol: str
    expiry: dt.date
    spot: float
    timestamp: dt.datetime
    days: int
    sessions: int
    pcr: dict
    pcr_bias: str
    chg_pcr_bias: str
    supports: list[Wall]
    resistances: list[Wall]
    max_pain: float | None
    atm: float
    atm_iv: float | None
    straddle: float | None
    iv_move: float | None
    range_low: float | None       # spot - move
    range_high: float | None      # spot + move
    range_from: str               # "straddle" | "IV" | ""
    range_hit_pct: float | None   # measured: expiry closes inside the range
    futures: FuturesView | None
    lot: int | None = None
    kind: str = "index"
    support_reach: "Reach | None" = None
    resistance_reach: "Reach | None" = None
    vol: "VolContext | None" = None
    conditional: str | None = None    # hit rate under a measured condition
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def support(self) -> Wall | None:
        return self.supports[0] if self.supports else None

    @property
    def resistance(self) -> Wall | None:
        return self.resistances[0] if self.resistances else None

    @property
    def bias(self) -> str:
        """One word, from PCR with today's change in OI taking precedence --
        it is what writers did today, where total OI includes positions weeks
        old."""
        return self.chg_pcr_bias if self.chg_pcr_bias != "n/a" else self.pcr_bias


def expected_range(spot: float, move: float | None, support: Wall | None,
                   resistance: Wall | None) -> tuple:
    """The tighter of the OI walls and spot +/- the expected move, per side.

    The live default until validation (2026-10-08) showed the walls only made
    the range narrower, never more accurate: it held exactly as often as its
    width predicted. Kept so run_fno_backtest.py can keep reporting it.
    Returns (low, high, low_from, high_from).
    """
    lo_c, hi_c = [], []
    if move is not None:
        lo_c.append((spot - move, "expected move"))
        hi_c.append((spot + move, "expected move"))
    if support is not None:
        lo_c.append((support.strike, "support"))
    if resistance is not None:
        hi_c.append((resistance.strike, "resistance"))
    lo = max(lo_c, key=lambda t: t[0]) if lo_c else (None, "")
    hi = min(hi_c, key=lambda t: t[0]) if hi_c else (None, "")
    return lo[0], hi[0], lo[1], hi[1]


def _fmt(x: float) -> str:
    return f"{x:,.0f}" if abs(x) >= 100 else f"{x:,.2f}"


def analyse(symbol: str, strikes: pd.DataFrame, spot: float, expiry: dt.date,
            timestamp: dt.datetime, today: dt.date | None = None,
            fut: pd.DataFrame | None = None, cfg: dict | None = None,
            lot: int | None = None, now: dt.datetime | None = None,
            kind: str | None = None, curve: dict | None = None,
            holidays=(), weekly: bool = False, iv_history: pd.Series | None = None,
            closes: pd.Series | None = None, iv_next: float | None = None,
            results: list | None = None) -> FnoView:
    """The whole read for one chain. The optional history arguments feed the
    volatility context (see vol_context); without them it is left blank."""
    s = settings(cfg)
    kind = kind or strikes.attrs.get("kind") or "index"
    today = today or timestamp.date()
    days = (expiry - today).days
    sess = sessions_to(today, expiry, holidays)

    p = pcr(strikes)
    sup = walls(strikes, spot, "pe", s)
    res = walls(strikes, spot, "ce", s)
    strad = straddle(strikes, spot)
    iv = atm_iv(strikes, spot)
    ivm = iv_move(spot, iv, days)
    move, move_from = (strad, "straddle") if strad is not None else (ivm, "IV")
    if move is None:
        move_from = ""
    fv = futures_view(fut, spot, expiry, today) if fut is not None else None

    v = FnoView(
        symbol=symbol, expiry=expiry, spot=spot, timestamp=timestamp, days=days,
        sessions=sess, pcr=p, pcr_bias=pcr_bias(p["oi"], s, kind),
        chg_pcr_bias=pcr_bias(p["chg_oi"], s, kind), supports=sup, resistances=res,
        max_pain=max_pain(strikes), atm=atm_strike(strikes, spot), atm_iv=iv,
        straddle=strad, iv_move=ivm,
        range_low=spot - move if move is not None else None,
        range_high=spot + move if move is not None else None,
        range_from=move_from,
        range_hit_pct=(range_hit_pct(f"{kind} weekly" if weekly else kind, sess, s)
                       if move_from == "straddle" else None),
        futures=fv, lot=lot, kind=kind,
    )
    if sup:
        v.support_reach = reach(spot, strad, sup[0].strike, kind, curve)
    if res:
        v.resistance_reach = reach(spot, strad, res[0].strike, kind, curve)
    v.vol = vol_context(strikes, spot, strad, days, s, iv_history, closes,
                        iv_next, results)
    v.reasons = _reasons(v)
    v.warnings = _warnings(v, s, now)
    return v


NO_EDGE = "positioning only: no measured edge (docs/f_o/validation.md)"


def _reasons(v: FnoView) -> list[str]:
    r = []
    norms = "stock" if v.kind == "equity" else "index"
    if v.range_low is not None and v.range_high is not None:
        if v.range_from == "straddle":
            line = (f"Range {_fmt(v.range_low)} - {_fmt(v.range_high)} is spot ± the ATM "
                    f"straddle ({100 * v.straddle / v.spot:.1f}%): the move the market is "
                    "pricing to expiry.")
            if v.range_hit_pct is not None:
                line += (f" Since {'Jan 2024' if v.kind == 'equity' else '2019'} the "
                         "expiry close landed inside it about "
                         f"{v.range_hit_pct:.0f}% of the time for "
                         f"{'stocks' if v.kind == 'equity' else 'indices'} this far out.")
        else:
            line = (f"Range {_fmt(v.range_low)} - {_fmt(v.range_high)} is spot ± one "
                    "standard deviation from ATM IV (no ATM quote for a straddle); "
                    "its hit rate has not been measured.")
        r.append(line)
    if v.support:
        r.append(f"Largest put OI below spot: {_fmt(v.support.strike)} "
                 f"({v.support.share_pct:.0f}% of puts in range) -- read as support; "
                 + NO_EDGE + ".")
    else:
        r.append("No put strike below spot stands out.")
    if v.resistance:
        r.append(f"Largest call OI above spot: {_fmt(v.resistance.strike)} "
                 f"({v.resistance.share_pct:.0f}% of calls in range) -- read as "
                 "resistance; same caveat.")
    else:
        r.append("No call strike above spot stands out.")
    if v.pcr["oi"] is not None:
        if v.kind == "equity":
            r.append(f"PCR {v.pcr['oi']:.2f} on open interest: {v.pcr_bias} against "
                     "stock norms. No absolute signal; against other stocks the same "
                     "day, high PCR leaned up ~5-6 points -- tentative, unconfirmed.")
        else:
            r.append(f"PCR {v.pcr['oi']:.2f} on open interest: {v.pcr_bias} against "
                     f"{norms} norms -- positioning only; it did not predict direction.")
    if v.pcr["chg_oi"] is not None:
        r.append(f"PCR {v.pcr['chg_oi']:.2f} on today's added OI: {v.chg_pcr_bias} "
                 "-- what writers did today (untested: end-of-day history only).")
    else:
        r.append(f"Today's OI change: calls {v.pcr['ce_chg_oi']:+,.0f}, puts "
                 f"{v.pcr['pe_chg_oi']:+,.0f} contracts -- a side is unwinding, so no ratio.")
    if v.max_pain is not None:
        r.append(f"Max pain {_fmt(v.max_pain)}: historically a worse guess for the "
                 "expiry close than today's price.")
    if v.futures and v.futures.buildup:
        r.append(f"Futures {v.futures.price_chg:+,.2f} with OI {v.futures.chg_oi:+,.0f}: "
                 f"{v.futures.buildup}.")
    return r


def _warnings(v: FnoView, s: dict, now: dt.datetime | None) -> list[str]:
    w = []
    if v.vol and v.vol.results:
        w.append("Results due " + ", ".join(f"{d:%d-%b}" for d in v.vol.results)
                 + " -- before expiry. Implied volatility is usually bid up into a "
                 "results date and collapses after it.")
    if v.sessions <= s["near_expiry_sessions"]:
        w.append(f"{v.sessions} session(s) to expiry: OI is distorted by rollover and "
                 "expiring positions. Consider the next monthly (--expiry).")
    if v.support is None or v.resistance is None:
        w.append("Thin chain: no clear OI wall on at least one side.")
    if v.straddle is None:
        w.append("No ATM quote: the expected move falls back to IV, or is missing.")
    if now is not None:
        age = (now - v.timestamp).total_seconds() / 60
        in_session = now.weekday() < 5 and dt.time(9, 15) <= now.time() <= dt.time(15, 30)
        if in_session and age > s["stale_minutes"]:
            w.append(f"Chain timestamp is {age:.0f} minutes old during market hours.")
    return w


# ---------------------------------------------------------------- history

def history(snaps: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    """One row per stored snapshot: how PCR, the walls and the range moved.

    `snaps` is store.load_option_snapshots(): strike rows with ts and spot.
    """
    cols = ["ts", "spot", "pcr_oi", "pcr_chg_oi", "ce_chg_oi", "pe_chg_oi", "support", "resistance",
            "max_pain", "straddle", "range_low", "range_high"]
    if snaps is None or snaps.empty:
        return pd.DataFrame(columns=cols)
    s = settings(cfg)
    out = []
    for ts, g in snaps.groupby("ts", sort=True):
        spot = float(g["spot"].iloc[0])
        g = g.sort_values("strike")
        p = pcr(g)
        sup, res = walls(g, spot, "pe", s, n=1), walls(g, spot, "ce", s, n=1)
        strad = straddle(g, spot)
        lo = spot - strad if strad is not None else None
        hi = spot + strad if strad is not None else None
        out.append({"ts": ts, "spot": spot, "pcr_oi": p["oi"], "pcr_chg_oi": p["chg_oi"],
                    "ce_chg_oi": p["ce_chg_oi"], "pe_chg_oi": p["pe_chg_oi"],
                    "support": sup[0].strike if sup else None,
                    "resistance": res[0].strike if res else None,
                    "max_pain": max_pain(g), "straddle": strad,
                    "range_low": lo, "range_high": hi})
    return pd.DataFrame(out, columns=cols)


# ------------------------------------------------------------------ reach

# Expected |move| of a normal variable is sigma * sqrt(2/pi), and an ATM
# straddle prices roughly that, so sigma*sqrt(t) ~= straddle / (0.798 * spot).
STRADDLE_TO_SIGMA = math.sqrt(2 / math.pi)
REACH_GRID = [round(0.05 * i, 2) for i in range(81)]          # 0 .. 4 moves


def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def reach_curve(obs: pd.DataFrame, offsets=(5, 10)) -> dict:
    """Historical reach probabilities by distance in straddle units, per kind.

    `obs` is run_fno_backtest.observe(): spot, close, path_min/max (daily
    closes) and the straddle at observation. Up and down moves are pooled:
    the sample's direction was the period's drift (mostly falling stocks),
    which the validation found nothing to predict, so baking it in would
    only bias every estimate. 20 sessions out is left out -- 11 expiries, all
    skewed by that same drift.
    """
    o = obs[(~obs["adjusted"]) & obs["sessions"].isin(offsets)].dropna(subset=["straddle"])
    o = o[o["straddle"] > 0]
    out = {}
    for kind, g in o.groupby("kind"):
        s = g["straddle"].to_numpy()
        up = (g["path_max"].to_numpy() - g["spot"].to_numpy()) / s
        dn = (g["spot"].to_numpy() - g["path_min"].to_numpy()) / s
        ret = (g["close"].to_numpy() - g["spot"].to_numpy()) / s
        touch = np.concatenate([up, dn])
        beyond = np.concatenate([ret, -ret])
        out[kind] = {
            "n": int(len(g)),
            "z": REACH_GRID,
            "touch": [round(float((touch >= z).mean()), 4) for z in REACH_GRID],
            "expiry": [round(float((beyond >= z).mean()), 4) for z in REACH_GRID],
        }
        # Intraday: the day's high/low (estimated from the near future for
        # stocks, the index's own for indices) instead of the close.
        if {"path_high", "path_low"} <= set(g.columns):
            h = g.dropna(subset=["path_high", "path_low"])
            if len(h) >= 100:
                sh = h["straddle"].to_numpy()
                ui = (h["path_high"].to_numpy() - h["spot"].to_numpy()) / sh
                di = (h["spot"].to_numpy() - h["path_low"].to_numpy()) / sh
                ti = np.concatenate([ui, di])
                out[kind]["touch_intraday"] = [round(float((ti >= z).mean()), 4)
                                               for z in REACH_GRID]
                out[kind]["n_intraday"] = int(len(h))
    return out


@dataclass
class Reach:
    level: float
    side: str                     # "up" | "down"
    distance_pct: float
    moves: float                  # distance in ATM straddles
    hist_touch: float | None      # closed at/beyond it on some day before expiry
    hist_touch_intraday: float | None  # traded at/beyond it (daily high/low)
    hist_expiry: float | None     # beyond it at expiry
    model_expiry: float | None    # same, from the straddle-implied normal
    n: int | None                 # observations behind the historical numbers


def reach(spot: float, straddle: float | None, level: float, kind: str,
          curve: dict | None) -> Reach | None:
    """How often price got to `level` before expiry, for moves this size.

    Not a forecast of direction: the same distance up or down gets the same
    historical number. It answers "is this target (or stop) realistic for
    this expiry?", not "will it get there?".
    """
    if not straddle or straddle <= 0 or level <= 0 or level == spot:
        return None
    side = "up" if level > spot else "down"
    z = abs(level - spot) / straddle
    sig = straddle / (STRADDLE_TO_SIGMA * spot)
    model = 1 - _ncdf(abs(math.log(level / spot)) / sig)
    c = (curve or {}).get(kind)
    ht = he = hi = None
    if c:
        ht = float(np.interp(z, c["z"], c["touch"]))
        he = float(np.interp(z, c["z"], c["expiry"]))
        if "touch_intraday" in c:
            hi = float(np.interp(z, c["z"], c["touch_intraday"]))
    return Reach(level=level, side=side, distance_pct=100 * (level / spot - 1),
                 moves=z, hist_touch=ht, hist_touch_intraday=hi,
                 hist_expiry=he, model_expiry=model,
                 n=c["n"] if c else None)


def load_reach_curve(path) -> dict | None:
    import json
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    return data.get("curves")


# ---------------------------------------------------------- option pricing
# Vectorised Black-Scholes on spot, no dividend. Used for Greeks on the live
# chain (NSE's per-strike IV) and to back out IV from bhavcopy closes, which
# carry no IV. numpy has no erf, and math.erf per element is far too slow for
# the history, so the normal CDF is the Abramowitz-Stegun 26.2.17
# approximation (abs error < 7.5e-8).

def ncdf(x):
    x = np.asarray(x, dtype=float)
    a1, a2, a3, a4, a5 = 0.319381530, -0.356563782, 1.781477937, -1.821255978, 1.330274429
    ax = np.abs(x)
    t = 1.0 / (1.0 + 0.2316419 * ax)
    pdf = np.exp(-0.5 * ax * ax) / math.sqrt(2 * math.pi)
    upper = pdf * t * (a1 + t * (a2 + t * (a3 + t * (a4 + t * a5))))
    return np.where(x >= 0, 1.0 - upper, upper)


def npdf(x):
    x = np.asarray(x, dtype=float)
    return np.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _d1d2(S, K, T, r, sigma):
    S, K, T, sigma = (np.asarray(v, dtype=float) for v in (S, K, T, sigma))
    vt = sigma * np.sqrt(T)
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(S / K) + (r + 0.5 * sigma * sigma) * T) / vt
    return d1, d1 - vt


def bs_price(S, K, T, r, sigma, call):
    d1, d2 = _d1d2(S, K, T, r, sigma)
    disc = np.exp(-r * np.asarray(T, dtype=float))
    c = S * ncdf(d1) - K * disc * ncdf(d2)
    p = K * disc * ncdf(-d2) - S * ncdf(-d1)
    return np.where(call, c, p)


def implied_vol(price, S, K, T, r, call, lo=0.01, hi=3.0, iters=60):
    """Bisection, vectorised. NaN where the price is missing or outside what
    any volatility in [lo, hi] can produce (e.g. below intrinsic)."""
    price = np.asarray(price, dtype=float)
    shape = np.broadcast(price, S, K, T, call).shape
    S, K, T, call = (np.broadcast_to(np.asarray(v), shape) for v in (S, K, T, call))
    price = np.broadcast_to(price, shape)
    a, b = np.full(shape, lo), np.full(shape, hi)
    pa, pb = bs_price(S, K, T, r, a, call), bs_price(S, K, T, r, b, call)
    ok = np.isfinite(price) & (price >= pa) & (price <= pb) & (T > 0)
    for _ in range(iters):
        m = 0.5 * (a + b)
        pm = bs_price(S, K, T, r, m, call)
        up = pm < price
        a = np.where(up, m, a)
        b = np.where(up, b, m)
    return np.where(ok, 0.5 * (a + b), np.nan)


def year_frac(days: float) -> float:
    """Calendar time to expiry; expiry day itself counts as a quarter day."""
    return max(days, 0.25) / 365.0


def add_greeks(strikes: pd.DataFrame, spot: float, days: int, r_pct: float) -> pd.DataFrame:
    """Delta, gamma, theta (per day), vega (per vol point) for every strike,
    from NSE's own IV for that strike. prob_itm is N(d2): the model's chance
    of finishing in the money -- calibrated in docs/f_o/validation.md."""
    out = strikes.copy()
    T, r = year_frac(days), r_pct / 100
    K = out["strike"].to_numpy(float)
    for side, call in (("ce", True), ("pe", False)):
        sig = out[f"{side}_iv"].to_numpy(float) / 100
        d1, d2 = _d1d2(spot, K, T, r, sig)
        disc = math.exp(-r * T)
        sqT = math.sqrt(T)
        if call:
            delta = ncdf(d1)
            theta = (-spot * npdf(d1) * sig / (2 * sqT) - r * K * disc * ncdf(d2)) / 365
            itm = ncdf(d2)
        else:
            delta = ncdf(d1) - 1
            theta = (-spot * npdf(d1) * sig / (2 * sqT) + r * K * disc * ncdf(-d2)) / 365
            itm = ncdf(-d2)
        with np.errstate(divide="ignore", invalid="ignore"):
            gamma = npdf(d1) / (spot * sig * sqT)
        vega = spot * npdf(d1) * sqT / 100
        out[f"{side}_delta"] = np.round(delta, 3)
        out[f"{side}_gamma"] = np.round(gamma, 6)
        out[f"{side}_theta"] = np.round(theta, 2)
        out[f"{side}_vega"] = np.round(vega, 2)
        out[f"{side}_prob_itm"] = np.round(itm, 3)
    return out


def straddle_iv(spot: float, straddle: float | None, days: float) -> float | None:
    """Annualised volatility implied by the ATM straddle, in %.

    The same measure is computed from bhavcopy closes for the history, so
    today's value and the IV percentile compare like with like -- NSE's own
    IV column does not exist in the history."""
    if not straddle or straddle <= 0 or spot <= 0:
        return None
    return 100 * straddle / (STRADDLE_TO_SIGMA * spot * math.sqrt(year_frac(days)))


# ------------------------------------------------------ volatility context

@dataclass
class VolContext:
    iv: float | None              # annualised %, from today's ATM straddle
    iv_pct: float | None          # percentile of iv against the past year
    iv_hist_n: int                # days of history behind iv_pct
    rv20: float | None            # realised volatility, last 20 sessions, %
    iv_rv: float | None           # iv / rv20
    skew: float | None            # put-wing IV minus call-wing IV, vol points
    term: float | None            # next monthly IV minus this one, vol points
    results: list = field(default_factory=list)   # results dates before expiry


def iv_percentile(iv: float | None, history: pd.Series | None,
                  min_days: int = 120) -> tuple[float | None, int]:
    """Share of the past year's daily IVs below today's, in %.

    The percentile (how often IV was lower), not the rank (where today sits
    between the year's high and low): one spike sets the rank's scale for a
    year, while the percentile shrugs it off."""
    if iv is None or history is None:
        return None, 0
    h = pd.Series(history).dropna().tail(252)
    if len(h) < min_days:
        return None, len(h)
    return float(100 * (h < iv).mean()), len(h)


def realized_vol(closes: pd.Series | None, n: int = 20) -> float | None:
    """Annualised % from daily log returns over the last n sessions."""
    if closes is None:
        return None
    c = pd.Series(closes).dropna().tail(n + 1)
    if len(c) < n + 1:
        return None
    r = np.diff(np.log(c.to_numpy(float)))
    return float(100 * r.std(ddof=1) * math.sqrt(252))


def wing_skew(strikes: pd.DataFrame, spot: float, straddle: float | None,
              days: int, r_pct: float) -> float | None:
    """IV of the put one straddle below spot minus the call one straddle above.

    One straddle is ~0.8 sigma, close to the conventional 25-delta wings, but
    scaled to each underlying's own priced move. IV is backed out of the
    quote here (not NSE's column) so it is computed exactly as it is for the
    history, which only has prices."""
    if not straddle:
        return None
    T, r = year_frac(days), r_pct / 100
    ivs = {}
    for side, call, level, keep in (("pe", False, spot - straddle, lambda k: k < spot),
                                    ("ce", True, spot + straddle, lambda k: k > spot)):
        c = strikes[strikes["strike"].map(keep)]
        if c.empty:
            return None
        row = c.iloc[int(np.argmin(np.abs(c["strike"].to_numpy(float) - level)))]
        px = _mid(row, side)
        iv = float(implied_vol(px, spot, row["strike"], T, r, call))
        if np.isnan(iv):
            return None
        ivs[side] = 100 * iv
    return ivs["pe"] - ivs["ce"]


def vol_context(strikes, spot, straddle, days, s, iv_history=None, closes=None,
                iv_next=None, results=None) -> VolContext:
    iv = straddle_iv(spot, straddle, days)
    pct, n = iv_percentile(iv, iv_history)
    rv = realized_vol(closes)
    return VolContext(
        iv=iv, iv_pct=pct, iv_hist_n=n, rv20=rv,
        iv_rv=iv / rv if iv and rv else None,
        skew=wing_skew(strikes, spot, straddle, days, s.get("risk_free_pct", 6.5)),
        term=(iv_next - iv) if iv is not None and iv_next is not None else None,
        results=list(results or []),
    )


def conditional_note(v: "FnoView", cond: dict | None) -> str | None:
    """When a stock sits in one of the two conditions that survived the
    validation's within-date and time-split checks -- results before expiry,
    or IV percentile below 20 -- the hit rate for that condition. Quoted only
    when it differs from overall by 3 points or more."""
    if not cond or v.kind != "equity" or v.range_from != "straddle" or not v.vol:
        return None
    base = cond.get("overall")
    notes = []
    if v.vol.results:
        r = (cond.get("results_in_cycle") or {}).get("true")
        if r and base is not None and abs(r["pct"] - base) >= 3:
            notes.append(f"with results inside the cycle it held {r['pct']:.0f}% "
                         f"(n={r['n']:,})")
    low = cond.get("low_iv_pct")
    if low and v.vol.iv_pct is not None and v.vol.iv_pct < low["below"] \
            and base is not None and abs(low["pct"] - base) >= 3:
        notes.append(f"with IV percentile below {low['below']} (options cheap for this "
                     f"stock) it held {low['pct']:.0f}% (n={low['n']:,})")
    if not notes:
        return None
    return (f"Since 2024, stocks' range held {base:.0f}% overall; " + "; ".join(notes) + ".")


# ------------------------------------------------------------ option check

@dataclass
class OptionCheck:
    """The facts about one contract a buyer would weigh. Not a recommendation:
    the validation found nothing here that predicts direction."""
    symbol: str
    strike: float
    side: str                     # "CE" | "PE"
    lots: int
    lot: int | None
    bid: float | None
    ask: float | None
    mid: float | None
    ltp: float | None
    spread_pct: float | None      # (ask - bid) / mid
    oi: float
    volume: float
    iv: float | None              # NSE's IV for this strike, %
    breakeven: float | None
    breakeven_pct: float | None
    breakeven_moves: float | None
    p_profit_hist: float | None   # beyond breakeven at expiry, historical
    p_profit_model: float | None
    p_itm_hist: float | None      # beyond the strike at expiry
    p_itm_model: float | None
    p_touch_be_intraday: float | None   # traded through breakeven before expiry
    delta: float | None
    theta_day: float | None       # premium per unit, per day
    vega: float | None            # premium per unit, per vol point
    payoff: list = field(default_factory=list)   # (label, level, P&L per lot)
    notes: list = field(default_factory=list)

    @property
    def units(self) -> int:
        return (self.lot or 1) * self.lots

    @property
    def cost(self) -> float | None:
        return self.mid * self.units if self.mid is not None else None


def _beyond(spot, straddle, level, side, kind, curve):
    """P(expiry close on the profitable side of `level`) for a call (above)
    or put (below), from the reach curves -- historical and model."""
    want_up = side == "CE"
    if level == spot:
        return 0.5, 0.5, None
    x = reach(spot, straddle, level, kind, curve)
    if x is None:
        return None, None, None
    same = (x.side == "up") == want_up
    if same:
        return x.hist_expiry, x.model_expiry, x.hist_touch_intraday
    # The level is on the other side of spot: profitable unless price moves
    # past it the wrong way.
    flip = lambda p: None if p is None else 1 - p
    return flip(x.hist_expiry), flip(x.model_expiry), None


def option_check(v: FnoView, strikes: pd.DataFrame, strike: float, side: str,
                 lots: int = 1, curve: dict | None = None,
                 r_pct: float = 6.5) -> OptionCheck | None:
    side = side.upper()
    s = side.lower()
    rows = strikes[strikes["strike"] == strike]
    if rows.empty:
        return None
    row = rows.iloc[0]
    bid, ask, ltp = (row.get(f"{s}_bid"), row.get(f"{s}_ask"), row.get(f"{s}_ltp"))
    bid = None if pd.isna(bid) else float(bid)
    ask = None if pd.isna(ask) else float(ask)
    ltp = None if pd.isna(ltp) else float(ltp)
    mid = _mid(row, s)
    mid = None if math.isnan(mid) else mid
    spread = (100 * (ask - bid) / mid) if bid and ask and mid else None

    g = add_greeks(rows, v.spot, v.days, r_pct).iloc[0]
    be = None
    if mid is not None:
        be = strike + mid if side == "CE" else strike - mid
    ph = pm = ih = im = tb = None
    if be is not None and v.straddle:
        ph, pm, tb = _beyond(v.spot, v.straddle, be, side, v.kind, curve)
        ih, im, _ = _beyond(v.spot, v.straddle, strike, side, v.kind, curve)

    oc = OptionCheck(
        symbol=v.symbol, strike=float(strike), side=side, lots=lots, lot=v.lot,
        bid=bid, ask=ask, mid=mid, ltp=ltp, spread_pct=spread,
        oi=float(row.get(f"{s}_oi", 0) or 0), volume=float(row.get(f"{s}_vol", 0) or 0),
        iv=None if pd.isna(row.get(f"{s}_iv")) else float(row[f"{s}_iv"]),
        breakeven=be, breakeven_pct=None if be is None else 100 * (be / v.spot - 1),
        breakeven_moves=None if be is None or not v.straddle else abs(be - v.spot) / v.straddle,
        p_profit_hist=ph, p_profit_model=pm, p_itm_hist=ih, p_itm_model=im,
        p_touch_be_intraday=tb,
        delta=None if pd.isna(g[f"{s}_delta"]) else float(g[f"{s}_delta"]),
        theta_day=None if pd.isna(g[f"{s}_theta"]) else float(g[f"{s}_theta"]),
        vega=None if pd.isna(g[f"{s}_vega"]) else float(g[f"{s}_vega"]),
    )

    # Payoff at expiry, per lot, at the levels the view already shows.
    if mid is not None:
        levels = [("range low", v.range_low), ("put wall", v.support.strike if v.support else None),
                  ("spot", v.spot), ("call wall", v.resistance.strike if v.resistance else None),
                  ("range high", v.range_high), ("breakeven", be)]
        unit = v.lot or 1
        for label, lvl in levels:
            if lvl is None:
                continue
            intrinsic = max(lvl - strike, 0) if side == "CE" else max(strike - lvl, 0)
            oc.payoff.append((label, float(lvl), (intrinsic - mid) * unit))
        oc.payoff.sort(key=lambda t: t[1])

    n = oc.notes
    n.append("Direction: nothing in this data predicted whether the price rose or fell "
             "by expiry (docs/f_o/validation.md). The odds below are for a move of the "
             "needed size, up or down alike.")
    if mid is None:
        n.append("No live quote or trade for this strike -- no price to check.")
    if spread is not None and spread > 5:
        n.append(f"Wide spread: {spread:.0f}% of the premium. Buying at the ask and "
                 "selling at the bid gives that up straight away.")
    if oc.oi < 100 or oc.volume < 50:
        n.append("Thin: little open interest or volume at this strike today.")
    if v.vol and v.vol.results:
        n.append("Results before expiry: IV is usually bid up into results and falls "
                 "after, which lowers premiums even if the price doesn't move.")
    if v.vol and v.vol.iv_pct is not None and v.vol.iv_pct < 20 and v.kind == "equity":
        n.append("Options are cheap for this stock against its own past year (IV "
                 "percentile below 20). For stocks like that, moves exceeded the priced "
                 "move more often than usual (range held 56% vs 61%).")
    if v.sessions <= 2:
        n.append("Near expiry: time decay is fastest now, and the premium is mostly "
                 "a bet on the next few sessions.")
    return oc
