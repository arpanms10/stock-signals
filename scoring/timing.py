"""Timing Score: five buckets, 0-100, computed daily.

Deliberately a *state* measure, not an instruction. It says how favourable a
setup looks right now; signals/engine.py turns crossings of this score, plus
discrete patterns, into the alerts you actually act on.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "scoring.yaml"

BUCKETS = ["trend", "momentum", "relative_strength", "volume", "volatility"]


def load_config(path: Path | str = CONFIG_PATH) -> dict:
    with open(path) as fh:
        cfg = yaml.safe_load(fh)
    total = sum(cfg["weights"].values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"config weights must sum to 1.0, got {total}")
    return cfg


def linmap(value, lo: float, hi: float):
    """Map value from [lo, hi] onto 0-100, clipped. Handles lo > hi (inverted)."""
    if isinstance(value, pd.Series):
        out = 100 * (value - lo) / (hi - lo)
        return out.clip(0, 100)
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    return float(np.clip(100 * (value - lo) / (hi - lo), 0, 100))


def _blend(parts: dict[str, pd.Series], weights: dict[str, float]) -> pd.Series:
    """Weighted mean that ignores components that are NaN.

    Early in a series 200-day inputs do not exist yet. Treating those as zero
    would drag the score down and manufacture false 'weak' readings, so absent
    components are dropped and the remaining weights renormalised.
    """
    num = None
    den = None
    for key, series in parts.items():
        w = weights[key]
        valid = series.notna()
        contrib = series.fillna(0.0) * w * valid
        weight = pd.Series(w, index=series.index) * valid
        num = contrib if num is None else num + contrib
        den = weight if den is None else den + weight
    return (num / den.replace(0, np.nan))


def trend_bucket(df: pd.DataFrame, cfg: dict) -> pd.Series:
    c = cfg["trend"]
    px200 = 100 * (df["close"] / df["sma200"] - 1)
    px50 = 100 * (df["close"] / df["sma50"] - 1)
    cross = (df["sma50"] > df["sma200"]).astype(float) * 100
    cross = cross.where(df["sma200"].notna() & df["sma50"].notna())
    return _blend({
        "px_vs_200": linmap(px200, *c["px_vs_200_pct"]),
        "px_vs_50": linmap(px50, *c["px_vs_50_pct"]),
        "slope_200": linmap(df["sma200_slope"], *c["slope_200_pct"]),
        "golden_cross": cross,
        "adx": linmap(df["adx14"], *c["adx"]),
    }, c["weights"])


def momentum_bucket(df: pd.DataFrame, cfg: dict) -> pd.Series:
    c = cfg["momentum"]
    macd_units = df["macd_hist"] / df["atr14"].replace(0, np.nan)
    return _blend({
        "rsi": linmap(df["rsi14"], *c["rsi"]),
        "macd": linmap(macd_units, *c["macd_hist_atr"]),
        "roc": linmap(df["roc60"], *c["roc60_pct"]),
    }, c["weights"])


def relative_strength_bucket(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """Return minus the benchmark's return. Rising with the market is not skill."""
    c = cfg["relative_strength"]
    if "bench_close" not in df.columns:
        return pd.Series(np.nan, index=df.index)
    parts = {}
    for key, bars in (("excess_1m", 21), ("excess_3m", 63), ("excess_6m", 126)):
        stock = 100 * (df["close"] / df["close"].shift(bars) - 1)
        bench = 100 * (df["bench_close"] / df["bench_close"].shift(bars) - 1)
        parts[key] = linmap(stock - bench, *c[f"{key}_pct"])
    return _blend(parts, c["weights"])


def volume_bucket(df: pd.DataFrame, cfg: dict) -> pd.Series:
    c = cfg["volume"]
    return _blend({
        "ud_ratio": linmap(df["ud_vol_ratio"], *c["ud_ratio"]),
        "obv_slope": linmap(df["obv_slope"], *c["obv_slope_pct"]),
        "vol_ratio": linmap(df["vol_ratio"], *c["vol_ratio"]),
    }, c["weights"])


def volatility_bucket(df: pd.DataFrame, cfg: dict) -> pd.Series:
    c = cfg["volatility"]
    return _blend({
        "atr": linmap(df["atr_pct"], *c["atr_pct"]),
        "bb_width": linmap(df["bb_width"], *c["bb_width_pct"]),
    }, c["weights"])


def compute(df: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    """Attach the five buckets and the blended timing_score to an indicator frame."""
    cfg = cfg or load_config()
    out = df.copy()
    out["trend"] = trend_bucket(out, cfg)
    out["momentum"] = momentum_bucket(out, cfg)
    out["relative_strength"] = relative_strength_bucket(out, cfg)
    out["volume_score"] = volume_bucket(out, cfg)
    out["volatility"] = volatility_bucket(out, cfg)
    parts = {b: out["volume_score" if b == "volume" else b] for b in BUCKETS}
    out["timing_score"] = _blend(parts, cfg["weights"])
    return out


def explain(row: pd.Series, cfg: dict | None = None) -> str:
    """One-line breakdown for an alert. Never show a score without its reasons."""
    cfg = cfg or load_config()
    bits = []
    for b in BUCKETS:
        key = "volume_score" if b == "volume" else b
        val = row.get(key)
        if val is not None and not pd.isna(val):
            bits.append(f"{b.replace('_', ' ')} {val:.0f}")
    return " | ".join(bits)
