"""Phase 6: capital gains, lots from tradebooks, timed trims."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from strategy import capgains as cg
from strategy import lots as lt
from strategy import transition as tr

CFG = cg.load_config()
D = dt.date
TODAY = D(2026, 11, 21)


def T(sym, d, side, q, p, i=""):
    return lt.Trade(sym, d, side, q, p, i)


# ---------------------------------------------------------------- capgains

def test_fy_and_regime():
    assert cg.fy_of(D(2026, 3, 31)) == "2025-26"
    assert cg.fy_of(D(2026, 4, 1)) == "2026-27"
    assert cg.regime(D(2024, 7, 22), CFG)["stcg_pct"] == 15
    assert cg.regime(D(2024, 7, 23), CFG)["stcg_pct"] == 20


def test_fifo_splits_long_and_short():
    lots = [cg.Lot("X", 10, 100, D(2025, 1, 1)), cg.Lot("X", 10, 150, D(2026, 6, 1))]
    s, left = cg.sell_fifo(lots, 15, 200, TODAY, CFG)
    assert s.lt_gain == pytest.approx(10 * 100)
    assert s.st_gain == pytest.approx(5 * 50)
    assert [l.qty for l in left] == [5]
    assert lots[0].qty == 10                       # input untouched


def test_set_off_and_exemption():
    m = 1.04
    # short-term loss eats long-term gain after short-term gain
    t = cg.fy_tax(-50_000, 200_000, TODAY, CFG)
    assert t["exemption_used"] == 125_000
    assert t["tax"] == pytest.approx(25_000 * 0.125 * m)
    # long-term loss can't offset short-term gain
    t = cg.fy_tax(10_000, -30_000, TODAY, CFG)
    assert t["tax"] == pytest.approx(10_000 * 0.20 * m)
    assert t["carry_lt_loss"] == 30_000


def test_costs_include_stt_and_dp():
    c = cg.sale_costs(100_000, CFG)
    assert 100 + 13.5 < c < 100 + 13.5 * 1.18 + 5


# ---------------------------------------------------------------- lots

def test_lots_match_tradebook_exactly():
    tr_ = [T("A", D(2025, 1, 10), "buy", 10, 100), T("A", D(2026, 2, 1), "buy", 5, 200)]
    b = lt.build("A", tr_, [], 15, (1000 + 1000) / 15, 10, 5, TODAY, CFG)
    assert b.status == "matched" and b.opening_qty == 0
    assert b.lt_qty_today == 10 and b.st_qty_today == 5


def test_opening_lot_sized_and_costed_from_average():
    # held 20 before the tradebook; sold 5 (FIFO from the opening), bought 10
    tr_ = [T("A", D(2025, 3, 1), "sell", 5, 300), T("A", D(2026, 1, 5), "buy", 10, 200)]
    avg = (15 * 80 + 10 * 200) / 25
    b = lt.build("A", tr_, [], 25, avg, 15, 10, TODAY, CFG)
    assert b.opening_qty == pytest.approx(20, abs=0.01)
    op = [l for l in b.lots if l.source == "opening"]
    assert sum(l.qty for l in op) == pytest.approx(15, abs=0.01)
    assert op[0].cost == pytest.approx(80, rel=1e-3)
    assert b.sales[0].lt_gain == pytest.approx(5 * (300 - 80), rel=1e-3)


def test_bonus_lot_has_nil_cost_and_its_own_date():
    tr_ = [T("A", D(2025, 1, 10), "buy", 10, 100)]
    b = lt.build("A", tr_, [(D(2026, 8, 26), 2.0, "bonus")], 20, 50, 10, 10, TODAY, CFG)
    bonus = [l for l in b.lots if l.source == "bonus"]
    assert bonus and bonus[0].qty == 10 and bonus[0].cost == 0 and bonus[0].date == D(2026, 8, 26)
    assert b.lt_qty_today == 10


def test_split_keeps_date_and_divides_cost():
    tr_ = [T("A", D(2025, 1, 10), "buy", 10, 500)]
    b = lt.build("A", tr_, [(D(2025, 12, 12), 5.0, "split")], 50, 100, 50, 0, TODAY, CFG)
    assert [(l.qty, l.cost, l.date) for l in b.lots] == [(50, 100, D(2025, 1, 10))]


def test_ipo_shares_marked_short_term_per_broker():
    # no buy in the tradebook (an IPO); sold half; broker says none long-term
    tr_ = [T("A", D(2026, 10, 30), "sell", 6, 150)]
    b = lt.build("A", tr_, [], 6, 100, 0, 6, TODAY, CFG)
    assert b.lt_qty_today == 0 and b.st_qty_today == 6


def test_fallback_without_trades_uses_broker_split():
    b = lt.build("A", [], [], 30, 100, 20, 10, TODAY, CFG)
    assert b.status == "no_trades"
    assert [(l.qty, l.long_term) for l in b.lots] == [(20, True), (10, False)]


def test_more_shares_in_tradebook_than_held_falls_back():
    b = lt.build("A", [T("A", D(2026, 1, 1), "buy", 50, 10)], [], 30, 10, 0, 30, TODAY, CFG)
    assert b.status == "fallback"


# ---------------------------------------------------------------- transition

def test_trim_waits_for_long_term_when_it_saves_enough():
    lots = {"A": [cg.Lot("A", 100, 100, D(2025, 12, 1))]}       # long-term from 2026-12-02
    s = tr.schedule([tr.Trim("A", 100, 1_000)], lots, TODAY, 3, {}, CFG)
    assert s.rows[0].date == D(2026, 12, 2)
    assert s.tax_all_now > s.tax_plan


def test_trim_now_when_waiting_saves_too_little():
    lots = {"A": [cg.Lot("A", 10, 100, D(2025, 12, 1))]}         # tiny gain
    s = tr.schedule([tr.Trim("A", 10, 105)], lots, TODAY, 3, {}, CFG)
    assert s.rows[0].date == TODAY


def test_large_long_term_gains_split_across_financial_years():
    lots = {"A": [cg.Lot("A", 1000, 100, D(2020, 1, 1))],
            "B": [cg.Lot("B", 1000, 100, D(2020, 1, 1))]}
    trims = [tr.Trim("A", 1000, 220), tr.Trim("B", 1000, 220)]  # 1.2 lakh gain each
    s = tr.schedule(trims, lots, TODAY, 12, {}, CFG)
    assert {r.date for r in s.rows} == {TODAY, D(2027, 4, 1)}
    assert s.tax_plan == pytest.approx(0, abs=1)


def test_harvest_fills_exemption():
    lots = {"A": [cg.Lot("A", 100, 100, D(2020, 1, 1))]}
    out = tr.harvest_gains(lots, {"A": 2_000}, TODAY, 95_000, CFG)
    assert out[0]["qty"] == 50 and out[0]["gain"] == pytest.approx(95_000)


def test_tradebook_reader_finds_header_and_dedupes(tmp_path):
    from book import tradebook as tb
    rows = [["", "Client ID", "XX"], ["", "", ""],
            ["Symbol", "ISIN", "Trade Date", "Segment", "Trade Type", "Quantity", "Price",
             "Trade ID"],
            ["ABC", "INE1", "2025-01-02", "EQ", "buy", 10, 100, "1"],
            ["ABC", "INE1", "2025-02-02", "EQ", "sell", 5, 120, "2"]]
    pd.DataFrame(rows).to_csv(tmp_path / "a.csv", header=False, index=False)
    pd.DataFrame(rows).to_csv(tmp_path / "b.csv", header=False, index=False)
    trades, files = tb.load(tmp_path)
    assert len(trades) == 2 and files[1]["new"] == 0
    assert trades[1].side == "sell" and trades[1].qty == 5
