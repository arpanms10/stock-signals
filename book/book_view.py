"""Assemble the overview from your holdings file and the data on disk.

The one place that does I/O for the overview: reads the holdings, labels
each security, prices it with its source (book/marks.py), attaches sector and
market-cap band, and hands plain Positions to strategy/overview.py.
"""
from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

from book import marks as mk
from book import portfolio as pf
from book import research_db as rdb
from book import securities as sec
from strategy import overview as ov

ROOT = Path(__file__).resolve().parents[1]
MARKET_DB = ROOT / "data" / "market.db"


def _dec(v) -> Decimal | None:
    if v in (None, "", 0, 0.0):
        return None
    return Decimal(str(v))


def positions(holdings: list[dict], mcon, rcon, ref: dict,
              fetch=None, refresh: bool = True) -> tuple[list[ov.Position], list[str]]:
    isins = mk.isin_map(mcon)
    secs = []
    for h in holdings:
        isin = h.get("isin") or ""
        s0 = sec.classify(h["tradingsymbol"], isin)
        # A plain symbol with no ISIN in the file: market.db's instrument map
        # says whether it is a share or an ETF.
        if not isin and s0.series == "EQ":
            s0 = sec.classify(h["tradingsymbol"], isins.get(s0.base, ""))
        secs.append(s0)
    marks, notes = mk.mark_all(secs, mcon, rcon, fetch=fetch, refresh=refresh)
    out = []
    for h, s in zip(holdings, secs):
        m = marks[s.symbol]
        out.append(ov.Position(
            symbol=s.symbol, kind=s.kind, quantity=Decimal(str(h["quantity"])),
            avg_cost=_dec(h.get("average_price")), price=m.price,
            prev_close=m.prev_close, price_date=m.date, price_source=m.source,
            price_note=m.note, bucket=(h.get("bucket") or "").lower(),
            sector=ref["sector"].get(s.base, "") if s.sleeve == sec.OPERATING else "",
            cap_band=mk.cap_band(s.base, s.kind, ref),
            name=ref["name"].get(s.base, ""), review=s.review))
    if ref.get("note"):
        notes.append(ref["note"].rstrip("; "))
    _attach_data(out, secs, rcon, mcon)
    return out, notes


def _attach_data(ps: list[ov.Position], secs, rcon, mcon) -> None:
    """Market cap and a data-freshness flag from research.db, when refreshed."""
    import datetime as dt
    from data import financials as fin
    session = (mk.sessions(mcon, 1) or [None])[0]
    today = dt.date.today()
    for p, s in zip(ps, secs):
        rows = rdb.metrics(rcon, s.base if s.kind == sec.EQUITY else s.symbol)
        if not rows:
            p.data_state = "none"
            continue
        mc = next((r for r in rows if r["metric"] == "market_cap"), None)
        p.market_cap_cr = mc["value"] if mc else None
        states = {fin.freshness(r, today, session)[0] for r in fin.latest_rows(rows)
                  if r["metric"] in ("market_cap", "revenue", "pat", "close_adjusted",
                                     "dps_ttm") and r["period"] in ("ttm", "point")}
        p.data_state = "stale" if "stale" in states else "fresh" if states else "none"


def load(refresh: bool = True, fetch=None) -> tuple[ov.Overview | None, list[str]]:
    """The overview of your current holdings file, or (None, why)."""
    holdings = pf.read_csv_holdings()
    if not holdings:
        return None, ["No holdings file with positions found (config/holdings.xlsx "
                      "or .csv)."]
    if not MARKET_DB.exists():
        return None, ["No market data yet: run the setup's market download first."]
    mcon = sqlite3.connect(MARKET_DB)
    rcon = rdb.connect()
    try:
        ps, notes = positions(holdings, mcon, rcon, mk.reference(), fetch, refresh)
    finally:
        mcon.close()
        rcon.close()
    o = ov.build(ps)
    o.disclosures = notes + o.disclosures
    return o, []


def snapshot(o: ov.Overview, label: str = "", rcon=None) -> int:
    """Save the overview as a dated snapshot in research.db."""
    own = rcon is None
    rcon = rcon or rdb.connect()
    try:
        rows = [{"symbol": p.symbol, "kind": p.kind, "sleeve": p.sleeve,
                 "bucket": p.bucket, "quantity": p.quantity, "avg_cost": p.avg_cost,
                 "price": p.price, "price_date": p.price_date,
                 "price_source": p.price_source, "market_value": p.market_value,
                 "sector": p.sector, "cap_band": p.cap_band} for p in o.positions]
        sid = rdb.save_snapshot(rcon, rows, o.summary(), o.price_date, label)
        rdb.log(rcon, "snapshot", f"portfolio_snapshot/{sid}",
                {"label": label, "price_date": o.price_date,
                 "total_value": str(o.total_value), "positions": len(rows)})
        return sid
    finally:
        if own:
            rcon.close()
