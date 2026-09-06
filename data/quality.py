"""Price-series quality gates.

NSE's corporate-actions API does not return everything: JSW Steel's 10:1 face
value split is simply absent, leaving an unadjusted -90% cliff in the raw data.
Demergers (Tata Motors, Oct 2025) are real events that cannot be adjusted at all
without the demerger ratio.

We deliberately do NOT infer a factor from the size of the gap. An unadjusted
split and a genuine crash look identical in price alone, and "helpfully"
adjusting a real crash away would erase exactly the event the exit rules exist
to catch. Instead a symbol's usable history begins *after* its last unexplained
gap, and the truncation is reported rather than applied silently.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from data import store

MAX_UNEXPLAINED_JUMP_PCT = 35.0


def suspect_bars(con, symbol: str) -> list[tuple[dt.date, float]]:
    """Day-over-day moves too large to be real, not explained by a known action."""
    df = store.load_prices(con, symbol, adjusted=True)
    if len(df) < 2:
        return []
    ex_dates = {a.ex_date for a in store.load_actions(con, symbol)}
    pct = df["close"].pct_change() * 100
    out = []
    for i in pct[pct.abs() > MAX_UNEXPLAINED_JUMP_PCT].index:
        d = df.loc[i, "date"]
        if d in ex_dates:
            continue
        out.append((d, float(pct.iloc[i])))
    return out


def usable_from(con, symbol: str) -> tuple[dt.date | None, list[str]]:
    """Earliest date whose history can be trusted, plus human-readable notes."""
    flags = suspect_bars(con, symbol)
    if not flags:
        return None, []
    last_date, last_pct = max(flags, key=lambda f: f[0])
    notes = [f"{symbol}: {len(flags)} unexplained gap(s); history truncated to "
             f"{last_date} onward (last was {last_pct:+.1f}%)"]
    return last_date, notes


def report(con, symbols: list[str]) -> dict[str, dt.date]:
    """Truncation points for a whole universe, printed once so it is visible."""
    cuts, notes = {}, []
    for s in symbols:
        cut, n = usable_from(con, s)
        if cut:
            cuts[s] = cut
            notes.extend(n)
    if notes:
        print("Data quality:")
        for n in notes:
            print(f"  {n}")
    return cuts
