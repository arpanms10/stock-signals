"""Build a fully scored frame for one symbol: prices -> indicators -> score."""
from __future__ import annotations

import datetime as dt

import pandas as pd

from data import store
from indicators import core
from scoring import timing


def benchmark_frame(con, index: str) -> pd.DataFrame:
    b = store.load_index(con, index)
    if b.empty:
        return b
    return b[["date", "close"]].rename(columns={"close": "bench_close"})


def scored_frame(con, symbol: str, cfg: dict | None = None,
                 bench: pd.DataFrame | None = None,
                 usable_from: "dt.date | None" = None) -> pd.DataFrame:
    """Adjusted prices + indicators + the five buckets + timing_score.

    `usable_from` drops history before an unexplained price gap (see
    data.quality). Indicators are computed on what remains, so a symbol with a
    missing split contributes clean bars rather than confident nonsense.
    """
    cfg = cfg or timing.load_config()
    df = store.load_prices(con, symbol, adjusted=True)
    if df.empty:
        return df
    if usable_from is not None:
        df = df[df["date"] >= usable_from].reset_index(drop=True)
        if df.empty:
            return df
    if bench is None:
        bench = benchmark_frame(con, cfg["signals"]["regime_index"])
    if not bench.empty:
        # Left join: a stock keeps its own bars even if the index is missing a
        # day, so relative strength degrades to NaN rather than dropping rows.
        df = df.merge(bench, on="date", how="left")
        df["bench_close"] = df["bench_close"].ffill()
    df = core.compute_all(df)
    return timing.compute(df, cfg)


def latest_scores(con, symbols: list[str], cfg: dict | None = None) -> pd.DataFrame:
    """One row per symbol: today's score and its components."""
    cfg = cfg or timing.load_config()
    bench = benchmark_frame(con, cfg["signals"]["regime_index"])
    rows = []
    for sym in symbols:
        f = scored_frame(con, sym, cfg, bench)
        if f.empty:
            continue
        r = f.iloc[-1]
        rows.append({
            "symbol": sym, "date": r["date"], "close": r["close"],
            "timing_score": r["timing_score"], "trend": r["trend"],
            "momentum": r["momentum"], "relative_strength": r["relative_strength"],
            "volume": r["volume_score"], "volatility": r["volatility"],
            "rsi14": r["rsi14"], "atr_pct": r["atr_pct"], "adx14": r["adx14"],
        })
    out = pd.DataFrame(rows)
    return out.sort_values("timing_score", ascending=False).reset_index(drop=True) \
        if not out.empty else out
