"""Valuation: what the price implies, against the stock's own history and peers,
and bear / base / bull scenario values from explicit assumptions (spec §5.9, §11).

Four reads, one per question:

  own band     today's multiple against its own last 5 years, month by month,
               each month using only results filed by then (no look-ahead).
               P/E for most, P/B for lenders, EV/EBITDA for cyclicals
  peers        the same multiple against the same NSE industry (Nifty 200
               filings on file)
  reverse DCF  the growth today's price needs to earn the cost of equity,
               set against the growth actually delivered. For lenders, the
               ROE the price needs against the ROE earned
  scenarios    bear / base / bull values from config/valuation.yaml:
                 DCF            only where free cash flow is dependable
                 earnings x own P/E band   otherwise
                 mid-cycle EBITDA x own EV/EBITDA band   cyclicals
                 justified P/B = (ROE - g) / (r - g)      lenders
                 trailing distribution / required yield   REITs, InvITs

Every value is a scenario under stated assumptions, not a price target.
Pure: no I/O. Money in ₹ crore, per-share values in ₹.
"""
from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "valuation.yaml"
SCEN = ("bear", "base", "bull")

DCF, EARNINGS, EV_EBITDA, PB, YIELD, SALES, NONE = (
    "dcf", "earnings_multiple", "ev_ebitda", "price_to_book", "distribution_yield",
    "sales_multiple", "none")
METHOD_LABEL = {DCF: "Discounted cash flow", EARNINGS: "Earnings × own P/E band",
                EV_EBITDA: "Mid-cycle EBITDA × own EV/EBITDA band",
                PB: "Justified price / book", YIELD: "Distribution yield",
                SALES: "Revenue × own P/S band", NONE: "—"}
HEADLINE = {DCF: "pe", EARNINGS: "pe", EV_EBITDA: "ev_ebitda", PB: "pb", YIELD: "yield",
            SALES: "ps"}
MULT_LABEL = {"pe": "P/E", "pb": "P/B", "ev_ebitda": "EV/EBITDA", "ps": "P/S",
              "yield": "Distribution yield"}


def load_config(path: Path | str = CONFIG) -> dict:
    import yaml
    return yaml.safe_load(Path(path).read_text())


# ------------------------------------------------------------------ inputs

@dataclass
class Inputs:
    """Everything one valuation needs; the book layer fills it from disk."""
    symbol: str
    group: str                     # sector group (analysis/sector.py), or reit / invit
    kind: str                      # company | bank | nbfc | reit
    price: float | None            # latest raw close, ₹
    price_date: str = ""
    prices: list[tuple[dt.date, float]] = field(default_factory=list)   # raw closes
    actions: list[tuple[dt.date, float]] = field(default_factory=list)  # splits / bonuses
    shares_q: dict[dt.date, float] = field(default_factory=dict)        # cr shares filed
    pat_q: dict[dt.date, float] = field(default_factory=dict)
    revenue_q: dict[dt.date, float] = field(default_factory=dict)
    ebitda_q: dict[dt.date, float] = field(default_factory=dict)
    equity: dict[dt.date, float] = field(default_factory=dict)          # instants
    net_debt: dict[dt.date, float] = field(default_factory=dict)
    fy: dict[str, dict[str, float]] = field(default_factory=dict)       # metric -> {end: v}
    revenue_cagr_pct: float | None = None      # 5y, else 3y
    pat_cagr_pct: float | None = None          # 3y
    roe_avg_pct: float | None = None           # 3 FY
    dps_ttm: float | None = None               # REIT / InvIT distribution, ₹ per unit


@dataclass
class Valuation:
    symbol: str
    method: str
    method_why: str
    price: float | None
    price_date: str
    current: dict = field(default_factory=dict)       # multiple -> value
    headline: str = ""                                 # which multiple leads
    band: dict = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)  # monthly multiples
    peers: dict = field(default_factory=dict)
    reverse: dict = field(default_factory=dict)
    scenarios: dict = field(default_factory=dict)      # bear/base/bull
    cross_check: dict = field(default_factory=dict)    # multiple scenarios beside a DCF
    margin_of_safety_pct: float | None = None
    assumptions: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {"symbol": self.symbol, "method": self.method, "price": self.price,
                "headline": self.headline, "current": self.current.get(self.headline),
                "band_percentile": self.band.get("percentile"),
                "peer_relative": self.peers.get("relative"),
                "implied": self.reverse.get("implied_pct"),
                "delivered": self.reverse.get("delivered_pct"),
                "fair": {k: v.get("fair_value") for k, v in self.scenarios.items()},
                "margin_of_safety_pct": self.margin_of_safety_pct}


