"""The 100-point scorecard: scoring rules, coverage, sector models, overrides, role."""
import copy
import datetime as dt

import pytest

from book import scorecards as bs
from data import financials as fin
from strategy import scorecard as sc

TODAY = dt.date(2026, 11, 21)
CFG = sc.load_config()
YEARS = ["2021-03-31", "2022-03-31", "2023-03-31", "2024-03-31", "2025-03-31", "2026-03-31"]


def r(metric, period, end, value, source=fin.NSE, **kw):
    return fin.row("X", metric, period, end, value, "₹ cr", source, **kw)


def company(**over) -> list[dict]:
    """A steady compounder: growing revenue, high returns, no debt, cash-rich."""
    rows = []
    for i, y in enumerate(YEARS):
        rev = 1000 * 1.12 ** i
        rows += [r("revenue", "fy", y, rev), r("pat", "fy", y, rev * 0.15),
                 r("net_margin_pct", "fy", y, 15 + 0.2 * i, fin.CALC),
                 r("ebitda_margin_pct", "fy", y, 22 + (i % 2) * 0.5, fin.CALC),
                 r("roce_pct", "fy", y, 30, fin.CALC), r("roe_pct", "fy", y, 24, fin.CALC),
                 r("cfo", "fy", y, rev * 0.16), r("capex", "fy", y, rev * 0.03),
                 r("fcf", "fy", y, rev * 0.13), r("interest_cover", "fy", y, 40, fin.CALC)]
    end = YEARS[-1]
    rows += [r("revenue_cagr_3y_pct", "fy", end, 12, fin.CALC),
             r("revenue_cagr_5y_pct", "fy", end, 12, fin.CALC),
             r("pat_cagr_3y_pct", "fy", end, 13, fin.CALC),
             r("eps_cagr_3y_pct", "fy", end, 13, fin.CALC),
             r("debt_equity", "instant", "2026-09-30", 0.02, fin.CALC),
             r("net_debt", "instant", "2026-09-30", -200, fin.CALC),
             r("ebitda", "fy", end, 400, fin.CALC),
             r("promoter_change_pct", "instant", "2026-09-30", 0, fin.CALC),
             r("pledge_pct", "instant", "2026-09-30", 0, "NSE shareholding pattern"),
             r("avg_volume_20d", "point", "2026-11-20", 1_000_000, "x"),
             r("close_adjusted", "point", "2026-11-20", 2000, "x")]
    for q in ("2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"):
        rows += [r("pbt", "quarter", q, 100), r("exceptional", "quarter", q, 0)]
    drop = over.pop("drop", ())
    rows = [x for x in rows if x["metric"] not in drop]
    for m, v in over.items():
        rows = [x for x in rows if x["metric"] != m] + [r(m, "instant", "2026-09-30", v)]
    return rows


GOOD_RESEARCH = {"moat_points": "7", "moat_evidence": "No.1 share (industry body 2026)",
                 "governance_points": "6", "governance_evidence": "clean RPT note",
                 "as_of": "2026-11-01"}
GOOD_OUTLOOK = {"outlook_points": "7", "evidence": "industry +10% (body)", "as_of": "2026-11-01"}


FAIR_VALUE = {"method": "dcf", "band": {"metric": "pe", "percentile": 0.3, "months": 60},
              "peers": {"relative": 0.9, "n": 6, "industry": "x"},
              "margin_of_safety_pct": 10.0,
              "reverse": {"gap_pts": 0.0, "implied_pct": 10.0, "delivered_pct": 10.0}}


def card(rows=None, group="consumer", cap="large", research=None, outlook=None, bucket="",
         cfg=CFG, valuation=FAIR_VALUE):
    return sc.score("X", company() if rows is None else rows, group, cap, cfg,
                    research, outlook, bucket, TODAY, valuation=valuation)


def crit(c, key):
    return next(x for comp in c.components for x in comp.criteria if x.key == key)


# ------------------------------------------------------------------ config

def test_config_weights_sum_and_every_model_adds_up():
    assert sum(CFG["weights"].values()) == 100
    bad = copy.deepcopy(CFG)
    bad["weights"]["moat"] = 20
    with pytest.raises(ValueError):
        sc.validate_config(bad)
    bad = copy.deepcopy(CFG)
    bad["sector_models"]["bank"]["replace"]["returns"]["roa_latest"]["points"] = 9
    with pytest.raises(ValueError, match="bank / returns"):
        sc.validate_config(bad)


def test_tune_changes_thresholds_only_for_that_sector():
    it = sc.criteria_for(CFG, "it")["returns"]["roce_latest"]
    base = sc.criteria_for(CFG, "consumer")["returns"]["roce_latest"]
    assert it["zero"] == 15 and base["zero"] == 8 and it["points"] == base["points"]


