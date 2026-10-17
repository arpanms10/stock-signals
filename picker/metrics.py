"""Point-in-time fundamentals for GARP and QARP, from filings and prices.

Everything is computed as it could have been known on `as_of`: only filings
NSE had broadcast by then, and the closing price that day. That is what lets
the same code drive both today's picks and the backtest.

Inputs are the quarterly lines results.filings parses (one row per symbol
per quarter) and a price lookup. No Yahoo: its P/E and ROE are today's
numbers only, and Yahoo reports some Indian companies in USD.

Choices worth stating:

  Growth uses TTM core profit -- PAT less other income and one-offs, after
  tax -- in TOTAL, not per share. Bonus issues and splits change the share
  count without changing the business, and a per-share series across one
  would show a 50% collapse. Dilution is ignored as a result; it is rarely
  large in this universe.

  The growth rate fed to PEG is the LOWER of one-year and two-year
  (annualised) growth. Lynch's PEG wants expected growth; trailing growth
  off a depressed base is the classic way it lies, and taking the minimum
  asks the growth to have lasted.

  Valuation uses the raw (unadjusted) price with the share count filed at
  the time, so a split after `as_of` cannot distort an old market cap.

  The balance sheet (ROE, ROCE, debt, cash flow) is only in filings since
  the integrated format of 2025. Before that those fields are blank and the
  quality score is built from what the P&L shows.
"""
from __future__ import annotations

import datetime as dt
import math
import statistics

import pandas as pd

from results import scorecard as sc

# A filing's broadcast time is missing on some old rows. Assume the latest
# the law allows (45 days, 60 for March) rather than the quarter end itself.
FILING_LAG = {3: 60}


def _known_by(row: dict) -> dt.date:
    b = row.get("broadcast")
    if isinstance(b, (dt.datetime, pd.Timestamp)) and not pd.isna(b):
        return b.date()
    return row["to_date"] + dt.timedelta(days=FILING_LAG.get(row["to_date"].month, 45))


def _clean(row: dict) -> dict:
    out = {k: (None if isinstance(v, float) and v != v else v) for k, v in row.items()}
    if isinstance(out.get("broadcast"), pd.Timestamp):
        out["broadcast"] = out["broadcast"].to_pydatetime()
    return out


def visible(rows: list[dict], as_of: dt.date) -> list[dict]:
    """The rows NSE had published by `as_of`, newest first."""
    return sorted((r for r in rows if _known_by(r) <= as_of),
                  key=lambda r: r["to_date"], reverse=True)


def quarter_lines(q: dict) -> dict:
    """Per-quarter lines the picker sums, for either kind of company."""
    lender = q.get("kind") in ("bank", "nbfc")
    base = sc.lender_lines(q) if lender else sc.company_lines(q)
    pat = base.get("pat")
    if lender:
        exc = q.get("exceptional_bank") if q.get("kind") == "bank" else q.get("exceptional")
        pbt = q.get("pbt_bank") if q.get("kind") == "bank" else q.get("pbt")
        tax_rate = _tax_rate(q.get("tax"), pbt)
        core_pat = None if pat is None else pat - (exc or 0.0) * (1 - tax_rate)
    else:
        tax_rate = _tax_rate(q.get("tax"), q.get("pbt"))
        oneoff = (q.get("exceptional") or 0.0) + (q.get("other_income") or 0.0)
        # A business sold or closed: its profit (often the sale gain) sits
        # below continuing operations and is no part of what is being bought.
        disc = 0.0
        if q.get("pat_continuing") is not None and q.get("pat") is not None:
            disc = q["pat"] - q["pat_continuing"]
        core_pat = None if pat is None else pat - disc - oneoff * (1 - tax_rate)
    return {**base, "core_pat": core_pat, "finance": q.get("finance_costs"),
            "shares_cr": shares(q, pat, base.get("eps"))}


def shares(q: dict, pat, eps) -> float | None:
    """Share count in crores, checked against profit / EPS.

    Paid-up capital over face value is exact when both are filed right, but
    filers do swap them (ATGL, June 2026: the paid-up figure in the face-value
    field, giving one crore shares instead of 110). Profit / EPS is rougher --
    EPS is rounded to the paisa -- but cannot be off by 100x. Prefer the filed
    count unless the two disagree by more than 20%."""
    filed = q.get("shares_cr")
    implied = pat / eps if pat and eps and pat > 0 and eps > 0 else None
    if filed and (implied is None or abs(filed / implied - 1) <= 0.2):
        return filed
    return implied


