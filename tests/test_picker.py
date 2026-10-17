"""GARP / QARP picker: point-in-time metrics and scoring. Network-free."""
import datetime as dt

import pandas as pd
import pytest
import yaml

from picker import engine as pe
from picker import metrics as pm
from picker import score as ps
from results import scorecard as sc

CFG = yaml.safe_load(open("config/scoring.yaml"))["picker"]
END = dt.date(2026, 9, 30)


def quarters(sym, pats, revenue=1000.0, shares=100.0, **latest):
    """Quarterly rows oldest -> newest ending Sep 2026, filed 30 days after."""
    n = len(pats)
    rows = []
    for i, pat in enumerate(pats):
        e = sc.quarter_back(END, n - 1 - i)
        rows.append({"symbol": sym, "to_date": e, "quarter": "Q", "kind": "company",
                     "broadcast": dt.datetime.combine(e + dt.timedelta(days=30),
                                                      dt.time(18)),
                     "revenue": revenue * (1 + 0.05 * i), "pbt": pat / 0.75,
                     "tax": pat / 3, "pat": pat, "other_income": 0.0, "exceptional": 0.0,
                     "finance_costs": 5.0, "depreciation": 10.0, "eps": pat / shares,
                     "shares_cr": shares})
    rows[-1].update(latest)
    return rows


def flat_price(p):
    return lambda d: p


def test_only_filings_broadcast_by_the_date_are_used():
    rows = quarters("X", [100.0] * 12)
    early = pm.compute(rows, END + dt.timedelta(days=10), flat_price(100.0))
    late = pm.compute(rows, END + dt.timedelta(days=40), flat_price(100.0))
    assert early["to_date"] == sc.quarter_back(END, 1)
    assert late["to_date"] == END


def test_pe_peg_and_growth_takes_the_lower_of_1y_and_2y():
    # Profit 100/qtr for 8 quarters, then 150: 1y TTM growth 50%, 2y CAGR ~22.5%.
    rows = quarters("X", [100.0] * 8 + [150.0] * 4)
    m = pm.compute(rows, END + dt.timedelta(days=40), flat_price(150.0))
    assert m["core_growth_1y"] == 50.0
    assert m["growth"] == m["core_growth_2y"] == pytest.approx(22.5, abs=0.1)
    assert m["mcap_cr"] == 15000 and m["pe"] == 25.0
    assert m["peg"] == pytest.approx(25.0 / 22.5, abs=0.01)


def test_one_offs_are_stripped_from_core_profit():
    rows = quarters("X", [100.0] * 12)
    rows[-1].update(other_income=40.0, pat=130.0, pbt=173.3, tax=43.3)
    m = pm.compute(rows, END + dt.timedelta(days=40), flat_price(100.0))
    assert m["ttm_pat"] == pytest.approx(430.0)
    assert m["ttm_core_pat"] == pytest.approx(400.0, abs=0.5)


def test_discontinued_gain_is_not_core():
    rows = quarters("X", [100.0] * 12)
    rows[-1].update(pat=900.0, pat_continuing=100.0)       # a business sold
    m = pm.compute(rows, END + dt.timedelta(days=40), flat_price(100.0))
    assert m["ttm_core_pat"] == pytest.approx(400.0, abs=0.5)


def test_swapped_share_fields_fall_back_to_profit_over_eps():
    """ATGL filed paid-up in the face-value field: one crore shares, not 110."""
    rows = quarters("X", [100.0] * 12, shares=110.0)
    rows[-1]["shares_cr"] = 1.0
    m = pm.compute(rows, END + dt.timedelta(days=40), flat_price(100.0))
    assert m["mcap_cr"] == pytest.approx(11000, rel=0.01)


def test_pe_history_percentile():
    rows = quarters("X", [100.0] * 16)
    # The price fell steadily: today's P/E is the lowest in the window.
    def falling(d):
        return 300.0 - (d - dt.date(2022, 1, 1)).days * 0.1
    m = pm.compute(rows, END + dt.timedelta(days=40), falling)
    assert m["pe_hist_pct"] == 0.0


