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


DAILY_MAX_SESSIONS = 30     # beyond this gap, per-symbol history is the better tool
DAILY_MAX_LAG = 5           # symbols further behind the pack than this go per symbol


def _weekdays_after(last: dt.date, today: dt.date) -> list[dt.date]:
    out, d = [], last + dt.timedelta(days=1)
    while d <= today:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def refresh_from_daily_files(con, symbols: list[str], today: dt.date | None = None,
                             verbose: bool = True) -> set[str]:
    """Catch symbols up from NSE's full daily file: one download per missed
    trading day for all of them, instead of one request per symbol, and one
    corporate-actions request for the whole window.

    Handles symbols that already have history ending within
    DAILY_MAX_SESSIONS weekdays of today and DAILY_MAX_LAG of the pack
    (the median last date); returns that set. The rest -- new symbols,
    long gaps, the back-fill to 10 years -- are left to backfill_symbol().
    A day that FAILED to download stops the run there (later days would
    leave a hole behind them); a day with no file (holiday, or today before
    NSE publishes) is skipped.
    """
    today = today or dt.date.today()
    want = {s.upper() for s in symbols}
    last = {s: dt.date.fromisoformat(d) for s, d in
            con.execute("SELECT symbol, MAX(date) FROM prices GROUP BY symbol")
            if s in want and d}
    if not last:
        return set()
    # One suspended or delisted stock must not drag every download back to
    # its last bar: only symbols near where most of them are use the files.
    pack = sorted(last.values())[len(last) // 2]
    window = {s: d for s, d in last.items()
              if len(_weekdays_after(d, today)) <= DAILY_MAX_SESSIONS
              and len(_weekdays_after(d, pack)) <= DAILY_MAX_LAG}
    if not window:
        return set()
    frm = min(window.values())
    days = _weekdays_after(frm, today)
    if verbose:
        print(f"Daily files: {len(window)} symbols, {len(days)} weekday(s) since {frm}",
              flush=True)
    saved, failed = 0, None
    for day in days:
        df = prices.daily_bars(day)
        if df is None:
            failed = day
            print(f"  {day}: download failed -- stopping here; the next run resumes",
                  flush=True)
            break
        if df.empty:
            if verbose:
                print(f"  {day}: no file (holiday, or not published yet)", flush=True)
            continue
        df = df[df["symbol"].isin(window.keys())]
        df = df[df.apply(lambda r: r["date"] > window[r["symbol"]], axis=1)]
        saved += store.save_prices(con, df)
        if verbose:
            print(f"  {day}: {len(df)} symbols", flush=True)
        time.sleep(PAUSE_SECONDS)
    try:
        actions, rights = ca.fetch_all(frm - dt.timedelta(days=7), today, set(window))
        store.save_actions(con, actions)
        if verbose:
            for a in actions:
                print(f"  corporate action: {a.symbol} {a.ex_date} {a.subject}", flush=True)
            for r in rights:
                print(f"  rights issue (not auto-adjusted): {r}", flush=True)
    except Exception as exc:          # noqa: BLE001 -- prices are still worth keeping
        print(f"  corporate actions unavailable: {exc}", flush=True)
    for s in window:
        store.mark_fetched(con, "stock", s, store.last_date(con, "stock", s))
    if verbose:
        print(f"Daily files: +{saved} rows" + (f", stopped at {failed}" if failed else ""),
              flush=True)
    return set(window) if failed is None else set()


def backfill(con, symbols: list[str], years: int = 10, verbose: bool = True,
             daily: bool = True) -> None:
    """Index first, then stocks: recent gaps from the daily files (fast), and
    anything they cannot cover one symbol at a time."""
    from data.quality import suspect_bars
    backfill_index(con, BENCHMARK, years, verbose=verbose)
    done: set[str] = set()
    if daily:
        try:
            done = refresh_from_daily_files(con, symbols, verbose=verbose)
        except Exception as exc:      # noqa: BLE001 -- fall back, don't fail
            print(f"Daily files unavailable ({exc}); fetching per symbol", flush=True)
    rest = [s for s in symbols if s.upper() not in done]
    if verbose and daily:
        print(f"Per symbol: {len(rest)} symbol(s)", flush=True)
    for i, sym in enumerate(rest, 1):
        if verbose:
            print(f"[{i}/{len(rest)}]", end=" ", flush=True)
        backfill_symbol(con, sym, years, verbose=verbose)
    for sym in symbols:
        for d, pct in suspect_bars(con, sym):
            print(f"  SUSPECT BAR: {sym} {d}: {pct:+.1f}%", flush=True)
