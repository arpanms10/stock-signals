"""Quality score correctness."""
import pandas as pd
import pytest

import fundamentals as fu


def sheet():
    return pd.DataFrame([
        {"symbol": "GOOD", "quarter": "FY26Q1", "revenue": 1000, "pat": 100,
         "cfo": 120, "roe": 22, "roce": 26, "debt_equity": 0.2,
         "interest_cover": 15, "promoter_pct": 55, "pledge_pct": 0, "pe": 30},
        {"symbol": "BAD", "quarter": "FY25Q4", "revenue": 900, "pat": 80,
         "cfo": 30, "roe": 9, "roce": 11, "debt_equity": 2.4,
         "interest_cover": 1.4, "promoter_pct": 40, "pledge_pct": 18, "pe": 40},
        {"symbol": "BAD", "quarter": "FY26Q1", "revenue": 910, "pat": 70,
         "cfo": 20, "roe": 8, "roce": 10, "debt_equity": 2.6,
         "interest_cover": 1.2, "promoter_pct": 40, "pledge_pct": 31, "pe": 45},
        {"symbol": "SPARSE", "quarter": "FY26Q1", "roe": 20},
    ])


def test_good_business_outscores_bad():
    d = sheet()
    assert fu.score_symbol(d, "GOOD").score > 80
    assert fu.score_symbol(d, "BAD").score < 20


def test_cash_quality_flag_fires_on_low_cfo():
    flags = fu.score_symbol(sheet(), "BAD").flags
    assert any("CFO/PAT" in f for f in flags)


def test_rising_pledge_is_flagged():
    """The loudest pre-collapse signal in Indian smallcaps -- must be caught."""
    flags = fu.score_symbol(sheet(), "BAD").flags
    assert any("pledge RISING" in f for f in flags)
    assert not any("pledge RISING" in f for f in fu.score_symbol(sheet(), "GOOD").flags)


def test_leverage_flags():
    flags = fu.score_symbol(sheet(), "BAD").flags
    assert any("interest cover" in f for f in flags)
    assert any("debt/equity" in f for f in flags)


def test_missing_data_is_dropped_not_scored_as_zero():
    """A stock with only ROE on file must not be punished for the blanks."""
    q = fu.score_symbol(sheet(), "SPARSE")
    assert q.score is not None and q.score > 50
    assert q.components["balance sheet"] is None


def test_unknown_symbol_has_no_score():
    q = fu.score_symbol(sheet(), "NOTTHERE")
    assert q.score is None
    assert "no fundamentals" in q.explain()


def test_lenders_get_the_lender_rubric_not_the_general_one():
    """Superseded behaviour: lenders used to be suppressed entirely. They are
    now scored on NPA, coverage, ROA and efficiency instead -- see
    tests/test_lenders.py. What must NOT happen is a lender being scored on
    debt/equity and CFO, which invert for a business that borrows to lend."""
    q = fu.score_symbol(sheet(), "GOOD", sector="Financial Services")
    assert q.score is not None
    assert "balance sheet" not in q.components
    assert "cash quality" not in q.components
    assert any("lender" in f for f in q.flags)


def test_gate_behaviour_on_unknown():
    unknown = fu.score_symbol(sheet(), "NOTTHERE")
    assert fu.passes_gate(unknown, 50, allow_unknown=True) is True
    assert fu.passes_gate(unknown, 50, allow_unknown=False) is False
    assert fu.passes_gate(fu.score_symbol(sheet(), "BAD"), 50) is False


def test_template_roundtrip(tmp_path):
    p = fu.write_template(["RELIANCE", "TCS"], tmp_path / "f.csv")
    df = fu.load(p)
    assert sorted(df["symbol"]) == ["RELIANCE", "TCS"]
    assert fu.score_symbol(df, "RELIANCE").score is None


def test_company_with_no_promoter_is_not_punished():
    """HDFC Bank and Crompton have no identifiable promoter by design.

    Scoring a 0% promoter stake as if it were an absentee founder penalises
    exactly the ownership structure that needs no promoter scrutiny.
    """
    d = pd.DataFrame([
        {"symbol": "PROF", "quarter": "FY26Q1", "roe": 20, "roce": 22,
         "debt_equity": 0.2, "interest_cover": 20, "promoter_pct": 0.15,
         "pledge_pct": 0, "cfo": 120, "pat": 100},
        {"symbol": "FOUNDER", "quarter": "FY26Q1", "roe": 20, "roce": 22,
         "debt_equity": 0.2, "interest_cover": 20, "promoter_pct": 55,
         "pledge_pct": 0, "cfo": 120, "pat": 100},
    ])
    prof, founder = fu.score_symbol(d, "PROF"), fu.score_symbol(d, "FOUNDER")
    assert any("no identifiable promoter" in f for f in prof.flags)
    # Same business quality: the no-promoter firm must not score materially worse.
    assert prof.score == pytest.approx(founder.score, abs=6)


