"""Value every holding (the I/O around strategy/valuation.py).

Fills valuation Inputs from what is already on disk -- research.db (filed
figures with provenance), market.db (raw closes, splits and bonuses), the
Nifty 200 quarterly filings (data/results/universe_quarters.csv) for peers --
and runs the valuation. Nothing is fetched.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from collections import defaultdict
from pathlib import Path

import pandas as pd

from book import marks as mk
from book import portfolio as pf
from book import research_db as rdb
from book import securities as sec
from book import scorecards as bs
from strategy import scorecard as sc
from strategy import valuation as val

ROOT = Path(__file__).resolve().parents[1]
MARKET_DB = ROOT / "data" / "market.db"
UNIVERSE = ROOT / "data" / "results" / "universe_quarters.csv"
NSE = sc.NSE


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(str(s)[:10])


def _q(rows, metric, period="quarter") -> dict[dt.date, float]:
    return {_d(k): v for k, (v, _) in sc.series(rows, metric, period).items()}


def prices(mcon, symbol: str, years: int = 6) -> list[tuple[dt.date, float]]:
    since = (dt.date.today() - dt.timedelta(days=366 * years)).isoformat()
    return [(_d(d), c) for d, c in mcon.execute(
        "SELECT date, close FROM market WHERE symbol = ? AND date >= ? AND close > 0 "
        "ORDER BY date", (symbol, since))]


def predecessor(mcon, symbol: str) -> str | None:
    """The symbol this one was renamed from, if the market data shows a clean
    hand-over: exactly one symbol that traded the session before `symbol`'s
    first day, never again after, at a close within 3% of its first close
    (a rename does not move the price). ZOMATO -> ETERNAL, for example."""
    first = mcon.execute("SELECT date, close FROM market WHERE symbol = ? ORDER BY date "
                         "LIMIT 1", (symbol,)).fetchone()
    if not first:
        return None
    prev = mcon.execute("SELECT MAX(date) FROM market WHERE date < ?", (first[0],)).fetchone()[0]
    if not prev:
        return None
    gone = mcon.execute(
        "SELECT symbol, close FROM market WHERE date = ? AND symbol NOT IN "
        "(SELECT symbol FROM market WHERE date = ?)", (prev, first[0])).fetchall()
    cands = [s for s, c in gone if c and abs(c - first[1]) / first[1] < 0.03]
    if len(cands) != 1:
        return None
    later = mcon.execute("SELECT 1 FROM market WHERE symbol = ? AND date > ? LIMIT 1",
                         (cands[0], prev)).fetchone()
    return None if later else cands[0]


def actions(mcon, symbol: str) -> list[tuple[dt.date, float]]:
    return [(_d(d), f) for d, f in mcon.execute(
        "SELECT ex_date, factor FROM market_actions WHERE symbol = ? AND factor > 0 "
        "AND factor != 1", (symbol,))]


def inputs(symbol: str, rows: list[dict], group: str, kind: str, mcon,
           notes: list[str] | None = None) -> val.Inputs:
    px = prices(mcon, symbol)
    acts = actions(mcon, symbol)
    old = predecessor(mcon, symbol) if px and px[0][0] > dt.date.today() - \
        dt.timedelta(days=366 * 5) else None
    if old:
        first = px[0][0]
        px = prices(mcon, old) + px
        acts = actions(mcon, old) + acts
        if notes is not None:
            notes.append(f"price history before {first:%d %b %Y} is {old}'s "
                         "(renamed; stitched at an unchanged price)")
    last = px[-1] if px else (None, None)
    def cagr(m):
        got = sc.latest(rows, m)
        return got[0] if got else None
    roe = [v for _, v, _ in sc._last(sc.fy(rows, "roe_pct"), 3)]
    return val.Inputs(
        symbol=symbol, group=group, kind=kind, price=last[1],
        price_date=last[0].isoformat() if last[0] else "", prices=px,
        actions=acts, shares_q=_q(rows, "shares_cr", "instant"),
        pat_q=_q(rows, "pat"), revenue_q=_q(rows, "revenue"), ebitda_q=_q(rows, "ebitda"),
        equity=_q(rows, "equity", "instant"), net_debt=_q(rows, "net_debt", "instant"),
        fy={m: {k: v for k, (v, _) in sc.fy(rows, m).items()}
            for m in ("revenue", "fcf", "pat", "ebitda")},
        revenue_cagr_pct=cagr("revenue_cagr_5y_pct") if cagr("revenue_cagr_5y_pct")
        is not None else cagr("revenue_cagr_3y_pct"),
        pat_cagr_pct=cagr("pat_cagr_3y_pct"),
        roe_avg_pct=sum(roe) / len(roe) if len(roe) >= 2 else None)


# ------------------------------------------------------------------ peers

def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else v


def peer_multiples(universe: pd.DataFrame, mcon, cfg: dict) -> dict[str, dict]:
    """Current P/E, P/B, EV/EBITDA for every company in the Nifty 200 filings,
    from its last four consecutive quarters on one basis and today's close."""
    out = {}
    if universe is None or universe.empty:
        return out
    last_close = dict(mcon.execute(
        "SELECT symbol, close FROM market WHERE date = (SELECT MAX(date) FROM market)"))
    for sym, g in universe.groupby("symbol"):
        bases = g["basis"].value_counts()
        basis = "consolidated" if bases.get("consolidated", 0) >= 4 else bases.index[0]
        g = g[g["basis"] == basis].sort_values("to_date")
        q = {_d(r["to_date"]): r for _, r in g.iterrows()}
        ends = sorted(q)[-4:]
        if len(ends) < 4 or any(val._months(a, b) != 3 for a, b in zip(ends, ends[1:])):
            continue
        rs = [q[e] for e in ends]
        bank = rs[-1].get("kind") == "bank"
        def s(*cols):
            tot = 0.0
            for r in rs:
                v = next((_num(r.get(c)) for c in cols if _num(r.get(c)) is not None), None)
                if v is None:
                    return None
                tot += v
            return tot
        pat = s("pat_bank_owners", "pat_bank") if bank else s("pat_owners", "pat")
        latest = rs[-1]
        close = last_close.get(sym)
        shares = next((_num(r.get("shares_cr")) for r in reversed(rs)
                       if _num(r.get("shares_cr"))), None)
        if not close or not shares:
            continue
        for ex, f in actions(mcon, sym):
            if ex > ends[-1]:
                shares *= f
        mcap = close * shares
        m = {"mcap": mcap, "kind": latest.get("kind"), "basis": basis}
        if pat and pat > 0 and mcap / pat <= cfg["band"]["max_pe"]:
            m["pe"] = mcap / pat
        eq = next((_num(r.get(c)) for r in reversed(rs) for c in ("equity_owners", "equity")
                   if _num(r.get(c))), None)
        if eq is None and bank:
            cap, res = _num(latest.get("bank_capital")), _num(latest.get("bank_reserves"))
            eq = cap + res if cap is not None and res is not None else None
        if eq and eq > 0:
            m["pb"] = mcap / eq
        if not bank:
            pbt, fin_c, dep = s("pbt"), s("finance_costs"), s("depreciation")
            if None not in (pbt, fin_c, dep) and pbt + fin_c + dep > 0:
                debt = sum(_num(latest.get(c)) or 0 for c in
                           ("borrowings_noncurrent", "borrowings_current", "debt_securities"))
                if not debt:
                    debt = _num(latest.get("borrowings")) or 0
                cash = _num(latest.get("cash")) or 0
                m["ev_ebitda"] = (mcap + debt - cash) / (pbt + fin_c + dep)
        out[sym] = m
    return out


