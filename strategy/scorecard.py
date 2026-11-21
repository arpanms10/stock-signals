"""The 100-point fundamental scorecard (spec §5–7), with every point explained.

For one operating company, from the provenance rows in research.db
(data/financials.py) and your research inputs file:

  components   nine, weighted as config/scorecard.yaml says (revenue growth,
               earnings growth, returns, balance sheet, cash flow, competitive
               advantage, governance, valuation, industry outlook)
  criteria     each scores linearly between a `zero` and a `full` threshold,
               shows the value, the rule and where the figure came from
  sector model lenders swap industrial leverage and cash flow for asset
               quality and ROA; other sectors move the thresholds
  coverage     a criterion with no data is "not assessed": it leaves the points
               available instead of scoring zero. The score is earned /
               available x 100, shown only above the coverage floor
  override     governance red flags cap the quality tier whatever the score
  two axes     fundamental quality tier (the company) and suggested portfolio
               role (core / satellite, where it fits your book)

DESCRIPTIVE: summarises the filed record against conventions. Not
backtested; it ranks nothing and predicts nothing. Pure: no I/O.
"""
from __future__ import annotations

import copy
import datetime as dt
import statistics
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "scorecard.yaml"

NSE, CALC, YAHOO = "NSE XBRL filing", "calculated", "Yahoo Finance"
RANK = {NSE: 0, CALC: 1, YAHOO: 2}

SCORED, NOT_ASSESSED, NA, PENDING = "scored", "not assessed", "n/a", "pending"
LENDERS = {"bank", "nbfc"}
CYCLICAL = {"cement", "metals", "chemicals", "energy"}
COMPONENT_LABEL = {
    "revenue_growth": "Revenue growth", "earnings_growth": "PAT / EPS growth",
    "returns": "Return on capital", "balance_sheet": "Balance sheet",
    "cash_flow": "Cash-flow quality", "moat": "Competitive advantage",
    "governance": "Management and governance", "valuation": "Valuation",
    "industry": "Industry outlook"}
SEVERE, MODERATE = "severe", "moderate"


# ------------------------------------------------------------------ results

@dataclass
class Criterion:
    key: str
    label: str
    max_points: float
    points: float | None = None        # None unless scored
    value: float | None = None
    status: str = NOT_ASSESSED
    rule: str = ""
    source: str = ""
    note: str = ""


@dataclass
class Component:
    key: str
    label: str
    weight: float
    criteria: list[Criterion] = field(default_factory=list)
    note: str = ""                     # e.g. why the whole component is n/a

    @property
    def earned(self) -> float:
        return sum(c.points for c in self.criteria if c.status == SCORED)

    @property
    def available(self) -> float:
        return sum(c.max_points for c in self.criteria if c.status == SCORED)


@dataclass
class Flag:
    severity: str          # severe | moderate
    text: str
    source: str


@dataclass
class Scorecard:
    symbol: str
    group: str
    model: str
    components: list[Component]
    flags: list[Flag]
    score: float | None
    earned: float
    available: float
    applicable: float          # points that apply to this business (n/a removed)
    coverage_pct: float
    tier: str | None
    tier_label: str
    raw_tier: str | None       # before the governance override
    role: str                  # core | satellite | review | n/a
    role_reasons: list[str]
    satellite_category: str
    bucket: str
    bucket_note: str
    manual_todo: list[str]
    as_of: str

    def summary(self) -> dict:
        return {"symbol": self.symbol, "model": self.model, "score": self.score,
                "earned": self.earned, "available": self.available,
                "coverage_pct": self.coverage_pct, "tier": self.tier,
                "raw_tier": self.raw_tier, "role": self.role,
                "satellite_category": self.satellite_category,
                "flags": [f.text for f in self.flags], "as_of": self.as_of}


# ------------------------------------------------------------------ config

def load_config(path: Path | str = CONFIG) -> dict:
    import yaml
    cfg = yaml.safe_load(Path(path).read_text())
    validate_config(cfg)
    return cfg