# ------------------------------------------------------------------ series

def _months(a: dt.date, b: dt.date) -> int:
    return (b.year - a.year) * 12 + b.month - a.month


def ttm(q: dict[dt.date, float]) -> dict[dt.date, float]:
    """Trailing four quarters, only where all four are on file and consecutive."""
    ends = sorted(q)
    out = {}
    for i in range(3, len(ends)):
        w = ends[i - 3:i + 1]
        if all(_months(a, b) == 3 for a, b in zip(w, w[1:])):
            out[ends[i]] = sum(q[e] for e in w)
    return out


def lag_days(end: dt.date, cfg: dict) -> int:
    lag = cfg.get("filing_lag_days", {"quarter": 45, "march_quarter": 60})
    return lag["march_quarter"] if end.month == 3 else lag["quarter"]


def pit(series: dict[dt.date, float], on: dt.date, cfg: dict) -> tuple[dt.date, float] | None:
    """The newest value that had been filed by `on`."""
    ok = [e for e in series if e + dt.timedelta(days=lag_days(e, cfg)) <= on]
    if not ok:
        return None
    e = max(ok)
    return e, series[e]


def shares_on(inp: Inputs, on: dt.date, cfg: dict) -> float | None:
    """Filed share count, carried forward through later splits and bonuses."""
    got = pit(inp.shares_q, on, cfg) or (
        (max(inp.shares_q), inp.shares_q[max(inp.shares_q)]) if inp.shares_q else None)
    if not got:
        return None
    end, n = got
    for ex, f in inp.actions:
        if end < ex <= on:
            n *= f
    return n


def multiples_on(inp: Inputs, on: dt.date, close: float, cfg: dict,
                 series: dict | None = None) -> dict:
    s = series or _ttms(inp)
    n = shares_on(inp, on, cfg)
    if not n or not close:
        return {}
    mcap = close * n
    out = {"mcap": mcap}
    pat = pit(s["pat"], on, cfg)
    if pat and pat[1] > 0 and mcap / pat[1] <= cfg["band"]["max_pe"]:
        out["pe"] = mcap / pat[1]
    eq = pit(inp.equity, on, cfg)
    if eq and eq[1] > 0:
        out["pb"] = mcap / eq[1]
    eb, nd = pit(s["ebitda"], on, cfg), pit(inp.net_debt, on, cfg)
    if eb and eb[1] > 0:
        out["ev_ebitda"] = (mcap + (nd[1] if nd else 0)) / eb[1]
    rev = pit(s["revenue"], on, cfg)
    if rev and rev[1] > 0:
        out["ps"] = mcap / rev[1]
    return out


def _ttms(inp: Inputs) -> dict:
    return {"pat": ttm(inp.pat_q), "ebitda": ttm(inp.ebitda_q), "revenue": ttm(inp.revenue_q)}


def month_ends(prices: list[tuple[dt.date, float]]) -> list[tuple[dt.date, float]]:
    last: dict[tuple[int, int], tuple[dt.date, float]] = {}
    for d, c in sorted(prices):
        last[(d.year, d.month)] = (d, c)
    return [last[k] for k in sorted(last)]


def history(inp: Inputs, cfg: dict) -> list[dict]:
    if not inp.prices:
        return []
    end = max(d for d, _ in inp.prices)
    start = end.replace(year=end.year - cfg["band"]["years"])
    s = _ttms(inp)
    out = []
    for d, c in month_ends([p for p in inp.prices if p[0] >= start]):
        m = multiples_on(inp, d, c, cfg, s)
        if m:
            out.append({"date": d.isoformat(), **m})
    return out


def percentile_rank(values: list[float], x: float) -> float:
    return sum(1 for v in values if v <= x) / len(values)


