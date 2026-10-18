"""config/mf_holdings.csv: your MF book as percentages, no amounts.

    fund,pct,held_over_1y,notes
    Parag Parikh Flexi Cap,30,yes,
    122639,30,,                      # an AMFI code works too

`pct` is the fund's share of the whole MF portfolio (all funds, all types),
so the column should add up to 100. `held_over_1y` is optional: yes / no /
blank. It only decides whether a sell verdict warns about exit load and
short-term gains.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from mf import lookup

ROOT = Path(__file__).resolve().parents[1]
HOLDINGS = ROOT / "config" / "mf_holdings.csv"


@dataclass
class Book:
    table: pd.DataFrame                       # code, name, weight, held_over_1y, notes
    unresolved: list[tuple[str, list]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def load(schemes: pd.DataFrame, latest_month: str | None,
         path: Path = HOLDINGS) -> Book | None:
    if not path.exists():
        return None
    raw = pd.read_csv(path, comment="#", dtype=str, skipinitialspace=True).fillna("")
    raw.columns = [c.strip().lower() for c in raw.columns]
    if not {"fund", "pct"} <= set(raw.columns):
        raise SystemExit(f"{path}: needs at least the columns fund,pct")
    rows, unresolved, warnings = [], [], []
    for r in raw.itertuples():
        if not r.fund.strip():
            continue
        m = lookup.find(r.fund, schemes, latest_month)
        if not m.resolved:
            unresolved.append((r.fund, m.candidates[:4]))
            continue
        try:
            w = float(str(r.pct).replace("%", ""))
        except ValueError:
            warnings.append(f"{r.fund}: pct '{r.pct}' is not a number; skipped")
            continue
        rows.append({"code": m.code, "name": m.name, "weight": w,
                     "held_over_1y": getattr(r, "held_over_1y", "") or None,
                     "notes": getattr(r, "notes", "")})
    t = pd.DataFrame(rows, columns=["code", "name", "weight", "held_over_1y", "notes"])
    if t["code"].duplicated().any():
        t = t.groupby("code", as_index=False).agg(
            {"name": "first", "weight": "sum", "held_over_1y": "first", "notes": "first"})
        warnings.append("a fund appears twice; weights were added together")
    total = t["weight"].sum()
    if len(t) and abs(total - 100) > 1 and not unresolved:
        warnings.append(f"weights add up to {total:.1f}%, not 100%; scaled to 100")
        t["weight"] = t["weight"] * 100 / total
    return Book(t, unresolved, warnings)
