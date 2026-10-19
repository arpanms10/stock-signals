"""Which stocks the results engine follows, and why each one is on the list.

  holding    config/holdings.csv (or the Kite snapshot)
  watch      config/watching.csv
  swing      the latest recorded swing scan's top picks, every universe
  momentum   the latest month's momentum picks (history/momentum_picks.xlsx)

Each source is read independently: a missing or broken one drops out of the
list with a note, never takes the others down with it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MOMENTUM = ROOT / "history" / "momentum_picks.xlsx"


def _holdings() -> list[str]:
    from book import portfolio as pf
    return [h["tradingsymbol"] for h in pf.load_holdings()]


def _watch() -> list[str]:
    from book import watching as wg
    return wg.symbols()


def _swing() -> list[str]:
    from records import swing_store as ss
    s, r = ss.scans(), ss.runs()
    if s.empty or r.empty:
        return []
    r = r.assign(top_n=[json.loads(c)["top_n"] for c in r["config_json"]])
    latest = s.groupby("universe")["price_date"].transform("max")
    s = s[s["price_date"] == latest].merge(r[["price_date", "universe", "top_n"]],
                                           on=["price_date", "universe"])
    top = s[s["suggest"].astype(bool) & (s["rank"] <= s["top_n"])]
    return top["symbol"].drop_duplicates().tolist()


def _momentum(path: Path = MOMENTUM) -> list[str]:
    if not path.exists():
        return []
    book = pd.ExcelFile(path)
    return book.parse(book.sheet_names[-1])["symbol"].dropna().astype(str).tolist()


SOURCES = {"holding": _holdings, "watch": _watch, "swing": _swing,
           "momentum": _momentum}


def symbols(log=None) -> dict[str, list[str]]:
    """symbol -> the sources it came from, in SOURCES order."""
    out: dict[str, list[str]] = {}
    for name, read in SOURCES.items():
        try:
            found = read()
        except Exception as exc:
            if log:
                log(f"coverage: could not read {name} list ({exc})")
            continue
        for sym in found:
            sym = str(sym).strip().upper()
            # REITs and InvITs file no Ind-AS results.
            if sym and not sym.endswith("-RR") and not sym.endswith("-IV"):
                out.setdefault(sym, [])
                if name not in out[sym]:
                    out[sym].append(name)
    return out
