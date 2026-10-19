"""Expected ranges for the next session and the next week: spot +/- one
standard deviation of the move, and nothing about direction.

Every directional read this framework has tested -- OI walls, PCR, max pain,
the swing score, RSI(2) -- failed to beat chance (docs/f_o/validation.md,
docs/swing.md). What did hold up is the size of the move the options market
prices in. So a "prediction" here is a range centred on today's close, with
the share of past closes that actually landed inside it.

Two sources for the one-session standard deviation, as a fraction of spot:

  implied   the near-monthly ATM straddle's IV (fo_spot.iv_straddle), or the
            next expiry's when the near one is <= 2 sessions out -- expiry-week
            IV is distorted by pin risk and the quarter-day floor. An annual
            IV becomes a session's by / sqrt(252).
  realised  the standard deviation of the last 20 daily log returns. For a
            stock with no options; also logged for every F&O name so the two
            can be compared.

Pure functions; no I/O. scripts/run_range_backtest.py measures exactly this code on
the bhavcopy history and writes the hit rates to config/range_calibration.json;
scripts/record_ranges.py logs it forward.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

HORIZONS = (1, 5)                 # sessions: the next session, the next week
SESSIONS_PER_YEAR = 252
NEAR_EXPIRY_SESSIONS = 2
REALISED_WINDOW = 20
HORIZON_LABEL = {1: "Next session", 5: "Next 5 sessions"}


def implied_sigma(iv_near: float | None, iv_next: float | None,
                  sessions_left: int | None) -> tuple[float | None, str]:
    """One session's standard deviation as a fraction of spot, and which IV
    gave it ('near' / 'next' / '')."""
    def ok(x):
        return x is not None and x == x and x > 0

    near_week = sessions_left is not None and sessions_left <= NEAR_EXPIRY_SESSIONS
    if ok(iv_next) and (near_week or not ok(iv_near)):
        return iv_next / 100 / math.sqrt(SESSIONS_PER_YEAR), "next"
    if ok(iv_near):
        return iv_near / 100 / math.sqrt(SESSIONS_PER_YEAR), "near"
    return None, ""


def realised_sigma(closes: pd.Series, window: int = REALISED_WINDOW) -> float | None:
    """Standard deviation of the last `window` daily log returns."""
    c = pd.Series(closes, dtype=float).dropna()
    c = c[c > 0]
    if len(c) < window + 1:
        return None
    r = np.log(c.iloc[-(window + 1):]).diff().dropna()
    s = float(r.std(ddof=1))
    return s if s > 0 else None


def band(spot: float, sigma: float | None, sessions: int) -> tuple[float | None, float | None]:
    """spot +/- one standard deviation over `sessions`, in log space so the
    low never goes negative."""
    if sigma is None or not spot or spot <= 0:
        return None, None
    m = sigma * math.sqrt(sessions)
    return spot * math.exp(-m), spot * math.exp(m)


def move_pct(sigma: float | None, sessions: int) -> float | None:
    """The one-standard-deviation move in %, for display."""
    return None if sigma is None else 100 * (math.exp(sigma * math.sqrt(sessions)) - 1)


def hit_rate(cal: dict | None, method: str, kind: str, sessions: int,
             results: bool | None = None, symbol: str = "") -> float | None:
    """The measured share of closes inside the band, from
    config/range_calibration.json. For a stock, `results` picks the rate over
    windows with (True) or without (False) a results date; NIFTY has its own."""
    table = (cal or {}).get("hit_pct") or {}
    keys = []
    if symbol == "NIFTY":
        keys.append(f"{method}|NIFTY|{sessions}")
    if kind == "equity" and results is not None:
        keys.append(f"{method}|equity|{sessions}|{'results' if results else 'clear'}")
    keys.append(f"{method}|{kind}|{sessions}")
    return next((table[k] for k in keys if table.get(k) is not None), None)


def inside(low, high, close) -> bool | None:
    if low is None or high is None or close is None:
        return None
    if any(x != x for x in (low, high, close)):
        return None
    return bool(low <= close <= high)