def band(hist: list[dict], metric: str, current: float | None, cfg: dict) -> dict:
    vals = [h[metric] for h in hist if h.get(metric) is not None]
    if len(vals) < cfg["band"]["min_months"]:
        return {"metric": metric, "months": len(vals),
                "note": f"{len(vals)} months of {MULT_LABEL[metric]} on file; "
                        f"needs {cfg['band']['min_months']}"}
    q = statistics.quantiles(vals, n=100, method="inclusive")
    pc = cfg["band"]["percentiles"]
    out = {"metric": metric, "months": len(vals),
           "from": next(h["date"] for h in hist if h.get(metric) is not None),
           "to": [h["date"] for h in hist if h.get(metric) is not None][-1],
           **{k: q[p - 1] for k, p in pc.items()}, "min": min(vals), "max": max(vals)}
    if current is not None:
        out["percentile"] = percentile_rank(vals, current)
    return out


# ------------------------------------------------------------------ DCF

def growth_path(g1: float, gt: float, horizon: int, high: int) -> list[float]:
    """g1 for `high` years, then a straight fade to terminal growth by `horizon`."""
    return [g1 if y <= high else g1 + (gt - g1) * (y - high) / (horizon - high)
            for y in range(1, horizon + 1)]


def dcf_value(base: float, g1: float, gt: float, r: float, horizon: int, high: int) -> float:
    """Present value of a cash flow `base` growing along growth_path, plus a
    Gordon terminal value. Rates as fractions."""
    if r <= gt:
        raise ValueError("discount rate must exceed terminal growth")
    v, x = 0.0, base
    for y, g in enumerate(growth_path(g1, gt, horizon, high), 1):
        x *= 1 + g
        v += x / (1 + r) ** y
    return v + x * (1 + gt) / (r - gt) / (1 + r) ** horizon