def test_cfo_over_twelve_months_from_half_years():
    rows = quarters("X", [100.0] * 12)
    by = {r["to_date"]: r for r in rows}
    by[END].update(cfo_ytd=300.0, ytd_months=6, capex_ytd=50.0)
    by[sc.quarter_back(END, 2)].update(cfo_ytd=500.0, ytd_months=12, capex_ytd=90.0)
    by[sc.quarter_back(END, 4)].update(cfo_ytd=200.0, ytd_months=6, capex_ytd=40.0)
    m = pm.compute(rows, END + dt.timedelta(days=40), flat_price(100.0))
    assert m["cfo_pat"] == pytest.approx(600.0 / 400.0)
    assert m["fcf_yield"] == pytest.approx(100 * (600 - 100) / 10000)


def universe(n=20):
    """n companies: growth rising with index, P/E flat, so PEG falls."""
    out = []
    for i in range(n):
        out.append({"symbol": f"S{i}", "kind": "company", "growth": 5.0 + 2 * i,
                    "topline_growth": 10.0, "consistency": 1.0, "pe": 30.0,
                    "core_pe": 30.0, "peg": 30.0 / (5.0 + 2 * i),
                    "interest_cover": 10.0, "margin": 20.0, "roe": 10.0 + i,
                    "pe_hist_pct": 0.5})
    return pd.DataFrame(out)


def test_garp_gates_on_growth_and_peg():
    s = ps.score(universe(), CFG)
    ok = s[s["garp_ok"]]
    assert ok["growth"].min() >= CFG["garp"]["min_growth"]
    assert (ok["peg"] <= CFG["garp"]["max_peg"]).all()
    top = ps.picks(s, "garp", 3)
    assert list(top["symbol"]) == ["S19", "S18", "S17"]


def test_qarp_skips_a_stock_at_its_own_peak_valuation():
    u = universe()
    u.loc[u["symbol"] == "S19", "pe_hist_pct"] = 0.95
    s = ps.score(u, CFG)
    assert not s.loc[s["symbol"] == "S19", "qarp_ok"].iloc[0]
    assert s.loc[s["symbol"] == "S18", "qarp_ok"].iloc[0]


def test_lenders_ranked_only_against_lenders():
    u = universe()
    banks = pd.DataFrame([{"symbol": f"B{i}", "kind": "bank", "roe": 5.0 + i,
                           "roa": 1.0, "gnpa": 2.0, "credit_cost": 20.0,
                           "growth": 10.0, "topline_growth": 10.0, "consistency": 1.0,
                           "pe": 15.0, "peg": 1.5} for i in range(6)])
    s = ps.score(pd.concat([u, banks], ignore_index=True), CFG)
    # The weakest bank by ROE is the bottom of the bank ranking, wherever it
    # would sit among companies.
    b = s[s["kind"] == "bank"].sort_values("roe")
    assert b["quality"].iloc[0] < b["quality"].iloc[-1]


def test_rebalance_dates():
    d = pe.rebalance_dates(CFG, dt.date(2024, 1, 1), dt.date(2024, 12, 31))
    assert d == [dt.date(2024, 2, 20), dt.date(2024, 6, 5),
                 dt.date(2024, 8, 20), dt.date(2024, 11, 20)]


def test_summary_excess_is_against_the_universe():
    p = pd.DataFrame({"start": [dt.date(2024, 1, 1), dt.date(2024, 7, 1)],
                      "end": [dt.date(2024, 7, 1), dt.date(2025, 1, 1)],
                      "garp": [10.0, 4.0], "qarp": [5.0, 5.0],
                      "universe": [6.0, 4.0], "index": [5.0, 3.0],
                      "garp_n": [10, 12], "qarp_n": [15, 15]})
    s = pe.summarise(p)
    assert s["garp"]["excess_mean"] == 2.0
    assert s["garp"]["beat_universe_pct"] == 50.0
    assert s["universe"]["total"] == 10.2           # 1.06 x 1.04, rounded