def validate_config(cfg: dict) -> None:
    """Weights sum to 100 and each component's criteria to its weight, for the
    default model and every sector model that replaces a component."""
    w = cfg["weights"]
    if abs(sum(w.values()) - 100) > 1e-9:
        raise ValueError(f"scorecard weights sum to {sum(w.values())}, not 100")
    for group in [None, *cfg.get("sector_models", {})]:
        crit = criteria_for(cfg, group)
        for comp, weight in w.items():
            cs = crit.get(comp)
            if isinstance(cs, str):          # not applicable for this model
                continue
            total = sum(c["points"] for c in cs.values())
            if abs(total - weight) > 1e-9:
                raise ValueError(f"{group or 'default'} / {comp}: criteria give "
                                 f"{total} points, weight is {weight}")


def criteria_for(cfg: dict, group: str | None) -> dict:
    """{component: {key: spec} | 'not applicable: why'} for a sector group."""
    crit = copy.deepcopy(cfg["criteria"])
    model = (cfg.get("sector_models") or {}).get(group or "", {})
    for comp, repl in (model.get("replace") or {}).items():
        crit[comp] = repl["not_applicable"] if "not_applicable" in repl else repl
    for key, t in (model.get("tune") or {}).items():
        for cs in crit.values():
            if isinstance(cs, dict) and key in cs:
                cs[key].update(t)
    return crit


# ------------------------------------------------------------------ figures

def _ok(r: dict) -> bool:
    return r["status"] == "ok" and r["value"] is not None and r.get("unit") != "USD cr"


def series(rows: list[dict], metric: str, period: str) -> dict[str, tuple[float, str]]:
    """{period_end: (value, source)}, best source per period."""
    out: dict[str, tuple[float, str, int]] = {}
    for r in rows:
        if r["metric"] != metric or r["period"] != period or not _ok(r):
            continue
        rank = RANK.get(r["source"], 3)
        cur = out.get(r["period_end"])
        if cur is None or rank < cur[2]:
            out[r["period_end"]] = (float(r["value"]), r["source"], rank)
    return {k: (v, s) for k, (v, s, _) in sorted(out.items())}


def fy(rows, metric) -> dict[str, tuple[float, str]]:
    """Fiscal-year values; instants only at 31 March."""
    s = series(rows, metric, "fy")
    if not s:
        s = {k: v for k, v in series(rows, metric, "instant").items() if k[5:] == "03-31"}
    return s


def latest(rows, metric, period=None) -> tuple[float, str, str] | None:
    """(value, source, period_end) of the newest ok row."""
    cands = [r for r in rows if r["metric"] == metric and _ok(r)
             and (period is None or r["period"] == period)]
    if not cands:
        return None
    r = max(cands, key=lambda r: (r["period_end"], -RANK.get(r["source"], 3)))
    return float(r["value"]), r["source"], r["period_end"]


def _na_row(rows, metric) -> dict | None:
    cands = [r for r in rows if r["metric"] == metric and r["status"] == "not_applicable"]
    return max(cands, key=lambda r: r["period_end"]) if cands else None


def _src(*sources: str) -> str:
    seen = []
    for s in sources:
        if s and s not in seen:
            seen.append(s)
    return " + ".join(seen)


def _last(s: dict, n: int) -> list[tuple[str, float, str]]:
    return [(k, v, src) for k, (v, src) in list(s.items())[-n:]]