def solve(f, target: float, lo: float = -0.3, hi: float = 0.8) -> float | None:
    """g with f(g) == target by bisection (f increasing in g), None if outside."""
    flo, fhi = f(lo), f(hi)
    if not (flo <= target <= fhi):
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        if f(mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def rate(cfg: dict, scen: str = "base") -> float:
    d = cfg["discount"]
    return (d["risk_free_pct"] + d["equity_risk_premium_pct"] + d["shift_pct"][scen]) / 100


# ------------------------------------------------------------------ the read

def _last_fy(inp: Inputs, metric: str, n: int) -> list[float]:
    s = inp.fy.get(metric) or {}
    return [s[k] for k in sorted(s)[-n:]]


def method_for(inp: Inputs, cfg: dict) -> tuple[str, str]:
    if inp.kind == "reit":
        return YIELD, "units are valued on the distribution they pay"
    if inp.kind in ("bank", "nbfc"):
        return PB, "lenders are valued on book: earnings and cash flow move with the loan book"
    if inp.group in cfg["cyclical_groups"]:
        return EV_EBITDA, ("cyclical: one year's earnings mislead, so mid-cycle (5-year "
                           "average) EBITDA is valued instead")
    fcf, rev = _last_fy(inp, "fcf", 5), _last_fy(inp, "revenue", 3)
    pos = sum(1 for x in fcf if x > 0)
    margin = (sum(_last_fy(inp, "fcf", 3)) / sum(rev)) if len(rev) == 3 and sum(rev) > 0 else None
    if len(fcf) >= 4 and pos >= cfg["dcf_min_positive_fcf_years"] and margin and margin > 0:
        return DCF, f"free cash flow positive in {pos} of {len(fcf)} years: dependable enough for a DCF"
    return EARNINGS, (f"free cash flow positive in only {pos} of {len(fcf)} years: no DCF, "
                      "earnings valued on the stock's own P/E band" if fcf else
                      "no free-cash-flow history: earnings valued on the own P/E band")


def value(inp: Inputs, cfg: dict, peers: dict | None = None,
          today: dt.date | None = None) -> Valuation:
    method, why = method_for(inp, cfg)
    v = Valuation(inp.symbol, method, why, inp.price, inp.price_date)
    v.headline = HEADLINE.get(method, "")
    if inp.price is None:
        v.method, v.method_why = NONE, "no price on file"
        return v
    on = dt.date.fromisoformat(inp.price_date) if inp.price_date else (today or dt.date.today())
    shares = shares_on(inp, on, cfg)
    if method == YIELD:
        return _reit(inp, v, cfg)
    if not shares:
        v.method, v.method_why = NONE, "no filed share count"
        return v
    s = _ttms(inp)
    v.current = {k: x for k, x in multiples_on(inp, on, inp.price, cfg, s).items()}
    mcap = v.current.pop("mcap", inp.price * shares)
    v.history = history(inp, cfg)
    if v.headline:
        v.band = band(v.history, v.headline, v.current.get(v.headline), cfg)
    if peers:
        v.peers = peers
    nd = pit(inp.net_debt, on, cfg)
    net_debt = nd[1] if nd else 0.0
    r = rate(cfg)
    gt = cfg["terminal_growth_pct"]["base"] / 100
    H, hi = cfg["horizon_years"], cfg["high_growth_years"]
    g_hist = _growth(inp, cfg)
    v.assumptions = {"cost_of_equity_pct": {k: round(rate(cfg, k) * 100, 2) for k in SCEN},
                     "terminal_growth_pct": cfg["terminal_growth_pct"],
                     "horizon_years": H, "high_growth_years": hi,
                     "growth_record_pct": None if g_hist is None else round(g_hist * 100, 2)}

    if method == PB:
        _lender(inp, v, cfg, on, shares, mcap)
    elif method == DCF:
        rev = pit(s["revenue"], on, cfg)
        fcf3, rev3 = _last_fy(inp, "fcf", 3), _last_fy(inp, "revenue", 3)
        m = sum(fcf3) / sum(rev3)
        fcf0 = sum(fcf3) / len(fcf3)
        v.reverse = _reverse(lambda g: dcf_value(fcf0, g, gt, r, H, hi), mcap + net_debt,
                             g_hist, "revenue (and free cash flow) growth, years 1–5, that "
                             "makes the DCF equal today's enterprise value",
                             "5-year revenue CAGR" if inp.revenue_cagr_pct is not None else "")
        if rev and g_hist is not None:
            for k in SCEN:
                g1 = _scen_growth(g_hist, cfg, k)
                mk = m * cfg["fcf_margin_scale"][k]
                gtk, rk = cfg["terminal_growth_pct"][k] / 100, rate(cfg, k)
                ev = dcf_value(rev[1] * mk, g1, gtk, rk, H, hi)
                v.scenarios[k] = _fair(ev - net_debt, shares, inp.price, {
                    "revenue_growth_y1_5_pct": round(g1 * 100, 2),
                    "fcf_margin_pct": round(mk * 100, 2), "discount_pct": round(rk * 100, 2),
                    "terminal_growth_pct": round(gtk * 100, 2),
                    "revenue_ttm_cr": round(rev[1], 1), "net_debt_cr": round(net_debt, 1)})
        else:
            v.notes.append("DCF needs trailing revenue and a growth record")
        v.cross_check = _earnings_scenarios(inp, v, cfg, on, shares, s, g_hist)
    elif method == EARNINGS and not _pe_usable(v, s, on, cfg) and \
            band(v.history, "ps", v.current.get("ps"), cfg).get("base"):
        v.method, v.headline = SALES, "ps"
        v.method_why = ("no usable P/E (losses or too little history): revenue valued on "
                        "the stock's own price/sales band instead")
        v.band = band(v.history, "ps", v.current.get("ps"), cfg)
        v.notes.append("low confidence: a price/sales band carries the margins and growth "
                       "of its own period, and a change in how revenue is booked moves it")
        rev = pit(s["revenue"], on, cfg)
        g = g_hist if g_hist is not None else 0.0
        for k in SCEN:
            gk = _scen_growth(g, cfg, k)
            v.scenarios[k] = _fair(rev[1] * (1 + gk) * v.band[k], shares, inp.price, {
                "revenue_ttm_cr": round(rev[1], 1), "next_year_growth_pct": round(gk * 100, 2),
                "ps": round(v.band[k], 3),
                "ps_source": f"own 5-year band, {cfg['band']['percentiles'][k]}th percentile"})
    elif method == EARNINGS:
        pat = pit(s["pat"], on, cfg)
        g_pat = (inp.pat_cagr_pct / 100) if inp.pat_cagr_pct is not None else g_hist
        exit_pe = v.band.get("base")
        if pat and pat[1] > 0 and exit_pe:
            v.reverse = _reverse(
                lambda g: pat[1] * (1 + g) ** H * exit_pe / (1 + r) ** H, mcap, g_pat,
                f"earnings growth over {H} years that earns the cost of equity if the P/E "
                f"returns to its 5-year median ({exit_pe:.1f}); dividends ignored",
                "3-year PAT CAGR" if inp.pat_cagr_pct is not None else "revenue CAGR")
        v.scenarios = _earnings_scenarios(inp, v, cfg, on, shares, s, g_hist)
    elif method == EV_EBITDA:
        eb5 = _last_fy(inp, "ebitda", 5)
        if len(eb5) >= 3 and v.band.get("base"):
            mid = sum(eb5) / len(eb5)
            for k in SCEN:
                v.scenarios[k] = _fair(mid * v.band[k] - net_debt, shares, inp.price, {
                    "mid_cycle_ebitda_cr": round(mid, 1), "ev_ebitda": round(v.band[k], 2),
                    "ev_ebitda_source": f"own 5-year band, {cfg['band']['percentiles'][k]}th "
                                        "percentile", "net_debt_cr": round(net_debt, 1)})
            ev = mcap + net_debt
            v.reverse = {"implied_pct": None, "basis": "cyclical: the read is EV / mid-cycle "
                         f"EBITDA = {ev / mid:.1f}x against the band",
                         "delivered_pct": None, "gap_pts": None}
        else:
            v.notes.append("needs 3+ years of EBITDA and a 24-month EV/EBITDA band")
    base = v.scenarios.get("base", {}).get("fair_value")
    if base and base > 0:
        v.margin_of_safety_pct = (base - inp.price) / base * 100
    return v


def _pe_usable(v: Valuation, s: dict, on, cfg) -> bool:
    pat = pit(s["pat"], on, cfg)
    return bool(pat and pat[1] > 0 and v.band.get("base"))


def _growth(inp: Inputs, cfg: dict) -> float | None:
    g = inp.revenue_cagr_pct
    if g is None:
        return None
    return max(cfg["growth"]["floor_pct"], min(cfg["growth"]["cap_pct"], g)) / 100


def _scen_growth(g: float, cfg: dict, k: str) -> float:
    return g * cfg["growth"]["scale"][k]


def _fair(equity_cr: float, shares: float, price: float, assumptions: dict) -> dict:
    fv = equity_cr / shares if shares else None
    return {"fair_value": fv, "upside_pct": None if fv is None else (fv - price) / price * 100,
            "assumptions": assumptions}


def _reverse(f, target: float, delivered: float | None, basis: str, delivered_basis: str) -> dict:
    g = solve(f, target)
    out = {"basis": basis, "implied_pct": None if g is None else g * 100,
           "delivered_pct": None if delivered is None else delivered * 100,
           "delivered_basis": delivered_basis}
    if g is None:
        out["note"] = "outside -30% to +80% a year: the price is not explained by growth alone"
    out["gap_pts"] = (None if g is None or delivered is None else (g - delivered) * 100)
    return out


def _earnings_scenarios(inp, v, cfg, on, shares, s, g_hist) -> dict:
    pat = pit(s["pat"], on, cfg)
    if not pat or pat[1] <= 0 or not v.band.get("base") or v.band.get("metric") != "pe":
        if v.method == EARNINGS:
            v.notes.append("earnings scenarios need positive trailing PAT and a 24-month P/E band")
        return {}
    g = g_hist if g_hist is not None else 0.0
    out = {}
    for k in SCEN:
        gk = _scen_growth(g, cfg, k)
        out[k] = _fair(pat[1] * (1 + gk) * v.band[k], shares, inp.price, {
            "pat_ttm_cr": round(pat[1], 1), "next_year_growth_pct": round(gk * 100, 2),
            "pe": round(v.band[k], 2),
            "pe_source": f"own 5-year band, {cfg['band']['percentiles'][k]}th percentile"})
    return out


def _lender(inp, v, cfg, on, shares, mcap) -> None:
    eq = pit(inp.equity, on, cfg)
    if not eq or eq[1] <= 0 or inp.roe_avg_pct is None:
        v.notes.append("needs book value and a 3-year ROE")
        return
    pb = mcap / eq[1]
    r, gt = rate(cfg), cfg["terminal_growth_pct"]["base"] / 100
    implied = gt + pb * (r - gt)
    v.reverse = {"basis": f"ROE the price needs: g + P/B × (r − g) with r {r*100:.1f}%, "
                          f"g {gt*100:.1f}%", "implied_pct": implied * 100,
                 "delivered_pct": inp.roe_avg_pct, "delivered_basis": "3-year average ROE",
                 "gap_pts": implied * 100 - inp.roe_avg_pct}
    if inp.roe_avg_pct / 100 <= cfg["terminal_growth_pct"]["bear"] / 100:
        v.notes.append(f"3-year ROE {inp.roe_avg_pct:.1f}% is at or below long-run growth: "
                       "the justified P/B model gives no meaningful value (a holding "
                       "company or a lender in a bad patch); see own and peer P/B instead")
        return
    for k in SCEN:
        roe = (inp.roe_avg_pct + cfg["lender"]["roe_shift_pts"][k]) / 100
        rk, gk = rate(cfg, k), cfg["terminal_growth_pct"][k] / 100
        jpb = max(0.0, (roe - gk) / (rk - gk))
        v.scenarios[k] = _fair(eq[1] * jpb, shares, inp.price, {
            "roe_pct": round(roe * 100, 2), "discount_pct": round(rk * 100, 2),
            "growth_pct": round(gk * 100, 2), "justified_pb": round(jpb, 2),
            "book_value_cr": round(eq[1], 1)})


def _reit(inp: Inputs, v: Valuation, cfg: dict) -> Valuation:
    if not inp.dps_ttm:
        v.notes.append("no trailing distribution on file")
        return v
    y = inp.dps_ttm / inp.price
    v.current = {"yield": y * 100}
    rf = cfg["discount"]["risk_free_pct"]
    rc = cfg["reit"]
    base_r = (rf + rc["premium_pct"]["base"]) / 100
    # Distribution growth the price implies at the base required return.
    implied = (base_r * inp.price - inp.dps_ttm) / (inp.price + inp.dps_ttm)
    v.reverse = {"basis": f"distribution growth the price implies at a {base_r*100:.1f}% "
                          "required return (Gordon model)",
                 "implied_pct": implied * 100, "delivered_pct": None, "gap_pts": None}
    for k in SCEN:
        r, g = (rf + rc["premium_pct"][k]) / 100, rc["growth_pct"][k] / 100
        fv = inp.dps_ttm * (1 + g) / (r - g)
        v.scenarios[k] = {"fair_value": fv, "upside_pct": (fv - inp.price) / inp.price * 100,
                          "assumptions": {"distribution_ttm": round(inp.dps_ttm, 2),
                                          "required_return_pct": round(r * 100, 2),
                                          "distribution_growth_pct": round(g * 100, 2)}}
    base = v.scenarios["base"]["fair_value"]
    v.margin_of_safety_pct = (base - inp.price) / base * 100
    v.notes.append("NAV is not on file (the trust's own reports); yield only")
    return v


# ------------------------------------------------------------------ peers

def peer_compare(symbol: str, metric: str, own: float | None, peers: dict[str, float],
                 cfg: dict) -> dict:
    """peers: {symbol: multiple} for the same industry, the holding excluded."""
    vals = {k: x for k, x in peers.items() if k != symbol and x is not None and x > 0}
    out = {"metric": metric, "n": len(vals), "names": sorted(vals)}
    if len(vals) < cfg["peers"]["min_peers"]:
        out["note"] = f"{len(vals)} peers with a {MULT_LABEL[metric]} on file; needs " \
                      f"{cfg['peers']['min_peers']}"
        return out
    med = statistics.median(vals.values())
    out.update({"median": med, "low": min(vals.values()), "high": max(vals.values())})
    if own is not None and own > 0:
        out["relative"] = own / med
        out["percentile"] = percentile_rank(list(vals.values()), own)
    return out
