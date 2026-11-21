"""Tax lots, this year's realised gains, and the tax-aware transition from your
book (the I/O around strategy/lots.py, capgains.py and transition.py).

Reads the overview (quantities, average cost, prices), the holdings file (the
broker's long/short split), config/tradebooks/ and market.db (splits,
bonuses, renames). The Phase 5 plan supplies the trims. Writes nothing.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

from book import book_view as bvw
from book import portfolio as pf
from book import risk_view as rv
from book import tradebook as tb
from book import valuations as bval
from strategy import capgains as cg
from strategy import lots as lt
from strategy import transition as tr

ROOT = Path(__file__).resolve().parents[1]
MARKET_DB = ROOT / "data" / "market.db"


def _f(x):
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _actions(mcon, symbol: str) -> list[tuple[dt.date, float, str]]:
    rows = mcon.execute("SELECT ex_date, factor, kind FROM market_actions WHERE symbol = ? "
                        "AND factor > 0 AND factor != 1", (symbol,)).fetchall()
    return [(dt.date.fromisoformat(d), f, "bonus" if (k or "").lower() == "bonus" else "split")
            for d, f, k in rows]


def gather(today: dt.date | None = None, cfg: dict | None = None) -> dict:
    today = today or dt.date.today()
    cfg = cfg or cg.load_config()
    o, why = bvw.load(refresh=False)
    if o is None:
        return {"error": "; ".join(why)}
    trades, files = tb.load()
    raw = {h["tradingsymbol"]: h for h in pf.read_csv_holdings()}
    held = {p.symbol: p for p in o.positions if p.quantity}
    by_isin = {}
    for s, h in raw.items():
        if h.get("isin"):
            by_isin[h["isin"]] = s
    mcon = sqlite3.connect(MARKET_DB) if MARKET_DB.exists() else None
    books, prices = {}, {}
    try:
        # Trades under an old name or matched by ISIN count for today's symbol.
        rename = {}
        for s in held:
            if mcon:
                old = bval.predecessor(mcon, s)
                if old:
                    rename[old] = s
        for t in trades:
            t.symbol = rename.get(t.symbol) or by_isin.get(t.isin) or t.symbol
        symbols = sorted(set(held) | {t.symbol for t in trades})
        for s in symbols:
            p, h = held.get(s), raw.get(s, {})
            acts = _actions(mcon, s) if mcon else []
            old = next((k for k, v in rename.items() if v == s), None)
            if old and mcon:
                acts += _actions(mcon, old)
            qty = float(p.quantity) if p else 0.0
            avg = float(p.avg_cost) if p and p.avg_cost else None
            books[s] = lt.build(s, trades, acts, qty, avg, _f(h.get("lt_quantity")),
                                _f(h.get("st_quantity")), today, cfg)
            if p and p.price:
                prices[s] = float(p.price)
    finally:
        if mcon:
            mcon.close()
    realised, unknown = _realised(books, held, today, cfg)
    return {"overview": o, "trades": trades, "files": files, "books": books,
            "prices": prices, "held": held, "realised": realised, "unknown": unknown,
            "today": today, "cfg": cfg}


def _realised(books, held, today, cfg) -> tuple[dict, list[dict]]:
    """Net short- and long-term gains realised per financial year, from the
    tradebook replay. A sale of shares bought before the first tradebook in a
    stock you no longer hold has no known cost: listed, not guessed."""
    by: dict[str, list[float]] = {}
    unknown = []
    for s, b in books.items():
        for sale in b.sales:
            fy = cg.fy_of(sale.date)
            row = by.setdefault(fy, [0.0, 0.0])
            for p in sale.pieces:
                if p["source"] == "opening" and s not in held:
                    unknown.append({"symbol": s, "date": sale.date, "qty": p["qty"],
                                    "value": p["qty"] * sale.price, "fy": fy})
                    continue
                row[1 if p["long_term"] else 0] += p["gain"]
            if sale.unmatched_qty:
                unknown.append({"symbol": s, "date": sale.date, "qty": sale.unmatched_qty,
                                "value": sale.unmatched_qty * sale.price, "fy": fy})
    ov = cfg.get("realised_override") or {}
    if ov.get("fy") and (ov.get("stcg") is not None or ov.get("ltcg") is not None):
        by[ov["fy"]] = [float(ov.get("stcg") or 0), float(ov.get("ltcg") or 0)]
    return {fy: tuple(v) for fy, v in by.items()}, unknown


def current_fy(g: dict) -> dict:
    fy = cg.fy_of(g["today"])
    st, ltg = g["realised"].get(fy, (0.0, 0.0))
    t = cg.fy_tax(st, ltg, g["today"], g["cfg"])
    return {"fy": fy, "st": st, "lt": ltg, **t,
            "unknown": [u for u in g["unknown"] if u["fy"] == fy],
            "overridden": (g["cfg"].get("realised_override") or {}).get("fy") == fy}


def trims_for(rg: dict, monthly: float, months: int, use_suggested: bool,
              prices: dict) -> tuple[list[tr.Trim], object]:
    p = rv.make_plan(rg, monthly, months, use_suggested)
    out = []
    for r in p.rows:
        px = prices.get(r.symbol)
        if r.trim > 0 and px:
            q = int(r.trim // px)
            if q >= 1:
                out.append(tr.Trim(r.symbol, q, px, "; ".join(n for n in r.notes
                                                               if "trim" in n)))
    return out, p


def schedules(g: dict, rg: dict, monthly: float, use_suggested: bool) -> list[dict]:
    """One tax-timed schedule per horizon, each from the Phase 5 plan run over
    that horizon (more months of new money, fewer trims)."""
    fy = cg.fy_of(g["today"])
    realised = {fy: g["realised"].get(fy, (0.0, 0.0))}
    lots = {s: b.lots for s, b in g["books"].items()}
    out = []
    for h in g["cfg"]["transition"]["horizons"]:
        trims, plan = trims_for(rg, monthly, h, use_suggested, g["prices"])
        sch = tr.schedule(trims, lots, g["today"], h, realised, g["cfg"])
        b = {x["role"]: x for x in plan.buckets}
        out.append({"horizon": h, "schedule": sch, "plan": plan,
                    "core_after": b["core"]["after_pct"]})
    return out


def harvest(g: dict) -> dict:
    c = current_fy(g)
    lots = {s: b.lots for s, b in g["books"].items() if s in g["held"]}
    return {"exemption_left": c["exemption_left"],
            "gains": tr.harvest_gains(lots, g["prices"], g["today"], c["exemption_left"],
                                      g["cfg"]),
            "losses": tr.unrealised_losses(lots, g["prices"], g["today"], g["cfg"]),
            "taxable_now": c["st_taxable"] + c["lt_taxable"]}
