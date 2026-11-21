"""Value every holding: own 5-year band, peers, reverse DCF, scenarios.

Scenarios from the explicit assumptions in config/valuation.yaml, not price
targets. Uses only data on disk (run scripts/refresh_holding_data.py first).

    PYTHONPATH=. .venv/bin/python scripts/value_holdings.py
    PYTHONPATH=. .venv/bin/python scripts/value_holdings.py --export data/results/valuations.json
"""
from __future__ import annotations
import sys as _sys; from pathlib import Path as _P; _sys.path.insert(0, str(_P(__file__).resolve().parents[1]))  # run from anywhere

import argparse
import dataclasses
import json
from pathlib import Path

from book import valuations as bv
from strategy import valuation as val


def _f(x, fmt="{:.0f}") -> str:
    return "—" if x is None else fmt.format(x)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--export", metavar="PATH", help="write every valuation as JSON")
    args = ap.parse_args()
    vs, notes = bv.build()
    for n in notes:
        print(n)
    print(f"{'holding':13} {'method':22} {'price':>9} {'multiple':>14} {'own pct':>7} "
          f"{'implied':>7} {'deliv':>6} {'bear':>8} {'base':>8} {'bull':>8} {'MoS':>5}")
    for v in sorted(vs, key=lambda v: v.symbol):
        s, h = v.scenarios, v.headline
        mult = f"{val.MULT_LABEL.get(h, h)} {v.current[h]:.1f}" if v.current.get(h) else "—"
        pct = v.band.get("percentile")
        print(f"{v.symbol:13} {val.METHOD_LABEL[v.method][:22]:22} {_f(v.price, '{:.1f}'):>9} "
              f"{mult:>14} {_f(None if pct is None else pct * 100):>7} "
              f"{_f(v.reverse.get('implied_pct'), '{:.1f}'):>7} "
              f"{_f(v.reverse.get('delivered_pct'), '{:.1f}'):>6} "
              + " ".join(f"{_f(s.get(k, {}).get('fair_value')):>8}" for k in val.SCEN)
              + f" {_f(v.margin_of_safety_pct):>5}")
    if args.export:
        p = Path(args.export)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"note": "scenarios from config/valuation.yaml, not price targets",
                                 "valuations": [dataclasses.asdict(v) for v in vs]},
                                default=str, indent=2))
        print("wrote", p)
    print("Scenarios, not price targets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
