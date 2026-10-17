"""One quarter's results, scored against the company's own history.

There is no free source of analyst estimates for Indian stocks, so "did it
beat?" cannot be asked. What can be asked, from filings alone:

  growth        revenue / EBITDA / profit, year on year and quarter on quarter
  acceleration  is revenue growth faster than its own recent pace?
  margins       EBITDA margin against the same quarter last year
  quality       how much of the profit came from other income or one-offs

Year on year is the headline, as Indian results are read: many businesses are
seasonal enough that a quarter-on-quarter fall means nothing.

Lenders get their own card. Revenue and EBITDA are meaningless for a bank;
net interest income, provisions and bad loans are what matter.

Pure functions over the rows results.filings.history() returns. Nothing here
touches the network.
"""
from __future__ import annotations

import calendar
import datetime as dt


def quarter_back(end: dt.date, n: int) -> dt.date:
    """The quarter end n quarters before `end` (a quarter end itself)."""
    m = end.month - 3 * n
    y = end.year + (m - 1) // 12
    m = (m - 1) % 12 + 1
    return dt.date(y, m, calendar.monthrange(y, m)[1])


def growth(cur, prev) -> float | None:
    """Percent change. None when either side is missing or the base is not
    positive: growth from a loss is not a number, it is a turnaround."""
    if cur is None or prev is None or prev <= 0:
        return None
    return round(100 * (cur / prev - 1), 1)


def _points(value, band) -> int:
    """+1 at or past the good end, -1 at or past the bad end, else 0. The band
    is [bad, good], so an inverted band (lower is better) needs no flag."""
    if value is None:
        return 0
    bad, good = band
    if good >= bad:
        return 1 if value >= good else -1 if value <= bad else 0
    return 1 if value <= good else -1 if value >= bad else 0


def company_lines(q: dict) -> dict:
    """Derived lines for an ordinary company (or an NBFC's P&L)."""
    pbt = q.get("pbt")
    exc = q.get("exceptional") or 0.0
    oth = q.get("other_income") or 0.0
    core = None if pbt is None else pbt - exc - oth
    fin, dep = q.get("finance_costs"), q.get("depreciation")
    ebitda = None if core is None or fin is None or dep is None else core + fin + dep
    rev = q.get("revenue")
    pat = q.get("pat_owners") if q.get("pat_owners") is not None else q.get("pat")
    return {
        "revenue": rev, "ebitda": ebitda, "core_pbt": core, "pbt": pbt, "pat": pat,
        "eps": q.get("eps"), "other_income": q.get("other_income"),
        "exceptional": q.get("exceptional"),
        "margin": round(100 * ebitda / rev, 2) if ebitda is not None and rev else None,
    }


def lender_lines(q: dict) -> dict:
    """Derived lines for a bank or NBFC."""
    if q.get("kind") == "bank":
        ie, ix = q.get("interest_earned"), q.get("interest_expended")
        ppop, prov = q.get("ppop"), q.get("provisions")
        pat = q.get("pat_bank_owners") if q.get("pat_bank_owners") is not None \
            else q.get("pat_bank")
        eps = q.get("eps_bank")
    else:
        # NBFC: interest earned less finance costs is NII; impairment is the
        # provision line, so pre-provision profit adds it back to core PBT.
        ie, ix = q.get("interest_earned"), q.get("finance_costs")
        prov = q.get("impairment")
        pbt = q.get("pbt")
        ppop = None if pbt is None or prov is None \
            else pbt - (q.get("exceptional") or 0.0) + prov
        pat = q.get("pat_owners") if q.get("pat_owners") is not None else q.get("pat")
        eps = q.get("eps")
    nii = None if ie is None or ix is None else ie - ix
    cc = round(100 * prov / ppop, 2) if prov is not None and ppop and ppop > 0 else None
    # Filed as a fraction (0.0111 = 1.11%). Zero means "not filed here" -- a
    # consolidated bank filing carries the tag with 0.00 -- never a clean book.
    g = q.get("gnpa_pct")
    gnpa = round(100 * g, 2) if g else None
    n = q.get("nnpa_pct")
    return {"nii": nii, "ppop": ppop, "provisions": prov, "pat": pat, "eps": eps,
            "credit_cost": cc, "gnpa": gnpa,
            "nnpa": round(100 * n, 2) if n else None}


