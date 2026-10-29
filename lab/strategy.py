"""What a strategy is: a ranking, the settings around it, and its family.

The ranking is a function of the engine's Inputs (backtest/pit_engine.py)
returning a wide date x symbol frame, higher is better. Everything else --
how many names, how often to rebalance, the 200-DMA gate, hysteresis -- is
the momentum_strategy block of config/scoring.yaml, overridden per strategy
in `settings`, so a new idea only states what it changes.

`family` groups variants of one idea. A holdout spent by one member is
spent for all of them: a slower-rebalancing momentum is still momentum,
and the 2023+ data has already been seen for it.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from backtest.pit_engine import ScoreFn

STATUSES = ("research", "forward", "retired")


@dataclass(frozen=True)
class Strategy:
    name: str
    family: str
    summary: str                       # one line, shown in listings
    score: ScoreFn
    settings: dict = field(default_factory=dict)
    status: str = "research"           # forward = recorded monthly in history/lab/

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError(f"{self.name}: status must be one of {STATUSES}")

    def config(self, base: dict) -> dict:
        """The scoring config with this strategy's settings applied. The base
        is never modified."""
        cfg = copy.deepcopy(base)
        cfg["momentum_strategy"] = {**cfg.get("momentum_strategy", {}), **self.settings}
        return cfg