def _tax_rate(tax, pbt) -> float:
    if tax is None or not pbt or pbt <= 0:
        return 0.25
    return min(max(tax / pbt, 0.0), 0.35)


def _ttm(by_end: dict, end: dt.date, key: str) -> float | None:
    """Sum of `key` over the four quarters ending `end`; None if any is missing."""
    vals = []
    for n in range(4):
        r = by_end.get(sc.quarter_back(end, n))
        v = None if r is None else r.get(key)
        if v is None:
            return None
        vals.append(v)
    return sum(vals)


def _growth(now, then) -> float | None:
    return sc.growth(now, then)


def _cagr2(now, then) -> float | None:
    if now is None or then is None or now <= 0 or then <= 0:
        return None
    return round(100 * (math.sqrt(now / then) - 1), 1)


def _ttm_cfo(rows: list[dict]) -> tuple[float | None, float | None]:
    """(CFO, capex) over the last twelve months from the half-yearly
    statements: a March filing is the year; a September one is H1 + last
    year's full year - last year's H1."""
    with_cf = [r for r in rows if r.get("cfo_ytd") is not None]
    if not with_cf:
        return None, None
    latest = with_cf[0]
    if latest.get("ytd_months") == 12:
        return latest["cfo_ytd"], latest.get("capex_ytd")
    fy = next((r for r in with_cf if r.get("ytd_months") == 12
               and r["to_date"] == sc.quarter_back(latest["to_date"], 2)), None)
    h1 = next((r for r in with_cf if r.get("ytd_months") == 6
               and r["to_date"] == sc.quarter_back(latest["to_date"], 4)), None)
    if fy is None or h1 is None:
        return None, None
    cfo = latest["cfo_ytd"] + fy["cfo_ytd"] - h1["cfo_ytd"]
    cx = [latest.get("capex_ytd"), fy.get("capex_ytd"), h1.get("capex_ytd")]
    capex = cx[0] + cx[1] - cx[2] if all(v is not None for v in cx) else None
    return cfo, capex


def _balance(rows: list[dict], kind: str) -> list[dict]:
    """Balance-sheet rows, newest first, with book value and debt filled in."""
    out = []
    for r in rows:
        if kind == "bank":
            if r.get("bank_capital") is None or r.get("bank_reserves") is None:
                continue
            book = r["bank_capital"] + r["bank_reserves"]
        else:
            book = r.get("equity_owners") if r.get("equity_owners") is not None \
                else r.get("equity")
            if book is None:
                continue
        debt = sum(v for v in (r.get("borrowings_noncurrent"), r.get("borrowings_current"),
                               r.get("borrowings"), r.get("debt_securities"))
                   if v is not None)
        out.append({"to_date": r["to_date"], "book": book, "debt": debt,
                    "assets": r.get("assets")})
    return out


