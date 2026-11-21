"""Valuation: point-in-time multiples, DCF maths, methods, scenarios, peers."""
import datetime as dt
import sqlite3

import pytest

from book import valuations as bv
from strategy import scorecard as sc
from strategy import valuation as val

CFG = val.load_config()
D = dt.date


def qends(n, last=D(2026, 9, 30)):
    out, y, m = [], last.year, last.month
    for _ in range(n):
        out.append(D(y, m, 30 if m in (6, 9) else 31))
        m -= 3
        if m <= 0:
            m += 12
            y -= 1
    return sorted(out)


def company(price=100.0, pe=20.0, kind="company", group="consumer", **kw):
    """Shares 10 cr, PAT 12.5 cr a quarter (50 TTM): a constant P/E of `pe`
    when the price is pe x EPS(5)."""
    ends = qends(28)
    days = []
    d = D(2020, 1, 1)
    while d <= D(2026, 11, 20):
        if d.weekday() < 5:
            days.append((d, price))
        d += dt.timedelta(days=1)
    fy = {"revenue": {f"{y}-03-31": 1000 * 1.1 ** (y - 2021) for y in range(2021, 2027)},
          "fcf": {f"{y}-03-31": 60 * 1.1 ** (y - 2021) for y in range(2021, 2027)},
          "pat": {f"{y}-03-31": 50.0 for y in range(2021, 2027)},
          "ebitda": {f"{y}-03-31": 90.0 for y in range(2021, 2027)}}
    base = dict(symbol="X", group=group, kind=kind, price=price, price_date="2026-11-20",
                prices=days, shares_q={e: 10.0 for e in ends}, pat_q={e: 12.5 for e in ends},
                revenue_q={e: 250.0 for e in ends}, ebitda_q={e: 22.5 for e in ends},
                equity={e: 400.0 for e in ends}, net_debt={e: 0.0 for e in ends}, fy=fy,
                revenue_cagr_pct=10.0, pat_cagr_pct=8.0, roe_avg_pct=15.0)
    base.update(kw)
    return val.Inputs(**base)


# ------------------------------------------------------------------ series

def test_ttm_needs_four_consecutive_quarters():
    q = {D(2025, 12, 31): 1, D(2026, 3, 31): 2, D(2026, 6, 30): 3, D(2026, 9, 30): 4}
    assert val.ttm(q) == {D(2026, 9, 30): 10}
    del q[D(2026, 3, 31)]
    q[D(2025, 9, 30)] = 9
    assert val.ttm(q) == {}


def test_point_in_time_uses_the_filing_lag():
    s = {D(2026, 3, 31): 1.0, D(2026, 6, 30): 2.0}
    assert val.pit(s, D(2026, 5, 29), CFG) is None            # March quarter: 60 days
    assert val.pit(s, D(2026, 5, 30), CFG)[1] == 1.0
    assert val.pit(s, D(2026, 8, 13), CFG)[1] == 1.0          # June quarter: 45 days
    assert val.pit(s, D(2026, 8, 14), CFG)[1] == 2.0


def test_shares_carry_forward_through_a_later_bonus():
    inp = company(actions=[(D(2026, 10, 15), 2.0)])
    assert val.shares_on(inp, D(2026, 10, 1), CFG) == 10.0
    assert val.shares_on(inp, D(2026, 11, 20), CFG) == 20.0
    # a bonus before the filing date is already in the filed count
    inp = company(actions=[(D(2026, 1, 15), 2.0)])
    assert val.shares_on(inp, D(2026, 11, 20), CFG) == 10.0


# ------------------------------------------------------------------ DCF

def test_dcf_without_growth_is_a_perpetuity():
    assert val.dcf_value(100, 0, 0, 0.10, 10, 5) == pytest.approx(1000)
    with pytest.raises(ValueError):
        val.dcf_value(100, 0.05, 0.12, 0.10, 10, 5)


def test_growth_path_fades_to_terminal():
    p = val.growth_path(0.20, 0.05, 10, 5)
    assert p[:5] == [0.20] * 5 and p[-1] == pytest.approx(0.05)
    assert p[5] < 0.20


def test_reverse_solve_recovers_the_growth():
    f = lambda g: val.dcf_value(100, g, 0.05, 0.12, 10, 5)
    target = f(0.15)
    assert val.solve(f, target) == pytest.approx(0.15, abs=1e-6)
    assert val.solve(f, 1e12) is None


# ------------------------------------------------------------------ methods

def test_method_choice():
    assert val.method_for(company(kind="bank"), CFG)[0] == val.PB
    assert val.method_for(company(group="cement"), CFG)[0] == val.EV_EBITDA
    assert val.method_for(company(kind="reit"), CFG)[0] == val.YIELD
    assert val.method_for(company(), CFG)[0] == val.DCF
    bad = company()
    bad.fy["fcf"] = {k: (-5 if i % 2 else 5) for i, k in enumerate(bad.fy["fcf"])}
    assert val.method_for(bad, CFG)[0] == val.EARNINGS