def score(series: list[dict], cfg: dict) -> dict | None:
    """Scorecard for the newest quarter in `series` (newest first).

    `cfg` is the `results:` block of config/scoring.yaml. Returns None for an
    empty series. Every comparison that lacks its base quarter is left blank
    and noted, never filled with a guess.
    """
    if not series:
        return None
    by_end = {r["to_date"]: r for r in series}
    q = series[0]
    end, kind = q["to_date"], q.get("kind", "company")
    lines = lender_lines if kind in ("bank", "nbfc") else company_lines
    cur = lines(q)

    def back(n: int) -> dict | None:
        r = by_end.get(quarter_back(end, n))
        return lines(r) if r else None

    py, pq = back(4), back(1)
    card: dict = {
        "symbol": q["symbol"], "quarter": q["quarter"], "to_date": end,
        "kind": kind, "basis": q.get("basis"), "broadcast": q.get("broadcast"),
        "xbrl": q.get("xbrl"), "quarters_on_file": len(series),
    }
    flags: list[str] = []
    points: dict[str, int] = {}
    if py is None:
        flags.append("no filing for the same quarter last year -- YoY blank")

    if kind in ("bank", "nbfc"):
        card.update({f"{k}": (round(v, 2) if isinstance(v, float) else v)
                     for k, v in cur.items()})
        card["nii_yoy"] = growth(cur["nii"], py and py["nii"])
        card["nii_qoq"] = growth(cur["nii"], pq and pq["nii"])
        card["ppop_yoy"] = growth(cur["ppop"], py and py["ppop"])
        card["pat_yoy"] = growth(cur["pat"], py and py["pat"])
        card["pat_qoq"] = growth(cur["pat"], pq and pq["pat"])
        cc_prev = py and py["credit_cost"]
        card["credit_cost_yoy_pp"] = (round(cur["credit_cost"] - cc_prev, 2)
                                      if cur["credit_cost"] is not None
                                      and cc_prev is not None else None)
        g_prev = pq and pq["gnpa"]
        card["gnpa_qoq_bps"] = (round(100 * (cur["gnpa"] - g_prev))
                                if cur["gnpa"] is not None and g_prev is not None
                                else None)
        points["nii"] = _points(card["nii_yoy"], cfg["nii_yoy_pct"])
        points["pat"] = _points(card["pat_yoy"], cfg["pat_yoy_pct"])
        points["credit_cost"] = _points(card["credit_cost_yoy_pp"],
                                        cfg["credit_cost_yoy_pp"])
        points["gnpa"] = _points(card["gnpa_qoq_bps"], cfg["gnpa_qoq_bps"])
        if card["gnpa_qoq_bps"] is not None and card["gnpa_qoq_bps"] >= 20:
            flags.append(f"gross NPA up {card['gnpa_qoq_bps']:.0f} bps QoQ "
                         f"to {cur['gnpa']:.2f}%")
        if card["credit_cost_yoy_pp"] is not None and card["credit_cost_yoy_pp"] >= 5:
            flags.append(f"provisions take {cur['credit_cost']:.0f}% of "
                         f"pre-provision profit, up {card['credit_cost_yoy_pp']:.0f} pp")
        _sign_flags(cur["pat"], py and py["pat"], flags)
    else:
        card.update({k: (round(v, 2) if isinstance(v, float) else v)
                     for k, v in cur.items()})
        for k in ("revenue", "ebitda", "pat", "core_pbt", "eps"):
            card[f"{k}_yoy"] = growth(cur[k], py and py[k])
        for k in ("revenue", "ebitda", "pat"):
            card[f"{k}_qoq"] = growth(cur[k], pq and pq[k])
        card["margin_yoy_bps"] = (round(100 * (cur["margin"] - py["margin"]))
                                  if py and cur["margin"] is not None
                                  and py["margin"] is not None else None)
        card["margin_qoq_bps"] = (round(100 * (cur["margin"] - pq["margin"]))
                                  if pq and cur["margin"] is not None
                                  and pq["margin"] is not None else None)
        # Acceleration: this quarter's YoY against the mean YoY of the three
        # before it. Needs seven quarters; fewer and it is left blank.
        prior = []
        for n in (1, 2, 3):
            a, b = back(n), back(n + 4)
            g = growth(a and a["revenue"], b and b["revenue"])
            if g is not None:
                prior.append(g)
        card["revenue_yoy_trend"] = round(sum(prior) / len(prior), 1) \
            if len(prior) == 3 else None
        card["revenue_accel_pp"] = (round(card["revenue_yoy"] - card["revenue_yoy_trend"], 1)
                                    if card["revenue_yoy"] is not None
                                    and card["revenue_yoy_trend"] is not None else None)

        points["revenue"] = _points(card["revenue_yoy"], cfg["revenue_yoy_pct"])
        points["accel"] = _points(card["revenue_accel_pp"], cfg["revenue_accel_pp"])
        points["margin"] = _points(card["margin_yoy_bps"], cfg["margin_yoy_bps"])
        points["core_pbt"] = _points(card["core_pbt_yoy"], cfg["core_pbt_yoy_pct"])

        pbt = cur["pbt"]
        quality_hit = False
        if pbt and pbt > 0:
            oi = cur["other_income"] or 0.0
            if 100 * oi / pbt >= cfg["other_income_pct_of_pbt"]:
                flags.append(f"other income is {100 * oi / pbt:.0f}% of PBT")
                quality_hit = True
            ex = cur["exceptional"] or 0.0
            if abs(100 * ex / pbt) >= cfg["exceptional_pct_of_pbt"]:
                flags.append(f"exceptional item of ₹{ex:,.0f} cr "
                             f"({100 * ex / pbt:+.0f}% of PBT)")
                quality_hit = True
        points["quality"] = -1 if quality_hit else 0
        # A one-off in last year's quarter distorts reported PAT growth the
        # other way; core PBT growth is the number to read instead.
        if py and py["pbt"] and py["pbt"] > 0:
            ex0 = py["exceptional"] or 0.0
            if abs(100 * ex0 / py["pbt"]) >= cfg["exceptional_pct_of_pbt"]:
                flags.append(f"last year's quarter had a ₹{ex0:,.0f} cr exceptional "
                             f"item -- read core PBT ({card['core_pbt_yoy']:+.0f}%), "
                             f"not PAT" if card["core_pbt_yoy"] is not None else
                             f"last year's quarter had a ₹{ex0:,.0f} cr exceptional item")
        if (card["pat_yoy"] or 0) > 10 and (card["revenue_yoy"] or 0) < 0:
            flags.append("profit up on falling revenue")
        if card["margin_yoy_bps"] is not None and card["margin_yoy_bps"] <= -200:
            flags.append(f"EBITDA margin down {-card['margin_yoy_bps']:.0f} bps YoY")
        _sign_flags(cur["pat"], py and py["pat"], flags)

    total = sum(points.values())
    card["points"] = total
    card["points_detail"] = " ".join(f"{k}{v:+d}" for k, v in points.items() if v)
    card["verdict"] = ("strong" if total >= cfg["strong_at"]
                       else "weak" if total <= cfg["weak_at"] else "in line")
    card["flags"] = "; ".join(flags)
    return card


