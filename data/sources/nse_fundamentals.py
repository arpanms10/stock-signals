"""Fundamentals straight from NSE: quarterly results and shareholding.

jugaad-data does not cover this -- it handles prices, bhavcopy, indices and
derivatives only. But NSE's own endpoints do, in two layers:

  JSON API      metadata plus a link to the filing
  XBRL filing   the actual numbers

Two things this gets that Yahoo cannot:

  1. Authoritative INR figures. Yahoo reports Indian IT companies in USD, which
     silently understated HCLTECH's revenue 85-fold until it was caught. NSE
     files in rupees, always.
  2. Promoter pledge. Not in Yahoo at all, and the single loudest pre-collapse
     signal in Indian smallcaps. It lives in the shareholding-pattern XBRL
     (~1.5 MB per filing), not the JSON API.
"""
from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET

from . import nse_client as nse

RESULTS_URL = "https://www.nseindia.com/api/corporates-financial-results"
SHP_URL = "https://www.nseindia.com/api/corporate-share-holdings-master"

CRORE = 1e7

# Ind-AS XBRL tag names, which are standardised across filers.
RESULT_TAGS = {
    "RevenueFromOperations": "revenue",
    "ProfitBeforeTax": "pbt",
    "ProfitLossForPeriod": "pat",
    "ProfitLossForPeriodFromContinuingOperations": "pat_continuing",
    "IncomeTaxExpense": "tax",
    "FinanceCosts": "finance_costs",
    "ProfitBeforeExceptionalItemsAndTax": "pbeit",
}


def _text(el) -> str:
    return (el.text or "").strip()


def _parse_xbrl_numbers(xml_text: str, wanted: dict[str, str]) -> dict:
    """Pull named tags out of an Ind-AS filing, taking the first occurrence.

    Filings repeat tags across contexts (quarter, year-to-date, prior year).
    The first is the current reporting period, which is what we want -- taking
    a max or a sum here would silently blend periods.
    """
    root = ET.fromstring(xml_text.encode())
    out: dict[str, float] = {}
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag in wanted and wanted[tag] not in out:
            try:
                out[wanted[tag]] = float(_text(el))
            except (TypeError, ValueError):
                continue
    return out


def latest_results(symbol: str, consolidated_first: bool = True) -> dict:
    """Most recent quarterly results for one symbol, in crores."""
    rows = nse.get(RESULTS_URL, params={"index": "equities", "symbol": symbol,
                                        "period": "Quarterly"}).json()
    if not rows:
        return {}
    # Prefer consolidated: it includes subsidiaries, which is what the business
    # actually earns. Standalone can look very different for a holding company.
    ordered = sorted(
        rows,
        key=lambda r: (
            0 if (consolidated_first and str(r.get("consolidated", "")).lower()
                  .startswith("consolidated")) else 1,
            -_date_key(r.get("toDate", "")),
        ),
    )
    for rec in ordered[:4]:
        url = rec.get("xbrl")
        if not url:
            continue
        try:
            nums = _parse_xbrl_numbers(nse.get(url, tries=2, timeout=60).text,
                                       RESULT_TAGS)
        except Exception:
            continue
        if not nums:
            continue
        pat = nums.get("pat") or nums.get("pat_continuing")
        return {
            "symbol": symbol,
            "from_date": rec.get("fromDate"),
            "to_date": rec.get("toDate"),
            "consolidated": rec.get("consolidated"),
            "revenue": round(nums["revenue"] / CRORE, 1) if "revenue" in nums else None,
            "pat": round(pat / CRORE, 1) if pat else None,
            "pbt": round(nums["pbt"] / CRORE, 1) if "pbt" in nums else None,
            "finance_costs": (round(nums["finance_costs"] / CRORE, 1)
                              if "finance_costs" in nums else None),
        }
    return {}


# Bank filings use an entirely different Ind-AS schedule (Schedule III Part A
# for banking companies) with its own tags.
LENDER_TAGS = {
    "InterestEarned": "interest_earned",
    "InterestExpended": "interest_expended",
    "OperatingProfitBeforeProvisionAndContingencies": "ppop",
    "ProvisionsOtherThanTaxAndContingencies": "provisions",
    "Income": "income",
    "OtherIncome": "other_income",
    "ExpenditureExcludingProvisionsAndContingencies": "opex",
    "PercentageOfGrossNpa": "gnpa_pct",
    "PercentageOfNpa": "nnpa_pct",
    "GrossNonPerformingAssets": "gnpa_abs",
    "NonPerformingAssets": "nnpa_abs",
    "ReturnOnAssets": "roa_pct",
    "ProfitLossAfterTaxesMinorityInterestAndShareOfProfitLossOfAssociates": "pat",
}


