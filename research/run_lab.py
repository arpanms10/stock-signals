"""Strategy lab: test a registered strategy with the same harness as every other.

    PYTHONPATH=. .venv/bin/python research/run_lab.py list
    PYTHONPATH=. .venv/bin/python research/run_lab.py show momentum_9_1
    PYTHONPATH=. .venv/bin/python research/run_lab.py train high52
    PYTHONPATH=. .venv/bin/python research/run_lab.py holdout high52     # once, after train
    PYTHONPATH=. .venv/bin/python research/run_lab.py robustness high52  # ranks 201-500
    PYTHONPATH=. .venv/bin/python research/run_lab.py measure momentum_9_1  # spent families
    PYTHONPATH=. .venv/bin/python research/run_lab.py compare high52 lowvol --weight 0.5

A strategy is registered in lab/strategies.py and its pass rule in
lab/rules.py, committed before `train` runs. `holdout` runs only for a
strategy whose train passed under the same rule, and only once. Both are
enforced by the ledger, research/lab_ledger.json. `robustness` (liquidity
ranks 201-500, whole period) is printed for reading and judged by no rule.
`measure` re-measures a strategy over the whole window with no verdict --
only for a family whose holdout is already spent, such as momentum.

Results go to config/lab_results.json for the Strategy lab page. See
docs/lab.md.
"""
from __future__ import annotations
import sys as _sys; from pathlib import Path as _P; _sys.path.insert(0, str(_P(__file__).resolve().parents[1]))  # run from anywhere

import argparse
import datetime as dt

from backtest.tax_lots import TaxRules
from data import bhavcopy as bc
from data import store
from lab import harness, ledger, registry, results
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
    if "after_tax_cagr_pct" in m:
        print(f"after tax ({r['tax']['regime']} rates): {m['after_tax_cagr_pct']}% with "
              f"each year's tax paid, {m['after_tax_liquidation_cagr_pct']}% sold at the "
              f"end; NIFTY 500 held and sold: {r['benchmark']['after_tax_cagr_pct']}%")
    if "deflated_sharpe" in m:
        import pandas as pd
        print(f"\nIs it luck? deflated Sharpe {m['deflated_sharpe']} after {r['trials']} "
              f"trial(s) in the family (above 0.95 is convincing); yearly return over "
              f"the index {m.get('excess_range_low')} to {m.get('excess_range_high')} pts "
              f"(90% range); halves {m['first_half_excess_pct']} / "
              f"{m['second_half_excess_pct']} pts over the index")
        print(pd.DataFrame(r["by_year"]).set_index("year").to_string())
    print(f"edge vs random picks: {m.get('edge_vs_controls_pct')} pts "
          f"({m.get('edge_vs_controls_sd')} sd); vs the universe: "
          f"{m.get('excess_vs_universe_pct')} pts")
    if verdict is not None:
        print("\nPass rule:")
        for check, ok in verdict["checks"].items():
            print(f"  {'PASS' if ok else 'FAIL'}  {check}")
        print(f"  => {'PASSED' if verdict['passed'] else 'FAILED'}")


def cmd_list(_a) -> int:
    data = ledger.load()
    for s in registry.all_strategies():
        rule = "rule" if rule_for(s.name) else "no rule"
        print(f"{s.name:<22} {s.family:<10} {s.status:<9} {rule:<8} "
              f"{ledger.status(data, s)}")
    print("\nFamilies:")
    for name, f in data["families"].items():
        h = f.get("holdout")
        state = f"holdout spent {h['on']}" if h else "holdout unseen"
        print(f"  {name:<18} {state:<24} trials {f.get('trials', 0)}")
    return 0


def cmd_show(a) -> int:
    s = registry.get(a.name)
    print(f"{s.name}  (family {s.family}, {s.status})\n  {s.summary}")
    print(f"  settings: {s.settings or 'momentum_strategy defaults'}")
    print(f"  ledger: {ledger.status(ledger.load(), s)}")
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
    guard = _guard(s, split, rule)
    if guard:
        print(guard)
        return 1
    con, base, bench, last = _context(a.db)
    if split == "train":
        start, end, size, skip = *harness.TRAIN, 200, 0
    elif split == "holdout":
        start, end, size, skip = harness.HOLDOUT_START, last, 200, 0
    elif split == "measure":
        start, end, size, skip = harness.TRAIN[0], last, 200, 0
    else:
        start, end, size, skip = harness.TRAIN[0], last, 300, 200
    tax = (False if a.tax == "none" else
           TaxRules.from_config(base, regime=a.tax, use_exemption=not a.no_exemption))
    data = ledger.load()
    trials = ledger.family(data, s.family).get("trials", 0) + (
        0 if data["strategies"].get(s.name, {}).get("runs") else 1)
    r = harness.evaluate(s, con, base, start, end, bench, seeds=a.seeds,
                         universe_size=size, universe_skip=skip, progress=_say, tax=tax,
                         trials=max(trials, 1))
    verdict = (harness.judge(s.name, split, r)
               if split in ("train", "holdout") else None)
    r["verdict"] = verdict
    r["run_on"] = dt.date.today().isoformat()
    _print_result(r, verdict)
    _record(s, split, rule, r)
    results.save(s.name, split, r)
    print(f"\nWritten: {results.PATH}")
    return 0


def _guard(s, split, rule) -> str | None:
    try:
        ledger.check(ledger.load(), s, split, rule.fingerprint() if rule else None)
    except ledger.Refused as e:
        return f"Refused: {e}"
    return None


def _record(s, split, rule, r) -> None:
    data = ledger.load()
    ledger.record(data, s, split, rule.fingerprint() if rule else None, r)
    ledger.save(data)
    print(f"Ledger: {ledger.PATH} -- commit it with the result")


def cmd_compare(a) -> int:
    from lab import compare
    corr = compare.correlation(a.split)
    if corr.empty:
        print(f"No {a.split} results yet: run train first.")
        return 1
    print(f"Correlation of monthly returns ({a.split}):")
    print(corr.to_string())
    if a.names:
        if len(a.names) != 2:
            print("Name two strategies to blend.")
            return 1
        b = compare.blend(*a.names, weight_a=a.weight, split=a.split)
        if not b:
            print("Those two have no months in common.")
            return 1
        print(f"\nBlend, rebalanced monthly, before costs (correlation {b.pop('correlation')}):")
        import pandas as pd
        print(pd.DataFrame(b).T.to_string())
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(ROOT / "data" / "market.db"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    cp = sub.add_parser("compare")
    cp.add_argument("names", nargs="*", help="two strategies to blend")
    cp.add_argument("--weight", type=float, default=0.5, help="weight of the first")
    cp.add_argument("--split", default="train",
                    choices=["train", "holdout", "robustness", "measure"])
    for name in ("show", "train", "holdout", "robustness", "measure"):
        p = sub.add_parser(name)
        p.add_argument("name")
        if name != "show":
            p.add_argument("--seeds", type=int, default=harness.SEEDS)
            p.add_argument("--tax", choices=["current", "historical", "none"],
                           default="current",
                           help="current = today's rates on every sale (default); "
                                "historical = the rates in force on each sale date")
            p.add_argument("--no-exemption", action="store_true",
                           help="assume the Rs 1.25 lakh LTCG exemption is used elsewhere")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        return cmd_list(a)
    if a.cmd == "show":
        return cmd_show(a)
    if a.cmd == "compare":
        return cmd_compare(a)
    return cmd_run(a, a.cmd)


if __name__ == "__main__":
    raise SystemExit(main())
