"""Pass rules, written before a strategy's first run.

A rule is a list of checks on the harness's metrics (lab/harness.py
METRICS), one list for train and one for the holdout. The holdout list is
only ever judged for a strategy that passed train. Rules are data, not
code, so the ledger can fingerprint them: a rule edited after its train
run no longer matches, and the harness refuses the holdout (lab/ledger.py).

Add a rule here, commit it, then run train. Never the other way round.
"""
from __future__ import annotations

import hashlib
import json
import operator
from dataclasses import asdict, dataclass

OPS = {">=": operator.ge, ">": operator.gt, "<=": operator.le, "<": operator.lt}


@dataclass(frozen=True)
class Check:
    metric: str
    op: str
    value: float

    def judge(self, metrics: dict) -> bool:
        v = metrics.get(self.metric)
        # bool(): a numpy comparison would be saved to JSON as the string "False".
        return bool(v is not None and v == v and OPS[self.op](v, self.value))

    def __str__(self) -> str:
        return f"{self.metric} {self.op} {self.value:g}"


@dataclass(frozen=True)
class PassRule:
    fixed_on: str                  # the date the rule was written
    train: tuple[Check, ...]
    holdout: tuple[Check, ...]
    note: str = ""

    def fingerprint(self) -> str:
        """Stable across runs; changes when any check, value or date does."""
        blob = json.dumps(asdict(self), sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def judge(self, split: str, metrics: dict) -> dict:
        checks = self.train if split == "train" else self.holdout
        results = {str(c): c.judge(metrics) for c in checks}
        return {"checks": results, "passed": all(results.values())}


def C(metric: str, op: str, value: float) -> Check:
    return Check(metric, op, value)


# The two factor rules of 2026-10-19 (research/run_factor_backtest.py),
# restated on the harness's metric names. Their train has already run.
RULES: dict[str, PassRule] = {
    "high52": PassRule(
        "2026-10-19",
        train=(C("excess_cagr_pct", ">=", 2.0),
               C("edge_vs_controls_pct", ">=", 2.0),
               C("edge_vs_controls_sd", ">=", 1.0)),
        holdout=(C("excess_cagr_pct", ">", 0.0),
                 C("edge_vs_controls_pct", ">", 0.0)),
        note="beat NIFTY 500 and random picks by 2 points a year"),
    "lowvol": PassRule(
        "2026-10-19",
        train=(C("sharpe_vs_bench", ">=", 0.20),
               C("drawdown_vs_bench_pts", ">=", 5.0),
               C("cagr_vs_bench", ">=", -1.0)),
        holdout=(C("sharpe_vs_bench", ">", 0.0),
                 C("drawdown_vs_bench_pts", ">", 0.0)),
        note="a better Sharpe and a shallower fall, for at most 1 point of return"),
}


def rule_for(name: str) -> PassRule | None:
    return RULES.get(name)