def lender_metrics(symbol: str) -> dict:
    """Bank / NBFC metrics from the STANDALONE quarterly filing.

    Standalone, deliberately, and this is the opposite of the rule for ordinary
    companies. Banks report asset quality -- GNPA, NNPA, ROA -- only in the
    standalone statement; the consolidated filing carries those same tags with
    a value of 0.00. Reading the consolidated one gives a bank with apparently
    zero bad loans, which is the most flattering possible error.
    """
    rows = nse.get(RESULTS_URL, params={"index": "equities", "symbol": symbol,
                                        "period": "Quarterly"}).json()
    if not rows:
        return {}
    standalone = [r for r in rows
                  if "non-consolidated" in str(r.get("consolidated", "")).lower()
                  or "standalone" in str(r.get("consolidated", "")).lower()]
    ordered = sorted(standalone or rows, key=lambda r: -_date_key(r.get("toDate", "")))

    for rec in ordered[:4]:
        url = rec.get("xbrl")
        if not url:
            continue
        try:
            nums = _parse_xbrl_numbers(nse.get(url, tries=2, timeout=60).text,
                                       LENDER_TAGS)
        except Exception:
            continue
        # A filing where every asset-quality figure is zero is the consolidated
        # schedule in disguise -- skip it rather than record a perfect bank.
        if not nums or not nums.get("gnpa_pct"):
            continue

        out: dict = {"symbol": symbol, "to_date": rec.get("toDate"),
                     "basis": rec.get("consolidated")}
        # Ratios are filed as fractions: 0.0136 means 1.36%.
        for key in ("gnpa_pct", "nnpa_pct", "roa_pct"):
            if nums.get(key) is not None:
                out[key] = round(nums[key] * 100, 3)
        # ROA is filed inconsistently: HDFC Bank reports the quarter's figure
        # (0.47%), while ICICI and PNB report an already-annualised one. Blindly
        # multiplying by four gave ICICI a 9.4% ROA, which no bank on earth
        # earns. Indian bank ROA tops out near 2.5%, so annualise only when the
        # result stays inside the range of the physically possible.
        raw = out.get("roa_pct")
        if raw is not None:
            annualised = raw * 4
            out["roa_pct"] = round(annualised if annualised <= 3.0 else raw, 3)
            out["roa_basis"] = "annualised x4" if annualised <= 3.0 else "as filed"

        ie, ix = nums.get("interest_earned"), nums.get("interest_expended")
        if ie and ix:
            out["nii"] = round((ie - ix) / CRORE, 1)
        # Cost-to-income for a bank is operating expenses over NET income
        # (NII + other income). Using total expenditure over total income puts
        # interest expense in the numerator and interest earned in the
        # denominator, which reported HDFC Bank at 71% against a real ~40% and
        # scored every large Indian bank as maximally inefficient.
        inc, opex = nums.get("income"), nums.get("opex")
        other = nums.get("other_income")
        if inc and opex and ie and ix:
            operating_expense = opex - ix          # strip interest expense
            net_income = (ie - ix) + (other or max(inc - ie, 0))
            if net_income > 0 and operating_expense > 0:
                out["cost_income_pct"] = round(100 * operating_expense / net_income, 2)
        ppop, prov = nums.get("ppop"), nums.get("provisions")
        if ppop and prov is not None and ppop > 0:
            out["credit_cost_pct"] = round(100 * prov / ppop, 2)
        g, n = nums.get("gnpa_abs"), nums.get("nnpa_abs")
        if g and n is not None and g > 0:
            # Provision coverage implied by how much of gross NPA is written down.
            out["pcr_pct"] = round(100 * (g - n) / g, 2)
        if nums.get("pat"):
            out["pat"] = round(nums["pat"] / CRORE, 1)
        return out
    return {}


def _date_key(s: str) -> float:
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt).timestamp()
        except (ValueError, TypeError):
            continue
    return 0.0