def test_constant_pe_band_and_earnings_scenarios():
    inp = company()
    inp.fy["fcf"] = {k: -1 for k in inp.fy["fcf"]}            # force the earnings method
    v = val.value(inp, CFG)
    assert v.method == val.EARNINGS
    assert v.current["pe"] == pytest.approx(20)               # 100 x 10 / 50
    assert v.band["base"] == pytest.approx(20) and v.band["months"] >= 24
    fv = {k: s["fair_value"] for k, s in v.scenarios.items()}
    assert fv["bear"] <= fv["base"] <= fv["bull"]
    # base: 50 x (1 + 10%) x 20 / 10 shares
    assert fv["base"] == pytest.approx(110)
    assert v.margin_of_safety_pct == pytest.approx((110 - 100) / 110 * 100)
    assert v.reverse["implied_pct"] is not None


def test_dcf_scenarios_order_and_assumptions_shown():
    v = val.value(company(), CFG)
    assert v.method == val.DCF
    fv = [v.scenarios[k]["fair_value"] for k in val.SCEN]
    assert fv[0] < fv[1] < fv[2]
    a = v.scenarios["base"]["assumptions"]
    assert {"revenue_growth_y1_5_pct", "fcf_margin_pct", "discount_pct",
            "terminal_growth_pct"} <= set(a)
    assert a["discount_pct"] == pytest.approx(12.0)
    assert v.cross_check                                       # P/E band beside the DCF


def test_lender_justified_pb_and_low_roe():
    v = val.value(company(kind="bank", roe_avg_pct=17.0), CFG)
    assert v.method == val.PB
    # justified P/B = (0.17 - 0.05) / (0.12 - 0.05); book 400, 10 cr shares
    assert v.scenarios["base"]["fair_value"] == pytest.approx(400 * (0.12 / 0.07) / 10)
    assert v.reverse["implied_pct"] == pytest.approx(5 + (1000 / 400) * 7)
    low = val.value(company(kind="nbfc", roe_avg_pct=2.0), CFG)
    assert low.scenarios == {} and any("at or below" in n for n in low.notes)


def test_reit_gordon_model():
    v = val.value(val.Inputs("R-RR", "reit", "reit", price=400, price_date="2026-11-20",
                             dps_ttm=26.0), CFG)
    assert v.method == val.YIELD and v.current["yield"] == pytest.approx(6.5)
    assert v.scenarios["base"]["fair_value"] == pytest.approx(26 * 1.03 / (0.095 - 0.03))
    assert v.reverse["implied_pct"] == pytest.approx((0.095 * 400 - 26) / 426 * 100)


def test_sales_band_when_no_usable_pe():
    inp = company(pat_q={e: -5.0 for e in qends(28)})
    inp.fy["fcf"] = {k: -1 for k in inp.fy["fcf"]}
    v = val.value(inp, CFG)
    assert v.method == val.SALES and v.headline == "ps"
    assert v.scenarios["base"]["fair_value"] == pytest.approx(1000 * 1.1 * 1.0 / 10)


def test_no_price_no_valuation():
    assert val.value(company(price=None), CFG).method == val.NONE


# ------------------------------------------------------------------ peers

def test_peer_compare():
    p = val.peer_compare("X", "pe", 30, {"A": 20, "B": 25, "X": 99}, CFG)
    assert p["n"] == 2 and "needs 3" in p["note"]
    p = val.peer_compare("X", "pe", 30, {"A": 20, "B": 25, "C": 40, "D": -3}, CFG)
    assert p["median"] == 25 and p["relative"] == pytest.approx(1.2) and p["n"] == 3


def test_predecessor_symbol_found_only_on_a_clean_handover():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE market (date TEXT, symbol TEXT, close REAL, volume REAL, "
                "turnover REAL)")
    rows = [("2025-04-07", "OLD", 200), ("2025-04-08", "OLD", 201),
            ("2025-04-08", "OTHER", 50), ("2025-04-09", "OTHER", 51),
            ("2025-04-08", "DEAD", 300),
            ("2025-04-09", "NEW", 202), ("2025-04-10", "NEW", 205)]
    con.executemany("INSERT INTO market VALUES (?,?,?,0,0)", rows)
    assert bv.predecessor(con, "NEW") == "OLD"
    con.execute("INSERT INTO market VALUES ('2025-04-08','TWIN',203,0,0)")
    assert bv.predecessor(con, "NEW") is None                 # two candidates: ambiguous


# ------------------------------------------------------------------ scorecard link

def test_scorecard_valuation_points_come_from_the_valuation():
    cfg = sc.load_config()
    v = {"method": "dcf", "band": {"metric": "pe", "percentile": 0.2, "months": 60},
         "peers": {"relative": 0.8, "n": 5, "industry": "IT"},
         "margin_of_safety_pct": 20.0,
         "reverse": {"gap_pts": -2.0, "implied_pct": 8.0, "delivered_pct": 10.0}}
    card = sc.score("X", [], "it", "large", cfg, valuation=v)
    comp = next(c for c in card.components if c.key == "valuation")
    assert comp.earned == 15 and comp.available == 15
    none = sc.score("X", [], "it", "large", cfg)
    comp = next(c for c in none.components if c.key == "valuation")
    assert comp.available == 0 and all(c.status == sc.NOT_ASSESSED for c in comp.criteria)