def compute(key: str, rows: list[dict], spec: dict, ctx: dict) -> tuple[float | None, str, str]:
    """(value, source, note) for one criterion; value None = not on file."""
    if key in ("revenue_cagr_3y", "revenue_cagr_5y", "pat_cagr_3y", "eps_cagr_3y"):
        m = {"revenue_cagr_3y": "revenue_cagr_3y_pct", "revenue_cagr_5y": "revenue_cagr_5y_pct",
             "pat_cagr_3y": "pat_cagr_3y_pct", "eps_cagr_3y": "eps_cagr_3y_pct"}[key]
        got = latest(rows, m)
        na = _na_row(rows, m)
        if na and (got is None or na["period_end"] >= got[2]):
            if "loss" in (na.get("note") or ""):
                return float("-inf"), CALC, "a loss year in the window: no growth points"
            return None, CALC, na.get("note") or ""
        return (got[0], got[1], f"FY to {got[2]}") if got else (None, "", "")

    if key == "revenue_consistency":
        s = _last(fy(rows, "revenue"), 6)
        if len(s) < 4:
            return None, "", "fewer than 4 fiscal years on file"
        ups = [b[1] > a[1] for a, b in zip(s, s[1:])]
        return sum(ups) / len(ups), _src(*(x[2] for x in s)), f"{sum(ups)} of {len(ups)} years"

    if key == "margin_trend":
        s = _last(fy(rows, "net_margin_pct"), 4)
        if len(s) < 4:
            return None, "", "needs 4 fiscal years"
        return s[-1][1] - s[0][1], CALC, f"FY{s[0][0][:4]} {s[0][1]:.1f}% → FY{s[-1][0][:4]} {s[-1][1]:.1f}%"

    if key == "earnings_quality":
        ex, pbt = series(rows, "exceptional", "quarter"), series(rows, "pbt", "quarter")
        qs = [q for q in pbt if q in ex][-4:]
        if len(qs) < 4:
            return None, "", "exceptional items not filed for 4 quarters"
        tot = sum(pbt[q][0] for q in qs)
        if tot <= 0:
            return None, NSE, "pre-tax profit not positive over 4 quarters"
        return abs(sum(ex[q][0] for q in qs)) / tot * 100, NSE, f"4 quarters to {qs[-1]}"

    if key in ("roce_latest", "roce_avg_3y", "roe_avg_3y"):
        m = "roe_pct" if key.startswith("roe") else "roce_pct"
        s = _last(fy(rows, m), 1 if key.endswith("latest") else 3)
        if not s or (key.endswith("3y") and len(s) < 2):
            return None, "", ""
        v = statistics.mean(x[1] for x in s)
        return v, CALC, f"FY{s[0][0][:4]}–FY{s[-1][0][:4]}" if len(s) > 1 else f"FY{s[0][0][:4]}"

    if key == "roa_latest":
        pat, assets = fy(rows, "pat"), series(rows, "assets", "instant")
        ends = [e for e in pat if e in assets]
        if not ends:
            return None, "", "needs PAT and total assets for the same year"
        e = ends[-1]
        prev = [a for a in assets if a < e and a[5:] == "03-31"]
        base = (assets[e][0] + assets[prev[-1]][0]) / 2 if prev else assets[e][0]
        return pat[e][0] / base * 100, _src(pat[e][1], NSE), f"FY{e[:4]}, average assets"

    if key == "debt_equity":
        got = latest(rows, "debt_equity", "instant") or latest(rows, "debt_equity")
        return (got[0], CALC, f"as of {got[2]}") if got else (None, "", "")

    if key == "net_debt_ebitda":
        nd, eb = latest(rows, "net_debt", "instant"), latest(rows, "ebitda", "fy")
        if nd is None or eb is None:
            return None, "", ""
        if eb[0] <= 0:
            return float("inf") if nd[0] > 0 else None, CALC, "EBITDA not positive"
        return nd[0] / eb[0], CALC, f"net debt {nd[2]} / EBITDA FY{eb[2][:4]}"

    if key == "interest_cover":
        got = latest(rows, "interest_cover", "fy")
        if got:
            return got[0], CALC, f"FY{got[2][:4]}"
        de = latest(rows, "debt_equity", "instant")
        if de and de[0] < 0.05:
            return float("inf"), CALC, "effectively debt-free"
        return None, "", ""

    if key == "cfo_pat_3y":
        cfo, pat = fy(rows, "cfo"), fy(rows, "pat")
        ends = [e for e in cfo if e in pat][-3:]
        if len(ends) < 2:
            return None, "", "needs 2+ years of cash flow and PAT"
        p = sum(pat[e][0] for e in ends)
        if p <= 0:
            return None, "", "PAT not positive over the window"
        return sum(cfo[e][0] for e in ends) / p, _src(cfo[ends[-1]][1], pat[ends[-1]][1]), \
            f"FY{ends[0][:4]}–FY{ends[-1][:4]}"

    if key == "fcf_positive":
        s = _last(fy(rows, "fcf"), 5)
        if len(s) < 3:
            return None, "", "fewer than 3 years of free cash flow"
        pos = sum(1 for x in s if x[1] > 0)
        return pos / len(s), _src(*(x[2] for x in s)), f"{pos} of {len(s)} years"

    if key == "capex_cfo_3y":
        cfo, capex = fy(rows, "cfo"), fy(rows, "capex")
        ends = [e for e in cfo if e in capex][-3:]
        if len(ends) < 2:
            return None, "", ""
        c = sum(cfo[e][0] for e in ends)
        if c <= 0:
            return float("inf"), CALC, "operating cash flow not positive"
        return sum(abs(capex[e][0]) for e in ends) / c, CALC, f"FY{ends[0][:4]}–FY{ends[-1][:4]}"

    if key == "returns_persistence":
        metric = "roe_pct" if ctx["group"] in LENDERS else "roce_pct"
        hurdle = spec.get("hurdle", ctx["hurdle"])
        s = _last(fy(rows, metric), 5)
        if len(s) < 3:
            return None, "", "fewer than 3 years of returns"
        n = sum(1 for x in s if x[1] >= hurdle)
        return n / len(s), CALC, f"{n} of {len(s)} years ≥ {hurdle:g}%"

    if key == "margin_stability":
        metric = "net_margin_pct" if ctx["group"] in LENDERS else "ebitda_margin_pct"
        s = _last(fy(rows, metric), 5)
        if len(s) < 3:
            return None, "", "fewer than 3 years of margins"
        return statistics.pstdev(x[1] for x in s), CALC, f"{len(s)} FY of {metric}"

    if key in ("gnpa", "nnpa"):
        got = latest(rows, f"{key}_pct", "quarter")
        return (got[0], got[1], f"quarter to {got[2]}") if got else (None, "", "")

    if key == "credit_cost":
        prov = series(rows, "provisions", "quarter")
        book = latest(rows, "advances", "instant") or latest(rows, "loans", "instant")
        qs = list(prov)[-4:]
        if len(qs) < 4 or not book or book[0] <= 0:
            return None, "", "needs 4 quarters of provisions and the loan book"
        return sum(prov[q][0] for q in qs) / book[0] * 100, NSE, f"4 quarters to {qs[-1]}"

    if key == "leverage":
        a, e = latest(rows, "assets", "instant"), latest(rows, "equity", "instant")
        if not a or not e or e[0] <= 0:
            return None, "", ""
        return a[0] / e[0], NSE, f"as of {a[2]}"

    if key == "promoter_trend":
        got = latest(rows, "promoter_change_pct")
        return (got[0], "NSE shareholding pattern", f"to {got[2]}") if got else (None, "", "")

    if key == "pledge":
        got = latest(rows, "pledge_pct")
        return (got[0], "NSE shareholding pattern", f"as of {got[2]}") if got else (None, "", "")

    return None, "", f"no rule for {key}"


