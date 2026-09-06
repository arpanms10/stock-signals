"""Signal rule correctness on synthetic series where the answer is known."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from scoring import timing
from signals import engine
from signals.state import Position


@pytest.fixture
def cfg():
    return timing.load_config()


def bar(**kw):
    base = dict(close=100.0, low=99.0, high=101.0, sma50=95.0, sma200=90.0,
                sma200_slope=2.0, rsi14=55.0, atr14=2.0, timing_score=50.0,
                vol_ratio=1.0, high_52w=105.0)
    base.update(kw)
    return pd.Series(base)


# ---------------------------------------------------------------- stops

def test_initial_stop_takes_the_tighter_of_atr_and_percent(cfg):
    # Calm stock: 2 x ATR is tighter than the 15% floor.
    assert engine.initial_stop(100, 2.0, cfg) == 96.0
    # Volatile stock: 2 x ATR would be a 40% stop, so the floor binds.
    assert engine.initial_stop(100, 20.0, cfg) == 85.0


def test_trailing_stop_only_ever_rises(cfg):
    p = Position("X", 10, 100.0, dt.date(2026, 1, 1), stop_price=96.0)
    p = engine.update_trailing(p, bar(close=120.0, atr14=2.0), cfg)
    first = p.stop_price
    assert p.trailing and first > 96.0
    # Price falls back: the stop must not follow it down.
    p = engine.update_trailing(p, bar(close=110.0, atr14=2.0), cfg)
    assert p.stop_price == first


def test_trailing_does_not_activate_below_profit_threshold(cfg):
    p = Position("X", 10, 100.0, dt.date(2026, 1, 1), stop_price=96.0)
    p = engine.update_trailing(p, bar(close=110.0), cfg)   # +10%, below 15%
    assert not p.trailing and p.stop_price == 96.0


def test_stop_fires_exactly_at_the_stop_price(cfg):
    p = Position("X", 10, 100.0, dt.date(2026, 1, 1), stop_price=96.0)
    assert engine.check_exit(bar(close=96.01), bar(), p, cfg) is None
    hit = engine.check_exit(bar(close=96.0), bar(), p, cfg)
    assert hit and hit[0] == "stop_hit"


# ---------------------------------------------------------------- exits

def test_trend_break_needs_two_consecutive_closes(cfg):
    p = Position("X", 10, 100.0, dt.date(2026, 1, 1), stop_price=50.0)
    one = engine.check_exit(bar(close=94.0), bar(close=96.0, sma50=95.0), p, cfg)
    assert one is None or one[0] != "trend_break"
    two = engine.check_exit(bar(close=94.0), bar(close=93.0, sma50=95.0), p, cfg)
    assert two and two[0] == "trend_break"


def test_death_cross_fires_on_the_crossing_bar(cfg):
    p = Position("X", 10, 100.0, dt.date(2026, 1, 1), stop_price=50.0)
    r = engine.check_exit(bar(close=100.0, sma50=89.0, sma200=90.0),
                          bar(close=100.0, sma50=91.0, sma200=90.0), p, cfg)
    assert r and r[0] == "death_cross"


def test_score_collapse_exit(cfg):
    p = Position("X", 10, 100.0, dt.date(2026, 1, 1), stop_price=50.0)
    r = engine.check_exit(bar(close=100.0, timing_score=25.0, sma50=95.0),
                          bar(close=100.0, sma50=95.0), p, cfg)
    assert r and r[0] == "score_collapse"


# ---------------------------------------------------------------- entries

def test_trend_entry_requires_an_upward_cross(cfg):
    assert engine.check_entry(bar(timing_score=65.0), bar(timing_score=55.0), cfg)[0] \
        == "trend_entry"
    # Already above the threshold on the previous bar -- not a fresh crossing.
    assert engine.check_entry(bar(timing_score=65.0), bar(timing_score=62.0), cfg) is None


def test_trend_entry_blocked_when_200dma_is_falling(cfg):
    r = engine.check_entry(bar(timing_score=65.0, sma200_slope=-1.0),
                           bar(timing_score=55.0), cfg)
    assert r is None or r[0] != "trend_entry"


def test_breakout_requires_volume_confirmation(cfg):
    strong = engine.check_entry(bar(close=110.0, vol_ratio=2.0),
                                bar(high_52w=105.0), cfg)
    assert strong and strong[0] == "breakout_entry"
    weak = engine.check_entry(bar(close=110.0, vol_ratio=1.0), bar(high_52w=105.0), cfg)
    assert weak is None or weak[0] != "breakout_entry"


def test_trim_needs_rsi_to_peak_then_turn_down(cfg):
    assert engine.check_trim(bar(rsi14=68.0), bar(rsi14=78.0), cfg)[0] == "overbought_cooling"
    assert engine.check_trim(bar(rsi14=78.0), bar(rsi14=68.0), cfg) is None


# ---------------------------------------------------------------- regime

def test_regime_filter_blocks_a_falling_market(cfg):
    dates = pd.date_range("2024-01-01", periods=300, freq="D").date
    rising = pd.DataFrame({"date": dates, "close": np.linspace(100, 200, 300)})
    falling = pd.DataFrame({"date": dates, "close": np.linspace(200, 100, 300)})
    assert engine.regime_ok(rising, dates[-1], cfg) is True
    assert engine.regime_ok(falling, dates[-1], cfg) is False


def test_regime_false_when_history_is_too_short(cfg):
    dates = pd.date_range("2026-01-01", periods=50, freq="D").date
    short = pd.DataFrame({"date": dates, "close": np.linspace(100, 200, 50)})
    assert engine.regime_ok(short, dates[-1], cfg) is False


# ---------------------------------------------------------------- sizing

def test_position_size_caps_loss_at_risk_budget(cfg):
    qty, _ = engine.position_size(price=100.0, stop=95.0,
                                  portfolio_value=1_000_000, cfg=cfg)
    assert qty * (100.0 - 95.0) <= 1_000_000 * 0.01 + 100


def test_tight_stop_is_capped_by_max_position(cfg):
    # A 0.1% stop would justify a huge position on risk alone; the cap binds.
    qty, binding = engine.position_size(100.0, 99.9, 1_000_000, cfg)
    assert qty * 100.0 <= 1_000_000 * 0.05 + 100
    assert "position cap" in binding


def test_cooldown_suppresses_repeats(cfg):
    today = dt.date(2026, 9, 4)
    assert engine.in_cooldown(dt.date(2026, 9, 2), today, cfg) is True
    assert engine.in_cooldown(dt.date(2026, 8, 20), today, cfg) is False
    assert engine.in_cooldown(None, today, cfg) is False


# ---------------------------------------------------------------- ingestion

def test_missing_ranges_fills_both_ends():
    from data.ingest import _missing_ranges
    start, today = dt.date(2016, 1, 1), dt.date(2026, 1, 1)
    # Nothing held yet.
    assert _missing_ranges(None, None, start, today) == [(start, today)]
    # Holds only the recent slice: the older years must still be fetched.
    gaps = _missing_ranges(dt.date(2023, 1, 1), dt.date(2025, 12, 1), start, today)
    assert gaps[0] == (start, dt.date(2022, 12, 31))
    assert gaps[1] == (dt.date(2025, 12, 2), today)
    # Fully covered.
    assert _missing_ranges(start, today, start, today) == []


def test_only_eq_series_rows_are_kept(monkeypatch):
    """NSE returns bonds and other series under the same symbol; only the
    equity series may reach the price store."""
    import pandas as pd
    from data.sources import prices

    raw = pd.DataFrame({
        "DATE": pd.to_datetime(["2020-09-04 18:30", "2020-09-04 18:30",
                                "2020-09-07 18:30"]),
        "SERIES": ["N2", "EQ", "EQ"],
        "OPEN": [1360.0, 101.0, 102.0], "HIGH": [1360.0, 104.0, 103.0],
        "LOW": [1360.0, 100.0, 101.0], "CLOSE": [1360.0, 101.5, 102.5],
        "VOLUME": [100.0, 2.1e7, 2.0e7], "SYMBOL": ["NTPC"] * 3,
    })
    monkeypatch.setattr("jugaad_data.nse.stock_df", lambda **kw: raw)
    out = prices.stock_history("NTPC", dt.date(2020, 9, 1), dt.date(2020, 9, 8))
    assert len(out) == 2
    assert 1360.0 not in out["close"].tolist()
    assert out["close"].tolist() == [101.5, 102.5]


def test_old_database_gains_new_columns(tmp_path):
    """A database created before targets existed must migrate, not crash.

    Reproduces the real failure: CREATE TABLE IF NOT EXISTS is a no-op on an
    existing table, so t1/t2/t1_booked were never added to databases created
    earlier and load_positions raised 'no such column: t1'.
    """
    import sqlite3
    from signals import state as stt

    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE positions (
            symbol TEXT PRIMARY KEY, qty REAL NOT NULL, entry_price REAL NOT NULL,
            entry_date TEXT NOT NULL, highest_close REAL, stop_price REAL,
            trailing INTEGER DEFAULT 0, source TEXT);
    """)
    con.execute("INSERT INTO positions VALUES ('RELIANCE',10,1200.0,'2026-01-02',"
                "1250.0,1150.0,0,'manual')")
    con.commit()

    stt.init(con)                      # must migrate in place
    positions = stt.load_positions(con)
    assert "RELIANCE" in positions
    p = positions["RELIANCE"]
    assert p.qty == 10 and p.entry_price == 1200.0
    assert p.t1 == 0.0 and p.t2 == 0.0 and p.t1_booked is False

    stt.init(con)                      # idempotent
    assert "RELIANCE" in stt.load_positions(con)
