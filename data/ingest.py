"""Incremental price ingestion.

Only ever downloads days it does not already have. NSE rate-limits, and a full
watchlist backfill is thousands of requests, so re-fetching what is already
cached is the difference between a 20-second nightly run and a 20-minute one.
"""
from __future__ import annotations

import datetime as dt
import time

from . import corporate_actions as ca
from . import store
from .sources import prices

MAX_UNEXPLAINED_JUMP_PCT = 35.0   # beyond this, a bar is suspect

BENCHMARK = "NIFTY 500"          # relative strength and the regime filter
PAUSE_SECONDS = 0.6              # be a polite client of a free endpoint


def _missing_ranges(have_from: dt.date | None, have_to: dt.date | None,
                    start: dt.date, today: dt.date) -> list[tuple[dt.date, dt.date]]:
    """Gaps to fetch, at both ends of what we already hold.

    Fetching only forward from the newest bar looks right and is wrong: asking
    for 10 years on a symbol that already holds 3 would silently return nothing
    and leave the backtest running on a third of the history it reported.
    """
    if have_from is None or have_to is None:
        return [(start, today)]
    gaps = []
    if start < have_from:
        gaps.append((start, have_from - dt.timedelta(days=1)))
    if have_to < today:
        gaps.append((have_to + dt.timedelta(days=1), today))
    return gaps


def _fetch_range(con, symbol: str, frm: dt.date, to: dt.date) -> int:
    """Walk a date range in yearly chunks -- NSE degrades on long spans."""
    rows, chunk_start = 0, frm
    while chunk_start <= to:
        chunk_end = min(chunk_start + dt.timedelta(days=365), to)
        try:
            df = prices.stock_history(symbol, chunk_start, chunk_end)
            rows += store.save_prices(con, df)
        except Exception as exc:
            print(f"  {symbol}: {chunk_start}..{chunk_end} failed: {exc}")
        chunk_start = chunk_end + dt.timedelta(days=1)
        time.sleep(PAUSE_SECONDS)
    return rows


def backfill_symbol(con, symbol: str, years: int = 10,
                    today: dt.date | None = None, verbose: bool = True) -> int:
    """Fetch missing bars for one symbol plus its corporate actions."""
    today = today or dt.date.today()
    start = today - dt.timedelta(days=365 * years)
    gaps = _missing_ranges(store.first_date(con, "stock", symbol),
                           store.last_date(con, "stock", symbol), start, today)
    if not gaps:
        if verbose:
            print(f"  {symbol}: up to date")
        return 0

    rows = sum(_fetch_range(con, symbol, frm, to) for frm, to in gaps)

    try:
        actions = ca.fetch(symbol, start, today)
        store.save_actions(con, actions)
        rights = ca.find_rights(symbol, start, today)
        if rights and verbose:
            print(f"  {symbol}: rights issue(s) not auto-adjusted -> {rights}")
    except Exception as exc:
        print(f"  {symbol}: corporate actions unavailable: {exc}")

    store.mark_fetched(con, "stock", symbol, store.last_date(con, "stock", symbol))
    if verbose:
        print(f"  {symbol}: +{rows} rows (through {store.last_date(con,'stock',symbol)})", flush=True)
    return rows


def backfill_index(con, index: str = BENCHMARK, years: int = 10,
                   today: dt.date | None = None, verbose: bool = True) -> int:
    today = today or dt.date.today()
    start = today - dt.timedelta(days=365 * years)
    gaps = _missing_ranges(store.first_date(con, "index", index),
                           store.last_date(con, "index", index), start, today)
    if not gaps:
        if verbose:
            print(f"  {index}: up to date")
        return 0
    rows = 0
    for frm, to in gaps:
        chunk_start = frm
        while chunk_start <= to:
            chunk_end = min(chunk_start + dt.timedelta(days=365), to)
            try:
                df = prices.index_history(index, chunk_start, chunk_end)
                rows += store.save_index_prices(con, df)
            except Exception as exc:
                print(f"  {index}: {chunk_start}..{chunk_end} failed: {exc}")
            chunk_start = chunk_end + dt.timedelta(days=1)
            time.sleep(PAUSE_SECONDS)
    store.mark_fetched(con, "index", index, store.last_date(con, "index", index))
    if verbose:
        print(f"  {index}: +{rows} rows (through {store.last_date(con,'index',index)})")
    return rows


def sanity_check(con, symbol: str) -> list[str]:
    """Flag day-over-day moves too large to be real and not explained by a
    corporate action.

    This exists because a data bug is invisible downstream: bad bars do not
    raise, they just quietly produce confident, wrong indicators. A NIFTY 50
    stock does not move 35% in a session without a split, a bonus, or a broken
    feed -- and the first two we already know about.
    """
    df = store.load_prices(con, symbol, adjusted=True)
    if len(df) < 2:
        return []
    ex_dates = {a.ex_date for a in store.load_actions(con, symbol)}
    move = df["close"].pct_change().abs() * 100
    flags = []
    for i in move[move > MAX_UNEXPLAINED_JUMP_PCT].index:
        d = df.loc[i, "date"]
        if d in ex_dates:
            continue
        flags.append(f"{symbol} {d}: close moved "
                     f"{df['close'].pct_change().iloc[i] * 100:+.1f}% "
                     f"({df['close'].iloc[i-1]:.2f} -> {df['close'].iloc[i]:.2f})")
    return flags


def backfill(con, symbols: list[str], years: int = 10, verbose: bool = True) -> None:
    backfill_index(con, BENCHMARK, years, verbose=verbose)
    for i, sym in enumerate(symbols, 1):
        if verbose:
            print(f"[{i}/{len(symbols)}]", end=" ", flush=True)
        backfill_symbol(con, sym, years, verbose=verbose)
        from data.quality import suspect_bars
        for d, pct in suspect_bars(con, sym):
            print(f"  SUSPECT BAR: {sym} {d}: {pct:+.1f}%", flush=True)
