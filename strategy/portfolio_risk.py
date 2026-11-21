"""Portfolio risk: historical behaviour of today's mix, and what it is exposed to.

  historical   volatility, beta, maximum drawdown, worst month, 1-day 95%
               loss, against the benchmark -- your CURRENT weights held
               constant over the past window. What this mix would have done,
               not a forecast
  correlation  the largest holdings' daily returns, pairs that move together
  liquidity    days to sell each position at a modest share of its volume
  exposures    themes that cut across sectors (rate-sensitive, cyclical,
               commodity, export, regulated, consumer), overlapping holdings
               in one sector group, satellite categories against targets
  flags        weight in holdings with governance flags, rich valuations,
               losses, or too little liquidity; effective number of holdings

Pure: no I/O. Weights as fractions of operating equity unless stated.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "risk.yaml"
TRADING_DAYS = 252


def load_config(path: Path | str = CONFIG) -> dict:
    import yaml
    return yaml.safe_load(Path(path).read_text())


@dataclass
class Holding:
    symbol: str
    value: float                 # ₹
    role: str = "satellite"      # core | satellite
    group: str = "other"         # sector group
    sector: str = ""             # NSE industry
    cap_band: str = ""
    adv_value: float | None = None        # average daily traded value, ₹
    tier: str | None = None
    flags: list[str] = field(default_factory=list)
    own_band_pct: float | None = None     # 0..1
    peer_relative: float | None = None
    pat_ttm_positive: bool | None = None
    satellite_category: str = ""


@dataclass
class RiskReport:
    as_of: str
    history: dict
    benchmark: dict
    correlation: dict
    liquidity: list[dict]
    themes: list[dict]
    overlaps: list[dict]
    satellite_mix: list[dict]
    flags: list[dict]
    effective_names: float | None
    notes: list[str] = field(default_factory=list)
    curve: list[dict] = field(default_factory=list)   # weekly growth of ₹100


# ------------------------------------------------------------------ history

def portfolio_returns(prices: dict[str, pd.Series], weights: dict[str, float]) -> pd.Series:
    """Daily returns of the current weights held constant. On a day a holding
    has no price (not yet listed), the others are re-weighted to sum to 1."""
    rets = pd.DataFrame({s: p.sort_index().pct_change() for s, p in prices.items()
                         if s in weights and len(p) > 1})
    if rets.empty:
        return pd.Series(dtype=float)
    rets = rets.iloc[1:]
    w = pd.Series({s: weights[s] for s in rets.columns})
    have = rets.notna()
    wt = have.mul(w, axis=1)
    norm = wt.sum(axis=1)
    out = (rets.fillna(0) * wt).sum(axis=1) / norm.replace(0, np.nan)
    return out.dropna()


def growth_curve(r: pd.Series, bench: pd.Series) -> list[dict]:
    """Weekly growth of ₹100 for the portfolio and the benchmark, common dates."""
    both = pd.concat([r.rename("portfolio"), bench.rename("benchmark")], axis=1,
                     join="inner").dropna()
    if both.empty:
        return []
    w = (1 + both).cumprod() * 100
    w = w.resample("W-FRI").last().dropna()
    return [{"date": d.date().isoformat(), "portfolio": float(a), "benchmark": float(b)}
            for d, a, b in w.itertuples()]


def stats(r: pd.Series) -> dict:
    """Volatility, drawdown, worst month and 1-day 95% loss of a return series."""
    if r is None or len(r) < 60:
        return {"days": 0 if r is None else len(r)}
    wealth = (1 + r).cumprod()
    dd = wealth / wealth.cummax() - 1
    trough = dd.idxmin()
    peak = wealth.loc[:trough].idxmax()
    monthly = (1 + r).groupby([r.index.year, r.index.month]).prod() - 1
    years = len(r) / TRADING_DAYS
    return {"days": len(r), "from": r.index[0].date().isoformat(),
            "to": r.index[-1].date().isoformat(),
            "return_ann_pct": (wealth.iloc[-1] ** (1 / years) - 1) * 100 if years > 0 else None,
            "volatility_pct": r.std() * math.sqrt(TRADING_DAYS) * 100,
            "max_drawdown_pct": dd.min() * 100,
            "drawdown_peak": peak.date().isoformat(), "drawdown_trough": trough.date().isoformat(),
            "worst_month_pct": monthly.min() * 100,
            "var95_1d_pct": float(np.percentile(r, 5)) * 100}


def beta(r: pd.Series, bench: pd.Series) -> float | None:
    both = pd.concat([r, bench], axis=1, join="inner").dropna()
    if len(both) < 60 or both.iloc[:, 1].var() == 0:
        return None
    return float(both.iloc[:, 0].cov(both.iloc[:, 1]) / both.iloc[:, 1].var())


def correlation(prices: dict[str, pd.Series], symbols: list[str], flag: float,
                min_days: int = 120) -> dict:
    rets = pd.DataFrame({s: prices[s].sort_index().pct_change() for s in symbols
                         if s in prices})
    rets = rets.dropna(axis=1, thresh=min_days)
    if rets.shape[1] < 2:
        return {"matrix": {}, "pairs": [], "symbols": list(rets.columns)}
    c = rets.corr(min_periods=min_days)
    pairs = []
    cols = list(c.columns)
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            x = c.loc[a, b]
            if pd.notna(x) and x >= flag:
                pairs.append({"a": a, "b": b, "corr": float(x)})
    return {"matrix": c.round(2).to_dict(), "pairs": sorted(pairs, key=lambda p: -p["corr"]),
            "symbols": cols}


# ------------------------------------------------------------------ exposures

def liquidity(holdings: list[Holding], cfg: dict) -> list[dict]:
    lc = cfg["liquidity"]
    out = []
    for h in holdings:
        if not h.adv_value:
            out.append({"symbol": h.symbol, "days": None, "flag": False,
                        "note": "no volume on file"})
            continue
        days = h.value / (h.adv_value * lc["participation_pct"] / 100)
        out.append({"symbol": h.symbol, "days": days, "flag": days > lc["max_exit_days"],
                    "note": f"at {lc['participation_pct']}% of ₹{h.adv_value / 1e7:,.2f} cr/day"})
    return sorted(out, key=lambda x: -(x["days"] or 0))


def themes(holdings: list[Holding], total: float, cfg: dict) -> list[dict]:
    out = []
    for key, t in cfg["themes"].items():
        members = [h for h in holdings if h.group in t["groups"]]
        pct = sum(h.value for h in members) / total * 100 if total else 0.0
        out.append({"key": key, "label": t["label"], "pct": pct, "warn_pct": t["warn_pct"],
                    "flag": pct > t["warn_pct"], "groups": t["groups"],
                    "holdings": [h.symbol for h in sorted(members, key=lambda h: -h.value)]})
    return sorted(out, key=lambda x: -x["pct"])


def overlaps(holdings: list[Holding], total: float) -> list[dict]:
    """Sector groups held through more than one company: one business model,
    several tickers."""
    by: dict[str, list[Holding]] = {}
    for h in holdings:
        if h.group and h.group != "other":
            by.setdefault(h.group, []).append(h)
    out = [{"group": g, "holdings": [h.symbol for h in sorted(hs, key=lambda h: -h.value)],
            "pct": sum(h.value for h in hs) / total * 100}
           for g, hs in by.items() if len(hs) > 1]
    return sorted(out, key=lambda x: -x["pct"])


def satellite_mix(holdings: list[Holding], total: float, targets: dict) -> list[dict]:
    """Satellite categories as % of operating equity, against the guideline."""
    sat = [h for h in holdings if h.role == "satellite"]
    by: dict[str, float] = {}
    for h in sat:
        by[h.satellite_category or "uncategorised"] = \
            by.get(h.satellite_category or "uncategorised", 0) + h.value
    keys = list(targets) + [k for k in by if k not in targets]
    return [{"category": k, "pct": by.get(k, 0) / total * 100 if total else 0.0,
             "target_pct": targets.get(k)} for k in keys]


def risk_flags(holdings: list[Holding], total: float, liq: list[dict], cfg: dict) -> list[dict]:
    vr = cfg["valuation_rich"]
    illiquid = {x["symbol"] for x in liq if x["flag"]}
    checks = [
        ("governance", "Governance flags", lambda h: bool(h.flags)),
        ("valuation", f"Rich on own band (≥{vr['own_band_pct']}th pct) and peers "
                      f"(≥{vr['peer_relative']}× median)", lambda h: rich(h, cfg)),
        ("earnings", "Trailing-twelve-month loss", lambda h: h.pat_ttm_positive is False),
        ("liquidity", "Slow to sell (liquidity)", lambda h: h.symbol in illiquid),
        ("quality", "Exit-candidate quality tier", lambda h: h.tier == "exit_candidate"),
    ]
    out = []
    for key, label, test in checks:
        hs = [h for h in holdings if test(h)]
        out.append({"key": key, "label": label,
                    "pct": sum(h.value for h in hs) / total * 100 if total else 0.0,
                    "holdings": [h.symbol for h in sorted(hs, key=lambda h: -h.value)]})
    return out


def rich(h: Holding, cfg: dict) -> bool:
    vr = cfg["valuation_rich"]
    return (h.own_band_pct is not None and h.own_band_pct * 100 >= vr["own_band_pct"]
            and h.peer_relative is not None and h.peer_relative >= vr["peer_relative"])


def effective_names(holdings: list[Holding]) -> float | None:
    tot = sum(h.value for h in holdings)
    if not tot:
        return None
    return 1 / sum((h.value / tot) ** 2 for h in holdings)


# ------------------------------------------------------------------ report

def report(holdings: list[Holding], prices: dict[str, pd.Series],
           bench: pd.Series | None, cfg: dict, satellite_targets: dict,
           as_of: str = "") -> RiskReport:
    total = sum(h.value for h in holdings)
    w = {h.symbol: h.value / total for h in holdings} if total else {}
    notes = []
    end = max((p.index.max() for p in prices.values() if len(p)), default=None)
    hist, bstats, corr, curve = {}, {}, {}, []
    if end is not None:
        start = end - pd.DateOffset(years=cfg["history_years"])
        win = {s: p[p.index >= start] for s, p in prices.items()}
        r = portfolio_returns(win, w)
        hist = stats(r)
        missing = [h.symbol for h in holdings if h.symbol not in prices or prices[h.symbol].empty]
        if missing:
            share = sum(w[s] for s in missing) * 100
            notes.append(f"No price history for {', '.join(missing)} ({share:.1f}% of "
                         "operating equity): left out of the historical figures")
        if bench is not None and len(bench):
            br = bench[bench.index >= start].sort_index().pct_change().dropna()
            bstats = stats(br)
            hist["beta"] = beta(r, br)
            curve = growth_curve(r, br)
        cstart = end - pd.DateOffset(years=cfg["correlation_years"])
        top = [h.symbol for h in sorted(holdings, key=lambda h: -h.value)][:cfg["correlation_top_n"]]
        corr = correlation({s: p[p.index >= cstart] for s, p in prices.items()}, top,
                           cfg["correlation_flag"])
    liq = liquidity(holdings, cfg)
    return RiskReport(as_of, hist, bstats, corr, liq, themes(holdings, total, cfg),
                      overlaps(holdings, total), satellite_mix(holdings, total, satellite_targets),
                      risk_flags(holdings, total, liq, cfg), effective_names(holdings), notes,
                      curve)
