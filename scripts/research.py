"""The research assistant's tools (Phase 7; docs/research.md).

    PYTHONPATH=. .venv/bin/python scripts/research.py pack [--monthly 25000]
        rebuild data/research/pack.json from the engines
    PYTHONPATH=. .venv/bin/python scripts/research.py check ANSWER.json [--save]
        check an answer against the pack; --save files it in data/research/answers/
    PYTHONPATH=. .venv/bin/python scripts/research.py path /holdings/TCS/scorecard/score
        print what the pack holds at a path
    PYTHONPATH=. .venv/bin/python scripts/research.py list
        saved answers and whether each passed

Claude Code drives these through the /research skill (.claude/skills/research).
"""
from __future__ import annotations
import sys as _sys; from pathlib import Path as _P; _sys.path.insert(0, str(_P(__file__).resolve().parents[1]))  # run from anywhere

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

from book import research_pack as rp
from strategy import answer_check as ac


def _pack() -> dict:
    p = rp.load()
    if p is None:
        sys.exit("no pack yet: run `scripts/research.py pack` first")
    return p


def save(answer: dict, result: dict, d: Path = rp.ANSWERS) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", answer["question"].lower()).strip("-")[:60]
    path = d / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{slug}.json"
    path.write_text(json.dumps({**answer, "check": result}, indent=1, ensure_ascii=False))
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack")
    p.add_argument("--monthly", type=float, default=0.0,
                   help="new money a month for the plan (default: none assumed)")
    p.add_argument("--months", type=int, default=12)
    c = sub.add_parser("check")
    c.add_argument("answer")
    c.add_argument("--save", action="store_true")
    g = sub.add_parser("path")
    g.add_argument("path")
    sub.add_parser("list")
    a = ap.parse_args()

    if a.cmd == "pack":
        pk = rp.build(a.monthly, a.months)
        out = rp.write(pk)
        print(f"wrote {out} ({out.stat().st_size // 1024} KB), generated_at "
              f"{pk['generated_at']}, prices as of {pk['as_of']['prices']}, "
              f"{len(pk['holdings'])} holdings")
        return 0
    if a.cmd == "path":
        try:
            print(json.dumps(ac.resolve(_pack(), a.path), indent=1, ensure_ascii=False))
        except (KeyError, IndexError, ValueError):
            print(f"{a.path}: not in the pack")
            return 1
        return 0
    if a.cmd == "check":
        ans = json.loads(Path(a.answer).read_text())
        ans.pop("check", None)
        r = ac.check(ans, _pack())
        print("PASS" if r["ok"] else "FAIL", f"({r['checked']} evidence items)")
        for e in r["errors"]:
            print("  error:", e)
        for w in r["warnings"]:
            print("  warning:", w)
        if a.save:
            print("saved", save(ans, r))
        return 0 if r["ok"] else 1
    for f, ans in rp.answers():
        ok = (ans.get("check") or {}).get("ok")
        print(f"{'PASS' if ok else 'FAIL'}  {f.name}  {ans.get('question', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
