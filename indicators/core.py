"""Technical indicators, hand-rolled and vectorised.

Wilder's smoothing (alpha = 1/n, not 2/(n+1)) is used for RSI, ATR and ADX
because that is what Wilder defined and what charting platforms show. Using a
standard EMA instead produces values that look plausible, drift a few points
from every chart you compare against, and quietly shift every threshold in the
scoring layer.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def wilder(s: pd.Series, n: int) -> pd.Series:
    """Wilder's smoothing: an EMA with alpha = 1/n."""
    return s.ewm(alpha=1 / n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = wilder(gain, n)
    avg_loss = wilder(loss, n)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    # No losses in the window -> RSI is 100 by definition, not undefined.
    out = out.where(avg_loss != 0, 100.0)
    out = out.where(avg_gain != 0, out.where(avg_loss == 0, 0.0))
    out.iloc[:n] = np.nan
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    return pd.concat([high - low, (high - prev).abs(), (low - prev).abs()],
                     axis=1).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    return wilder(true_range(high, low, close), n)


def atr_pct(high, low, close, n: int = 14) -> pd.Series:
    return 100 * atr(high, low, close, n) / close


def adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14):
    """Returns (adx, +di, -di). ADX measures trend *strength*, not direction --
    it is what separates a real trend from chop, which matters because the
    pullback and breakout rules behave very differently in the two regimes."""
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)
    tr_n = wilder(true_range(high, low, close), n)
    plus_di = 100 * wilder(plus_dm, n) / tr_n.replace(0, np.nan)
    minus_di = 100 * wilder(minus_dm, n) / tr_n.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return wilder(dx, n), plus_di, minus_di


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume.fillna(0.0)).cumsum()


def obv_pressure(close: pd.Series, volume: pd.Series, n: int = 21) -> pd.Series:
    """Net directional volume over n bars, as a fraction of volume traded.

    OBV itself is a running total from an arbitrary origin, so its *level* is
    meaningless and its percent change is not well defined -- near a zero
    crossing it explodes, and the value depends on how much history you happened
    to load. Dividing the n-bar *change* in OBV by the volume actually traded
    over those bars gives the same information, scale-free, origin-independent
    and naturally bounded in [-1, 1]. Returned as a percentage.
    """
    net = obv(close, volume).diff(n)
    traded = volume.rolling(n, min_periods=n).sum()
    return 100 * net / traded.replace(0, np.nan)


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return mid - k * sd, mid, mid + k * sd


def bandwidth(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.Series:
    lo, mid, hi = bollinger(close, n, k)
    return 100 * (hi - lo) / mid


def roc(s: pd.Series, n: int) -> pd.Series:
    return 100 * (s / s.shift(n) - 1)


def slope_pct(s: pd.Series, n: int = 21) -> pd.Series:
    """Percent change of a series over n bars -- used for 200 DMA direction."""
    return 100 * (s / s.shift(n) - 1)


def rolling_high(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=1).max()


def rolling_low(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=1).min()


def up_down_volume_ratio(close: pd.Series, volume: pd.Series, n: int = 20) -> pd.Series:
    """Volume on up days vs down days.

    Above 1 means the buying days carry more volume than the selling days --
    accumulation. This separates a drift higher on nothing from a real bid.
    """
    delta = close.diff()
    up_vol = volume.where(delta > 0, 0.0).rolling(n, min_periods=n).sum()
    dn_vol = volume.where(delta < 0, 0.0).rolling(n, min_periods=n).sum()
    return up_vol / dn_vol.replace(0, np.nan)


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """Attach every indicator the scoring layer needs to an OHLCV frame."""
    out = df.copy()
    c, h, l, v = out["close"], out["high"], out["low"], out["volume"]

    out["sma20"] = sma(c, 20)
    out["sma50"] = sma(c, 50)
    out["sma200"] = sma(c, 200)
    out["sma200_slope"] = slope_pct(out["sma200"], 21)

    out["rsi14"] = rsi(c, 14)
    line, sig, hist = macd(c)
    out["macd"], out["macd_signal"], out["macd_hist"] = line, sig, hist
    out["roc60"] = roc(c, 60)

    out["atr14"] = atr(h, l, c, 14)
    out["atr_pct"] = atr_pct(h, l, c, 14)
    out["bb_width"] = bandwidth(c, 20)

    adx_v, plus_di, minus_di = adx(h, l, c, 14)
    out["adx14"], out["plus_di"], out["minus_di"] = adx_v, plus_di, minus_di

    out["obv"] = obv(c, v)
    out["obv_slope"] = obv_pressure(c, v, 21)
    out["vol_sma20"] = sma(v, 20)
    out["vol_ratio"] = v / out["vol_sma20"]
    out["ud_vol_ratio"] = up_down_volume_ratio(c, v, 20)

    out["high_52w"] = rolling_high(c, 250)
    out["low_52w"] = rolling_low(c, 250)
    return out
