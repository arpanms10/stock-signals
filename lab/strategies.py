"""The registered strategies.

Each is a ranking from backtest/pit_engine.py's SCORES or a function
written here, plus the settings it changes. Pass rules live with the
harness (lab/rules.py), keyed by name, so a rule can be fixed before the
strategy is ever run.
"""
from __future__ import annotations

from backtest import pit_engine as pit
from lab.registry import register
from lab.strategy import Strategy

# --- momentum: the live ranking, its comparison lookback and variants -----
register(Strategy(
    "momentum_9_1", "momentum",
    "9-month return skipping the latest month, / volatility, above the 200 DMA "
    "(the live ranking)",
    pit.score_ram, {"lookback_months": 9, "skip_months": 1}))
register(Strategy(
    "momentum_12_1", "momentum",
    "12-1 momentum / volatility: the comparison lookback shown alongside",
    pit.score_ram, {"lookback_months": 12, "skip_months": 1}))
register(Strategy(
    "momentum_nse_blend", "momentum",
    "6- and 12-month momentum / volatility, z-scored 50/50 (Nifty200 Momentum 30 style)",
    pit.score_nse_blend, {"lookback_months": 12, "skip_months": 1}))
register(Strategy(
    "momentum_overlap", "momentum",
    "9-1 momentum, holding only names top-15 on both 9-1 and 12-1",
    pit.score_ram, {"lookback_months": 9, "skip_months": 1, "selection": "overlap",
                    "compare_lookback_months": 12}))

# --- factors tested 2026-10-19 (research/run_factor_backtest.py) ----------
register(Strategy(
    "high52", "high52",
    "closeness to the 52-week high, no 200-DMA gate",
    pit.score_high52, {"require_above_200dma": False}))
register(Strategy(
    "lowvol", "lowvol",
    "lowest 252-session volatility first, no 200-DMA gate",
    pit.score_lowvol, {"require_above_200dma": False}))