# ------------------------------------------------------------------ scoring

def test_linear_both_directions_and_limits():
    assert sc.linear(15, 0, 15, 4) == 4 and sc.linear(-5, 0, 15, 4) == 0
    assert sc.linear(7.5, 0, 15, 4) == 2
    assert sc.linear(0.1, 1.5, 0.1, 4) == 4 and sc.linear(2.0, 1.5, 0.1, 4) == 0
    assert sc.linear(float("inf"), 2, 12, 3) == 3          # debt-free interest cover
    assert sc.linear(float("inf"), 3, 0, 3) == 0           # net debt / negative EBITDA


def test_strong_company_with_research_is_core():
    c = card(research=GOOD_RESEARCH, outlook=GOOD_OUTLOOK)
    assert c.coverage_pct == 100 and c.score >= 85
    assert c.tier == "high_conviction_core" and c.role == "core"
    assert crit(c, "val_own_band").status == sc.SCORED


def test_missing_data_is_not_assessed_not_zero():
    full = card(research=GOOD_RESEARCH, outlook=GOOD_OUTLOOK)
    less = card(company(drop=("interest_cover", "revenue_cagr_5y_pct")),
                research=GOOD_RESEARCH, outlook=GOOD_OUTLOOK)
    ic = crit(less, "revenue_cagr_5y")
    assert ic.status == sc.NOT_ASSESSED and ic.points is None
    assert less.available == full.available - 3
    assert less.score == pytest.approx(less.earned / less.available * 100, abs=0.1)


def test_debt_free_gets_full_interest_cover():
    c = card(company(drop=("interest_cover",)))
    ic = crit(c, "interest_cover")
    assert ic.status == sc.SCORED and ic.points == 3 and "debt-free" in ic.note


def test_loss_year_cagr_scores_zero_not_missing():
    rows = company(drop=("pat_cagr_3y_pct",))
    rows.append(r("pat_cagr_3y_pct", "fy", YEARS[-1], None, fin.CALC, status=fin.NA,
                  note="not meaningful across a loss year"))
    x = crit(card(rows), "pat_cagr_3y")
    assert x.status == sc.SCORED and x.points == 0


def test_coverage_floor_withholds_the_tier():
    rows = [x for x in company() if x["metric"] in ("revenue_cagr_3y_pct", "pledge_pct")]
    c = card(rows)
    assert c.score is not None and c.tier is None and "Not enough data" in c.tier_label
    assert c.role == "n/a"


def test_manual_points_need_evidence_and_are_clamped():
    c = card(research={"moat_points": "8"})
    assert crit(c, "moat_evidence").status == sc.NOT_ASSESSED
    assert "without evidence" in crit(c, "moat_evidence").note
    c = card(research={"moat_points": "12", "moat_evidence": "x"})
    assert crit(c, "moat_evidence").points == 8
    old = dict(GOOD_RESEARCH, as_of="2024-01-01")
    assert "over a year old" in crit(card(research=old), "moat_evidence").note


# ------------------------------------------------------------------ overrides

def test_governance_flag_caps_tier_but_not_score():
    clean = card(research=GOOD_RESEARCH, outlook=GOOD_OUTLOOK)
    flagged = card(research=dict(GOOD_RESEARCH, auditor_resigned="yes"), outlook=GOOD_OUTLOOK)
    assert flagged.score == clean.score
    assert flagged.raw_tier == "high_conviction_core" and flagged.tier == "watchlist"
    assert "capped from" in flagged.tier_label and flagged.role == "satellite"


def test_pledge_and_promoter_selling_flags():
    sev = card(company(pledge_pct=30))
    assert any(f.severity == sc.SEVERE and "pledge" in f.text.lower() for f in sev.flags)
    mod = card(company(pledge_pct=12, promoter_change_pct=-6))
    assert {f.severity for f in mod.flags} == {sc.MODERATE}
    assert len(mod.flags) == 2


def test_cap_tier_keeps_lower_tiers():
    flags = [sc.Flag(sc.MODERATE, "x", "y")]
    assert sc.cap_tier("core_candidate", flags, CFG) == "satellite_candidate"
    assert sc.cap_tier("exit_candidate", flags, CFG) == "exit_candidate"
    assert sc.cap_tier(None, flags, CFG) is None


# ------------------------------------------------------------------ sectors

