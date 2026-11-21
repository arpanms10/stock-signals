"""The Core–Satellite plan: target weights, and the buys and trims to move
toward them, with each constraint explained (spec §9).

Constrained allocation, not return-chasing. Nothing here forecasts returns,
and the descriptive scorecard is used only as a gate (no new money into a
flagged holding), never as a tilt. The steps, in order:

  1. Budget      new money C over the plan; book after = book + C. REITs and
                 other sleeves are left as they are
  2. Buckets     core target = 70% of operating equity after C, satellite 30%
                 (config/core_satellite.yaml), by your bucket labels
  3. Hold-only   a holding with an exit-candidate tier, a governance flag, a
                 rich valuation or too little liquidity keeps its current value
                 as its target: held, no new money
  4. Targets     each bucket's remaining budget is shared among its other
                 holdings in proportion to what you hold now (your existing
                 conviction is preserved), never above a holding's position
                 cap (8% core, 4% satellite, 2% small cap, of the whole book)
                 or its liquidity limit; whatever a capped holding can't take
                 flows to the rest ("water-filling")
  5. Sectors     a sector above 25% of operating equity is scaled back to 25%
                 and the freed budget flows to other holdings in the bucket
  6. Buys        new money goes to holdings below target, in proportion to
                 their shortfall
  7. Trims       proposed only to bring a holding under its cap or a sector
                 under its limit, never on rank or score, and only when new
                 money alone would not fix it within the configured months
                 (the turnover penalty). Each trim shows its estimated tax

Pure: no I/O. Money in ₹.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PlanHolding:
    symbol: str
    value: float
    role: str                    # core | satellite
    cap_pct: float               # position cap, % of the whole book
    sector: str = ""
    cap_band: str = ""
    no_buy: list[str] = field(default_factory=list)   # reasons: held, no new money
    liquidity_max: float | None = None                # ₹ value it can reach and still exit in time
    tax_per_rupee: float = 0.0                        # estimated tax per ₹ trimmed
    role_source: str = "bucket"                       # bucket | default | suggested


@dataclass
class PlanRow:
    symbol: str
    role: str
    current: float
    target: float
    buy: float = 0.0
    trim: float = 0.0
    trim_tax: float = 0.0
    after: float = 0.0
    cap_pct: float = 0.0
    notes: list[str] = field(default_factory=list)


@dataclass
class Plan:
    contribution: float
    months: int
    book_before: float
    book_after: float
    operating_before: float
    operating_after: float
    rows: list[PlanRow]
    buckets: list[dict]
    sectors: list[dict]
    unallocated: float
    months_to_target: float | None
    constraints: list[str]
    warnings: list[str]


def water_fill(budget: float, base: dict[str, float], upper: dict[str, float]) -> dict[str, float]:
    """Split `budget` in proportion to `base`, never above `upper`; what a
    capped name can't take is shared among the rest. Returns allocations
    summing to min(budget, sum(upper))."""
    alloc = {k: 0.0 for k in base}
    open_ = {k for k in base if upper.get(k, 0) > 1e-9}
    left = budget
    while left > 1e-6 and open_:
        wsum = sum(max(base[k], 0) for k in open_)
        share = {k: (max(base[k], 0) / wsum if wsum > 0 else 1 / len(open_)) for k in open_}
        capped = set()
        used = 0.0
        for k in open_:
            give = left * share[k]
            room = upper[k] - alloc[k]
            if give >= room - 1e-9:
                give = room
                capped.add(k)
            alloc[k] += give
            used += give
        left -= used
        open_ -= capped
        if not capped:
            break
    return alloc


def plan(holdings: list[PlanHolding], book_total: float, monthly: float, months: int,
         cs_cfg: dict, plan_cfg: dict) -> Plan:
    C = max(0.0, monthly) * max(1, months)
    V1 = book_total + C
    op0 = sum(h.value for h in holdings)
    op1 = op0 + C
    t = cs_cfg["targets"]
    targets_pct = {"core": t["core_pct"] / 100, "satellite": t["satellite_pct"] / 100}
    smax = cs_cfg["sector_max_pct"] / 100
    tol = plan_cfg["trim_tolerance_pts"] / 100
    by = {h.symbol: h for h in holdings}
    warnings, constraints = [], []

    upper = {}
    for h in holdings:
        u = h.cap_pct / 100 * V1
        if h.liquidity_max is not None:
            u = min(u, max(h.liquidity_max, h.value if h.no_buy else 0.0))
        upper[h.symbol] = u

    target: dict[str, float] = {}
    frozen = {h.symbol for h in holdings if h.no_buy}
    for h in holdings:
        if h.symbol in frozen:
            target[h.symbol] = min(h.value, h.cap_pct / 100 * V1)

    sector_cap_upper = dict(upper)
    unalloc = 0.0
    for _ in range(6):                      # sector scaling converges in a few passes
        unalloc = 0.0
        for role in ("core", "satellite"):
            members = [h for h in holdings if h.role == role]
            active = [h for h in members if h.symbol not in frozen]
            budget = targets_pct[role] * op1 - sum(target[h.symbol] for h in members
                                                   if h.symbol in frozen)
            if budget <= 0:
                for h in active:
                    target[h.symbol] = 0.0
                continue
            if not active:
                unalloc += budget
                continue
            got = water_fill(budget, {h.symbol: h.value for h in active},
                             {h.symbol: sector_cap_upper[h.symbol] for h in active})
            target.update(got)
            unalloc += budget - sum(got.values())
        over = _sector_over(holdings, target, smax * op1)
        if not over:
            break
        for sector, excess in over.items():
            act = [h for h in holdings if h.sector == sector and h.symbol not in frozen]
            tot = sum(target[h.symbol] for h in act)
            if tot <= 0:
                continue
            keep = max(0.0, tot - excess) / tot
            for h in act:
                sector_cap_upper[h.symbol] = target[h.symbol] * keep

    # ---------------------------------------------------------- trims
    rows = {h.symbol: PlanRow(h.symbol, h.role, h.value, target.get(h.symbol, 0.0),
                              cap_pct=h.cap_pct) for h in holdings}
    for h in holdings:
        r = rows[h.symbol]
        if h.no_buy:
            r.notes.append("held, no new money: " + "; ".join(h.no_buy))
        if h.role_source == "suggested":
            r.notes.append("role from the scorecard's suggestion (bucket blank)")
        cap_v = h.cap_pct / 100 * V1
        if h.value > cap_v + tol * V1 and h.role_source == "default":
            r.notes.append(f"over the {h.cap_pct:g}% satellite cap, but its bucket is not "
                           "set (satellite by default): set the bucket before acting; "
                           "no trim proposed")
        elif h.value > cap_v + tol * V1:
            k = _months_to_cap(h.value, h.cap_pct / 100, book_total, monthly)
            if monthly > 0 and k is not None and k <= plan_cfg["fix_by_contributions_months"]:
                r.notes.append(f"over its {h.cap_pct:g}% cap; new money alone brings it "
                               f"under in about {k:.0f} months: no trim")
            else:
                r.trim = h.value - cap_v
                r.notes.append(f"trim to its {h.cap_pct:g}% position cap")
    for sector, excess in _sector_over(holdings, {h.symbol: h.value - rows[h.symbol].trim
                                                  for h in holdings}, smax * op1).items():
        members = [h for h in holdings if h.sector == sector and h.role_source != "default"]
        if not members:
            continue
        k = _months_to_cap(sum(h.value for h in members), smax, op0, monthly)
        if monthly > 0 and k is not None and k <= plan_cfg["fix_by_contributions_months"]:
            for h in members:
                rows[h.symbol].notes.append(f"{sector} over {smax*100:g}%: no new money to "
                                            f"the sector; under the limit in about {k:.0f} months")
            continue
        if excess < smax * op1 * tol:
            continue
        tot = sum(h.value - rows[h.symbol].trim for h in members)
        for h in members:
            cut = excess * (h.value - rows[h.symbol].trim) / tot
            rows[h.symbol].trim += cut
            rows[h.symbol].notes.append(f"trim share of {sector} back to {smax*100:g}%")
    for r in rows.values():
        if r.trim < plan_cfg["min_trade_rupees"]:
            r.trim = 0.0
        r.trim_tax = r.trim * by[r.symbol].tax_per_rupee

    # ---------------------------------------------------------- buys
    freed = sum(r.trim for r in rows.values())
    gaps = {s: max(0.0, r.target - (r.current - r.trim)) for s, r in rows.items()
            if s not in frozen}
    buys = water_fill(C + freed, gaps, gaps)
    for s, b in buys.items():
        rows[s].buy = b if b >= plan_cfg["min_trade_rupees"] else 0.0
    spent = sum(r.buy for r in rows.values())
    unallocated = C + freed - spent
    for r in rows.values():
        r.after = r.current - r.trim + r.buy

    # ---------------------------------------------------------- summary
    buckets = []
    for role in ("core", "satellite"):
        b0 = sum(r.current for r in rows.values() if r.role == role)
        b1 = sum(r.after for r in rows.values() if r.role == role)
        buckets.append({"role": role, "before_pct": b0 / op0 * 100 if op0 else 0,
                        "after_pct": b1 / (op1 - unallocated) * 100 if op1 - unallocated else 0,
                        "target_pct": targets_pct[role] * 100})
    sectors = []
    for sec in sorted({h.sector or "Unclassified" for h in holdings}):
        hs = [h for h in holdings if (h.sector or "Unclassified") == sec]
        sectors.append({"sector": sec,
                        "before_pct": sum(h.value for h in hs) / op0 * 100 if op0 else 0,
                        "after_pct": sum(rows[h.symbol].after for h in hs)
                        / (op1 - unallocated) * 100 if op1 - unallocated else 0})
    core0 = sum(r.current for r in rows.values() if r.role == "core")
    months_to = _months_to_share(core0, op0, targets_pct["core"], monthly)
    if not any(h.role == "core" for h in holdings):
        warnings.append("No holding is core, so the 70% core target can't be reached "
                        "from what you own: set bucket = core in your holdings file for "
                        "the companies you mean to own for a decade or more, or try the "
                        "suggested-role what-if.")
    if unallocated > plan_cfg["min_trade_rupees"]:
        warnings.append(f"₹{unallocated:,.0f} of new money has no eligible holding with "
                        "room under its cap and its bucket target: it stays as cash, or "
                        "goes to a new company you add.")
    constraints = [
        f"Budget: ₹{C:,.0f} of new money (₹{monthly:,.0f} × {months} months); REITs and "
        "other sleeves untouched.",
        f"Buckets: core {t['core_pct']}% / satellite {t['satellite_pct']}% of operating equity "
        "after new money, by your bucket labels.",
        f"Position caps: core {cs_cfg['position_max_pct']['core']}%, satellite "
        f"{cs_cfg['position_max_pct']['satellite']}%, small cap "
        f"{cs_cfg['position_max_pct']['small_cap']}% of the whole book; the tightest applies.",
        f"Sector limit: {cs_cfg['sector_max_pct']}% of operating equity (unclassified "
        "companies are not grouped).",
        "Hold-only: no new money into a holding with an exit-candidate tier, a governance "
        "flag, a rich valuation (own band and peers) or a position it couldn't exit in "
        "the liquidity limit.",
        "Targets keep your current proportions inside each bucket; nothing is scored or "
        "ranked to set them.",
        f"Trims only for a cap or sector breach larger than {plan_cfg['trim_tolerance_pts']} pts "
        f"that new money can't fix within {plan_cfg['fix_by_contributions_months']} months; "
        f"trades under ₹{plan_cfg['min_trade_rupees']:,} are skipped.",
    ]
    return Plan(C, months, book_total, book_total + C, op0, op1,
                sorted(rows.values(), key=lambda r: -r.current), buckets, sectors,
                unallocated, months_to, constraints, warnings)


def _sector_over(holdings, values: dict[str, float], limit: float) -> dict[str, float]:
    by: dict[str, float] = {}
    for h in holdings:
        if h.sector:
            by[h.sector] = by.get(h.sector, 0.0) + values.get(h.symbol, 0.0)
    return {s: v - limit for s, v in by.items() if v > limit + 1e-6}


def _months_to_cap(value: float, cap: float, total: float, monthly: float) -> float | None:
    """Months of new money (none of it to this holding) until value / total <= cap."""
    if value <= cap * total:
        return 0.0
    if monthly <= 0:
        return None
    return (value / cap - total) / monthly


def _months_to_share(part: float, whole: float, share: float, monthly: float) -> float | None:
    """Months of new money, all of it to `part`, until part / whole >= share."""
    if whole and part / whole >= share:
        return 0.0
    if monthly <= 0 or share >= 1:
        return None
    return (share * whole - part) / ((1 - share) * monthly)
