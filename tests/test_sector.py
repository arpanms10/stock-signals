"""Sector checklist: the right group, the right checks, honest gaps."""
from analysis import fundamental as fa
from analysis import sector as sec


def test_group_prefers_yahoo_industry_then_nse_sector():
    assert sec.group_of("Financial Services", "Financial Services", "Banks - Regional") == "bank"
    assert sec.group_of("Financial Services", "Financial Services", "Credit Services") == "nbfc"
    assert sec.group_of("Financial Services", "Financial Services", "Insurance - Life") == "insurance"
    assert sec.group_of("Construction Materials", "Basic Materials", "") == "cement"
    assert sec.group_of("", "Technology", "Information Technology Services") == "it"
    assert sec.group_of("Fast Moving Consumer Goods") == "fmcg"
    assert sec.group_of("", "", "", kind="bank") == "bank"
    assert sec.group_of("Diversified", "", "") == "other"


def test_cyclicals_are_valued_on_ev_ebitda_and_leverage_is_read():
    info = {"enterpriseToEbitda": 11.0, "ebitda": 1000e7, "totalDebt": 3500e7,
            "totalCash": 500e7, "ebitdaMargins": 0.18}
    cl = sec.checklist("cement", {"roce": 9.0}, info)
    by = {k.label: k for k in cl.checks}
    assert "EV / EBITDA" in cl.value_on
    assert by["Net debt / EBITDA"].value == "3.00×" and by["Net debt / EBITDA"].reading == "watch"
    assert by["Return on capital"].reading == "weak"
    assert by["EV / EBITDA"].reading is None           # a multiple, no verdict
    assert cl.manual and not cl.missing


def test_missing_figures_are_listed_not_guessed():
    cl = sec.checklist("it", {}, {"operatingMargins": 0.24})
    assert [k.label for k in cl.checks] == ["Operating margin"]
    assert cl.checks[0].reading == "good"
    assert "Free cash flow / profit" in cl.missing


def test_bank_checks_use_filing_fields_and_book_value():
    cl = sec.checklist("bank", {"gnpa_pct": 1.2, "pcr_pct": 75, "roa_pct": 1.9,
                                "car_pct": 18, "casa_pct": 38}, {"priceToBook": 2.6})
    by = {k.label: k.reading for k in cl.checks}
    assert by["Gross NPA"] == "good" and by["CASA share of deposits"] == "watch"
    assert cl.value_on == "price / book"


def test_report_carries_the_checklist():
    rows = [{"symbol": "X", "quarter": "FY26Q1", "period": "quarterly", "revenue": 100,
             "pat": 20, "pat_annual": 80, "cfo": 75, "roe": 30, "roce": 35,
             "debt_equity": 0.1, "interest_cover": 40, "promoter_pct": 60,
             "pledge_pct": 0}]
    data = {"symbol": "X", "rows": rows, "fetched_at": "2026-10-19T00:00:00",
            "info": {"sector": "Technology", "industry": "Information Technology Services",
                     "operatingMargins": 0.21}}
    r = fa.analyse(data, "Information Technology")
    assert r.sector_checks.group == "it"
    assert r.sector_checks.checks[0].label == "Operating margin"
