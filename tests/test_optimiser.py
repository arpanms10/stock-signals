"""The Core–Satellite plan: water-filling, bucket targets, hold-only, trims."""
from __future__ import annotations

import pytest

from strategy import optimiser as opt

CS = {"targets": {"core_pct": 70, "satellite_pct": 30}, "sector_max_pct": 25,
      "position_max_pct": {"core": 8, "satellite": 4, "small_cap": 2}}
PLAN = {"trim_tolerance_pts": 0.5, "fix_by_contributions_months": 12, "min_trade_rupees": 2000}


def H(sym, value, role="core", cap=8, sector="", **kw):
    return opt.PlanHolding(sym, value, role, cap, sector or sym, **kw)


def test_water_fill_proportional_when_uncapped():
    got = opt.water_fill(100, {"a": 1, "b": 3}, {"a": 1e9, "b": 1e9})
    assert got == pytest.approx({"a": 25, "b": 75})


def test_water_fill_redistributes_what_a_capped_name_cannot_take():
    got = opt.water_fill(100, {"a": 1, "b": 1, "c": 2}, {"a": 10, "b": 1e9, "c": 1e9})
    assert got["a"] == pytest.approx(10)
    assert got["b"] + got["c"] == pytest.approx(90)
    assert got["c"] == pytest.approx(2 * got["b"])


def test_water_fill_stops_at_total_room():
    got = opt.water_fill(100, {"a": 1, "b": 1}, {"a": 20, "b": 30})
    assert sum(got.values()) == pytest.approx(50)


def _book(n_core=12, n_sat=10, v=10_000):
    return ([H(f"C{i}", v, "core", 8) for i in range(n_core)]
            + [H(f"S{i}", v, "satellite", 4) for i in range(n_sat)])


def test_new_money_moves_buckets_toward_70_30():
    hs = _book(n_core=11, n_sat=11)            # 50:50 now
    p = opt.plan(hs, 220_000, 20_000, 12, CS, PLAN)
    b = {x["role"]: x for x in p.buckets}
    assert b["core"]["before_pct"] == pytest.approx(50)
    assert b["core"]["after_pct"] > 60
    assert all(r.trim == 0 for r in p.rows)
    assert sum(r.buy for r in p.rows) + p.unallocated == pytest.approx(240_000)
    core_buy = sum(r.buy for r in p.rows if r.role == "core")
    assert core_buy > 0.8 * sum(r.buy for r in p.rows)


def test_hold_only_gets_no_new_money():
    hs = _book()
    hs[0].no_buy = ["quality tier exit candidate"]
    p = opt.plan(hs, 220_000, 25_000, 12, CS, PLAN)
    r = next(r for r in p.rows if r.symbol == "C0")
    assert r.buy == 0 and r.trim == 0
    assert any("no new money" in n for n in r.notes)


def test_no_trim_when_bucket_is_blank():
    hs = [H("BIG", 50_000, "satellite", 4, role_source="default")] + _book(5, 5)
    p = opt.plan(hs, 150_000, 0, 1, CS, PLAN)
    r = next(r for r in p.rows if r.symbol == "BIG")
    assert r.trim == 0
    assert any("bucket is not set" in n for n in r.notes)


def test_trim_with_tax_when_contributions_cannot_fix_it():
    hs = [H("BIG", 50_000, "core", 8, tax_per_rupee=0.1)] + _book(5, 5)
    p = opt.plan(hs, 150_000, 1_000, 12, CS, PLAN)
    r = next(r for r in p.rows if r.symbol == "BIG")
    cap_v = 0.08 * (150_000 + 12_000)
    assert r.trim == pytest.approx(50_000 - cap_v)
    assert r.trim_tax == pytest.approx(r.trim * 0.1)


def test_no_trim_when_contributions_fix_the_breach_in_time():
    hs = [H("BIG", 11_000, "core", 8)] + _book(5, 5)  # 11k / 111k = 9.9%
    p = opt.plan(hs, 111_000, 3_000, 1, CS, PLAN)     # ~9 months of new money fixes it
    r = next(r for r in p.rows if r.symbol == "BIG")
    assert r.trim == 0
    assert any("new money alone" in n for n in r.notes)


def test_sector_targets_scaled_to_limit():
    hs = [H(f"B{i}", 20_000, "core", 30, sector="Banks") for i in range(3)] + \
         [H(f"O{i}", 10_000, "core", 30) for i in range(4)] + \
         [H(f"S{i}", 10_000, "satellite", 30) for i in range(3)]
    p = opt.plan(hs, 130_000, 10_000, 12, CS, PLAN)
    banks = sum(r.target for r in p.rows if r.symbol.startswith("B"))
    assert banks <= 0.25 * p.operating_after + 1


def test_months_to_share_and_cap():
    assert opt._months_to_share(50, 100, 0.7, 10) == pytest.approx((70 - 50) / (0.3 * 10))
    assert opt._months_to_share(80, 100, 0.7, 10) == 0
    assert opt._months_to_cap(10, 0.08, 100, 5) == pytest.approx((125 - 100) / 5)
    assert opt._months_to_cap(10, 0.08, 100, 0) is None


def test_warns_when_no_core():
    hs = [H(f"S{i}", 10_000, "satellite", 4, role_source="default") for i in range(5)]
    p = opt.plan(hs, 50_000, 10_000, 12, CS, PLAN)
    assert any("No holding is core" in w for w in p.warnings)