def bank_rows():
    rows = []
    for i, y in enumerate(YEARS):
        rows += [r("revenue", "fy", y, 1000 * 1.15 ** i), r("pat", "fy", y, 180 * 1.15 ** i),
                 r("roe_pct", "fy", y, 16, fin.CALC), r("net_margin_pct", "fy", y, 18, fin.CALC),
                 r("assets", "instant", y, 12000 * 1.15 ** i),
                 r("cfo", "fy", y, -500)]                        # meaningless for a bank
    rows += [r("gnpa_pct", "quarter", "2026-09-30", 3.0), r("nnpa_pct", "quarter", "2026-09-30", 0.3),
             r("advances", "instant", "2026-09-30", 8000)]
    rows += [r("provisions", "quarter", q, 10) for q in
             ("2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30")]
    return rows


def test_bank_model_swaps_leverage_and_drops_cash_flow():
    c = card(bank_rows(), group="bank")
    cf = next(x for x in c.components if x.key == "cash_flow")
    assert cf.criteria[0].status == sc.NA and cf.available == 0
    assert c.applicable == 100 - 10             # cash flow n/a
    assert crit(c, "gnpa").points == pytest.approx(4 * (6 - 3.0) / (6 - 1.5), abs=0.01)
    assert crit(c, "credit_cost").value == pytest.approx(0.5)
    assert crit(c, "roa_latest").status == sc.SCORED
    assert not any(x.key == "debt_equity" for comp in c.components for x in comp.criteria)


def test_company_kind_from_filings():
    assert bs.company_kind(bank_rows()) == "bank"
    nbfc = [r("roce_pct", "fy", "2026-03-31", None, fin.CALC, status=fin.NA,
              note="not meaningful for a lender")]
    assert bs.company_kind(nbfc) == "nbfc"
    assert bs.company_kind(company()) == "company"


# ------------------------------------------------------------------ role

def test_role_satellite_reasons_and_categories():
    small = card(cap="small", research=GOOD_RESEARCH, outlook=GOOD_OUTLOOK)
    assert small.role == "satellite" and small.satellite_category == "smallcap_growth"
    assert "small cap" in small.role_reasons
    cyc = card(group="cement", cap="small", research=GOOD_RESEARCH, outlook=GOOD_OUTLOOK)
    assert cyc.satellite_category == "cyclical"
    own = card(cap="mid", research=dict(GOOD_RESEARCH, satellite_category="emerging",
                                        moat_points="1"))
    if own.role == "satellite":
        assert own.satellite_category == "emerging"
    illiquid = card(company(avg_volume_20d=100), research=GOOD_RESEARCH, outlook=GOOD_OUTLOOK)
    assert illiquid.role == "satellite"
    assert any("traded value" in x for x in illiquid.role_reasons)


def test_bucket_comparison():
    c = card(research=GOOD_RESEARCH, outlook=GOOD_OUTLOOK, bucket="satellite")
    assert "suggests core" in c.bucket_note
    assert "matches" in card(research=GOOD_RESEARCH, outlook=GOOD_OUTLOOK,
                             bucket="core").bucket_note
    assert "trade bucket" in card(bucket="trade").bucket_note


def test_summary_and_dict_are_json_ready():
    import json
    c = card(research=GOOD_RESEARCH, outlook=GOOD_OUTLOOK)
    json.dumps(c.summary())
    d = bs.card_dict(c)
    json.dumps(d, default=str)
    assert d["components"][0]["available"] > 0


def test_a_loss_year_keeps_it_out_of_core():
    rows = [x for x in company() if not (x["metric"] == "pat" and x["period_end"] == YEARS[-3])]
    rows.append(r("pat", "fy", YEARS[-3], -50))
    c = card(rows, research=GOOD_RESEARCH, outlook=GOOD_OUTLOOK)
    assert c.role == "satellite" and any("a loss" in x for x in c.role_reasons)


def test_fill_in_files_add_missing_rows_only(tmp_path):
    from book import userfiles as uf
    cards = [card(), sc.score("Y", company(), "it", "large", CFG, today=TODAY)]
    cards[0].symbol = "X"
    assert len(bs.fill_in_files(cards, tmp_path)) == 2
    _, rows = uf.read(tmp_path / "research_inputs.xlsx")
    rows[0]["moat_points"] = "5"
    uf.write(tmp_path / "research_inputs.xlsx", uf.RESEARCH, rows)
    assert bs.fill_in_files(cards, tmp_path) == []
    _, rows = uf.read(tmp_path / "research_inputs.xlsx")
    assert [x["symbol"] for x in rows] == ["X", "Y"] and rows[0]["moat_points"] == "5"
    _, sectors = uf.read(tmp_path / "sector_outlook.xlsx")
    assert sorted(x["sector"] for x in sectors) == ["consumer", "it"]