def compute(rows: list[dict], as_of: dt.date, price_on) -> dict | None:
    """Metrics for one symbol on `as_of`.

    `rows` are that symbol's quarterly lines (any order, any dates);
    `price_on(date)` returns the raw close on or before a date, or None.
    Returns None when there is too little on file to say anything.
    """
    rows = visible([_clean(r) for r in rows], as_of)
    if len(rows) < 5:
        return None
    q0 = rows[0]
    end, kind = q0["to_date"], q0.get("kind", "company")
    lines = {r["to_date"]: quarter_lines(r) for r in rows}
    shares = lines[end]["shares_cr"]
    price = price_on(as_of)
    out: dict = {"symbol": q0["symbol"], "as_of": as_of, "kind": kind,
                 "quarter": q0.get("quarter"), "to_date": end,
                 "quarters_on_file": len(rows), "price": price}

    pat = _ttm(lines, end, "pat")
    core = _ttm(lines, end, "core_pat")
    out["ttm_pat"], out["ttm_core_pat"] = pat, core
    core_1y = _ttm(lines, sc.quarter_back(end, 4), "core_pat")
    core_2y = _ttm(lines, sc.quarter_back(end, 8), "core_pat")
    out["core_growth_1y"] = _growth(core, core_1y)
    out["core_growth_2y"] = _cagr2(core, core_2y)
    gs = [g for g in (out["core_growth_1y"], out["core_growth_2y"]) if g is not None]
    out["growth"] = min(gs) if gs else None

    top = "nii" if kind in ("bank", "nbfc") else "revenue"
    t_now = _ttm(lines, end, top)
    out["topline_growth"] = _growth(t_now, _ttm(lines, sc.quarter_back(end, 4), top))
    # Consistency: of the last eight quarters, how many grew the top line YoY.
    yoy = []
    for n in range(8):
        a = lines.get(sc.quarter_back(end, n))
        b = lines.get(sc.quarter_back(end, n + 4))
        g = _growth(a and a.get(top), b and b.get(top))
        if g is not None:
            yoy.append(g > 0)
    out["consistency"] = round(sum(yoy) / len(yoy), 2) if len(yoy) >= 4 else None

    # Valuation.
    mcap = price * shares if price and shares else None
    out["mcap_cr"] = round(mcap) if mcap else None
    out["pe"] = round(mcap / pat, 1) if mcap and pat and pat > 0 else None
    out["core_pe"] = round(mcap / core, 1) if mcap and core and core > 0 else None
    out["peg"] = (round(out["core_pe"] / out["growth"], 2)
                  if out["core_pe"] and out["growth"] and out["growth"] > 0 else None)
    out["pe_hist_pct"] = _pe_history(rows, lines, price_on, out["pe"])

    bal = _balance(rows, kind)
    if kind in ("bank", "nbfc"):
        out["ppop_ttm"] = _ttm(lines, end, "ppop")
        prov = _ttm(lines, end, "provisions")
        out["credit_cost"] = (round(100 * prov / out["ppop_ttm"], 1)
                              if prov is not None and out["ppop_ttm"] and out["ppop_ttm"] > 0
                              else None)
        out["gnpa"] = lines[end].get("gnpa")
        if bal and pat:
            b0 = bal[0]
            out["roe"] = round(100 * pat / b0["book"], 1) if b0["book"] > 0 else None
            out["roa"] = round(100 * pat / b0["assets"], 2) if b0.get("assets") else None
            out["pb"] = round(mcap / b0["book"], 2) if mcap and b0["book"] > 0 else None
    else:
        rev = _ttm(lines, end, "revenue")
        ebitda = _ttm(lines, end, "ebitda")
        fin = _ttm(lines, end, "finance")
        cpbt = _ttm(lines, end, "core_pbt")
        pbt = _ttm(lines, end, "pbt")
        oth = _ttm(lines, end, "other_income")
        out["margin"] = round(100 * ebitda / rev, 1) if ebitda is not None and rev else None
        margins = [lines[e]["margin"] for e in sorted(lines, reverse=True)[:8]
                   if lines[e].get("margin") is not None]
        out["margin_vol"] = round(statistics.pstdev(margins), 2) if len(margins) >= 6 else None
        if cpbt is not None and fin is not None:
            out["interest_cover"] = 50.0 if fin <= 0 else round(min((cpbt + fin) / fin, 50.0), 1)
        out["other_income_share"] = (round(100 * (oth or 0.0) / pbt, 1)
                                     if pbt and pbt > 0 else None)
        if bal and pat:
            b0 = bal[0]
            prior = next((b for b in bal if b["to_date"] == sc.quarter_back(b0["to_date"], 4)),
                         None)
            avg_book = (b0["book"] + prior["book"]) / 2 if prior else b0["book"]
            out["roe"] = round(100 * pat / avg_book, 1) if avg_book > 0 else None
            cap_employed = b0["book"] + b0["debt"]
            if cpbt is not None and fin is not None and cap_employed > 0:
                out["roce"] = round(100 * (cpbt + fin) / cap_employed, 1)
            out["de"] = round(b0["debt"] / b0["book"], 2) if b0["book"] > 0 else None
        cfo, capex = _ttm_cfo(rows)
        if cfo is not None:
            out["cfo_pat"] = round(cfo / pat, 2) if pat and pat > 0 else None
            if mcap and capex is not None:
                out["fcf_yield"] = round(100 * (cfo - capex) / mcap, 2)
    return out


def _pe_history(rows, lines, price_on, pe_now) -> float | None:
    """Where today's P/E sits in the stock's own last three years: the share
    of past quarterly P/Es below it (0 = cheapest it has been, 1 = dearest)."""
    if pe_now is None:
        return None
    past = []
    for r in rows[1:13]:
        e = r["to_date"]
        pat = _ttm(lines, e, "pat")
        p = price_on(_known_by(r))
        sh = lines[e]["shares_cr"]
        if pat and pat > 0 and p and sh:
            past.append(p * sh / pat)
    if len(past) < 6:
        return None
    return round(sum(v < pe_now for v in past) / len(past), 2)
