"""Capital-gains estimates and trim timing.

The question this exists to answer: a trim is a drift correction, so it can
afford to wait. An exit cannot.
"""
import datetime as dt

import pytest

from scoring import timing
from strategy import tax as tx


@pytest.fixture
def cfg():
    return timing.load_config()


TODAY = dt.date(2026, 9, 6)


def ago(days):
    return (TODAY - dt.timedelta(days=days)).isoformat()


def test_long_held_position_gets_the_lower_rate(cfg):
    v = tx.assess(160.0, 100.0, 100, cfg, ago(800), TODAY)
    assert v.is_long_term is True
    assert v.rate_pct == pytest.approx(cfg["tax"]["ltcg_rate_pct"])
    assert v.estimated_tax == pytest.approx(6000 * 0.125)


def test_recent_position_gets_the_higher_rate(cfg):
    v = tx.assess(160.0, 100.0, 100, cfg, ago(100), TODAY)
    assert v.is_long_term is False
    assert v.rate_pct == pytest.approx(cfg["tax"]["stcg_rate_pct"])


def test_unknown_purchase_date_assumes_the_worse_rate(cfg):
    """An underestimate of tax is the error that costs money."""
    v = tx.assess(160.0, 100.0, 100, cfg, None, TODAY)
    assert v.rate_pct == pytest.approx(cfg["tax"]["stcg_rate_pct"])
    assert "purchase date unknown" in v.note


def test_a_loss_incurs_no_tax_and_says_so(cfg):
    v = tx.assess(80.0, 100.0, 100, cfg, ago(800), TODAY)
    assert v.total_gain < 0
    assert v.estimated_tax == 0
    assert "offset gains" in v.note


def test_days_to_long_term_is_reported(cfg):
    v = tx.assess(160.0, 100.0, 100, cfg, ago(340), TODAY)
    assert v.days_to_ltcg == 26
    assert "only 26 days" in v.note


# ------------------------------------------------------------------ timing

def test_trim_waits_when_long_term_is_close(cfg):
    """Waiting weeks to cut the rate from 20% to 12.5% beats a marginally
    faster rebalance on a position that is not broken."""
    v = tx.assess(160.0, 100.0, 500, cfg, ago(340), TODAY)
    t, why = tx.trim_timing(v, 160.0, 150.0, 155.0, drift_pct=50, cfg=cfg)
    assert t == "wait_for_ltcg"
    assert any("saves about" in w for w in why)


def test_trim_does_not_wait_when_already_long_term(cfg):
    v = tx.assess(160.0, 100.0, 500, cfg, ago(800), TODAY)
    t, _ = tx.trim_timing(v, 160.0, 150.0, 155.0, drift_pct=150, cfg=cfg)
    assert t == "now"


def test_trim_waits_for_strength_rather_than_selling_into_weakness(cfg):
    v = tx.assess(160.0, 100.0, 500, cfg, ago(800), TODAY)
    t, why = tx.trim_timing(v, 140.0, 150.0, 155.0, drift_pct=30, cfg=cfg)
    assert t == "wait_for_strength"
    assert any("not an escape" in w for w in why)


def test_severe_drift_acts_now_even_below_the_20dma(cfg):
    v = tx.assess(160.0, 100.0, 500, cfg, ago(800), TODAY)
    t, _ = tx.trim_timing(v, 140.0, 150.0, 155.0, drift_pct=250, cfg=cfg)
    assert t == "now"


def test_loss_position_never_waits_for_tax(cfg):
    """There is no long-term rate to wait for on a loss."""
    v = tx.assess(80.0, 100.0, 500, cfg, ago(340), TODAY)
    t, _ = tx.trim_timing(v, 80.0, 90.0, 95.0, drift_pct=200, cfg=cfg)
    assert t != "wait_for_ltcg"


# ---------------------------------------------------------------- tranches

def test_large_trim_is_split(cfg):
    parts = tx.tranche_plan(300, 500.0, cfg)
    assert len(parts) == cfg["trimming"]["tranches"]
    assert sum(parts) == pytest.approx(300)


def test_small_trim_stays_one_order(cfg):
    """Below the minimum, brokerage and spread on each slice outweigh the
    benefit of spreading."""
    assert tx.tranche_plan(10, 100.0, cfg) == [10]


def test_tranches_always_sum_to_the_requested_quantity(cfg):
    for qty in (7, 100, 301, 1000):
        assert sum(tx.tranche_plan(qty, 500.0, cfg)) == pytest.approx(qty)


# ------------------------------------------------- long/short split (broker)

def test_split_sells_long_term_shares_first(cfg):
    """Long-term is taxed at 12.5% against 20% short-term, so when the holding
    offers a choice of which shares to deliver, delivering long-term ones is
    strictly cheaper. Ignoring the split leaves money on the table every trim."""
    v = tx.assess(200.0, 100.0, 50, cfg, lt_qty=80, st_qty=20)
    assert v.lt_shares_sold == 50 and v.st_shares_sold == 0
    assert v.estimated_tax == pytest.approx(50 * 100 * 0.125)
    assert v.tax_saved_vs_worst == pytest.approx(50 * 100 * (0.20 - 0.125))


def test_split_spills_into_short_term_when_long_term_runs_out(cfg):
    v = tx.assess(200.0, 100.0, 90, cfg, lt_qty=60, st_qty=40)
    assert v.lt_shares_sold == 60 and v.st_shares_sold == 30
    expected = (60 * 100 * 0.125) + (30 * 100 * 0.20)
    assert v.estimated_tax == pytest.approx(expected)
    assert "blended" in v.note


def test_split_with_only_short_term_uses_the_higher_rate(cfg):
    v = tx.assess(200.0, 100.0, 30, cfg, lt_qty=0, st_qty=50)
    assert v.st_shares_sold == 30 and v.lt_shares_sold == 0
    assert v.estimated_tax == pytest.approx(30 * 100 * 0.20)
    assert v.tax_saved_vs_worst == 0


def test_split_beats_the_unknown_date_fallback(cfg):
    """The whole point: a broker statement is strictly better information."""
    known = tx.assess(200.0, 100.0, 50, cfg, lt_qty=80, st_qty=20)
    unknown = tx.assess(200.0, 100.0, 50, cfg, purchase_date=None)
    assert known.estimated_tax < unknown.estimated_tax


def test_split_on_a_loss_is_still_zero_tax(cfg):
    v = tx.assess(80.0, 100.0, 50, cfg, lt_qty=40, st_qty=10)
    assert v.total_gain < 0 and v.estimated_tax == 0
    assert "offsets gains" in v.note


def test_only_one_column_given_infers_the_other():
    """A statement that lists only long-term quantity still tells us the split."""
    import csv
    import portfolio as pf
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "h.csv"
        p.write_text("symbol,quantity,long term qty,avg_price\n"
                     "TCS,58,50,3130.61\n")
        rows = pf.read_csv_holdings(p)
    assert rows[0]["lt_quantity"] == 50
    assert rows[0]["st_quantity"] == 8


def test_fully_long_term_trim_has_no_reason_to_wait(cfg):
    v = tx.assess(200.0, 100.0, 50, cfg, lt_qty=80, st_qty=20)
    t, why = tx.trim_timing(v, 200.0, 190.0, 195.0, drift_pct=50, cfg=cfg)
    assert any("no tax reason to delay" in w for w in why)
