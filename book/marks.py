"""Prices and reference data for valuing the book, each with its source.

Every price carries the date of the session it closed on and where it came
from. The book is valued at the latest available trading-session close --
never assumed to be today's, since today may be a holiday or not yet closed.

  equity, ETF      market.db: NSE bhavcopy closes (EQ series; trade-for-trade
                   series from market_t2t). Unadjusted, which is right for
                   valuing what you hold today.
  REIT, InvIT      research.db listed_close, filled from the same NSE
                   bhavcopy for series RR and IV (market.db keeps EQ only).
  bond, gold bond  not priced: shown at cost and listed as unpriced.

Reference data:

  sector           NSE's industry for the Nifty 500 constituents.
  market-cap band  NSE index membership: Nifty 100 = large, Nifty Midcap 150
                   = mid, any other listed share = small. A proxy for SEBI's
                   rank cut (AMFI's half-yearly list: 1-100 large, 101-250
                   mid), which these indices track; the source is stated
                   wherever the band is shown.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from book import research_db as rdb
from book import securities as sec

STALE_SESSIONS = 10          # not traded for this many sessions: suspended?
CAP_SOURCE = "NSE index membership (Nifty 100 / Midcap 150), proxy for AMFI ranks"


@dataclass(frozen=True)
class Mark:
    price: Decimal | None
    prev_close: Decimal | None
    date: str | None
    source: str
    note: str = ""


def _d(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


# ---------------------------------------------------------------- market.db

def sessions(mcon, n: int = STALE_SESSIONS + 1) -> list[str]:
    return [r[0] for r in mcon.execute(
        "SELECT date FROM market_days WHERE rows > 0 ORDER BY date DESC LIMIT ?", (n,))]


def isin_map(mcon) -> dict[str, str]:
    """symbol -> ISIN, from market.db's instrument map."""
    return {s: i for s, i in mcon.execute("SELECT symbol, isin FROM instruments")
            if i}


def _last_two(mcon, table: str, symbol: str) -> list[tuple]:
    return mcon.execute(
        f"SELECT date, close FROM {table} WHERE symbol = ? AND close IS NOT NULL "
        "ORDER BY date DESC LIMIT 2", (symbol,)).fetchall()


def equity_mark(mcon, symbol: str, recent: list[str]) -> Mark:
    rows = _last_two(mcon, "market", symbol)
    t2t = _last_two(mcon, "market_t2t", symbol)
    source = "NSE bhavcopy, EQ series (market.db)"
    if t2t and (not rows or t2t[0][0] > rows[0][0]):
        rows, source = t2t, "NSE bhavcopy, trade-for-trade series (market.db)"
    if not rows:
        return Mark(None, None, None, "none", "no NSE close on file")
    date, close = rows[0]
    prev = rows[1][1] if len(rows) > 1 else None
    note = ""
    if recent and date < recent[-1]:
        note = (f"last traded {date}, {len(recent) - 1}+ sessions ago: "
                "suspended or delisted?")
    return Mark(_d(close), _d(prev), date, source, note)


# ------------------------------------------------------------ REIT / InvIT

LISTED_SERIES = ("RR", "IV")
LISTED_SOURCE = "NSE bhavcopy, series RR/IV"


def refresh_listed(rcon, recent: list[str], fetch=None) -> str:
    """Make sure research.db has RR/IV closes for the latest two sessions
    market.db knows. Returns a note ("" when nothing needed doing)."""
    if not recent:
        return "no market sessions on file"
    if fetch is None:
        from data.bhavcopy import fetch_day as fetch
    have = set(rdb.listed_close_dates(rcon))
    notes = []
    for day in recent[:2]:
        if day in have:
            continue
        df = fetch(dt.date.fromisoformat(day), series=LISTED_SERIES)
        if df is None:
            notes.append(f"NSE bhavcopy for {day} could not be fetched")
            continue
        if df.empty:
            continue
        rows = [{"symbol": r.symbol, "series": r.series, "close": r.close,
                 "isin": getattr(r, "isin", None)} for r in df.itertuples()]
        rdb.save_listed_closes(rcon, day, rows, LISTED_SOURCE)
    return "; ".join(notes)


def listed_mark(rcon, s: sec.Security, recent: list[str]) -> Mark:
    got = rdb.latest_listed_close(rcon, s.base, s.series)
    if not got:
        return Mark(None, None, None, "none", f"no {s.series} close on file")
    note = ""
    if recent and got["date"] < recent[-1]:
        note = f"last close on file {got['date']}: suspended or delisted?"
    return Mark(got["close"], got["prev_close"], got["date"], got["source"], note)


# --------------------------------------------------------------- reference

def reference() -> dict:
    """{'sector': {sym: industry}, 'cap': {sym: band}, 'name': {sym: name},
    'note': str}. Offline: empty maps and a note, never a guess."""
    from data.sources import universe as uni
    out = {"sector": {}, "cap": {}, "name": {}, "note": ""}
    try:
        n500 = uni.nifty500()
        out["sector"] = dict(zip(n500["symbol"], n500["sector"]))
        out["name"] = dict(zip(n500["symbol"], n500["name"]))
    except Exception as exc:                 # noqa: BLE001
        out["note"] += f"Nifty 500 list unavailable ({exc}); "
    try:
        large = set(uni.nifty100()["symbol"])
        mid = set(uni.midcap150()["symbol"])
        out["cap"] = {**{s: "large" for s in large}, **{s: "mid" for s in mid}}
        out["cap_known"] = True
    except Exception as exc:                 # noqa: BLE001
        out["note"] += f"cap-band lists unavailable ({exc}); "
        out["cap_known"] = False
    return out


def cap_band(symbol: str, kind: str, ref: dict) -> str:
    if kind not in (sec.EQUITY, sec.PARTLY_PAID):
        return ""
    if not ref.get("cap_known"):
        return "unknown"
    return ref["cap"].get(symbol, "small")


# -------------------------------------------------------------- everything

def mark_all(securities: list[sec.Security], mcon, rcon, fetch=None,
             refresh: bool = True) -> tuple[dict[str, Mark], list[str]]:
    """{symbol: Mark} for every holding, and notes about what failed."""
    recent = sessions(mcon)
    notes = []
    listed = [s for s in securities if s.kind in (sec.REIT, sec.INVIT)]
    if listed and refresh:
        try:
            n = refresh_listed(rcon, recent, fetch)
        except Exception as exc:             # noqa: BLE001
            n = f"REIT/InvIT prices not refreshed ({exc})"
        if n:
            notes.append(n)
    out = {}
    for s in securities:
        if s.kind in (sec.EQUITY, sec.ETF, sec.PARTLY_PAID):
            out[s.symbol] = equity_mark(mcon, s.base if s.kind != sec.PARTLY_PAID
                                        else s.symbol, recent)
        elif s.kind in (sec.REIT, sec.INVIT):
            out[s.symbol] = listed_mark(rcon, s, recent)
        else:
            out[s.symbol] = Mark(None, None, None, "none",
                                 f"{s.label.lower()}: no price source; shown at cost")
    return out, notes
