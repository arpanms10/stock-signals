"""Swing scan, trade plan and simulation."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from backtest import swing_backtest as sb
from indicators import core
from scoring import timing
from strategy import swing as sw


@pytest.fixture
def cfg():
    return timing.load_config()


def frame(close, volume=1e6, spread=0.01):
    close = pd.Series(close, dtype=float)
    n = len(close)
    df = pd.DataFrame({
        "date": [dt.date(2020, 1, 1) + dt.timedelta(days=i) for i in range(n)],
        "open": close.shift(1).fillna(close.iloc[0]).to_numpy(),
        "high": (close * (1 + spread)).to_numpy(),
        "low": (close * (1 - spread)).to_numpy(),
        "close": close.to_numpy(),
        "volume": np.full(n, volume) if np.isscalar(volume) else volume,
    })
    out = core.compute_all(df)
    out["bench_close"] = np.linspace(100, 110, n)
    return out


def uptrend_then_pullback(n=320):
    """Steady rise, a dip to the 20 EMA, then an up close."""
    rng = np.random.default_rng(1)
    up = np.linspace(100, 220, n - 6) * (1 + rng.normal(0, 0.004, n - 6))
    dip = np.linspace(up[-1], up[-1] * 0.975, 5)
    return np.concatenate([up, dip, [dip[-1] * 1.02]])


def test_uptrend_pullback_is_eligible_and_flagged(cfg):
    f = sw.features(frame(uptrend_then_pullback(), volume=2e6), cfg)
    last = f.iloc[-1]
    assert last["uptrend"]
    assert last["eligible"]
    assert last["setup_pullback"]
    assert not last["setup_breakout"]


def test_downtrend_is_never_eligible(cfg):
    f = sw.features(frame(np.linspace(300, 100, 320), volume=2e6), cfg)
    assert not f["eligible"].tail(50).any()


def test_illiquid_stock_is_not_eligible(cfg):
    # Rs 220 x 1,000 shares a day is about Rs 2 lakh -- far below 20 crore.
    f = sw.features(frame(uptrend_then_pullback(), volume=1_000), cfg)
    assert f.iloc[-1]["uptrend"]
    assert not f.iloc[-1]["eligible"]


def test_breakout_needs_volume(cfg):
    base = list(np.linspace(100, 200, 280)) + [200.0] * 25
    vol = np.full(len(base) + 1, 2e6)
    quiet = sw.features(frame(base + [206.0], volume=vol), cfg).iloc[-1]
    vol[-1] = 6e6
    loud = sw.features(frame(base + [206.0], volume=vol), cfg).iloc[-1]
    assert not quiet["setup_breakout"]
    assert loud["setup_breakout"]


def test_stop_is_kept_between_atr_bounds(cfg):
    f = sw.features(frame(uptrend_then_pullback(), volume=2e6), cfg)
    lo, hi = cfg["swing"]["stop_atr"]
    risk_atr = (f["risk"] / f["atr14"]).dropna()
    assert (risk_atr >= lo - 1e-9).all() and (risk_atr <= hi + 1e-9).all()
    last = f.iloc[-1]
    assert last["t1"] == pytest.approx(last["entry"] + cfg["swing"]["t1_r"] * last["risk"])


def test_position_size_risks_the_configured_amount(cfg):
    z = sw.position_size(100.0, 95.0, 1_000_000, cfg)
    # 0.75% of 10 lakh = 7,500 at risk; Rs 5 a share -> 1,500 shares.
    assert z["qty"] == 1500
    assert z["risk_amt"] == pytest.approx(7500)
    # A very tight stop is capped by max_position_pct instead.
    tight = sw.position_size(100.0, 99.9, 1_000_000, cfg)
    assert tight["capped"]
    assert tight["value"] <= 1_000_000 * cfg["swing"]["max_position_pct"] / 100


def _ohlc(rows):
    a = np.array(rows, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


def test_simulate_needs_the_trigger(cfg):
    o, h, l, c = _ohlc([[100, 101, 99, 100]] + [[100, 100.5, 99.5, 100]] * 5)
    assert sw.simulate(o, h, l, c, 0, 102.0, 98.0, cfg) is None


def test_simulate_gap_through_stop_fills_at_open(cfg):
    o, h, l, c = _ohlc([[100, 101, 99, 100], [101, 103, 101, 102],
                        [90, 91, 89, 90]] + [[90, 91, 89, 90]] * 3)
    t = sw.simulate(o, h, l, c, 0, 102.0, 98.0, cfg)
    assert t.exit_reason == "stop"
    # Fill 102, risk 4; the gap to 90 loses 3R before costs, not 1R.
    assert t.r_gross == pytest.approx(-3.0)
    assert t.r_net < t.r_gross


def test_simulate_books_half_at_t1_then_t2(cfg):
    # entry 100, stop 96 -> R 4: T1 106, T2 112.
    o, h, l, c = _ohlc([[99, 99, 98, 99], [99, 101, 99, 100.5],
                        [101, 106.5, 100.5, 106], [106, 112.5, 105, 112]]
                       + [[112, 113, 111, 112]] * 3)
    t = sw.simulate(o, h, l, c, 0, 100.0, 96.0, cfg)
    assert t.exit_reason == "t2"
    # Half at 1.5R, half at 3R = 2.25R gross.
    assert t.r_gross == pytest.approx(2.25)


def test_simulate_after_t1_stop_moves_to_entry(cfg):
    o, h, l, c = _ohlc([[99, 99, 98, 99], [99, 101, 99, 100.5],
                        [101, 106.5, 100.5, 106], [104, 104, 99, 99.5]]
                       + [[99, 100, 98, 99]] * 3)
    t = sw.simulate(o, h, l, c, 0, 100.0, 96.0, cfg)
    assert t.exit_reason == "t1+stop"
    assert t.r_gross == pytest.approx(0.75)        # half of 1.5R, rest flat


def test_simulate_time_stop(cfg):
    n = cfg["swing"]["max_hold_sessions"] + 5
    rows = [[99, 99, 98, 99], [99, 100.5, 99.5, 100.2]] + [[100.2, 101, 99.5, 100.5]] * n
    t = sw.simulate(*_ohlc(rows), 0, 100.0, 96.0, cfg)
    assert t.exit_reason == "time"
    assert t.sessions == cfg["swing"]["max_hold_sessions"] + 1


def _panel(cfg):
    frames = {"UP": frame(uptrend_then_pullback(), volume=2e6),
              "FLAT": frame(np.r_[np.linspace(100, 200, 300), np.full(20, 200.0)],
                            volume=2e6),
              "DOWN": frame(np.linspace(300, 100, 320), volume=2e6)}
    return sw.build_panel(frames, cfg), frames


def test_scan_puts_setups_first_and_excludes_downtrends(cfg):
    panel, _ = _panel(cfg)
    t = sw.scan(panel, panel["date"].max(), cfg)
    assert "DOWN" not in set(t["symbol"])
    assert t.iloc[0]["symbol"] == "UP" and t.iloc[0]["suggest"]
    assert list(t["rank"]) == list(range(1, len(t) + 1))


def test_scan_holds_back_names_reporting_results(cfg):
    panel, _ = _panel(cfg)
    d = panel["date"].max()
    t = sw.scan(panel, d, cfg, results={"UP": [d + dt.timedelta(days=2)]})
    up = t[t["symbol"] == "UP"].iloc[0]
    assert not up["suggest"]
    assert up["results_on"] == d + dt.timedelta(days=2)


def test_score_is_zero_to_hundred(cfg):
    panel, _ = _panel(cfg)
    s = panel["score"].dropna()
    assert ((s >= 0) & (s <= 100)).all()


def test_backtest_runs_end_to_end(cfg):
    panel, frames = _panel(cfg)
    bench = pd.DataFrame({"date": frames["UP"]["date"],
                          "close": frames["UP"]["bench_close"]})
    res = sb.run(panel, frames, bench, cfg, "test")
    assert "groups" in res or res == {"error": "no trades"}
