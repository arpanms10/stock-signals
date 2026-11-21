"""Refresh the financial and market data behind each holding.

For every holding: fetch, compute, store in research.db with provenance, and
log each attempt in retrieval_log -- ok, empty or failed, with the reason. A
source that fails keeps its previous rows (still dated by when they were
retrieved), so one bad request never blanks a company's history; the
freshness check then shows them ageing.

  equity        NSE filings (primary), Yahoo annual statements and dividends
                (secondary, cross-checked), ratios, market stats, market cap,
                promoter holding
  REIT / InvIT  distributions (Yahoo) and yield. NOI, occupancy, NAV, WALE,
                leverage and tenant concentration are published only in the
                trusts' own reports: recorded as missing, pending a manual
                input (Phase 3), never guessed.
  other types   skipped, logged
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pandas as pd

from book import research_db as rdb
from book import securities as sec
from data import financials as fin
from data import market_stats as ms

ROOT = Path(__file__).resolve().parents[1]

REIT_PENDING = ("noi", "occupancy_pct", "nav_per_unit", "net_debt_to_asset_pct",
                "wale_years", "top_tenant_pct")


def _actions(mcon, symbol: str) -> list[tuple[dt.date, float]]:
    return [(dt.date.fromisoformat(d), f) for d, f in mcon.execute(
        "SELECT ex_date, factor FROM market_actions WHERE symbol = ? AND factor > 0 "
        "AND factor != 1", (symbol,))]


def _raw_close(mcon, symbol: str) -> tuple[float | None, str | None]:
    r = mcon.execute("SELECT close, date FROM market WHERE symbol = ? "
                     "ORDER BY date DESC LIMIT 1", (symbol,)).fetchone()
    return (r[0], r[1]) if r else (None, None)


def _frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    return df.assign(date=pd.to_datetime(df["date"])).set_index("date")


def _kept(rcon, symbol: str, source: str) -> list[dict]:
    """Rows from an earlier successful retrieval, kept when this one failed."""
    return [r for r in rdb.metrics(rcon, symbol) if r["source"] == source]


def _attempt(rcon, symbol, source, dataset, fn, log):
    try:
        got = fn()
    except Exception as exc:                     # noqa: BLE001
        rdb.log_retrieval(rcon, symbol, source, dataset, "failed", 0, str(exc))
        log(f"  {symbol}: {dataset} from {source} failed ({exc})")
        return None
    n = len(got) if hasattr(got, "__len__") else 1
    rdb.log_retrieval(rcon, symbol, source, dataset, "ok" if n else "empty", n)
    return got


def refresh_equity(symbol: str, rcon, mcon, scon, log=print,
                   fetch_nse=fin.fetch_nse, fetch_yahoo=fin.fetch_yahoo,
                   fetch_promoter=None) -> dict:
    """All rows for one company share. Returns a small summary."""
    from data import store
    rows: list[dict] = []
    filings = _attempt(rcon, symbol, fin.NSE, "quarterly results",
                       lambda: fetch_nse(symbol), log)
    if filings:
        rows += fin.from_filings(symbol, filings, _actions(mcon, symbol))
    else:
        rows += _kept(rcon, symbol, fin.NSE)
    lender = any(f.get("kind") in ("bank", "nbfc") for f in filings or [])

    y = _attempt(rcon, symbol, fin.YAHOO, "annual statements",
                 lambda: fetch_yahoo(symbol), log)
    if y:
        statements, currency, dividends = y
        rows += fin.from_yahoo(symbol, statements, currency, dividends, lender=lender)
    else:
        rows += _kept(rcon, symbol, fin.YAHOO)
    issues = fin.cross_check(rows)
    for msg in issues:
        rdb.log_retrieval(rcon, symbol, "cross-check", "NSE vs Yahoo", "mismatch", 0, msg)
    rows += fin.ratios(symbol, rows, lender)

    px = _frame(store.load_prices(scon, symbol))
    idx = _frame(store.load_index(scon, "NIFTY 500"))
    rows += ms.from_prices(symbol, px, idx)
    close, cdate = _raw_close(mcon, symbol)
    shares = max((r for r in rows if r["metric"] == "shares_cr" and r["value"] is not None),
                 key=lambda r: r["period_end"], default=None)
    rows += ms.market_cap(symbol, close, cdate, shares)
    if fetch_promoter is None:
        from data.sources import nse_fundamentals as nsef
        fetch_promoter = lambda s: nsef.shareholding_history(s, 8)  # noqa: E731
    prom = _attempt(rcon, symbol, "NSE shareholding pattern", "promoter holding",
                    lambda: fetch_promoter(symbol), log)
    if prom:
        rows += ms.free_float(symbol, prom)
    else:
        rows += [r for r in _kept(rcon, symbol, "NSE shareholding pattern")]
    dps = next((r for r in rows if r["metric"] == "dps_ttm"), None)
    rows += ms.dividend_yield(symbol, dps, close, cdate)

    _store(rcon, symbol, rows)
    return {"symbol": symbol, "rows": len(rows), "quarters": len(filings or []),
            "lender": lender, "mismatches": issues}


def refresh_listed(s: sec.Security, rcon, log=print, fetch_yahoo=fin.fetch_yahoo) -> dict:
    """REIT / InvIT: distributions and yield; the rest recorded as pending."""
    rows = []
    y = _attempt(rcon, s.symbol, fin.YAHOO, "distributions",
                 lambda: fetch_yahoo(s.base), log)
    if y:
        rows += [r | {"symbol": s.symbol} for r in
                 fin.from_yahoo(s.base, {}, y[1], y[2]) if r["metric"] == "dps_ttm"]
    else:
        rows += _kept(rcon, s.symbol, fin.YAHOO)
    last = rdb.latest_listed_close(rcon, s.base, s.series)
    dpu = next((r for r in rows if r["metric"] == "dps_ttm"), None)
    if last:
        rows += [r | {"metric": "distribution_yield_pct"} for r in
                 ms.dividend_yield(s.symbol, dpu, float(last["close"]), last["date"])]
    today = dt.date.today().isoformat()
    for m in REIT_PENDING:
        rows.append(fin.row(s.symbol, m, "point", today, None, "", "manual input",
                            method="reported", confidence=fin.HIGH,
                            note="published only in the trust's own reports; "
                                 "pending manual input"))
    _store(rcon, s.symbol, rows)
    return {"symbol": s.symbol, "rows": len(rows)}


def _store(rcon, symbol: str, rows: list[dict]) -> None:
    rcon.execute("DELETE FROM financial_metric WHERE symbol = ?", (symbol,))
    rdb.save_metrics(rcon, rows)


def refresh(holdings: list[dict], symbols: list[str] | None = None,
            max_age_days: float = 0, log=print) -> list[dict]:
    """Refresh every holding (or `symbols`). max_age_days skips a symbol whose
    last successful retrieval is younger than that."""
    from data import store
    mcon = sqlite3.connect(ROOT / "data" / "market.db")
    scon = store.connect()
    rcon = rdb.connect()
    out = []
    try:
        isins = {s: i for s, i in mcon.execute("SELECT symbol, isin FROM instruments")}
        recent = {r["symbol"]: r["at"] for r in rdb.last_retrievals(rcon)
                  if r["status"] == "ok"}
        cutoff = (dt.datetime.now() - dt.timedelta(days=max_age_days)).isoformat()
        for h in holdings:
            s = sec.classify(h["tradingsymbol"], h.get("isin") or "")
            if not h.get("isin") and s.series == "EQ":
                s = sec.classify(h["tradingsymbol"], isins.get(s.base, ""))
            if symbols and s.symbol not in symbols and s.base not in symbols:
                continue
            if max_age_days and recent.get(s.symbol, "") > cutoff:
                log(f"{s.symbol}: retrieved {recent[s.symbol]}, skipped")
                continue
            log(f"{s.symbol} ({s.label})")
            if s.kind == sec.EQUITY:
                out.append(refresh_equity(s.base, rcon, mcon, scon, log))
            elif s.kind in (sec.REIT, sec.INVIT):
                out.append(refresh_listed(s, rcon, log))
            else:
                rdb.log_retrieval(rcon, s.symbol, "-", "all", "skipped", 0,
                                  f"{s.label}: no data source")
    finally:
        mcon.close()
        scon.close()
        rcon.close()
    return out