def shareholding_history(symbol: str, limit: int = 8) -> list[dict]:
    """Promoter holding by quarter, newest first.

    The *level* matters less than the *change*: promoters quietly reducing
    their stake over several quarters is a signal no single snapshot shows.
    """
    rows = nse.get(SHP_URL, params={"index": "equities", "symbol": symbol,
                                    "type": "promoter"}).json()
    if not isinstance(rows, list):
        return []
    out = []
    for r in rows[:limit]:
        try:
            promoter = float(str(r.get("pr_and_prgrp") or
                                 r.get("promoter_val") or "").strip() or "nan")
        except ValueError:
            promoter = float("nan")
        out.append({"date": r.get("date"), "promoter_pct": promoter,
                    "xbrl": r.get("xbrl")})
    return out


PLEDGE_FLAG_TAGS = (
    "WhetherAnySharesHeldByPromotersAreEncumberedUnderPledgedForPromoterAndPromoterGroup",
    "WhetherAnySharesHeldByPromotersAreEncumberedUnderPledged",
)
# Filings repeat these per promoter entity and then once for the whole promoter
# group. The group total is the LAST occurrence, which is the figure that gets
# quoted publicly.
PLEDGED_SHARES_TAG = "NumberOfSharesEncumberedUnderPledged"
PROMOTER_SHARES_TAG = "NumberOfEquitySharesHeldInDematerializedForm"


def pledge_from_shp(xbrl_url: str, promoter_pct: float | None = None) -> dict:
    """Promoter pledge from a shareholding-pattern filing.

    Reported the way India quotes it: pledged shares as a percentage of
    PROMOTER holding, not of total shares outstanding. The two differ by
    roughly the promoter stake, so quoting the wrong one badly understates
    pledge risk on a closely held company.

    Extraction is deliberately not positional. A single filing repeats these
    tags across ~240 contexts -- once per promoter entity, once for the group,
    then padded with zeros -- so "first" and "last" both pick the wrong number
    (taking the last gave 0 on a company that genuinely has a pledge). Instead:

        pledged shares  = the largest reported figure, which is the group total
                          because individual entities sum to it
        promoter shares = total shares x promoter %, using the promoter
                          percentage from the JSON API, which is unambiguous

    Returns {"pledged": bool|None, "pledge_pct": float|None,
             "pledged_shares": int|None}. An explicit "no encumbrance"
    declaration gives a confident 0.0; an unreadable filing gives None --
    "no pledge" and "we don't know" must never merge.
    """
    text = nse.get(xbrl_url, tries=2, timeout=90).text
    root = ET.fromstring(text.encode())

    flag = None
    pledged_vals: list[float] = []
    share_vals: list[float] = []
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag in PLEDGE_FLAG_TAGS and flag is None:
            flag = _text(el).lower() == "true"
        elif tag == PLEDGED_SHARES_TAG:
            try:
                pledged_vals.append(float(_text(el)))
            except (TypeError, ValueError):
                pass
        elif tag == PROMOTER_SHARES_TAG:
            try:
                share_vals.append(float(_text(el)))
            except (TypeError, ValueError):
                pass

    if flag is False:
        return {"pledged": False, "pledge_pct": 0.0, "pledged_shares": 0}
    if flag is not True:
        return {"pledged": None, "pledge_pct": None, "pledged_shares": None}

    pledged = max(pledged_vals) if pledged_vals else None
    total_shares = max(share_vals) if share_vals else None
    pct = None
    if pledged and total_shares and promoter_pct and promoter_pct > 0:
        promoter_shares = total_shares * promoter_pct / 100
        if promoter_shares > 0:
            pct = round(100 * pledged / promoter_shares, 3)
    return {"pledged": True, "pledge_pct": pct,
            "pledged_shares": int(pledged) if pledged else None}


def pledge_for(symbol: str) -> dict:
    """Convenience: latest shareholding filing plus its pledge figure."""
    hist = shareholding_history(symbol, 1)
    if not hist or not hist[0].get("xbrl"):
        return {"pledged": None, "pledge_pct": None, "pledged_shares": None}
    pp = hist[0].get("promoter_pct")
    pp = None if pp != pp else pp                     # NaN -> None
    out = pledge_from_shp(hist[0]["xbrl"], pp)
    out["as_of"] = hist[0].get("date")
    out["promoter_pct"] = pp
    return out
