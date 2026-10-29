"""Strategy lab: test a registered strategy with the same harness as every other.

    PYTHONPATH=. .venv/bin/python research/run_lab.py list
    PYTHONPATH=. .venv/bin/python research/run_lab.py show momentum_9_1
    PYTHONPATH=. .venv/bin/python research/run_lab.py train high52
    PYTHONPATH=. .venv/bin/python research/run_lab.py holdout high52     # once, after train
    PYTHONPATH=. .venv/bin/python research/run_lab.py robustness high52  # ranks 201-500

A strategy is registered in lab/strategies.py and its pass rule in
lab/rules.py, committed before `train` runs. `holdout` runs only for a
strategy whose train passed under the same rule, and only once. Both are
enforced by the ledger, research/lab_ledger.json. `robustness` (liquidity
ranks 201-500, whole period) is printed for reading and judged by no rule.

Results go to config/lab_results.json for the Strategy lab page. See
docs/lab.md.
"""
from __future__ import annotations
import sys as _sys; from pathlib import Path as _P; _sys.path.insert(0, str(_P(__file__).resolve().parents[1]))  # run from anywhere

import argparse
import datetime as dt

from data import bhavcopy as bc
from data import store
from lab import harness, registry, results
from lab.rules import rule_for
from scoring import timing

ROOT = _P(__file__).resolve().parents[1]


def _say(msg: str) -> None:
    print(msg, flush=True)


def _context(db: str):
    con = bc.connect(db)
    base = timing.load_config()
    bench = store.load_index(store.connect(), base["signals"]["regime_index"])
    last = dt.date.fromisoformat(sorted(bc.have_days(con))[-1])
    return con, base, bench, last


def _print_result(r: dict, verdict: dict | None) -> None:
    print()
    print(harness.table(r).to_string())
    m = r["metrics"]
    if "controls" in r:
        print(f"random-pick CAGRs: {r['controls']['cagr_pct']} (sd {r['controls']['sd']})")
    print(f"edge vs random picks: {m.get('edge_vs_controls_pct')} pts "
          f"({m.get('edge_vs_controls_sd')} sd); vs the universe: "
          f"{m.get('excess_vs_universe_pct')} pts")
    if verdict is not None:
        print("\nPass rule:")
        for check, ok in verdict["checks"].items():
            print(f"  {'PASS' if ok else 'FAIL'}  {check}")
        print(f"  => {'PASSED' if verdict['passed'] else 'FAILED'}")


def cmd_list(_a) -> int:
    for s in registry.all_strategies():
        rule = "rule" if rule_for(s.name) else "no rule"
        print(f"{s.name:<22} {s.family:<10} {s.status:<9} {rule:<8} {s.summary}")
    return 0


def cmd_show(a) -> int:
    s = registry.get(a.name)
    print(f"{s.name}  (family {s.family}, {s.status})\n  {s.summary}")
    print(f"  settings: {s.settings or 'momentum_strategy defaults'}")
    rule = rule_for(s.name)
    if rule is None:
        print("  no pass rule yet: add one to lab/rules.py and commit it before train")
    else:
        print(f"  rule fixed {rule.fixed_on} ({rule.fingerprint()}): {rule.note}")
        print("    train:   " + "; ".join(map(str, rule.train)))
        print("    holdout: " + "; ".join(map(str, rule.holdout)))
    return 0


def cmd_run(a, split: str) -> int:
    s = registry.get(a.name)
    rule = rule_for(s.name)
    if split != "robustness" and rule is None:
        print(f"{s.name} has no pass rule. Write one in lab/rules.py and commit it first.")
        return 1
    guard = _guard(s, split, rule)
    if guard:
        print(guard)
        return 1
    con, base, bench, last = _context(a.db)
    if split == "train":
        start, end, size, skip = *harness.TRAIN, 200, 0
    elif split == "holdout":
        start, end, size, skip = harness.HOLDOUT_START, last, 200, 0
    else:
        start, end, size, skip = harness.TRAIN[0], last, 300, 200
    r = harness.evaluate(s, con, base, start, end, bench, seeds=a.seeds,
                         universe_size=size, universe_skip=skip, progress=_say)
    verdict = harness.judge(s.name, split, r) if split != "robustness" else None
    r["verdict"] = verdict
    r["run_on"] = dt.date.today().isoformat()
    _print_result(r, verdict)
    _record(s, split, rule, r)
    results.save(s.name, split, r)
    print(f"\nWritten: {results.PATH}")
    return 0


def _guard(s, split, rule) -> str | None:
    """Phase 3 hands this to the ledger."""
    return None


def _record(s, split, rule, r) -> None:
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(ROOT / "data" / "market.db"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    for name in ("show", "train", "holdout", "robustness"):
        p = sub.add_parser(name)
        p.add_argument("name")
        if name != "show":
            p.add_argument("--seeds", type=int, default=harness.SEEDS)
    a = ap.parse_args(argv)
    if a.cmd == "list":
        return cmd_list(a)
    if a.cmd == "show":
        return cmd_show(a)
    return cmd_run(a, a.cmd)


if __name__ == "__main__":
    raise SystemExit(main())