def linear(value: float, zero: float, full: float, points: float) -> float:
    if value == float("inf"):
        frac = 1.0 if full > zero else 0.0
    elif value == float("-inf"):
        frac = 0.0 if full > zero else 1.0
    else:
        frac = (value - zero) / (full - zero)
    return round(points * max(0.0, min(1.0, frac)), 2)


def _rule(spec: dict) -> str:
    z, f = spec["zero"], spec["full"]
    return f"0 at {z:g}, full at {f:g} ({'higher' if f > z else 'lower'} is better)"


# ------------------------------------------------------------------ manual

def _yes(v) -> bool:
    return str(v or "").strip().lower() in ("yes", "y", "true", "1")


def _num(v) -> float | None:
    try:
        x = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _stale(as_of: str, today: dt.date, days: int = 365) -> bool:
    try:
        return (today - dt.date.fromisoformat(str(as_of)[:10])).days > days
    except ValueError:
        return False


def manual(key: str, spec: dict, research: dict, outlook: dict, today: dt.date) -> Criterion:
    src_row, pts_col, ev_col, where = {
        "moat_evidence": (research, "moat_points", "moat_evidence", "research inputs"),
        "governance_evidence": (research, "governance_points", "governance_evidence",
                                "research inputs"),
        "sector_outlook": (outlook, "outlook_points", "evidence", "sector outlook file"),
    }[key]
    c = Criterion(key, spec["label"], spec["points"], rule=f"0–{spec['points']:g}, needs evidence",
                  source=where)
    pts = _num(src_row.get(pts_col))
    ev = str(src_row.get(ev_col) or "").strip()
    if pts is None:
        c.note = f"not assessed: add {pts_col} and {ev_col} in the {where}"
        return c
    if not ev:
        c.note = f"{pts_col} given without evidence: ignored"
        return c
    c.value = pts
    c.points = round(max(0.0, min(spec["points"], pts)), 2)
    c.status = SCORED
    c.note = ev + (" (review is over a year old)" if _stale(src_row.get("as_of"), today) else "")
    return c


