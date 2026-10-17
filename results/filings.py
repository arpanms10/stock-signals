"""Quarterly result filings, read period by period.

nse_fundamentals reads the first occurrence of a tag, which is right for one
snapshot and wrong for a series: a filing carries the quarter AND the
year-to-date under the same tag, and the Q4 filing carries the full year.
Here every number is taken from a context that spans one quarter (80-100 days)
ending on the filing's quarter end, with no dimension -- so a September filing
yields July-September and nothing else.

What a filing does NOT carry is last year's quarter. NSE's XBRL has only the
current period and year-to-date, so a year-on-year comparison needs the filing
from four quarters back. `history()` fetches the last N quarters for that.

Parsed filings are cached on disk keyed by their URL: a filing at a given URL
never changes (a revision is published under a new one), so a quarter is
fetched once, ever.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import fundamentals as fu
from data.sources import nse_client as nse
from data.sources import nse_fundamentals as nsef

CACHE = Path(__file__).resolve().parents[1] / "data" / "cache" / "results"
CRORE = 1e7

# Tag -> field. One table for all three schedules: a tag that a schedule does
# not use is simply absent from its filings.
TAGS = {
    # Ordinary companies, and NBFCs (which file the Ind-AS Division III
    # schedule: same tags plus interest and impairment lines).
    "RevenueFromOperations": "revenue",
    "OtherIncome": "other_income",
    "EmployeeBenefitExpense": "employee",
    "FinanceCosts": "finance_costs",
    "DepreciationDepletionAndAmortisationExpense": "depreciation",
    "ExceptionalItemsBeforeTax": "exceptional",
    "ProfitBeforeTax": "pbt",
    "TaxExpense": "tax",
    "ProfitLossForPeriod": "pat",
    "ProfitLossForPeriodFromContinuingOperations": "pat_continuing",
    "ProfitOrLossAttributableToOwnersOfParent": "pat_owners",
    "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations": "eps",
    "InterestEarned": "interest_earned",
    "ImpairmentOnFinancialInstruments": "impairment",
    # Banks: Schedule III Part A, an entirely different set of tags.
    "InterestExpended": "interest_expended",
    "OperatingProfitBeforeProvisionAndContingencies": "ppop",
    "ProvisionsOtherThanTaxAndContingencies": "provisions",
    "ExceptionalItems": "exceptional_bank",
    "ProfitLossFromOrdinaryActivitiesBeforeTax": "pbt_bank",
    "ProfitLossForThePeriod": "pat_bank",
    "ProfitLossAfterTaxesMinorityInterestAndShareOfProfitLossOfAssociates":
        "pat_bank_owners",
    "BasicEarningsPerShareAfterExtraordinaryItems": "eps_bank",
    "PercentageOfGrossNpa": "gnpa_pct",
    "PercentageOfNpa": "nnpa_pct",
}
# Per-share and ratio fields; everything else is rupees, converted to crores.
UNSCALED = {"eps", "eps_bank", "gnpa_pct", "nnpa_pct"}


def _date(s: str | None) -> dt.date | None:
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(str(s).strip()[:11], fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def broadcast_time(s: str | None) -> dt.datetime | None:
    """'08-Oct-2026 22:07:33' -> datetime. None when absent or unparseable."""
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y"):
        try:
            return dt.datetime.strptime(str(s).strip(), fmt)
        except (ValueError, TypeError):
            continue
    return None


def parse_quarter(xml_text: str, quarter_end: dt.date) -> dict:
    """The quarter's numbers from one filing, in crores (ratios as filed).

    Only facts in a dimensionless context spanning one quarter and ending on
    `quarter_end` are read. Taking the first fact per tag otherwise would mix
    in year-to-date or full-year figures, which is the bug this module exists
    to avoid.
    """
    root = ET.fromstring(xml_text.encode())
    quarterly: set[str] = set()
    for el in root.iter():
        if not el.tag.endswith("}context"):
            continue
        start = end = None
        dims = False
        for sub in el.iter():
            name = sub.tag.split("}")[-1]
            if name == "startDate":
                start = _date(sub.text)
            elif name == "endDate":
                end = _date(sub.text)
            elif name == "explicitMember":
                dims = True
        if start and end and not dims and end == quarter_end \
                and 80 <= (end - start).days <= 100:
            quarterly.add(el.get("id"))

    out: dict[str, float] = {}
    for el in root.iter():
        field = TAGS.get(el.tag.split("}")[-1])
        if field is None or field in out or el.get("contextRef") not in quarterly:
            continue
        try:
            v = float((el.text or "").strip())
        except ValueError:
            continue
        out[field] = v if field in UNSCALED else v / CRORE
    return out


def kind_of(nums: dict) -> str:
    """bank | nbfc | company, from which schedule the filing used."""
    if nums.get("interest_expended") is not None and nums.get("ppop") is not None:
        return "bank"
    ie, rev = nums.get("interest_earned"), nums.get("revenue")
    if ie and rev and ie >= 0.5 * rev:
        return "nbfc"
    return "company"


def _cache_path(url: str) -> Path:
    return CACHE / f"{hashlib.sha1(url.encode()).hexdigest()[:20]}.json"


def fetch_quarter(url: str, quarter_end: dt.date) -> dict:
    """Parse one filing, from the disk cache when it has been read before."""
    path = _cache_path(url)
    if path.exists():
        return json.loads(path.read_text())
    nums = parse_quarter(nse.get(url, tries=2, timeout=60).text, quarter_end)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(nums))
    return nums


def _is_consolidated(rec: dict) -> bool:
    return str(rec.get("consolidated", "")).lower().startswith("consolidated")


def history(symbol: str, quarters: int = 8, filings: list[dict] | None = None,
            log=None) -> list[dict]:
    """The last `quarters` quarters for one symbol, newest first.

    One basis for the whole series -- a growth rate from a standalone quarter
    to a consolidated one measures the subsidiaries, not the business. The
    basis is consolidated when the latest quarter has it, except for banks:
    their asset-quality tags are zero in the consolidated filing, so banks are
    always read standalone. A quarter missing on the chosen basis is a gap,
    never filled from the other basis.

    Each row: to_date, quarter, basis, kind, broadcast, xbrl, plus the numbers.
    """
    rows = filings if filings is not None else nsef.result_filings(symbol)
    by_end: dict[dt.date, dict[bool, dict]] = {}
    for r in rows:
        end = _date(r.get("toDate"))
        if end and r.get("xbrl"):
            by_end.setdefault(end, {})[_is_consolidated(r)] = r
    if not by_end:
        return []
    ends = sorted(by_end, reverse=True)[:quarters]

    def read(rec: dict, end: dt.date) -> dict | None:
        try:
            return fetch_quarter(rec["xbrl"], end)
        except Exception as exc:          # one bad filing is a gap, not a failure
            if log:
                log(f"{symbol}: could not read {end} filing ({exc})")
            return None

    # The basis with the fuller series over the window, consolidated on a
    # tie. Choosing by the latest quarter alone gave ABB -- consolidated only
    # since late 2025 -- a series of five scattered quarters.
    n_cons = sum(True in by_end[e] for e in ends)
    n_sa = sum(False in by_end[e] for e in ends)
    consolidated = n_cons >= n_sa
    latest = by_end[ends[0]]
    probe = latest.get(consolidated) or latest.get(not consolidated)
    first = read(probe, ends[0]) if probe else None
    if first is not None and kind_of(first) == "bank" and n_sa:
        consolidated = False

    out = []
    for end in ends:
        rec = by_end[end].get(consolidated)
        if rec is None:
            continue
        nums = read(rec, end)             # the latest is a disk-cache hit
        if not nums:
            continue
        out.append({
            "symbol": symbol, "to_date": end, "quarter": fu.fiscal_quarter(end),
            "basis": "consolidated" if consolidated else "standalone",
            "kind": kind_of(nums),
            "broadcast": broadcast_time(rec.get("broadCastDate")),
            "xbrl": rec["xbrl"], **nums,
        })
    return out
