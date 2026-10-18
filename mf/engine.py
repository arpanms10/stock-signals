"""Glue for run_mf.py and the dashboard: load, measure, decide."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from mf import holdings as hold
from mf import lookup, metrics, navdb, verdict


@dataclass
class Evaluation:
    as_of: pd.Period
    snap: pd.DataFrame                  # metrics for every fund in the categories touched
    decisions: list[verdict.Decision]
    names: dict[int, str]
    book: hold.Book | None
    unresolved: list[tuple[str, list]] = field(default_factory=list)
    corr: pd.DataFrame | None = None
    nav_date: dict[int, str] = field(default_factory=dict)


def latest_complete_month(P: pd.DataFrame, min_share: float = 0.8) -> pd.Period:
    """Newest month where most schemes have a NAV.

    The newest month is usually the partial current one, built from NAVAll
    alone. It counts once it carries most of the schemes the month before did.
    """
    counts = P.notna().sum(axis=1)
    t = P.index.max()
    if counts[t] >= min_share * counts[t - 1]:
        return t
    return t - 1


def evaluate(queries: list[str], use_holdings: bool = True,
             rules: dict | None = None, con=None) -> Evaluation:
    rules = rules or verdict.load_rules()
    con = con or navdb.connect()
    sch = navdb.schemes(con)
    if sch.empty:
        raise SystemExit("No NAV history yet: run  run_mf.py --refresh  first.")
    newest = sch["last_month"].max()
    names = dict(zip(sch["code"], sch["name"]))
    category = sch.set_index("code")["category"]

    codes, unresolved = [], []
    for q in queries:
        m = lookup.find(q, sch, newest)
        if m.resolved:
            codes.append(m.code)
        else:
            unresolved.append((q, m.candidates[:5]))

    book = hold.load(sch, newest) if use_holdings else None
    if book is not None:
        unresolved += book.unresolved
    held = list(book.table["code"]) if book is not None else []

    cats = {category.get(c) for c in codes + held} - {None}
    P = navdb.panel(con)
    t = latest_complete_month(P)
    snap = metrics.snapshot(P, category, t, rules.get("index_funds") or {},
                            categories=cats)
    corr = metrics.correlation(P, held, t) if len(held) > 1 else None
    decisions = verdict.decide(
        snap, names, rules,
        holdings=book.table if book is not None else None,
        evaluate=codes, corr=corr)
    return Evaluation(t, snap, decisions, names, book, unresolved, corr,
                      navdb.last_dates(con))


def compare_table(ev: Evaluation, codes: list[int]) -> pd.DataFrame:
    """Side by side, one column per fund."""
    rows = {
        "Category": "category", "History (months)": "history_months",
        "CAGR 1y": "cagr_1y", "CAGR 3y": "cagr_3y", "CAGR 5y": "cagr_5y",
        "vs category median 1y": "excess_1y", "vs category median 3y": "excess_3y",
        "vs category median 5y": "excess_5y",
        "Rank in category 1y": "rank_1y", "Rank in category 3y": "rank_3y",
        "Rank in category 5y": "rank_5y",
        "vs index fund 3y": "vs_index_3y", "vs index fund 5y": "vs_index_5y",
        "Beat median, rolling 1y windows": "consistency_3y",
        "Volatility 3y": "vol_3y", "Max drawdown 3y": "max_drawdown_3y",
        "Downside capture 3y": "downside_capture_3y", "Peers": "peers",
    }
    by_code = {d.code: d for d in ev.decisions}
    out = {}
    for c in codes:
        col = {}
        r = ev.snap.loc[c] if c in ev.snap.index else pd.Series(dtype=object)
        for label, key in rows.items():
            col[label] = r.get(key)
        d = by_code.get(c)
        col["State"] = d.state if d else None
        col["Action"] = d.action if d else None
        out[ev.names.get(c, str(c))] = col
    return pd.DataFrame(out)