def _sign_flags(cur, prev, flags: list[str]) -> None:
    if cur is None or prev is None:
        return
    if prev > 0 and cur < 0:
        flags.append("swung to a loss")
    elif prev <= 0 < cur:
        flags.append("back to profit")
    elif prev <= 0 and cur <= 0:
        flags.append("loss-making both years")


def headline(card: dict) -> str:
    """One line: the numbers a reader would quote."""
    def g(k: str) -> str:
        v = card.get(k)
        return "n/a" if v is None else f"{v:+.0f}%"
    if card["kind"] in ("bank", "nbfc"):
        parts = [f"NII {g('nii_yoy')}", f"PAT {g('pat_yoy')}"]
        if card.get("gnpa") is not None:
            d = card.get("gnpa_qoq_bps")
            parts.append(f"GNPA {card['gnpa']:.2f}%"
                         + (f" ({d:+.0f}bps)" if d is not None else ""))
    else:
        parts = [f"Rev {g('revenue_yoy')}", f"EBITDA {g('ebitda_yoy')}",
                 f"PAT {g('pat_yoy')}"]
        if card.get("margin") is not None:
            d = card.get("margin_yoy_bps")
            parts.append(f"margin {card['margin']:.1f}%"
                         + (f" ({d:+.0f}bps)" if d is not None else ""))
    return " · ".join(parts) + " YoY"