def peers_for(symbol: str, metric: str, own: float | None, sector: str, ref: dict,
              table: dict[str, dict], cfg: dict, kind: str = "company") -> dict:
    """Same NSE industry and the same kind of business: banks against banks,
    NBFCs against NBFCs, companies against companies."""
    same = {s: m.get(metric) for s, m in table.items()
            if sector and ref["sector"].get(s, "") == sector
            and (m.get("kind") or "company") == kind}
    out = val.peer_compare(symbol, metric, own, same, cfg)
    out["industry"] = sector or "unknown"
    return out


# ------------------------------------------------------------------ book

def build(holdings: list[dict] | None = None, cfg: dict | None = None,
          ref: dict | None = None) -> tuple[list[val.Valuation], list[str]]:
    holdings = pf.read_csv_holdings() if holdings is None else holdings
    cfg = cfg or val.load_config()
    ref = ref if ref is not None else mk.reference()
    notes = []
    if not MARKET_DB.exists():
        return [], ["no market data (market.db)"]
    mcon = sqlite3.connect(MARKET_DB)
    rcon = rdb.connect()
    research, _, _ = bs.read_inputs()
    try:
        isins = mk.isin_map(mcon)
        universe = pd.read_csv(UNIVERSE) if UNIVERSE.exists() else pd.DataFrame()
        if universe.empty:
            notes.append("no Nifty 200 filings on file (data/results/universe_quarters.csv): "
                         "no peer comparison")
        table = peer_multiples(universe, mcon, cfg)
        out = []
        for h in holdings:
            s = sec.classify(h["tradingsymbol"], h.get("isin") or "")
            if not h.get("isin") and s.series == "EQ":
                s = sec.classify(h["tradingsymbol"], isins.get(s.base, ""))
            if s.kind in (sec.REIT, sec.INVIT):
                out.append(_listed(s, rcon, cfg))
                continue
            if s.kind != sec.EQUITY:
                continue
            rows = rdb.metrics(rcon, s.base)
            override = str((research.get(s.base) or {}).get("sector_group") or "").lower()
            group = override or bs.group_for(rows, ref["sector"].get(s.base, ""))
            kind = bs.company_kind(rows)
            extra: list[str] = []
            inp = inputs(s.base, rows, group, kind, mcon, extra)
            v = val.value(inp, cfg)
            v.notes = extra + v.notes
            if v.headline and v.headline != "yield":
                v.peers = peers_for(s.base, v.headline, v.current.get(v.headline),
                                    ref["sector"].get(s.base, ""), ref, table, cfg, kind)
            out.append(v)
    finally:
        mcon.close()
        rcon.close()
    return out, notes


def _listed(s: sec.Security, rcon, cfg: dict) -> val.Valuation:
    last = rdb.latest_listed_close(rcon, s.base, s.series)
    dps = sc.latest(rdb.metrics(rcon, s.symbol), "dps_ttm")
    inp = val.Inputs(symbol=s.symbol, group="reit", kind="reit",
                     price=float(last["close"]) if last else None,
                     price_date=last["date"] if last else "",
                     dps_ttm=dps[0] if dps else None)
    return val.value(inp, cfg)
