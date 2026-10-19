"""config/mf_holdings.csv or .xlsx: your MF book as percentages, no amounts.

    fund,pct,held_over_1y,notes
    Parag Parikh Flexi Cap,30,yes,
    122639,30,,                      # an AMFI code works too

`pct` is the fund's share of the whole MF portfolio (all funds, all types),
so the column should add up to 100. `held_over_1y` is optional: yes / no /
blank. It only decides whether a sell verdict warns about exit load and
short-term gains.

The .xlsx is read from its first sheet. Headers can be loose ("Fund Name",
"Scheme", "Allocation %", "Weight"), and a column Excel formats as a
percentage (30% stored as 0.3) is read as 30. Keep one of the two files:
with both present it is not obvious which one you last edited.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from mf import lookup

ROOT = Path(__file__).resolve().parents[1]
HOLDINGS_CSV = ROOT / "config" / "mf_holdings.csv"
HOLDINGS_XLSX = ROOT / "config" / "mf_holdings.xlsx"

# Header as typed (lower-cased, punctuation dropped) -> the column used here.
COLUMNS = {
    "fund": "fund", "fund name": "fund", "scheme": "fund", "scheme name": "fund",
    "name": "fund", "mutual fund": "fund", "amfi code": "fund", "scheme code": "fund",
    "pct": "pct", "percent": "pct", "percentage": "pct", "weight": "pct",
    "allocation": "pct", "allocation pct": "pct", "share": "pct", "pct of book": "pct",
    "held over 1y": "held_over_1y", "held_over_1y": "held_over_1y",
    "held over 1 year": "held_over_1y", "over 1y": "held_over_1y",
    "notes": "notes", "note": "notes", "comments": "notes",
}


def find_file(config_dir: Path = ROOT / "config") -> Path | None:
    csv, xlsx = config_dir / "mf_holdings.csv", config_dir / "mf_holdings.xlsx"
    if csv.exists() and xlsx.exists():
        raise SystemExit(f"Both {csv.name} and {xlsx.name} are in config/; keep one.")
    return csv if csv.exists() else xlsx if xlsx.exists() else None


def _header(c) -> str:
    h = re.sub(r"[^a-z0-9_ ]+", " ", str(c).lower())
    return COLUMNS.get(re.sub(r"\s+", " ", h).strip(), str(c).strip().lower())


def read_raw(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in (".xlsx", ".xls"):
        # The first sheet that isn't the "How to fill" guide (book/userfiles.py).
        from book import userfiles as uf
        sheets = pd.ExcelFile(path).sheet_names
        data = next((n for n in sheets if n.strip().lower() != uf.GUIDE_SHEET.lower()),
                    sheets[0])
        raw = pd.read_excel(path, sheet_name=data, dtype=object)
        raw = raw.dropna(how="all")
    else:
        raw = pd.read_csv(path, comment="#", dtype=str, skipinitialspace=True)
    raw.columns = [_header(c) for c in raw.columns]
    raw = raw.loc[:, ~raw.columns.duplicated()]
    return raw.fillna("").astype(str)


@dataclass
class Book:
    table: pd.DataFrame                       # code, name, weight, held_over_1y, notes
    unresolved: list[tuple[str, list]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def load(schemes: pd.DataFrame, latest_month: str | None,
         path: Path | None = None) -> Book | None:
    path = path or find_file()
    if path is None or not path.exists():
        return None
    raw = read_raw(path)
    if not {"fund", "pct"} <= set(raw.columns):
        raise SystemExit(f"{path.name}: needs a fund column and a percentage column "
                         f"(found: {', '.join(raw.columns)})")
    rows, unresolved, warnings = [], [], []
    for r in raw.itertuples():
        if not r.fund.strip() or r.fund.strip().startswith("#"):
            continue
        if r.fund.strip().endswith(".0") and r.fund.strip()[:-2].isdigit():
            r = r._replace(fund=r.fund.strip()[:-2])    # AMFI code read as a number
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
    # Excel stores a cell formatted as 30% as 0.3.
    if len(t) and t["weight"].max() <= 1 and abs(t["weight"].sum() - 1) < 0.02:
        t["weight"] = t["weight"] * 100
    total = t["weight"].sum()
    if len(t) and abs(total - 100) > 1 and not unresolved:
        warnings.append(f"weights add up to {total:.1f}%, not 100%; scaled to 100")
        t["weight"] = t["weight"] * 100 / total
    return Book(t, unresolved, warnings)
