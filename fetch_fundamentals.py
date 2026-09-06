"""Populate the fundamentals sheet. NSE first, Yahoo for the gaps.

Source precedence, and why:

  NSE (primary)   Authoritative and always in rupees. Yahoo reports Indian IT
                  companies in USD, which silently understated HCLTECH's
                  revenue 85-fold until it was caught. NSE also has the two
                  things Yahoo simply does not carry -- promoter holding
                  history and promoter pledge.
  Yahoo (fill)    Ratios NSE does not publish directly: ROE, ROCE, debt/equity,
                  P/E, and operating cash flow. Currency-independent ratios are
                  safe from any reporter; absolute values are taken from Yahoo
                  ONLY when it reports in INR.

Every field records where it came from, so a wrong number can be traced to a
source rather than guessed at.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import fundamentals as fu
from data.sources import nse_fundamentals as nsef

CRORE = 1e7

# Fields NSE is authoritative for. Yahoo never overwrites these.
NSE_FIELDS = ("revenue", "pat", "promoter_pct", "pledge_pct", "interest_cover")
LENDER_FIELDS = ("gnpa_pct", "nnpa_pct", "pcr_pct", "roa_pct",
                 "cost_income_pct", "credit_cost_pct")


def from_nse(sym: str) -> tuple[dict, list[str]]:
    """Results + shareholding + pledge. Returns (fields, sources)."""
    out: dict = {}
    src: list[str] = []

    try:
        r = nsef.latest_results(sym)
        if r.get("revenue") is not None:
            out["revenue"] = r["revenue"]
        if r.get("pat") is not None:
            out["pat"] = r["pat"]
        # EBIT is approximated as PBT + finance costs, which is the standard
        # reconstruction when EBIT is not filed as its own line.
        pbt, fin = r.get("pbt"), r.get("finance_costs")
        if pbt is not None and fin:
            out["interest_cover"] = round((pbt + fin) / abs(fin), 2)
        if out:
            src.append(f"nse-results({r.get('to_date')})")
    except Exception:
        pass

    # Banks file a different schedule entirely. If it parses, this is a lender
    # and the ordinary revenue/PAT figures matter far less than asset quality.
    try:
        lend = nsef.lender_metrics(sym)
        if lend:
            for f in LENDER_FIELDS:
                if lend.get(f) is not None:
                    out[f] = lend[f]
            if lend.get("pat") is not None:
                out["pat"] = lend["pat"]
            src.append(f"nse-lender({lend.get('to_date')})")
    except Exception:
        pass

    try:
        p = nsef.pledge_for(sym)
        if p.get("promoter_pct") is not None:
            out["promoter_pct"] = round(float(p["promoter_pct"]), 2)
        if p.get("pledge_pct") is not None:
            out["pledge_pct"] = p["pledge_pct"]
            src.append(f"nse-shp({p.get('as_of')})")
    except Exception:
        pass
    return out, src


def from_yahoo(sym: str) -> tuple[dict, list[str]]:
    """Ratios, plus absolute values only when Yahoo reports in INR."""
    import yfinance as yf

    out: dict = {}
    t = yf.Ticker(f"{sym}.NS")
    info = t.info or {}
    currency = (info.get("financialCurrency") or "INR").upper()

    if info.get("returnOnEquity") is not None:
        out["roe"] = round(info["returnOnEquity"] * 100, 2)
    # Yahoo's debtToEquity is a PERCENTAGE: 10.2 means a ratio of 0.102.
    if info.get("debtToEquity") is not None:
        out["debt_equity"] = round(info["debtToEquity"] / 100, 3)
    if info.get("trailingPE") is not None:
        out["pe"] = round(info["trailingPE"], 2)

    if currency == "INR":
        for key, col in (("totalRevenue", "revenue"),
                         ("netIncomeToCommon", "pat")):
            if info.get(key) is not None:
                out[col] = round(info[key] / CRORE, 1)
        # Yahoo's figure is annual. Kept under its own name so it survives NSE
        # overwriting `pat` with a quarterly number, and so cash quality has an
        # annual profit to divide into an annual cash flow.
        if info.get("netIncomeToCommon") is not None:
            out["pat_annual"] = round(info["netIncomeToCommon"] / CRORE, 1)
        if info.get("heldPercentInsiders") is not None:
            out["promoter_pct"] = round(info["heldPercentInsiders"] * 100, 2)
        try:
            cf = t.cashflow
            rows = [r for r in cf.index if "operating cash flow" in str(r).lower()]
            if rows and not cf.loc[rows[0]].empty:
                v = cf.loc[rows[0]].iloc[0]
                if v == v:
                    out["cfo"] = round(float(v) / CRORE, 1)
        except Exception:
            pass
        try:
            fin = t.financials
            ebit = [r for r in fin.index if str(r).strip() == "EBIT"]
            eq = info.get("totalStockholderEquity") or \
                info.get("bookValue", 0) * (info.get("sharesOutstanding") or 0)
            debt = info.get("totalDebt") or 0
            if ebit and eq and (eq + debt) > 0:
                out["roce"] = round(
                    100 * float(fin.loc[ebit[0]].iloc[0]) / (eq + debt), 2)
        except Exception:
            pass

    try:
        fin = t.financials
        ebit = [r for r in fin.index if str(r).strip() == "EBIT"]
        inte = [r for r in fin.index if "interest expense" in str(r).lower()]
        if ebit and inte:
            e, i = float(fin.loc[ebit[0]].iloc[0]), float(fin.loc[inte[0]].iloc[0])
            if i and i == i and e == e:
                out["interest_cover"] = round(e / abs(i), 2)
    except Exception:
        pass

    return out, ([f"yahoo({currency})"] if out else [])


def merge(sym: str) -> dict:
    """NSE wins on its authoritative fields; Yahoo fills the rest."""
    nse_vals, nse_src = from_nse(sym)
    try:
        y_vals, y_src = from_yahoo(sym)
    except Exception:
        y_vals, y_src = {}, []

    row = dict(y_vals)
    for k, v in nse_vals.items():
        row[k] = v                       # NSE overrides Yahoo wherever it has data
    row["symbol"] = sym

    row["_nse_abs"] = "revenue" in nse_vals or "pat" in nse_vals
    row["_is_lender"] = any(f in nse_vals for f in LENDER_FIELDS)
    used = []
    if any(k in nse_vals for k in NSE_FIELDS):
        used += nse_src
    if y_vals:
        used += y_src
    row["_sources"] = "; ".join(used) or "none"
    row["_nse_fields"] = sorted(k for k in nse_vals if k in NSE_FIELDS)
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=str(fu.FUNDAMENTALS_PATH))
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--yahoo-only", action="store_true",
                    help="skip NSE (much faster; loses pledge and promoter data)")
    args = ap.parse_args()

    path = Path(args.path)
    existing = fu.load(path)
    symbols = args.symbols or sorted(existing["symbol"].unique())
    quarter = f"{dt.date.today().year}Q{(dt.date.today().month - 1) // 3 + 1}"

    print(f"Fetching fundamentals for {len(symbols)} symbols "
          f"({'Yahoo only' if args.yahoo_only else 'NSE primary, Yahoo fallback'})")
    rows, failed = [], []
    for i, sym in enumerate(symbols, 1):
        if sym.endswith("-RR"):
            failed.append(f"{sym} (REIT -- not an equity, cannot be scored)")
            continue
        try:
            r = (dict(from_yahoo(sym)[0], symbol=sym, _sources="yahoo",
                      _nse_fields=[], _nse_abs=False)
                 if args.yahoo_only else merge(sym))
            r.setdefault("_is_lender", False)
            data_fields = [k for k in r if not k.startswith("_") and k != "symbol"]
            if not data_fields:
                failed.append(f"{sym} (no data from any source)")
                continue
            r["quarter"] = quarter
            # NSE results are quarterly; Yahoo's absolute figures are annual.
            # Record which basis this row's revenue/pat sit on.
            r["period"] = "quarterly" if r.pop("_nse_abs", False) else "annual"
            r["notes"] = r.pop("_sources")
            nsef_used = r.pop("_nse_fields")
            lender = r.pop("_is_lender", False)
            rows.append(r)
            print(f"  [{i}/{len(symbols)}] {sym}: {len(data_fields)} fields"
                  f"{'  [LENDER]' if lender else ''}"
                  f"{'  NSE: ' + ','.join(nsef_used) if nsef_used else ''}")
        except Exception as exc:
            failed.append(f"{sym} ({type(exc).__name__}: {str(exc)[:50]})")

    # Keep two kinds of existing row: anything hand-entered, and any symbol
    # this run did not touch. Rewriting the whole file from a --symbols subset
    # silently deleted the other 33 holdings the first time.
    fetched = {r["symbol"] for r in rows}
    keep = existing
    if not existing.empty:
        auto = existing["notes"].astype(str).str.contains("nse-|yahoo", na=False) \
            if "notes" in existing.columns else False
        is_manual = ~auto if auto is not False else existing["symbol"].notna()
        untouched = ~existing["symbol"].isin(fetched)
        keep = existing[is_manual | untouched]
    manual = keep.dropna(subset=[c for c in fu.COLUMNS
                                 if c not in ("symbol", "quarter", "notes", "period")],
                         how="all")

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        fh.write(fu.TEMPLATE_HELP)
        fh.write("#\n# 'notes' records the source of each row. NSE is authoritative\n"
                 "# for revenue, pat, promoter_pct, pledge_pct and interest_cover.\n")
        w = csv.DictWriter(fh, fieldnames=fu.COLUMNS)
        w.writeheader()
        for _, m in manual.iterrows():
            w.writerow({c: ("" if m.get(c) != m.get(c) else m.get(c, ""))
                        for c in fu.COLUMNS})
        for r in rows:
            w.writerow({c: r.get(c, "") for c in fu.COLUMNS})

    print(f"\nWrote {len(rows)} rows (+{len(manual)} hand-entered kept) to {path}")
    if failed:
        print("\nNot fetched:")
        for f in failed:
            print(f"  {f}")


if __name__ == "__main__":
    main()