# ------------------------------------------------------------------ the card

def score(symbol: str, rows: list[dict], group: str, cap_band: str, cfg: dict,
          research: dict | None = None, outlook: dict | None = None,
          bucket: str = "", today: dt.date | None = None,
          manual_todo: list[str] | None = None) -> Scorecard:
    today = today or dt.date.today()
    research, outlook = research or {}, outlook or {}
    crit = criteria_for(cfg, group)
    model = (cfg.get("sector_models") or {}).get(group, {}).get("label", "General")
    ctx = {"group": group, "hurdle": (cfg.get("hurdle_roce") or {}).get(
        group, (cfg.get("hurdle_roce") or {}).get("default", 15))}
    comps = []
    for comp, weight in cfg["weights"].items():
        cs = crit[comp]
        c = Component(comp, COMPONENT_LABEL.get(comp, comp), weight)
        if isinstance(cs, str):
            c.note = cs
            c.criteria.append(Criterion(comp, "Not applicable to this business", weight,
                                        status=NA, note=cs))
            comps.append(c)
            continue
        for key, spec in cs.items():
            if spec.get("manual"):
                c.criteria.append(manual(key, spec, research, outlook, today))
                continue
            if spec.get("pending"):
                c.criteria.append(Criterion(key, spec["label"], spec["points"], status=PENDING,
                                            note=f"comes with the {spec['pending']}"))
                continue
            v, src, note = compute(key, rows, spec, ctx)
            cr = Criterion(key, spec["label"], spec["points"], value=v, rule=_rule(spec),
                           source=src, note=note)
            if v is not None:
                cr.points = linear(v, spec["zero"], spec["full"], spec["points"])
                cr.status = SCORED
            c.criteria.append(cr)
        comps.append(c)

    earned = round(sum(c.earned for c in comps), 2)
    available = sum(c.available for c in comps)
    # Points that apply to this business and that the platform can assess today:
    # n/a (e.g. cash flow for a bank) and pending (valuation, until Phase 4)
    # are left out, so neither counts against coverage.
    applicable = sum(cr.max_points for c in comps for cr in c.criteria
                     if cr.status not in (NA, PENDING))
    coverage = round(available / applicable * 100, 1) if applicable else 0.0
    s = round(earned / available * 100, 1) if available else None
    raw = tier_of(s, cfg) if s is not None and coverage >= cfg["min_coverage_pct"] else None
    flags = governance_flags(rows, research, cfg)
    tier = cap_tier(raw, flags, cfg)
    labels = {t[1]: t[2] for t in cfg["tiers"]}
    if tier is None:
        tl = (f"Not enough data ({coverage:.0f}% of applicable points assessed; "
              f"needs {cfg['min_coverage_pct']}%)")
    else:
        tl = labels[tier] + (f" (capped from {labels[raw]})" if raw != tier else "")
    role, reasons, cat = suggest_role(rows, tier, flags, group, cap_band, cfg, research)
    b = (bucket or "").lower()
    if b == "trade":
        bnote = "trade bucket: chosen for trading, role not compared"
    elif role in ("core", "satellite"):
        mine = "core" if b == "core" else "satellite"
        yours = f"your bucket says {mine}" if b else "no bucket set (satellite by default)"
        bnote = (f"matches your bucket ({mine})" if mine == role and b else
                 f"{yours}; the scorecard suggests {role}" if mine != role else
                 f"{yours}; the scorecard agrees")
    else:
        bnote = ""
    return Scorecard(symbol, group, model, comps, flags, s, earned, available, applicable,
                     coverage, tier, tl, raw, role, reasons, cat, b, bnote,
                     list(manual_todo or []), today.isoformat())


def tier_of(score: float, cfg: dict) -> str:
    for floor, key, _ in cfg["tiers"]:
        if score >= floor:
            return key
    return cfg["tiers"][-1][1]


