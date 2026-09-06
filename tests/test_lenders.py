"""Bank and NBFC scoring.

A lender is a different kind of business, not a company with odd accounting.
Debt is its raw material, so debt/equity says nothing; operating cash flow goes
deeply negative precisely when lending grows well; and ROE is flattered by
leverage. The ordinary rubric does not merely miss these -- it inverts them.
"""
import pandas as pd
import pytest

import fundamentals as fu


def bank_row(**kw):
    base = dict(symbol="B", quarter="FY26Q1", gnpa_pct=1.4, nnpa_pct=0.4,
                pcr_pct=72.0, roa_pct=1.9, cost_income_pct=42.0,
                credit_cost_pct=10.0, car_pct=18.0)
    base.update(kw)
    return base


def test_healthy_bank_outscores_a_troubled_one():
    d = pd.DataFrame([bank_row(symbol="GOOD"),
                      bank_row(symbol="WEAK", gnpa_pct=7.4, nnpa_pct=2.8,
                               pcr_pct=43.0, roa_pct=0.3,
                               cost_income_pct=74.0, credit_cost_pct=44.0,
                               car_pct=11.4)])
    assert fu.score_symbol(d, "GOOD").score > 85
    assert fu.score_symbol(d, "WEAK").score < 20


def test_lender_is_detected_from_its_data_not_only_its_sector():
    """A bank must score as a bank even if the sector map is missing."""
    d = pd.DataFrame([bank_row()])
    q = fu.score_symbol(d, "B", sector="")
    assert "asset quality" in q.components


def test_asset_quality_dominates():
    """Weight sits on whether the loans get repaid -- everything else is
    secondary for a lender."""
    d = pd.DataFrame([bank_row(symbol="CLEAN"),
                      bank_row(symbol="DIRTY", gnpa_pct=9.0, nnpa_pct=4.0)])
    assert (fu.score_symbol(d, "CLEAN").score
            - fu.score_symbol(d, "DIRTY").score) > 25


def test_high_npa_and_thin_coverage_are_flagged():
    d = pd.DataFrame([bank_row(gnpa_pct=6.8, nnpa_pct=2.5, pcr_pct=44.0)])
    flags = fu.score_symbol(d, "B").flags
    assert any("gross NPA 6.80%" in f and "high" in f for f in flags)
    assert any("net NPA" in f for f in flags)
    assert any("provision coverage" in f for f in flags)


def test_rising_npa_is_flagged():
    d = pd.DataFrame([bank_row(quarter="FY25Q4", gnpa_pct=2.0),
                      bank_row(quarter="FY26Q1", gnpa_pct=2.9)])
    assert any("gross NPA RISING" in f for f in fu.score_symbol(d, "B").flags)


def test_capital_near_the_regulatory_floor_is_flagged():
    d = pd.DataFrame([bank_row(car_pct=11.4)])
    assert any("regulatory floor" in f for f in fu.score_symbol(d, "B").flags)


def test_provision_writeback_is_neutral_not_good():
    """Negative credit cost flatters current profit; it is not evidence of
    quality, so it must not score better than genuinely low provisioning."""
    d = pd.DataFrame([bank_row(symbol="WB", credit_cost_pct=-5.0),
                      bank_row(symbol="LOW", credit_cost_pct=5.0)])
    assert fu.score_symbol(d, "WB").score <= fu.score_symbol(d, "LOW").score


def test_missing_capital_adequacy_does_not_sink_the_score():
    """CAR is not in NSE's XBRL. Absent data must be dropped, not scored zero."""
    d = pd.DataFrame([bank_row(car_pct=None)])
    q = fu.score_symbol(d, "B")
    assert q.score > 80
    assert q.components["capital"] is None
    assert any("not on file" in f for f in q.flags)


# ---------------------------------------------------------------- NBFCs

def test_nbfc_is_scored_without_debt_equity_or_cfo():
    """An NBFC files the ordinary schedule, so there is no NPA data -- but
    debt/equity and CFO still mislead and must stay excluded."""
    d = pd.DataFrame([{"symbol": "N", "quarter": "FY26Q1", "roe": 20.0,
                       "interest_cover": 2.5, "promoter_pct": 55.0,
                       "pledge_pct": 0.0, "debt_equity": 4.5, "cfo": -5000.0,
                       "pat": 1000.0}])
    q = fu.score_symbol(d, "N", sector="Financial Services")
    assert q.score is not None
    assert "balance sheet" not in q.components      # debt/equity excluded
    assert "cash quality" not in q.components       # CFO excluded
    assert any("do not mean for a lender" in f for f in q.flags)


def test_nbfc_high_leverage_is_not_punished():
    """4.5x debt/equity is normal for an NBFC and ruinous for a manufacturer.
    The same number must not produce the same verdict."""
    nbfc = pd.DataFrame([{"symbol": "N", "quarter": "FY26Q1", "roe": 20.0,
                          "interest_cover": 2.5, "promoter_pct": 55.0,
                          "pledge_pct": 0.0, "debt_equity": 4.5}])
    manufacturer = nbfc.copy()
    a = fu.score_symbol(nbfc, "N", sector="Financial Services")
    b = fu.score_symbol(manufacturer, "N", sector="Industrials")
    assert a.score > b.score
