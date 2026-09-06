"""Indicator correctness.

The vectorised implementations are checked against slow, literal, loop-based
versions written straight from Wilder's definitions. Two independent routes to
the same number is a much stronger check than asserting against hardcoded
values copied from the implementation being tested.
"""
import numpy as np
import pandas as pd
import pytest

from indicators import core


def sample_close(n=300, seed=7):
    rng = np.random.default_rng(seed)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0005, 0.015, n))))


def sample_ohlcv(n=300, seed=7):
    close = sample_close(n, seed)
    rng = np.random.default_rng(seed + 1)
    spread = close * rng.uniform(0.005, 0.03, n)
    high = close + spread * rng.uniform(0.2, 1.0, n)
    low = close - spread * rng.uniform(0.2, 1.0, n)
    vol = pd.Series(rng.integers(1e5, 5e6, n).astype(float))
    return pd.DataFrame({"open": close.shift(1).fillna(close.iloc[0]),
                         "high": high, "low": low, "close": close, "volume": vol})


def rsi_oracle(close, n=14):
    """Textbook Wilder RSI: simple average seed, then Wilder smoothing."""
    delta = close.diff()
    gains = delta.clip(lower=0).to_numpy()
    losses = (-delta).clip(lower=0).to_numpy()
    out = np.full(len(close), np.nan)
    ag = gains[1:n + 1].mean()
    al = losses[1:n + 1].mean()
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(close)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return pd.Series(out, index=close.index)


def atr_oracle(high, low, close, n=14):
    tr = core.true_range(high, low, close).to_numpy()
    out = np.full(len(close), np.nan)
    out[n] = np.nanmean(tr[1:n + 1])
    for i in range(n + 1, len(close)):
        out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return pd.Series(out, index=close.index)


def test_rsi_matches_wilder_oracle():
    close = sample_close()
    got, want = core.rsi(close, 14), rsi_oracle(close, 14)
    # Seeded differently (EWM from bar 0 vs simple mean at bar 14), so the two
    # converge rather than match immediately. After ~5 half-lives they agree.
    assert np.allclose(got.iloc[100:], want.iloc[100:], atol=0.05)


def test_rsi_is_bounded_and_directional():
    rising = pd.Series(np.linspace(100, 200, 120))
    falling = pd.Series(np.linspace(200, 100, 120))
    assert core.rsi(rising).iloc[-1] > 99
    assert core.rsi(falling).iloc[-1] < 1
    r = core.rsi(sample_close()).dropna()
    assert r.between(0, 100).all()


def test_atr_matches_oracle():
    d = sample_ohlcv()
    got = core.atr(d.high, d.low, d.close, 14)
    want = atr_oracle(d.high, d.low, d.close, 14)
    assert np.allclose(got.iloc[100:], want.iloc[100:], rtol=1e-3)


def test_atr_is_positive():
    d = sample_ohlcv()
    assert (core.atr(d.high, d.low, d.close).dropna() > 0).all()


def test_macd_definition():
    close = sample_close()
    line, sig, hist = core.macd(close)
    assert np.allclose(line, core.ema(close, 12) - core.ema(close, 26))
    assert np.allclose(hist.dropna(), (line - sig).dropna())


def test_adx_bounds_and_trend_detection():
    d = sample_ohlcv()
    a, p, m = core.adx(d.high, d.low, d.close)
    assert a.dropna().between(0, 100).all()
    # A clean straight-line trend must register as strongly trending.
    n = 200
    close = pd.Series(np.linspace(100, 300, n))
    trend = pd.DataFrame({"high": close * 1.01, "low": close * 0.99, "close": close})
    a2, p2, m2 = core.adx(trend.high, trend.low, trend.close)
    assert a2.iloc[-1] > 40
    assert p2.iloc[-1] > m2.iloc[-1]


def test_obv_follows_direction():
    close = pd.Series([10, 11, 12, 11, 10])
    vol = pd.Series([100.0] * 5)
    assert core.obv(close, vol).tolist() == [0, 100, 200, 100, 0]


def test_sma_needs_full_window():
    s = pd.Series(range(10), dtype=float)
    out = core.sma(s, 5)
    assert out.iloc[:4].isna().all()
    assert out.iloc[4] == pytest.approx(2.0)


def test_up_down_volume_ratio():
    close = pd.Series([1, 2, 3, 4, 5] * 8, dtype=float)   # mostly up days
    vol = pd.Series([1000.0] * 40)
    assert core.up_down_volume_ratio(close, vol, 20).iloc[-1] > 1


def test_compute_all_produces_expected_columns():
    d = sample_ohlcv(400)
    out = core.compute_all(d)
    for col in ("sma200", "rsi14", "macd_hist", "atr_pct", "adx14",
                "obv", "vol_ratio", "high_52w", "sma200_slope"):
        assert col in out.columns, col
    assert out["rsi14"].iloc[-1] == pytest.approx(core.rsi(d.close).iloc[-1])


def test_obv_pressure_is_independent_of_history_length():
    """The score must not depend on how much history happens to be loaded.

    OBV is a running total from an arbitrary origin, so its percent change was
    start-date dependent and exploded near zero crossings. obv_pressure
    normalises the n-bar change by volume actually traded, which is
    origin-free -- the last value must be identical however far back you start.
    """
    d = sample_ohlcv(800)
    full = core.obv_pressure(d.close, d.volume, 21)
    for cut in (250, 400, 600):
        tail = d.tail(cut).reset_index(drop=True)
        got = core.obv_pressure(tail.close, tail.volume, 21).iloc[-1]
        assert got == pytest.approx(full.iloc[-1], abs=1e-9), f"differs at {cut} bars"


def test_obv_pressure_is_bounded_and_signed():
    d = sample_ohlcv(400)
    p = core.obv_pressure(d.close, d.volume, 21).dropna()
    assert p.between(-100, 100).all()
    rising = pd.Series(np.linspace(100, 200, 120))
    vol = pd.Series([1000.0] * 120)
    assert core.obv_pressure(rising, vol, 21).iloc[-1] == pytest.approx(100.0)
