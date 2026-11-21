"""Portfolio risk and the Core–Satellite plan from your book (the I/O around
strategy/portfolio_risk.py and strategy/optimiser.py).

Reads the overview (holdings priced, buckets, caps), the scorecards and
valuations (gates and flags), research.db (volume) and signals.db
(split-adjusted daily prices for history). Writes nothing: the plan is a
simulation you read.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import yaml

from book import book_view as bvw
from book import portfolio as pf
from book import research_db as rdb
from book import scorecards as bsc
from book import securities as sec
from book import valuations as bval
from strategy import optimiser as opt
from strategy import overview as ov
from strategy import portfolio_risk as pr
from strategy import scorecard as sc
from strategy import tax as tx

ROOT = Path(__file__).resolve().parents[1]


def _adv(rows) -> float | None:
    vol, px = sc.latest(rows, "avg_volume_20d"), sc.latest(rows, "close_adjusted")
    return vol[0] * px[0] if vol and px else None


def gather() -> dict:
    """Everything the page needs, assembled once: overview, cards, valuations,
    per-holding risk inputs."""
    o, why = bvw.load(refresh=False)
    if o is None:
        return {"error": "; ".join(why)}
    cards, cnotes = bsc.build(save=False)
    vals, _ = bval.build()
    cards = {c.symbol: c for c in cards}
    vals = {v.symbol: v for v in vals}
    rcon = rdb.connect()
    try:
        hs = []
        for p in o.positions:
            if p.sleeve != sec.OPERATING or p.market_value is None:
                continue
            base = sec.split_symbol(p.symbol)[0] if hasattr(sec, "split_symbol") else p.symbol
            c, v = cards.get(base), vals.get(base)
            rows = rdb.metrics(rcon, base)
            pat = sc.latest(rows, "pat", "ttm")
            hs.append({
                "position": p, "base": base, "card": c, "valuation": v,
                "holding": pr.Holding(
                    symbol=base, value=float(p.market_value), role=p.role,
                    group=c.group if c else "other", sector=p.sector, cap_band=p.cap_band,
                    adv_value=_adv(rows), tier=c.tier if c else None,
                    flags=[f.text for f in c.flags] if c else [],
                    own_band_pct=v.band.get("percentile") if v else None,
                    peer_relative=v.peers.get("relative") if v else None,
                    pat_ttm_positive=None if not pat else pat[0] > 0,
                    satellite_category=c.satellite_category if c else "")})
    finally:
        rcon.close()
    return {"overview": o, "items": hs, "notes": cnotes}


def prices(symbols: list[str], years: int) -> tuple[dict[str, pd.Series], pd.Series | None]:
    from data import store
    con = store.connect()
    try:
        out = {}
        for s in symbols:
            df = store.load_prices(con, s)
            if df is not None and len(df):
                out[s] = df.assign(date=pd.to_datetime(df["date"])).set_index("date")["close"]
        idx = store.load_index(con, pr.load_config()["benchmark"])
        bench = (idx.assign(date=pd.to_datetime(idx["date"])).set_index("date")["close"]
                 if idx is not None and len(idx) else None)
    finally:
        con.close()
    return out, bench


def risk(g: dict, cfg: dict | None = None) -> pr.RiskReport:
    cfg = cfg or pr.load_config()
    hs = [i["holding"] for i in g["items"]]
    px, bench = prices([h.symbol for h in hs], cfg["history_years"])
    cs = ov.load_config()
    return pr.report(hs, px, bench, cfg, cs.get("satellite_mix_pct", {}),
                     g["overview"].price_date or "")


def _tax_cfg() -> dict:
    return yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text())


def plan_inputs(g: dict, use_suggested: bool, cfg: dict | None = None) -> list[opt.PlanHolding]:
    cfg = cfg or pr.load_config()
    cs = ov.load_config()
    caps = cs["position_max_pct"]
    gate = cfg["plan"]["no_new_money"]
    lc = cfg["liquidity"]
    taxcfg = _tax_cfg()
    holdings = {h["tradingsymbol"]: h for h in pf.read_csv_holdings()}
    out = []
    for i in g["items"]:
        p, c, h = i["position"], i["card"], i["holding"]
        role, src = p.role, "bucket" if p.bucket else "default"
        if use_suggested and not p.bucket and c and c.role in ("core", "satellite"):
            role, src = c.role, "suggested"
        cap = caps[role]
        if p.cap_band == "small":
            cap = min(cap, caps["small_cap"])
        why = []
        if c and c.tier in gate["tiers"]:
            why.append(f"quality tier {c.tier.replace('_', ' ')}")
        if gate["governance_flags"] and h.flags:
            why.append("governance flag")
        if gate["valuation_rich"] and pr.rich(h, cfg):
            why.append("rich on own band and peers")
        liq_max = (h.adv_value * lc["participation_pct"] / 100 * lc["max_exit_days"]
                   if h.adv_value else None)
        if gate["illiquid"] and liq_max is not None and h.value >= liq_max:
            why.append("at its liquidity limit")
        raw = holdings.get(p.symbol) or holdings.get(i["base"]) or {}
        tpr = 0.0
        if p.avg_cost and p.price and float(p.price) > 0:
            t = tx.assess(float(p.price), float(p.avg_cost), 1.0, taxcfg,
                          purchase_date=raw.get("purchase_date"),
                          lt_qty=_f(raw.get("lt_quantity")), st_qty=_f(raw.get("st_quantity")))
            tpr = max(t.estimated_tax, 0.0) / float(p.price)
        out.append(opt.PlanHolding(i["base"], h.value, role, float(cap), p.sector, p.cap_band,
                                   why, liq_max if gate["illiquid"] else None, tpr, src))
    return out


def _f(x):
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def make_plan(g: dict, monthly: float, months: int, use_suggested: bool,
              cfg: dict | None = None) -> opt.Plan:
    cfg = cfg or pr.load_config()
    return opt.plan(plan_inputs(g, use_suggested, cfg), float(g["overview"].total_value),
                    monthly, months, ov.load_config(), cfg["plan"])
