"""Fetch, score and store: the one place the results pipeline is wired up.

run_results.py, the daily job and the dashboard all go through `refresh()`,
so a filing is scored the same way whichever of them saw it first.
"""
from __future__ import annotations

import calendar
import datetime as dt
import time

import pandas as pd

from data.sources import nse_fundamentals as nsef

from . import filings as rf
from . import scorecard as sc
from . import store


def load_config() -> dict:
    from scoring import timing
    return timing.load_config()["results"]


def expected_quarter(today: dt.date) -> dt.date:
    """The latest quarter end that has fully passed -- the one now reporting."""
    m = (today.month - 1) // 3 * 3          # last month of the previous quarter
    if m == 0:
        return dt.date(today.year - 1, 12, 31)
    return dt.date(today.year, m, calendar.monthrange(today.year, m)[1])


def refresh(symbols, cfg: dict | None = None, force: bool = False,
            log=print, pause: float = 0.3) -> list[dict]:
    """Score every symbol whose newest filing is not on file yet.

    Two NSE requests per symbol to list filings; the XBRL itself only when a
    quarter has not been read before (results.filings caches by URL). Returns
    the cards scored this run.
    """
    cfg = cfg or load_config()
    have = store.cards()
    new_cards, new_quarters = [], []
    for sym in symbols:
        try:
            filings = nsef.result_filings(sym)
        except Exception as exc:
            log(f"{sym}: filing list unavailable ({exc})")
            continue
        dated = [(rf._date(r.get("toDate")), r) for r in filings if r.get("xbrl")]
        dated = [(d, r) for d, r in dated if d]
        if not dated:
            continue                       # ETFs and the like file nothing
        newest = max(d for d, _ in dated)
        urls = {r["xbrl"] for d, r in dated if d == newest}
        if not force and any(store.scored(have, sym, newest, u) for u in urls):
            continue
        hist = rf.history(sym, cfg["quarters"], filings=filings, log=log)
        card = sc.score([_clean(r) for r in hist], cfg)
        if card is None:
            log(f"{sym}: filings listed but none could be read")
            continue
        new_cards.append(card)
        new_quarters.extend(hist)
        log(f"{sym}: {card['quarter']} {card['verdict']} -- {sc.headline(card)}")
        time.sleep(pause)
    if new_quarters:
        store.save_quarters(new_quarters)
    if new_cards:
        store.save_cards(new_cards)
    return new_cards


def _clean(row: dict) -> dict:
    """A CSV row back into what the scorecard expects: NaN is missing (and
    NaN is truthy, so `x or 0.0` would keep it), timestamps as datetimes."""
    out = {k: (None if isinstance(v, float) and v != v else v) for k, v in row.items()}
    if isinstance(out.get("broadcast"), pd.Timestamp):
        out["broadcast"] = out["broadcast"].to_pydatetime()
    return out


def cards_from_quarters(quarters: pd.DataFrame, cfg: dict) -> list[dict]:
    """Every scoreable card from stored quarterly lines.

    Each quarter is scored on the quarters up to and including it, as it
    would have been on the day. The newest quarter always gets a card; older
    ones only when their year-ago quarter is on file, since without it there
    is nothing to judge.
    """
    out = []
    for _, g in quarters.groupby("symbol"):
        rows = [_clean(r) for r in g.sort_values("to_date", ascending=False)
                .to_dict("records")]
        for i, row in enumerate(rows):
            card = sc.score(rows[i:], cfg)
            if card and (i == 0 or card.get("revenue_yoy") is not None
                         or card.get("nii_yoy") is not None):
                out.append(card)
    return out


def rescore(cfg: dict | None = None) -> int:
    """Rebuild every card from the stored quarters, so a change to the rules
    in scoring.yaml reaches cards already on file. Reactions are kept."""
    cfg = cfg or load_config()
    q = store.quarters()
    if q.empty:
        return 0
    fresh = pd.DataFrame(cards_from_quarters(q, cfg))
    old = store.cards()
    keep = [c for c in REACTION_FIELDS if c in old]
    if keep:
        fresh = fresh.merge(old[store.KEY + keep], on=store.KEY, how="left")
    store.CARDS.parent.mkdir(parents=True, exist_ok=True)
    fresh.sort_values(store.KEY).to_csv(store.CARDS, index=False)
    return len(fresh)


def upcoming(symbols, today: dt.date, days: int = 21, log=print) -> dict[str, dt.date]:
    """Next announced results date for each symbol within `days`."""
    from data.sources import nse_events
    try:
        cal = nse_events.calendar(today, today + dt.timedelta(days=days))
    except Exception as exc:
        log(f"results calendar unavailable ({exc})")
        return {}
    out = {}
    for s in symbols:
        dates = [d for d in cal.get(s, []) if d >= today]
        if dates:
            out[s] = dates[0]
    return out


def status(coverage: dict[str, list[str]], today: dt.date,
           cards: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per covered symbol: its newest card, or why there is none."""
    latest = store.latest(cards)
    want = expected_quarter(today)
    rows = []
    for sym, sources in coverage.items():
        row = {"symbol": sym, "sources": ", ".join(sources)}
        hit = latest[latest["symbol"] == sym] if not latest.empty else latest
        if not hit.empty:
            row.update(hit.iloc[0].to_dict())
        row["current"] = bool(row.get("to_date") and row["to_date"] >= want)
        rows.append(row)
    return pd.DataFrame(rows)


REACTION_FIELDS = ["reaction_date", "gap_pct", "day_pct", "day_atr",
                   "day_excess_pct", "d5_pct", "d5_excess_pct", "reaction"]


def _prices(con, symbol: str, since: dt.date) -> pd.DataFrame:
    """Bars from the local store; straight from NSE for a symbol it lacks
    (a holding outside the Nifty 500). The fallback is unadjusted, which only
    matters if a split lands inside the few weeks measured."""
    from data import store as price_store
    df = price_store.load_prices(con, symbol)
    if not df.empty and df["date"].max() >= since:
        return df
    try:
        from data.sources import prices
        return prices.stock_history(symbol, since - dt.timedelta(days=45),
                                    dt.date.today())
    except Exception:
        return df


def update_reactions(cfg: dict | None = None, log=print) -> int:
    """Fill or extend the reaction on every card still missing its 5-session
    figure. Local prices only, plus the NSE fallback for unstored symbols."""
    from data import store as price_store
    from scoring import timing
    from . import reaction as rx

    cfg = cfg or load_config()
    cards = store.cards()
    if cards.empty:
        return 0
    if "d5_pct" not in cards:
        cards["d5_pct"] = float("nan")
    todo = cards[cards["d5_pct"].isna() & cards["broadcast"].notna()]
    if todo.empty:
        return 0
    con = price_store.connect()
    index = price_store.load_index(con, timing.load_config()["signals"]["regime_index"])
    updated = []
    for _, row in todo.iterrows():
        since = row["broadcast"].date()
        m = rx.measure(_prices(con, row["symbol"], since), row["broadcast"], index,
                       cfg.get("reaction_atr", 1.0))
        if m:
            updated.append({**row.to_dict(), **m})
    if updated:
        store.save_cards(updated)
    return len(updated)
