"""Run one registered strategy through the point-in-time engine."""
from __future__ import annotations

import datetime as dt

import pandas as pd

from backtest import pit_engine as pit
from backtest.costs import CostModel
from backtest.portfolio_engine import PortfolioResult
from lab.strategy import Strategy


def run(strategy: Strategy, con, base: dict, start: dt.date, end: dt.date,
        bench: pd.DataFrame, *, universe_size: int = 200, universe_skip: int = 0,
        shuffle_seed: int | None = None, costs: CostModel | None = None,
        capital: float = 1_000_000) -> PortfolioResult:
    """shuffle_seed picks at random from the same eligible names -- the
    strategy's own universe and gate -- which is the control it must beat."""
    return pit.run(con, strategy.config(base), start, end, bench, capital,
                   costs or CostModel(), universe_size, shuffle_seed=shuffle_seed,
                   universe_skip=universe_skip, score=strategy.score)
