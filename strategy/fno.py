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


def sessions_to(today: dt.date, expiry: dt.date) -> int:
    """Weekdays after today up to and including expiry. Exchange holidays are
    not subtracted, so this can overstate by one around a holiday."""
    return int(np.busday_count(today + dt.timedelta(days=1), expiry + dt.timedelta(days=1)))


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
            kind: str | None = None) -> FnoView:
    s = settings(cfg)
    kind = kind or strikes.attrs.get("kind") or "index"
    today = today or timestamp.date()
    days = (expiry - today).days
    sess = sessions_to(today, expiry)

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
        range_hit_pct=range_hit_pct(kind, sess, s) if move_from == "straddle" else None,
        futures=fv, lot=lot, kind=kind,
    )
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
                line += (f" Since Jan 2024 the expiry close landed inside it about "
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
    cols = ["ts", "spot", "pcr_oi", "pcr_chg_oi", "support", "resistance",
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
                    "support": sup[0].strike if sup else None,
                    "resistance": res[0].strike if res else None,
                    "max_pain": max_pain(g), "straddle": strad,
                    "range_low": lo, "range_high": hi})
    return pd.DataFrame(out, columns=cols)
