"""The latest result of each strategy's runs, for the dashboard and reports.

config/lab_results.json, git-ignored like the other calibration files:
{strategy: {split: result}}. Curves are kept month-end only, which is
what the Strategy lab page draws. The ledger (lab/ledger.py) is the record
of what was run; this file only holds the numbers of the latest run.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "config" / "lab_results.json"


def _monthly(curve: pd.Series) -> dict[str, float]:
    if curve is None or len(curve) == 0:
        return {}
    c = curve.copy()
    c.index = pd.to_datetime(c.index)
    m = c.resample("ME").last().dropna()
    return {d.strftime("%Y-%m-%d"): round(float(v), 2) for d, v in m.items()}


def load(path: Path = PATH) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save(strategy: str, split: str, result: dict, path: Path = PATH) -> None:
    data = load(path)
    r = dict(result)
    curves = r.pop("_curves", {})
    r["curves"] = {k: _monthly(v) for k, v in curves.items()
                   if k in ("strategy", "benchmark", "universe_ew", "after_tax")}
    data.setdefault(strategy, {})[split] = r
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str))
    tmp.replace(path)
