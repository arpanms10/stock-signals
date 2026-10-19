"""The Fundamental Analyst: is this a sound business, and what is it priced at?

Rule-based, from free data only -- NSE filings first, Yahoo for the ratios NSE
does not publish -- and scored by the same Quality rubric the rest of the
framework uses (fundamentals.py), so a stock reads the same here as it does on
the Market tab.

It describes; it does not predict. The Quality Score has not been shown to
pick winners, and neither has anything here. What it is good for is catching
the things that sink a position regardless of the chart: profit that never
becomes cash, rising promoter pledge, leverage a bad year would break.

Fetched data is cached per symbol for a week under data/cache/analysis/ --
results are quarterly, so refetching daily only costs NSE requests. It is not
written to config/fundamentals.csv: that sheet is the watchlist's, and an
ad-hoc lookup should not change what the advisor scores.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

import fundamentals as fu
from analysis import sector as sec

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "cache" / "analysis"
MAX_AGE = dt.timedelta(days=7)
CACHE_VERSION = 2      # 2: Yahoo margins, EBITDA, debt and cash for the sector checklist

STANCES = ["Strong", "Sound", "Mixed", "Weak"]


@dataclass
class FundamentalReport:
    symbol: str
    stance: str                        # Strong / Sound / Mixed / Weak / Not enough data
    summary: str
    score: float | None
    components: dict
    strengths: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)       # Quality's other flags
    metrics: dict = field(default_factory=dict)          # label -> display string
    quarters: list[dict] = field(default_factory=list)   # newest first
    promoter_trend: list[dict] = field(default_factory=list)
    valuation: dict = field(default_factory=dict)
    valuation_note: str = ""
    kind: str = "company"              # company / bank / nbfc
    as_of: str = ""                    # latest quarter on file
    fetched_at: str = ""
    sources: str = ""
    sector_checks: sec.Checklist | None = None


# --------------------------------------------------------------------- data

def _cache_path(symbol: str) -> Path:
    return CACHE_DIR / f"fundamentals_{symbol.upper()}.json"


def _clean(v):
    """JSON-safe: NaN and numpy scalars to plain Python."""
    if isinstance(v, (np.floating, float)):
        return None if v != v else float(v)
    if isinstance(v, np.integer):
        return int(v)
    return v


def fetch(symbol: str, refresh: bool = False, now: dt.datetime | None = None) -> dict:
    """Rows for the sheet rubric, Yahoo's info and promoter history.

    Served from the cache when younger than a week, unless `refresh`.
    """
    now = now or dt.datetime.now()
    path = _cache_path(symbol)
    if path.exists() and not refresh:
        try:
            data = json.loads(path.read_text())
            if (now - dt.datetime.fromisoformat(data["fetched_at"]) < MAX_AGE
                    and data.get("v") == CACHE_VERSION):
                return data
        except Exception:
            pass

    import fetch_fundamentals as ff
    from data.sources import nse_fundamentals as nsef

    info: dict = {}
    rows, meta = ff.fetch_rows(symbol, fu.fiscal_quarter(now.date()), 6,
                               info_out=info)
    try:
        promoter = nsef.shareholding_history(symbol, 8)
    except Exception:
        promoter = []
    keep = ("sector", "industry", "longName", "marketCap", "trailingPE",
            "forwardPE", "priceToBook", "dividendYield", "currentPrice",
            "financialCurrency", "earningsGrowth", "revenueGrowth",
            "grossMargins", "ebitdaMargins", "operatingMargins", "returnOnAssets",
            "enterpriseToEbitda", "ebitda", "totalDebt", "totalCash",
            "freeCashflow", "netIncomeToCommon", "payoutRatio")
    data = {
        "symbol": symbol.upper(), "v": CACHE_VERSION,
        "fetched_at": now.isoformat(timespec="seconds"),
        "rows": [{k: _clean(v) for k, v in r.items()} for r in rows],
        "info": {k: _clean(info.get(k)) for k in keep if info.get(k) is not None},
        "promoter": [{"date": p.get("date"), "promoter_pct": _clean(p.get("promoter_pct"))}
                     for p in promoter],
        "lender": bool(meta.get("lender")),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1))
    return data


def frame(rows: list[dict]) -> pd.DataFrame:
    """Rows -> the frame fundamentals.score_symbol expects."""
    df = pd.DataFrame(rows).reindex(columns=fu.COLUMNS)
    df["symbol"] = df["symbol"].astype(str).str.upper()
    for c in fu.COLUMNS:
        if c not in ("symbol", "quarter", "notes", "period"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# ------------------------------------------------------------------ helpers

def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _pct(v, digits: int = 1) -> str:
    return "—" if v is None else f"{v:.{digits}f}%"


def _signed(v) -> str:
    return "—" if v is None else f"{v:+.0f}%"


def results_quarter(notes) -> str | None:
    """FY27Q2 from a source note like 'nse-results(30-Sep-2026)'."""
    m = re.search(r"nse-(?:results|lender)\(([^)]+)\)", str(notes or ""))
    if not m:
        return None
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return fu.fiscal_quarter(dt.datetime.strptime(m.group(1), fmt).date())
        except ValueError:
            continue
    return None


def quarter_table(df: pd.DataFrame) -> list[dict]:
    """Quarterly revenue and profit, newest first, with growth.

    Only quarterly rows are compared: an annual Yahoo figure next to a
    quarterly NSE one would read as 4x growth.
    """
    q = df[df["period"].astype(str).str.lower().eq("quarterly")
           & df["revenue"].notna()].copy()
    if q.empty:
        return []
    # The current row is labelled with the quarter it was FETCHED in; its
    # figures belong to the results' own period, which its notes record.
    q["quarter"] = [results_quarter(n) or lab for n, lab in zip(q["notes"], q["quarter"])]
    q["_k"] = q["quarter"].map(fu.quarter_key)
    q = q.sort_values("_k").drop_duplicates("_k", keep="last").reset_index(drop=True)
    out = []
    for i, r in q.iterrows():
        rev, pat = _num(r["revenue"]), _num(r["pat"])
        row = {"quarter": r["quarter"], "revenue_cr": rev, "pat_cr": pat,
               "margin_pct": (100 * pat / rev) if rev and pat is not None else None,
               "revenue_yoy_pct": None, "pat_yoy_pct": None}
        # Same quarter last year: four rows back, if the quarters are contiguous.
        if i >= 4 and q.loc[i - 4, "_k"] == (r["_k"][0] - 1, r["_k"][1]):
            r0, p0 = _num(q.loc[i - 4, "revenue"]), _num(q.loc[i - 4, "pat"])
            if r0 and r0 > 0 and rev is not None:
                row["revenue_yoy_pct"] = 100 * (rev / r0 - 1)
            # Growth off a loss is meaningless as a percentage.
            if p0 and p0 > 0 and pat is not None:
                row["pat_yoy_pct"] = 100 * (pat / p0 - 1)
        out.append(row)
    return out[::-1]


def red_flags(df: pd.DataFrame, kind: str = "company") -> list[str]:
    """The hard warnings: things that can sink a position on their own.

    Leverage, interest cover and cash conversion are left out for lenders:
    borrowing is their raw material, and cash flow goes negative precisely
    when lending grows."""
    rows = df.iloc[sorted(range(len(df)), key=lambda i: fu.quarter_key(df["quarter"].iloc[i]))]
    cur = rows.iloc[-1]
    prev = rows.iloc[-2] if len(rows) > 1 else None
    out = []
    pledge = _num(cur.get("pledge_pct"))
    if pledge is not None and pledge >= 25:
        out.append(f"Promoter pledge {pledge:.1f}% — lenders can force-sell")
    if prev is not None:
        pp = _num(prev.get("pledge_pct"))
        if pledge is not None and pp is not None and pledge > pp + 1:
            out.append(f"Promoter pledge rising: {pp:.1f}% → {pledge:.1f}%")
    if kind != "company":
        ic = de = cfo = None
    else:
        ic = _num(cur.get("interest_cover"))
        de = _num(cur.get("debt_equity"))
        cfo = _num(cur.get("cfo"))
    if ic is not None and ic < 2:
        out.append(f"Interest cover {ic:.1f}× — earnings barely cover interest")
    if de is not None and de > 2:
        out.append(f"Debt/equity {de:.2f} — highly leveraged")
    pat_a = _num(cur.get("pat_annual"))
    if cfo is not None and pat_a and pat_a > 0 and cfo / pat_a < 0.5:
        out.append(f"Cash flow is {cfo / pat_a:.2f}× profit — profit not arriving as cash")
    gnpa, nnpa = _num(cur.get("gnpa_pct")), _num(cur.get("nnpa_pct"))
    if gnpa is not None and gnpa > 6:
        out.append(f"Gross NPA {gnpa:.2f}% — bad loans are high")
    if nnpa is not None and nnpa > 2:
        out.append(f"Net NPA {nnpa:.2f}% — bad loans not provided for")
    return out


def _metrics(cur: pd.Series, kind: str) -> dict:
    g = lambda k: _num(cur.get(k))  # noqa: E731
    if kind == "bank":
        pairs = [("Gross NPA", _pct(g("gnpa_pct"), 2)), ("Net NPA", _pct(g("nnpa_pct"), 2)),
                 ("Provision coverage", _pct(g("pcr_pct"), 0)), ("ROA", _pct(g("roa_pct"), 2)),
                 ("Cost / income", _pct(g("cost_income_pct"), 0)),
                 ("Capital adequacy", _pct(g("car_pct"), 1))]
    else:
        cfo, pat_a = g("cfo"), g("pat_annual")
        pairs = [("ROE", _pct(g("roe"))), ("ROCE", _pct(g("roce"))),
                 ("Debt / equity", "—" if g("debt_equity") is None else f"{g('debt_equity'):.2f}"),
                 ("Interest cover", "—" if g("interest_cover") is None else f"{g('interest_cover'):.1f}×"),
                 ("Cash flow / profit", f"{cfo / pat_a:.2f}×" if cfo is not None and pat_a and pat_a > 0 else "—")]
        if kind == "nbfc":
            # Meaningless for a lender; shown, they would read as alarms.
            pairs = [p for p in pairs if p[0] not in ("Debt / equity", "Cash flow / profit")]
    pairs += [("Promoter holding", _pct(g("promoter_pct"))),
              ("Promoter pledge", _pct(g("pledge_pct"), 2))]
    return dict(pairs)


def _reasons(components: dict, cur: pd.Series, quarters: list[dict],
             kind: str) -> tuple[list[str], list[str]]:
    """Plain-language strengths and concerns from the component scores."""
    g = lambda k: _num(cur.get(k))  # noqa: E731
    latest_yoy = next((q["revenue_yoy_pct"] for q in quarters
                       if q["revenue_yoy_pct"] is not None), None)
    cfo, pat_a = g("cfo"), g("pat_annual")
    what = {
        "growth": f"revenue {_signed(latest_yoy)} year on year",
        "profitability": ", ".join(
            f"{lab} {_pct(g(k), d)}" for lab, k, d in
            (("ROE", "roe", 1), ("ROCE", "roce", 1), ("ROA", "roa_pct", 2))
            if g(k) is not None),
        "balance sheet": (f"debt/equity {g('debt_equity'):.2f}" if g("debt_equity") is not None
                          else "") + (f", interest cover {g('interest_cover'):.1f}×"
                                      if g("interest_cover") is not None else ""),
        "cash quality": (f"operating cash flow {cfo / pat_a:.2f}× profit"
                         if cfo is not None and pat_a and pat_a > 0 else ""),
        "governance": f"promoter {_pct(g('promoter_pct'))}, pledge {_pct(g('pledge_pct'), 2)}",
        "asset quality": f"gross NPA {_pct(g('gnpa_pct'), 2)}, net NPA {_pct(g('nnpa_pct'), 2)}",
        "efficiency": f"cost/income {_pct(g('cost_income_pct'), 0)}",
        "provisioning": f"provision coverage {_pct(g('pcr_pct'), 0)}",
        "capital": f"capital adequacy {_pct(g('car_pct'))}",
        "interest coverage": (f"interest cover {g('interest_cover'):.1f}×"
                              if g("interest_cover") is not None else ""),
    }
    strengths, concerns = [], []
    for k, v in components.items():
        if v is None or pd.isna(v):
            continue
        detail = what.get(k, "").strip(", ")
        text = f"{k.capitalize()}: {detail}" if detail else k.capitalize()
        if v >= 70:
            strengths.append(text)
        elif v <= 35:
            concerns.append(text)
    return strengths, concerns


def valuation(info: dict, quarters: list[dict]) -> tuple[dict, str]:
    """Headline multiples and one sentence on what they imply.

    There is no free sector-P/E series, so the read is absolute and paired
    with growth (a PEG-style ratio) rather than ranked against peers.
    """
    pe, pb = _num(info.get("trailingPE")), _num(info.get("priceToBook"))
    dy = _num(info.get("dividendYield"))
    mcap = _num(info.get("marketCap"))
    v = {"P/E": None if pe is None else round(pe, 1),
         "P/B": None if pb is None else round(pb, 2),
         # yfinance has reported this both as a fraction and as a percent.
         "Dividend yield %": None if dy is None else round(dy * 100 if dy < 0.5 else dy, 2),
         "Market cap (₹ cr)": None if mcap is None else round(mcap / 1e7)}
    if pe is None or pe <= 0:
        return v, "No meaningful P/E — loss-making, or not reported."
    band = ("inexpensive" if pe < 15 else "moderate" if pe < 30 else
            "rich" if pe < 50 else "very rich")
    growth = next((q["pat_yoy_pct"] for q in quarters if q["pat_yoy_pct"] is not None), None)
    note = f"P/E {pe:.0f} is {band} in absolute terms"
    if growth is not None and growth > 0:
        peg = pe / growth
        note += (f"; against profit growth of {growth:.0f}% that is a PEG of "
                 f"{peg:.1f} ({'growth covers the price' if peg <= 1.5 else 'the price assumes growth continues or speeds up'})")
    elif growth is not None:
        note += f", with profit shrinking ({growth:.0f}% year on year)"
    return v, note + "."


def stance(score: float | None, flags: list[str]) -> str:
    if score is None:
        return "Not enough data"
    s = ("Strong" if score >= 70 else "Sound" if score >= 55 else
         "Mixed" if score >= 40 else "Weak")
    # A red flag caps the stance: a high score with rising pledge is not strong.
    if flags:
        s = STANCES[min(STANCES.index(s) + 1, len(STANCES) - 1)]
    return s


# ------------------------------------------------------------------ analyse

def analyse(data: dict, sector: str = "") -> FundamentalReport:
    """Turn fetched data into a report. Pure: no network."""
    sym = data["symbol"]
    info = data.get("info", {})
    sector = sector or info.get("sector", "")
    if not data.get("rows"):
        return FundamentalReport(
            sym, "Not enough data", "Neither NSE nor Yahoo returned figures for "
            "this stock — common for SME listings and recently listed companies.",
            None, {}, fetched_at=data.get("fetched_at", ""))

    df = frame(data["rows"])
    q = fu.score_symbol(df, sym, sector)
    cur = df.iloc[sorted(range(len(df)), key=lambda i: fu.quarter_key(df["quarter"].iloc[i]))].iloc[-1]
    kind = ("bank" if _num(cur.get("gnpa_pct")) is not None else
            "nbfc" if sector.strip().lower() in fu.LENDER_SECTORS else "company")
    quarters = quarter_table(df)
    if kind == "bank":
        # A bank's "revenue" is interest earned; profit over it is not a margin.
        for r in quarters:
            r["margin_pct"] = None
    flags = red_flags(df, kind)
    strengths, concerns = _reasons(q.components, cur, quarters, kind)
    val, val_note = valuation(info, quarters)
    st = stance(q.score, flags)

    if q.score is None:
        summary = (f"Too few figures to score ({q.flags[0] if q.flags else 'no data'}). "
                   "What was found is shown below.")
    else:
        lead = {"Strong": "A financially strong business",
                "Sound": "A sound business with some soft spots",
                "Mixed": "A mixed picture",
                "Weak": "Weak fundamentals"}[st]
        summary = f"{lead} — quality {q.score:.0f}/100"
        if flags:
            summary += f", held back by {len(flags)} red flag{'s' if len(flags) > 1 else ''}"
        summary += "."
        if kind != "company":
            summary += (" Scored as a bank (asset quality first)." if kind == "bank" else
                        " Scored as a non-bank lender: debt/equity and cash flow are left out.")

    group = sec.group_of(sector, info.get("sector", ""), info.get("industry", ""), kind)
    checks = sec.checklist(group, cur.to_dict(), info)

    promoter = [p for p in data.get("promoter", []) if p.get("promoter_pct") is not None]
    notes = [f for f in q.flags if not any(w in f for w in ("HIGH", "RISING", "below 2",
                                                          "highly leveraged", "not arriving"))]
    return FundamentalReport(
        sym, st, summary, q.score, q.components, strengths, concerns, flags, notes,
        _metrics(cur, kind), quarters, promoter, val, val_note, kind,
        results_quarter(cur.get("notes")) or str(cur.get("quarter", "")),
        data.get("fetched_at", ""),
        str(cur.get("notes", "") or ""), checks)


def analyse_symbol(symbol: str, sector: str = "", refresh: bool = False) -> FundamentalReport:
    return analyse(fetch(symbol, refresh=refresh), sector)
