"""The research pack: everything the assistant may cite, in one JSON file.

The research assistant (Phase 7) is Claude Code reading this file -- no API
key, nothing sent anywhere by the app. Every number in it comes from the
deterministic engines (scorecard, valuation, risk, plan, tax); the assistant
interprets, explains and synthesises, and must cite a path in this file for
every figure it uses (strategy/answer_check.py verifies that).

Each figure carries what the spec asks for (§14.1): its source, its period,
whether it is reported, calculated or estimated, and whether it is fresh.

Written to data/research/pack.json (never committed: it holds your positions).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

from book import marks as mk
from book import research_db as rdb
from book import risk_view as rv
from book import securities as sec
from book import tax_view as tv
from data import financials as fin
from strategy import capgains as cg

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "data" / "research"
PACK = DIR / "pack.json"
ANSWERS = DIR / "answers"
SCHEMA_VERSION = 1

# The figures a research question usually needs, per holding (metric, period).
KEY_METRICS = [
    ("revenue", "ttm"), ("pat", "ttm"), ("eps", "ttm"),
    ("revenue", "fy"), ("pat", "fy"), ("ebitda", "fy"), ("eps", "fy"),
    ("revenue_growth_pct", "fy"), ("pat_growth_pct", "fy"),
    ("revenue_cagr_3y_pct", "fy"), ("pat_cagr_3y_pct", "fy"), ("eps_cagr_3y_pct", "fy"),
    ("cfo", "fy"), ("fcf", "fy"), ("capex", "fy"), ("cash_conversion", "fy"),
    ("roe_pct", "fy"), ("roce_pct", "fy"), ("ebitda_margin_pct", "fy"),
    ("net_margin_pct", "fy"), ("interest_cover", "fy"),
    ("debt_equity", "instant"), ("net_debt", "instant"),
    ("gnpa_pct", "quarter"), ("nnpa_pct", "quarter"),
    ("promoter_pct", "instant"), ("promoter_change_pct", "instant"), ("pledge_pct", "instant"),
    ("market_cap", "point"), ("dividend_yield_pct", "point"), ("dps_ttm", "ttm"),
    ("close_adjusted", "point"), ("high_52w", "point"), ("low_52w", "point"),
    ("beta_1y", "point"), ("volatility_1y_pct", "point"), ("avg_volume_20d", "point"),
    ("return_1y_pct", "point"), ("return_3y_cagr_pct", "point"),
]
# Quarterly series for "what changed in the latest results": last 5 quarters,
# so the latest can be read against the one before and a year before.
QUARTERLY = ["revenue", "pat", "eps", "ebitda_margin_pct", "net_margin_pct", "nii", "ppop",
             "provisions", "gnpa_pct", "nnpa_pct", "exceptional"]
SOURCE_RANK = {"NSE XBRL filing": 0, "calculated": 1, "NSE shareholding pattern": 1}

RULES = [
    "Every figure in an answer must cite a path in this pack; never compute a score, "
    "fair value or tax figure the pack doesn't contain.",
    "If the pack doesn't hold what the question needs, answer INSUFFICIENT DATA and say "
    "what is missing.",
    "Say a figure's period and freshness; a 'stale' figure must be named as stale.",
    "Say whether a figure is reported, calculated or estimated (the 'kind' field).",
    "Governance flags on a holding are always mentioned when that holding is discussed.",
    "The scorecard is descriptive and has not been backtested: it may describe, not rank "
    "or predict.",
    "Valuation scenarios are scenarios, not price targets; historical risk is "
    "backward-looking.",
    "No buy or sell instruction: describe what the rules and plan say, and leave the "
    "decision to the user.",
]

QUESTIONS = {
    "Why is this stock classified as Core?":
        ["/holdings/{S}/position/role", "/holdings/{S}/scorecard/role_suggested",
         "/holdings/{S}/scorecard/role_reasons", "/holdings/{S}/scorecard/tier"],
    "Which holdings have deteriorating fundamentals?":
        ["/holdings/*/changes", "/holdings/*/scorecard/components/*/criteria/*"],
    "Which companies have the strongest cash-flow profiles?":
        ["/holdings/*/scorecard/components/cash_flow"],
    "Which companies are expensive relative to their growth?":
        ["/holdings/*/valuation/reverse", "/holdings/*/valuation/band"],
    "Which stocks overlap significantly in sector exposure?":
        ["/risk/overlaps", "/risk/themes", "/risk/correlation_pairs"],
    "How can I move towards 70:30 without selling my best holdings?":
        ["/plan/suggested_roles", "/plan/your_buckets", "/tax/this_year"],
    "Which stocks should receive the next monthly investment?":
        ["/plan/*/rows/*/buy", "/holdings/*/plan/no_new_money"],
    "What are the largest risks in the portfolio?":
        ["/risk/flags", "/risk/history", "/risk/themes", "/portfolio/concentration"],
    "What has changed in the latest quarterly results?":
        ["/holdings/{S}/quarters", "/holdings/{S}/metrics"],
    "Which recommendations have changed since the previous review?":
        ["/holdings/*/changes", "/holdings/*/history"],
}


def _num(v):
    if isinstance(v, Decimal):
        return float(v)
    return v


def _clean(o):
    """JSON-safe: Decimals to float, dates to ISO, dataclasses to dicts."""
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        o = dataclasses.asdict(o)
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    if isinstance(o, float) and (o != o or o in (float("inf"), float("-inf"))):
        return None
    return o


def _metrics(rows: list[dict], today: dt.date, session: str | None) -> dict:
    latest = fin.latest_rows(rows)
    out = {}
    for metric, period in KEY_METRICS:
        cand = [r for r in latest if r["metric"] == metric and r["period"] == period
                and r["status"] == fin.OK]
        if not cand:
            continue
        r = sorted(cand, key=lambda r: (str(r["period_end"]),
                                        -SOURCE_RANK.get(r["source"], 2)))[-1]
        fr, why = fin.freshness(r, today, session)
        out[f"{metric}:{period}"] = {
            "value": _num(r["value"]), "unit": r["unit"], "period": period,
            "period_end": str(r["period_end"]), "source": r["source"],
            "kind": r["method"], "confidence": r["confidence"], "ref": r["ref"] or None,
            "freshness": fr, "freshness_why": why}
    return out


def _quarters(rows: list[dict]) -> dict:
    out = {}
    for m in QUARTERLY:
        rs = [r for r in rows if r["metric"] == m and r["period"] == "quarter"
              and r["status"] == fin.OK]
        by: dict[str, dict] = {}
        for r in rs:                       # one row per quarter, best source
            k = str(r["period_end"])
            if k not in by or SOURCE_RANK.get(r["source"], 2) < SOURCE_RANK.get(by[k]["source"], 2):
                by[k] = r
        if by:
            out[m] = {k: {"value": _num(r["value"]), "unit": r["unit"], "source": r["source"],
                          "kind": r["method"], "ref": r["ref"] or None}
                      for k, r in sorted(by.items())[-5:]}
    return out


def _card(c) -> dict | None:
    if c is None:
        return None
    return {
        "kind": "calculated", "note": "descriptive scorecard, not backtested",
        "as_of": c.as_of, "model": c.model, "score": c.score, "earned": c.earned,
        "available": c.available, "coverage_pct": c.coverage_pct, "tier": c.tier,
        "tier_label": c.tier_label, "raw_tier": c.raw_tier, "role_suggested": c.role,
        "role_reasons": c.role_reasons, "satellite_category": c.satellite_category,
        "flags": [_clean(f) for f in c.flags], "manual_todo": c.manual_todo,
        "components": {comp.key: {
            "label": comp.label, "weight": comp.weight, "earned": comp.earned,
            "available": comp.available,
            "criteria": {cr.key: {"label": cr.label, "value": cr.value, "points": cr.points,
                                  "max_points": cr.max_points, "status": cr.status,
                                  "source": cr.source, "note": cr.note, "rule": cr.rule}
                         for cr in comp.criteria}} for comp in c.components}}


def _valuation(v) -> dict | None:
    if v is None:
        return None
    d = _clean(v)
    d.pop("history", None)
    d["kind"] = "estimated"
    d["note"] = "scenarios from config/valuation.yaml assumptions, not price targets"
    return d


def _history(rcon, symbol: str) -> tuple[list[dict], dict | None]:
    hist = [{"as_of": h["as_of"], "score": h["score"], "tier": h["tier"], "role": h["role"],
             "coverage_pct": h["coverage_pct"]}
            for h in sorted(rdb.scorecard_history(rcon, symbol), key=lambda h: h["as_of"])]
    if len(hist) < 2:
        return hist[-5:], None
    a, b = hist[-2], hist[-1]
    return hist[-5:], {"from": a["as_of"], "to": b["as_of"],
                       "score_change": (None if a["score"] is None or b["score"] is None
                                        else round(b["score"] - a["score"], 2)),
                       "tier_from": a["tier"], "tier_to": b["tier"],
                       "role_from": a["role"], "role_to": b["role"]}


def _plan(p) -> dict:
    return {"contribution": p.contribution, "months": p.months,
            "buckets": p.buckets, "months_to_70_core": p.months_to_target,
            "unallocated": p.unallocated, "warnings": p.warnings,
            "constraints": p.constraints, "sectors": p.sectors,
            "rows": {r.symbol: {"role": r.role, "current": r.current, "target": r.target,
                                "buy": r.buy, "trim": r.trim, "trim_tax_estimate": r.trim_tax,
                                "after": r.after, "cap_pct": r.cap_pct, "notes": r.notes}
                     for r in p.rows}}


def build(monthly: float = 0.0, months: int = 12, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    rg = rv.gather()
    if "error" in rg:
        raise RuntimeError(rg["error"])
    o = rg["overview"]
    risk = rv.risk(rg)
    plans = {"your_buckets": rv.make_plan(rg, monthly, months, False),
             "suggested_roles": rv.make_plan(rg, monthly, months, True)}
    pin = {h.symbol: h for h in rv.plan_inputs(rg, True)}
    tg = tv.gather(today)
    rcon = rdb.connect()
    mcon = sqlite3.connect(ROOT / "data" / "market.db")
    try:
        session = (mk.sessions(mcon, 1) or [None])[0]
        total = float(o.total_value)
        holdings = {}
        for i in rg["items"]:
            p, c, v, h = i["position"], i["card"], i["valuation"], i["holding"]
            s = i["base"]
            hist, changes = _history(rcon, s)
            book = tg["books"].get(p.symbol) or tg["books"].get(s)
            px = float(p.price) if p.price else None
            unreal = None
            if book and px:
                days = tg["cfg"]["long_term_after_days"]
                unreal = {"short_term": sum((px - l.cost) * l.qty for l in book.lots
                                            if not l.is_long(today, days)),
                          "long_term": sum((px - l.cost) * l.qty for l in book.lots
                                           if l.is_long(today, days)),
                          "kind": "calculated", "lots_source": book.status,
                          "notes": book.notes}
            holdings[s] = {
                "symbol": s, "name": p.name, "sector": p.sector, "group": h.group,
                "cap_band": p.cap_band, "data_freshness": p.data_state,
                "position": {"quantity": _num(p.quantity), "avg_cost": _num(p.avg_cost),
                             "price": px, "price_date": p.price_date,
                             "market_value": _num(p.market_value),
                             "weight_pct": _num(p.market_value) / total * 100 if total else None,
                             "pnl_pct": _num(p.pnl_pct), "bucket": p.bucket or None,
                             "role": p.role, "role_source": "bucket" if p.bucket else
                             "default (bucket blank: satellite)", "kind": "reported",
                             "source": p.price_source or "holdings file + market data"},
                "scorecard": _card(c),
                "valuation": _valuation(v),
                "metrics": _metrics(rows := rdb.metrics(rcon, s), today, session),
                "quarters": _quarters(rows),
                "risk": {"liquidity_days": next((x["days"] for x in risk.liquidity
                                                 if x["symbol"] == s), None),
                         "themes": [t["key"] for t in risk.themes if s in t["holdings"]],
                         "flags": [f["key"] for f in risk.flags if s in f["holdings"]]},
                "plan": {"no_new_money": pin[s].no_buy if s in pin else [],
                         "cap_pct": pin[s].cap_pct if s in pin else None},
                "tax": unreal,
                "history": hist, "changes": changes}
    finally:
        rcon.close()
        mcon.close()
    fy = tv.current_fy(tg)
    pack = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "as_of": {"prices": o.price_date, "latest_market_session": session,
                  "today": today.isoformat()},
        "rules": RULES,
        "questions": QUESTIONS,
        "portfolio": {
            "total_value": total, "operating_equity": sum(_num(p.market_value) or 0
                                                          for p in o.positions
                                                          if p.sleeve == sec.OPERATING),
            "holdings_count": len(holdings),
            "buckets_blank": sum(1 for x in holdings.values() if not x["position"]["bucket"]),
            "concentration": {"effective_names": risk.effective_names,
                              "kind": "calculated", "note": "1 / sum of squared weights"},
            "notes": rg["notes"]},
        "holdings": holdings,
        "risk": {"kind": "calculated", "note": "historical: current weights held constant; "
                 "price returns", "history": risk.history, "benchmark": risk.benchmark,
                 "correlation_pairs": risk.correlation.get("pairs", []),
                 "themes": risk.themes, "overlaps": risk.overlaps, "flags": risk.flags,
                 "satellite_mix": risk.satellite_mix, "notes": risk.notes},
        "plan": {"kind": "calculated", "note": "simulation from rules; nothing ordered; "
                 "scorecard used only as a no-new-money gate",
                 "monthly": monthly, "monthly_note": "none assumed" if not monthly else
                 "entered by you", **{k: _plan(p) for k, p in plans.items()}},
        "tax": {"kind": "estimated", "note": "estimates from config/tax.yaml; not tax advice",
                "this_year": {k: fy[k] for k in ("fy", "st", "lt", "tax", "exemption_used",
                                                  "exemption_left", "overridden")},
                "incomplete_sales": fy["unknown"],
                "harvest": tv.harvest(tg)},
    }
    return _clean(pack)


def write(pack: dict, path: Path = PACK) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pack, indent=1, ensure_ascii=False))
    return path


def load(path: Path = PACK) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def answers(d: Path = ANSWERS) -> list[tuple[Path, dict]]:
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("*.json"), reverse=True):
        try:
            out.append((f, json.loads(f.read_text())))
        except json.JSONDecodeError:
            out.append((f, {"_error": "not valid JSON"}))
    return out


def fy_label(today: dt.date | None = None) -> str:
    return cg.fy_of(today or dt.date.today())
