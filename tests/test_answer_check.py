"""Phase 7: the research answer checker."""
from __future__ import annotations

import copy

from strategy import answer_check as ac

PACK = {
    "generated_at": "2026-11-21T10:00:00",
    "holdings": {
        "AAA": {"data_freshness": "fresh",
                "scorecard": {"score": 87.3, "tier": "high_conviction_core", "flags": []},
                "metrics": {"pat:ttm": {"value": 51608.0, "freshness": "fresh"},
                            "roe_pct:fy": {"value": 50.63, "freshness": "stale"}},
                "quarters": {"pat": {"2026-06-30": {"value": 13349.0},
                                     "2026-09-30": {"value": 13884.0}}},
                "position": {"weight_pct": 11.6096}},
        "BBB": {"scorecard": {"score": 40.0, "flags": [{"text": "auditor resigned"}]},
                "position": {"weight_pct": 3.2}},
    },
}


def A(findings, **kw):
    a = {"question": "What is it?", "pack_generated_at": PACK["generated_at"], "status": "answered",
         "summary": "See findings.", "findings": findings, "not_advice": True}
    a.update(kw)
    return a


def F(claim, kind, *ev):
    return {"claim": claim, "kind": kind, "evidence": list(ev)}


def E(path, value):
    return {"path": path, "value": value}


def test_good_answer_passes_with_rounding():
    r = ac.check(A([F("AAA is 11.6% of the book, score 87.3.", "calculated",
                      E("/holdings/AAA/position/weight_pct", 11.61),
                      E("/holdings/AAA/scorecard/score", 87.3))]), PACK)
    assert r["ok"], r["errors"]


def test_wrong_value_and_missing_path_fail():
    r = ac.check(A([F("Score 91.", "calculated", E("/holdings/AAA/scorecard/score", 91)),
                    F("Fair value 4,200.", "estimated", E("/holdings/AAA/fair", 4200))]), PACK)
    assert not r["ok"]
    assert any("cited as 91" in e for e in r["errors"])
    assert any("not in the pack" in e for e in r["errors"])


def test_uncited_number_in_claim_or_summary_fails():
    r = ac.check(A([F("PAT ₹51,608 cr, ROE 45%.", "reported",
                      E("/holdings/AAA/metrics/pat:ttm/value", 51608.0))],
                   summary="ROE 45%."), PACK)
    errs = " ".join(r["errors"])
    assert "figure 45 in the claim" in errs and "summary" in errs


def test_derived_figure_is_recomputed():
    ok = ac.check(A([F("Quarterly PAT rose 4.0%.", "calculated",
                       {"derived": {"op": "pct_change",
                                    "of": ["/holdings/AAA/quarters/pat/2026-09-30/value",
                                           "/holdings/AAA/quarters/pat/2026-06-30/value"]},
                        "value": 4.0})]), PACK)
    assert ok["ok"], ok["errors"]
    bad = ac.check(A([F("Quarterly PAT rose 9%.", "calculated",
                        {"derived": {"op": "pct_change",
                                     "of": ["/holdings/AAA/quarters/pat/2026-09-30/value",
                                            "/holdings/AAA/quarters/pat/2026-06-30/value"]},
                         "value": 9})]), PACK)
    assert not bad["ok"]


def test_stale_figure_must_be_declared():
    f = [F("ROE 50.6% (stale).", "calculated", E("/holdings/AAA/metrics/roe_pct:fy/value", 50.63))]
    assert not ac.check(A(f), PACK)["ok"]
    assert ac.check(A(f, stale_data_used=["/holdings/AAA/metrics/roe_pct:fy"]), PACK)["ok"]


def test_governance_flags_must_be_named():
    f = [F("BBB scores 40.", "calculated", E("/holdings/BBB/scorecard/score", 40.0))]
    assert not ac.check(A(f), PACK)["ok"]
    assert ac.check(A(f, governance_flags=[{"symbol": "BBB", "flag": "auditor resigned"}]),
                    PACK)["ok"]


def test_opinion_has_no_figures_certainty_and_orders_rejected():
    r = ac.check(A([F("Cheap at 22x.", "opinion")],
                   summary="It will outperform; you should buy."), PACK)
    errs = " ".join(r["errors"])
    assert "opinion carries no figures" in errs
    assert "certainty" in errs and "order" in errs


def test_insufficient_data_needs_missing_and_schema_enforced():
    r = ac.check(A([], status="INSUFFICIENT DATA"), PACK)
    assert any("missing" in e for e in r["errors"])
    assert ac.check(A([], status="INSUFFICIENT DATA", missing=["deal wins"]), PACK)["ok"]
    bad = copy.deepcopy(A([]))
    del bad["not_advice"]
    assert any(e.startswith("schema") for e in ac.check(bad, PACK)["errors"])


def test_older_pack_flagged():
    r = ac.check(A([F("Score 87.3.", "calculated", E("/holdings/AAA/scorecard/score", 87.3))],
                   pack_generated_at="2026-11-01T00:00:00"), PACK)
    assert any("re-run against the current pack" in e for e in r["errors"])


def test_numbers_skip_years_counts_and_ratios():
    assert ac.numbers("Towards 70:30 over 3/6/12 months in FY 2026-27, 5 holdings") == []
    assert ac.numbers("₹1.25 lakh and 12.5%") == [125000.0, 12.5]