def test_low_promoter_stake_above_threshold_is_still_scored():
    d = pd.DataFrame([{"symbol": "LOW", "quarter": "FY26Q1", "roe": 20,
                       "promoter_pct": 20, "pledge_pct": 0}])
    q = fu.score_symbol(d, "LOW")
    assert not any("no identifiable promoter" in f for f in q.flags)


def test_cfo_pat_refuses_to_mix_quarterly_profit_with_annual_cash():
    """NSE files quarterly, Yahoo's cash flow is annual.

    Pairing them reports a ~4x inflated CFO/PAT as excellent cash conversion --
    exactly backwards from the signal this field exists to give.
    """
    d = pd.DataFrame([
        {"symbol": "Q", "quarter": "FY26Q1", "period": "quarterly",
         "pat": 12444, "cfo": 52094, "roe": 40},
        {"symbol": "A", "quarter": "FY26", "period": "annual",
         "pat": 49799, "cfo": 52094, "roe": 40},
    ])
    q = fu.score_symbol(d, "Q")
    assert q.components["cash quality"] is None
    assert any("quarterly" in f and "4x" in f for f in q.flags)

    a = fu.score_symbol(d, "A")
    assert a.components["cash quality"] is not None      # 1.05 -- healthy
    assert not any("4x" in f for f in a.flags)


def test_missing_period_does_not_score_cash_quality():
    """Absent basis means unknown basis; refuse rather than assume annual."""
    d = pd.DataFrame([{"symbol": "X", "quarter": "FY26Q1", "pat": 100,
                       "cfo": 120, "roe": 20}])
    assert fu.score_symbol(d, "X").components["cash quality"] is None


def test_pledge_severity_is_graded():
    """0.08% is housekeeping; 25% is a solvency question about the promoter."""
    d = pd.DataFrame([
        {"symbol": "TINY", "quarter": "FY26Q1", "pledge_pct": 0.08, "roe": 20},
        {"symbol": "BIG", "quarter": "FY26Q1", "pledge_pct": 31.0, "roe": 20},
        {"symbol": "MID", "quarter": "FY26Q1", "pledge_pct": 8.0, "roe": 20},
    ])
    assert any("small" in f for f in fu.score_symbol(d, "TINY").flags)
    assert any("HIGH" in f and "Forced selling" in f
               for f in fu.score_symbol(d, "BIG").flags)
    assert any("worth watching" in f for f in fu.score_symbol(d, "MID").flags)


def test_falling_promoter_stake_is_flagged():
    d = pd.DataFrame([
        {"symbol": "X", "quarter": "FY25Q4", "promoter_pct": 60.0, "roe": 20},
        {"symbol": "X", "quarter": "FY26Q1", "promoter_pct": 55.0, "roe": 20},
    ])
    assert any("promoter holding fell" in f for f in fu.score_symbol(d, "X").flags)


def test_stable_promoter_stake_is_not_flagged():
    d = pd.DataFrame([
        {"symbol": "X", "quarter": "FY25Q4", "promoter_pct": 60.0, "roe": 20},
        {"symbol": "X", "quarter": "FY26Q1", "promoter_pct": 59.8, "roe": 20},
    ])
    assert not any("promoter holding fell" in f for f in fu.score_symbol(d, "X").flags)


def test_annual_pat_column_enables_cash_quality_on_quarterly_rows():
    """NSE gives quarterly profit; Yahoo gives annual cash flow. Carrying an
    explicit annual profit lets the ratio be computed correctly instead of
    being skipped or, worse, computed 4x wrong."""
    d = pd.DataFrame([{"symbol": "X", "quarter": "FY26Q1", "period": "quarterly",
                       "pat": 12444, "pat_annual": 49799, "cfo": 52094,
                       "roe": 40}])
    q = fu.score_symbol(d, "X")
    assert q.components["cash quality"] is not None
    assert not any("4x" in f for f in q.flags)


def test_quarterly_row_without_annual_pat_still_refuses():
    d = pd.DataFrame([{"symbol": "X", "quarter": "FY26Q1", "period": "quarterly",
                       "pat": 12444, "cfo": 52094, "roe": 40}])
    q = fu.score_symbol(d, "X")
    assert q.components["cash quality"] is None
    assert any("4x" in f for f in q.flags)
