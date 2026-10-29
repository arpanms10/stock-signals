"""Which test windows each idea has already seen. In git, because it is a
fact about the research, not about you.

research/lab_ledger.json has two parts:

  families    one per idea. `holdout` says whether the 2023+ window has been
              seen for it, when and by what; once it has, every member of the
              family is forward-tracked, never holdout-tested. `seen` lists
              other windows already looked at (liquidity ranks 201-500, say),
              so nobody reports them later as an untouched check. `trials`
              counts the variants tried, for the luck check.
  strategies  one per registered name: the rule fingerprint each run was
              judged under and every train / holdout run with its verdict.

The guard refuses:
  train    without a pass rule;
  holdout  when the family's holdout is spent, when train has not passed,
           when the rule changed after the train run, or a second time.

Families from before the lab were entered by hand from docs/ROADMAP.md and
the write-ups; they carry `source` instead of runs.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "research" / "lab_ledger.json"


class Refused(Exception):
    """The run would reuse a window this idea has already seen."""


def load(path: Path = PATH) -> dict:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        data = {}
    data.setdefault("families", {})
    data.setdefault("strategies", {})
    return data


def save(data: dict, path: Path = PATH) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=False) + "\n")
    tmp.replace(path)


def family(data: dict, name: str) -> dict:
    return data["families"].setdefault(name, {"holdout": None, "seen": [], "trials": 0})


def _runs(data: dict, strategy: str, split: str) -> list[dict]:
    return [r for r in data["strategies"].get(strategy, {}).get("runs", [])
            if r["split"] == split]


def check(data: dict, strategy, split: str, fingerprint: str | None) -> None:
    """Raise Refused if this run would break the rules above."""
    if split == "robustness":
        return
    fam = data["families"].get(strategy.family, {})
    if split == "holdout" and fam.get("holdout"):
        h = fam["holdout"]
        raise Refused(f"The {strategy.family} family's holdout was spent on "
                      f"{h.get('on')} ({h.get('by')}). Forward-track {strategy.name} "
                      "instead: set status='forward' in lab/strategies.py.")
    if fingerprint is None:
        raise Refused(f"{strategy.name} has no pass rule. Write one in lab/rules.py "
                      "and commit it before the first run.")
    if split == "train":
        return
    if _runs(data, strategy.name, "holdout"):
        raise Refused(f"{strategy.name}'s holdout already ran: it is spent.")
    trains = _runs(data, strategy.name, "train")
    if not trains:
        raise Refused(f"Run train for {strategy.name} first.")
    if any(r["rule"] != fingerprint for r in trains):
        raise Refused(f"{strategy.name}'s pass rule changed after its train run, so "
                      "the holdout would judge a rule chosen with the train result "
                      "in view. Register the idea under a new name.")
    if not trains[-1]["passed"]:
        raise Refused(f"{strategy.name} failed train; its holdout stays unseen.")


def record(data: dict, strategy, split: str, fingerprint: str | None,
           result: dict, on: dt.date | None = None) -> None:
    on = (on or dt.date.today()).isoformat()
    entry = data["strategies"].setdefault(strategy.name, {"family": strategy.family,
                                                          "runs": []})
    fam = family(data, strategy.family)
    m = result.get("metrics", {})
    verdict = result.get("verdict") or {}
    if split == "robustness":
        window = f"{result.get('universe')}, {result.get('start')} .. {result.get('end')}"
        if window not in fam["seen"]:
            fam["seen"].append(window)
        return
    if not _runs(data, strategy.name, "train") and split == "train":
        fam["trials"] = fam.get("trials", 0) + 1
    entry["runs"].append({
        "split": split, "on": on, "rule": fingerprint,
        "window": f"{result.get('start')} .. {result.get('end')}",
        "passed": bool(verdict.get("passed")),
        "cagr_pct": m.get("cagr_pct"), "excess_cagr_pct": m.get("excess_cagr_pct"),
        "edge_vs_controls_pct": m.get("edge_vs_controls_pct"),
        "sharpe": m.get("sharpe"), "max_drawdown_pct": m.get("max_drawdown_pct"),
    })
    if split == "holdout":
        fam["holdout"] = {"on": on, "by": strategy.name,
                          "window": f"{result.get('start')} .."}


def status(data: dict, strategy) -> str:
    """One phrase for listings: where this strategy stands."""
    trains = _runs(data, strategy.name, "train")
    holds = _runs(data, strategy.name, "holdout")
    fam = data["families"].get(strategy.family, {})
    if holds:
        return "holdout " + ("passed" if holds[-1]["passed"] else "failed")
    if trains:
        if not trains[-1]["passed"]:
            return "train failed"
        if fam.get("holdout"):
            return "train passed, family holdout spent: forward only"
        return "train passed, holdout unseen"
    if fam.get("holdout"):
        return "not run; family holdout spent: forward only"
    return "not run"