def _order(cfg) -> list[str]:
    return [t[1] for t in cfg["tiers"]]          # best first


def cap_tier(tier: str | None, flags: list[Flag], cfg: dict) -> str | None:
    if tier is None or not flags:
        return tier
    o = _order(cfg)
    caps = [cfg["overrides"]["severe_cap" if f.severity == SEVERE else "moderate_cap"]
            for f in flags]
    worst_cap = max(caps, key=o.index)
    return tier if o.index(tier) >= o.index(worst_cap) else worst_cap


def governance_flags(rows: list[dict], research: dict, cfg: dict) -> list[Flag]:
    o = cfg["overrides"]
    out = []
    for col, text in (("auditor_resigned", "Auditor resigned mid-term"),
                      ("qualified_opinion", "Qualified / adverse audit opinion"),
                      ("regulatory_action", "Live regulatory action")):
        if _yes(research.get(col)):
            out.append(Flag(SEVERE, text, "research inputs"))
    if _yes(research.get("related_party_concern")):
        out.append(Flag(MODERATE, "Related-party dealings of concern", "research inputs"))
    pl = latest(rows, "pledge_pct")
    if pl and pl[0] >= o["pledge_severe_pct"]:
        out.append(Flag(SEVERE, f"Promoter pledge {pl[0]:.1f}% of promoter holding",
                        "NSE shareholding pattern"))
    elif pl and pl[0] >= o["pledge_moderate_pct"]:
        out.append(Flag(MODERATE, f"Promoter pledge {pl[0]:.1f}% of promoter holding",
                        "NSE shareholding pattern"))
    rise = latest(rows, "pledge_change_pct")
    if rise and rise[0] >= o["pledge_rise_severe_pts"]:
        out.append(Flag(SEVERE, f"Pledge up {rise[0]:.1f} pts in ~2 years",
                        "NSE shareholding pattern"))
    pc = latest(rows, "promoter_change_pct")
    if pc and pc[0] <= -o["promoter_sold_moderate_pts"]:
        out.append(Flag(MODERATE, f"Promoter stake down {-pc[0]:.1f} pts in ~2 years",
                        "NSE shareholding pattern"))
    return out


def suggest_role(rows, tier, flags, group, cap_band, cfg, research) -> tuple[str, list[str], str]:
    """(core | satellite | review | n/a, reasons, satellite category)."""
    r = cfg["role"]
    if tier is None:
        return "n/a", ["not enough data for a quality tier"], ""
    if tier == _order(cfg)[-1]:
        return "review", ["quality tier is Exit candidate: review the thesis"], ""
    why = []
    if tier not in r["core_tiers"]:
        why.append("quality tier below Core candidate")
    if flags:
        why.append("governance flag")
    if cap_band not in r["core_cap_bands"]:
        why.append(f"{cap_band or 'unknown'} cap")
    if group not in LENDERS:
        de = latest(rows, "debt_equity", "instant")
        if de and de[0] > r["core_max_debt_equity"]:
            why.append(f"debt/equity {de[0]:.2f} above {r['core_max_debt_equity']:g}")
    vol, px = latest(rows, "avg_volume_20d"), latest(rows, "close_adjusted")
    if vol and px:
        traded = vol[0] * px[0] / 1e7
        if traded < r["core_min_traded_cr"]:
            why.append(f"traded value ₹{traded:.1f} cr/day below ₹{r['core_min_traded_cr']:g} cr")
    else:
        why.append("liquidity unknown")
    pat = _last(fy(rows, "pat"), 5)
    good = sum(1 for x in pat if x[1] > 0)
    if len(pat) < r["core_min_years_on_file"]:
        why.append(f"only {len(pat)} fiscal years on file")
    elif good < len(pat):
        why.append(f"a loss in {len(pat) - good} of the last {len(pat)} years")
    if not why:
        return "core", ["meets every core condition"], ""
    cat = str(research.get("satellite_category") or "").strip().lower()
    if not cat:
        cat = ("cyclical" if group in CYCLICAL else
               "smallcap_growth" if cap_band == "small" else
               "midcap_growth" if cap_band == "mid" else "")
    return "satellite", why, cat
