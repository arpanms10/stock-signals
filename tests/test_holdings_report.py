"""The holdings report renders every holding from the pack, deterministically."""
from __future__ import annotations

import copy

from book import holdings_report as hr


def holding(sym, weight, score=80.0, tier="core_candidate", label="Core candidate", **kw):
    h = {"symbol": sym, "name": f"{sym} Ltd", "sector": "Information Technology",
         "cap_band": "large", "data_freshness": "fresh",
         "position": {"quantity": 10.0, "avg_cost": 100.0, "price": 120.0,
                      "price_date": "2026-11-20", "market_value": 1200.0,
                      "weight_pct": weight, "pnl_pct": 20.0, "bucket": None},
         "scorecard": {"model": "IT services", "score": score, "coverage_pct": 75.0,
                       "tier": tier, "tier_label": label, "role_suggested": "core",
                       "role_reasons": ["meets every core condition"], "flags": []},
         "valuation": {"headline": "pe", "current": {"pe": 20.0},
                       "band": {"percentile": 0.02, "months": 60},
                       "peers": {"relative": 0.8, "n": 5},
                       "reverse": {"implied_pct": 8.0, "delivered_pct": 10.0,
                                   "delivered_basis": "5-year revenue CAGR"},
                       "scenarios": {"bear": {"fair_value": 80.0},
                                     "base": {"fair_value": 130.0},
                                     "bull": {"fair_value": 160.0}}},
         "metrics": {"pat:ttm": {"value": 500.0}, "roe_pct:fy": {"value": 30.0},
                     "promoter_pct:instant": {"value": 70.0},
                     "promoter_change_pct:instant": {"value": 0.0},
                     "pledge_pct:instant": {"value": 0.0}},
         "quarters": {"pat": {"2025-09-30": {"value": 100.0}, "2026-09-30": {"value": 70.0}},
                      "revenue": {"2025-09-30": {"value": 1000.0},
                                  "2026-09-30": {"value": 1100.0}}},
         "risk": {"themes": ["export"], "flags": [], "liquidity_days": 0.01},
         "plan": {"cap_pct": 8.0, "no_new_money": []},
         "tax": {"short_term": 50.0, "long_term": 150.0, "lots_source": "matched"},
         "events": [], "changes": None}
    h.update(kw)
    return h


PACK = {
    "generated_at": "2026-11-21T16:00:00",
    "as_of": {"today": "2026-11-21", "prices": "2026-11-20"},
    "portfolio": {"total_value": 2500.0, "operating_equity": 2400.0, "buckets_blank": 2,
                  "concentration": {"effective_names": 2.0}},
    "holdings": {
        "AAA": holding("AAA", 40.0, events=[{
            "date": "2026-11-06", "severity": "thesis_check", "status": "open",
            "title": "Top management change", "detail": "Cessation of CEO",
            "link": "https://nse/x.pdf"}]),
        "BBB": holding("BBB", 56.0, score=40.0, tier=None, label="—",
                       scorecard={"model": "company", "score": 40.0, "coverage_pct": 20.0,
                                  "tier": None, "role_suggested": "n/a",
                                  "flags": [{"text": "Promoter pledge 30.0%",
                                             "severity": "severe"}]}),
    },
    "plan": {"your_buckets": {"rows": {}, "buckets": [
                 {"role": "core", "before_pct": 0.0}, {"role": "satellite", "before_pct": 100.0}]},
             "suggested_roles": {"rows": {"AAA": {"trim": 300.0, "trim_tax_estimate": 12.0},
                                          "BBB": {"buy": 0.0, "trim": 0.0}},
                                 "buckets": [{"role": "core", "before_pct": 40.0},
                                             {"role": "satellite", "before_pct": 60.0}]}},
    "risk": {"history": {"from": "2023-11-21", "to": "2026-11-20", "return_ann_pct": 5.0,
                         "volatility_pct": 14.0, "max_drawdown_pct": -20.0, "beta": 0.9},
             "flags": [{"label": "Governance flags", "pct": 56.0, "holdings": ["BBB"]}]},
    "tax": {"this_year": {"fy": "2026-27", "st": 1000.0, "lt": 0.0, "tax": 208.0,
                          "exemption_left": 125000.0},
            "incomplete_sales": [{"symbol": "ZZZ", "value": 5000.0}],
            "harvest": {"gains": [{"symbol": "AAA", "qty": 10, "gain": 5000.0,
                                   "future_tax_saved": 650.0, "round_trip_cost": 20.0}]}},
    "review": {"latest": "2026-11-21", "previous": None, "changes": [],
               "open_thesis_checks": [{}]},
}


def test_every_holding_and_section_is_rendered():
    t = hr.render(PACK)
    for s in ("## The book", "## Holdings at a glance", "## Each holding", "### AAA",
              "### BBB", "## Tax this year", "## Monitoring"):
        assert s in t
    assert t.index("| BBB |") < t.index("| AAA |")             # largest weight first
    assert "trim ₹300 (est. tax ₹12)" in t
    assert "2nd percentile of its own 5-year range" in t
    assert "PAT -30.0% YoY, revenue +10.0% YoY" in t
    assert "Thesis check 2026-11-06" in t and "[filing](https://nse/x.pdf)" in t
    assert "Promoter pledge 30.0% (severe)" in t
    assert "no tier (too little assessable)" in t
    assert "1 sales with unknown cost (ZZZ" in t
    assert "not backtested" in t and "not targets" in t and "places an order" in t


def test_same_pack_same_report_and_input_untouched():
    before = copy.deepcopy(PACK)
    assert hr.render(PACK) == hr.render(PACK)
    assert PACK == before


def test_ordinals_and_money():
    assert [hr._ord(x) for x in (0.01, 0.02, 0.03, 0.11, 0.22, None)] == \
        ["1st", "2nd", "3rd", "11th", "22nd", "—"]
    assert hr._r(1.25e5) == "₹1.25 lakh" and hr._r(-2.5e7) == "−₹2.50 cr"
