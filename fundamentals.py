"""Fundamentals sheet: your manual analysis, made scoreable.

v1 cut fundamentals because no free, complete Indian source exists. This does
not try to solve that -- it accepts a hand-entered CSV instead. For a watchlist
of 15-30 names, filling six numbers a quarter from results you already read is
perhaps twenty minutes of work, and it turns your judgement into something the
tool can rank, track and warn on.

Everything is optional. A stock with no fundamentals data simply has no Quality
Score and is reported as such -- never silently scored as if it were average,
which would let unknown risk masquerade as acceptable risk.
"""
from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

FUNDAMENTALS_PATH = Path(__file__).parent / "config" / "fundamentals.csv"

COLUMNS = ["symbol", "quarter", "period", "revenue", "pat", "pat_annual",
           "cfo", "roe", "roce", "debt_equity", "interest_cover",
           "promoter_pct", "pledge_pct", "pe",
           # Lender-only. Meaningless for an ordinary company, and the columns
           # above are largely meaningless for a bank -- which is why lenders
           # get their own rubric rather than a discount on this one.
           "gnpa_pct", "nnpa_pct", "pcr_pct", "roa_pct", "cost_income_pct",
           "credit_cost_pct", "car_pct", "casa_pct",
           "notes"]

TEMPLATE_HELP = """\
# One row per stock per quarter. Fill what you have; blanks are fine.
#
#   quarter        FY26Q1 style, or 2026-06-30
#   period         quarterly | annual -- WHICH BASIS revenue/pat/cfo are on.
#                  Mixing a quarterly profit with an annual cash flow makes
#                  CFO/PAT wrong by 4x, so the score refuses to compute it
#                  unless this says the two agree.
#   revenue, pat   in crores, from the results (basis given by `period`)
#   pat_annual     annual profit in crores. Kept separate so cash quality can
#                  be computed against annual CFO without mixing periods.
#   cfo            cash from operations (annual or trailing) -- the single
#                  most useful number here; profits that never become cash
#                  are the most common Indian accounting problem
#   roe, roce      percent
#   debt_equity    ratio (0.5 not 50)
#   interest_cover EBIT / interest. Below 2 is a red flag
#   promoter_pct   promoter holding, percent
#   pledge_pct     percent of promoter holding pledged. RISING PLEDGE IS THE
#                  LOUDEST PRE-COLLAPSE SIGNAL IN INDIAN SMALLCAPS
#   pe             current P/E
#
# --- banks and NBFCs only -------------------------------------------------
#   gnpa_pct       gross NPA %. THE number for a lender. Below 2 is clean,
#                  above 6 is a problem you can see from orbit.
#   nnpa_pct       net NPA % (after provisions). Below 1 is healthy.
#   pcr_pct        provision coverage: how much of bad loans is already
#                  written down. Above 70 is prudent, below 50 is thin.
#   roa_pct        return on assets, annualised. For a lender this beats ROE,
#                  which leverage flatters by construction.
#   cost_income_pct  operating efficiency. Lower is better.
#   credit_cost_pct  provisions as % of pre-provision profit.
#   car_pct        capital adequacy. Regulatory floor ~11.5%; below 12 is
#                  uncomfortably close. NOT filed in XBRL -- enter by hand.
#   casa_pct       current + savings deposits as % of total. Cheap funding.
#                  Banks only, not NBFCs. Enter by hand.
"""

# Financials need entirely different metrics (NIM, GNPA, CASA, capital
# adequacy). Scoring them on debt/equity and CFO produces confident nonsense,
# so they are flagged rather than scored.
LENDER_SECTORS = {"financial services", "banking", "financial service"}


LENDER_FIELDS = ("gnpa_pct", "nnpa_pct", "pcr_pct", "roa_pct",
                 "cost_income_pct", "credit_cost_pct")


def _has_lender_data(row) -> bool:
    return any(not pd.isna(row.get(f)) for f in LENDER_FIELDS)


@dataclass
class Quality:
    symbol: str
    score: float | None
    components: dict
    flags: list[str]
    as_of: str = ""

    def explain(self) -> str:
        if self.score is None:
            # Say *why* there is no score. "No data" and "data present but not
            # scoreable" are different situations and must not read alike.
            reason = self.flags[0] if self.flags else "no fundamentals on file"
            return f"{self.symbol}: no quality score -- {reason}"
        bits = " | ".join(f"{k} {v:.0f}" for k, v in self.components.items()
                          if v is not None and not pd.isna(v))
        out = f"{self.symbol}: quality {self.score:.0f}  [{bits}]"
        if self.flags:
            out += "\n      " + "\n      ".join(f"FLAG: {f}" for f in self.flags)
        return out


def write_template(symbols: list[str], path: Path | str = FUNDAMENTALS_PATH) -> Path:
    """Create the sheet pre-filled with your symbols and blank columns."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    q = f"{dt.date.today().year}Q{(dt.date.today().month - 1) // 3 + 1}"
    with path.open("w", newline="", encoding="utf-8") as fh:
        fh.write(TEMPLATE_HELP)
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for s in symbols:
            w.writerow({"symbol": s, "quarter": q})
    return path


def load(path: Path | str = FUNDAMENTALS_PATH) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(path, comment="#")
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = np.nan
        if c not in ("symbol", "quarter", "notes", "period"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["symbol"])


def _lin(value, lo: float, hi: float):
    """Map onto 0-100, clipped. lo > hi inverts (lower is better)."""
    if value is None or pd.isna(value):
        return None
    return float(np.clip(100 * (value - lo) / (hi - lo), 0, 100))


def _mean(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return float(np.mean(vals)) if vals else None


def score_symbol(df: pd.DataFrame, symbol: str, sector: str = "") -> Quality:
    """Quality Score from whatever is on file. Missing inputs are dropped,
    not treated as zero -- absent data must not read as bad data."""
    rows = df[df["symbol"] == symbol.upper()].sort_values("quarter")
    if rows.empty:
        return Quality(symbol, None, {}, ["no fundamentals on file"])
    cur = rows.iloc[-1]
    flags: list[str] = []

    if _has_lender_data(cur):
        return score_lender(symbol, rows, flags)
    if sector.strip().lower() in LENDER_SECTORS:
        # An NBFC: a lender that files the ordinary corporate schedule, so
        # there is no NPA data to score. Debt/equity and CFO still mean nothing
        # -- borrowing is the raw material, and operating cash flow goes deeply
        # negative precisely when lending is growing well -- so those are
        # dropped rather than counted against it.
        return score_nbfc(symbol, rows, flags)

    profitability = _mean([_lin(cur.get("roe"), 8, 25),
                           _lin(cur.get("roce"), 10, 30)])
    balance = _mean([_lin(cur.get("debt_equity"), 1.5, 0.1),
                     _lin(cur.get("interest_cover"), 2, 12)])

    # CFO/PAT is only meaningful when both cover the same span. NSE files
    # quarterly results while Yahoo's cash flow is annual, so pairing them
    # blindly reports a 4x-inflated ratio as excellent cash conversion.
    # Prefer an explicitly annual profit; fall back to `pat` only when the
    # row says it is annual. Cash flow is always annual, so anything else
    # would inflate the ratio roughly fourfold.
    cfo = cur.get("cfo")
    basis = str(cur.get("period", "")).strip().lower()
    pat_a = cur.get("pat_annual")
    pat = pat_a if not pd.isna(pat_a) else (
        cur.get("pat") if basis == "annual" else float("nan"))
    cash = None
    if not pd.isna(cfo) and not pd.isna(pat) and pat > 0:
        ratio = cfo / pat
        cash = _lin(ratio, 0.3, 1.2)
        if ratio < 0.5:
            flags.append(f"CFO/PAT is {ratio:.2f} -- reported profit is not "
                         f"arriving as cash")
    elif not pd.isna(cfo) and not pd.isna(cur.get("pat")) and basis != "annual":
        flags.append("cash quality not scored: no annual profit on file, and "
                     "pairing quarterly profit with annual cash flow would "
                     "overstate CFO/PAT ~4x")

    # Professionally managed companies (HDFC Bank, Crompton, most banks) have
    # no identifiable promoter at all. Scoring them as if a 0% promoter stake
    # were an absentee founder punishes exactly the ownership structure that
    # needs no promoter scrutiny -- so the promoter component is dropped rather
    # than scored, and only pledge risk applies.
    promoter = cur.get("promoter_pct")
    no_promoter = not pd.isna(promoter) and promoter < 5
    if no_promoter:
        flags.append("no identifiable promoter (professionally managed); "
                     "promoter holding not scored")
    gov = _mean([None if no_promoter else _lin(promoter, 25, 60),
                 _lin(cur.get("pledge_pct"), 25, 0)])
    pledge = cur.get("pledge_pct")
    if not pd.isna(pledge) and pledge > 0:
        # Severity matters: 0.08% is housekeeping, 25% is a solvency question
        # about the promoter that becomes a solvency question about the stock.
        if pledge >= 25:
            flags.append(f"promoter pledge {pledge:.1f}% -- HIGH. Forced selling "
                         f"by lenders is how these positions unwind")
        elif pledge >= 5:
            flags.append(f"promoter pledge {pledge:.1f}% -- worth watching")
        else:
            flags.append(f"promoter pledge {pledge:.2f}% (small)")
        if len(rows) > 1:
            prev = rows.iloc[-2].get("pledge_pct")
            if not pd.isna(prev) and pledge > prev + 1:
                flags.append(f"pledge RISING: {prev:.1f}% -> {pledge:.1f}%")

    # Promoters quietly reducing their stake over several quarters is a signal
    # no single snapshot shows.
    if len(rows) > 1:
        prev_p = rows.iloc[-2].get("promoter_pct")
        now_p = cur.get("promoter_pct")
        if not pd.isna(prev_p) and not pd.isna(now_p) and now_p < prev_p - 1:
            flags.append(f"promoter holding fell {prev_p:.2f}% -> {now_p:.2f}%")

    growth = None
    if len(rows) >= 5:
        r0, r1 = rows.iloc[-5].get("revenue"), cur.get("revenue")
        if not pd.isna(r0) and not pd.isna(r1) and r0 > 0:
            growth = _lin(100 * (r1 / r0 - 1), -5, 25)

    ic = cur.get("interest_cover")
    if not pd.isna(ic) and ic < 2:
        flags.append(f"interest cover {ic:.1f} -- below 2")
    de = cur.get("debt_equity")
    if not pd.isna(de) and de > 2:
        flags.append(f"debt/equity {de:.2f} -- highly leveraged")

    components = {"growth": growth, "profitability": profitability,
                  "balance sheet": balance, "cash quality": cash,
                  "governance": gov}
    score = _mean(list(components.values()))
    return Quality(symbol, score, components, flags, str(cur.get("quarter", "")))


def score_lender(symbol: str, rows: pd.DataFrame,
                flags: list[str]) -> Quality:
    """Quality score for a bank or NBFC.

    A lender is not a company with unusual accounting -- it is a different kind
    of business, and the ordinary rubric actively misleads on one. Debt IS the
    raw material, so debt/equity says nothing. Operating cash flow swings with
    loan growth, so a fast-growing healthy lender shows deeply negative CFO.
    ROE is flattered by leverage, so ROA is the honest profitability measure.

    What actually matters is whether the loans get repaid, which is why asset
    quality carries the most weight here.
    """
    cur = rows.iloc[-1]
    W = {"asset_quality": 0.35, "profitability": 0.25, "efficiency": 0.15,
         "provisioning": 0.15, "capital": 0.10}

    gnpa, nnpa, pcr = cur.get("gnpa_pct"), cur.get("nnpa_pct"), cur.get("pcr_pct")
    asset_quality = _mean([_lin(gnpa, 8.0, 1.0),      # inverted: lower is better
                           _lin(nnpa, 3.0, 0.3)])
    profitability = _lin(cur.get("roa_pct"), 0.3, 2.0)
    efficiency = _lin(cur.get("cost_income_pct"), 75.0, 40.0)

    # A provision write-back (negative credit cost) flatters current profit and
    # is not evidence of quality, so it is treated as neutral rather than good.
    cc = cur.get("credit_cost_pct")
    cc = 0.0 if (not pd.isna(cc) and cc < 0) else cc
    provisioning = _mean([_lin(pcr, 40.0, 80.0), _lin(cc, 45.0, 5.0)])
    capital = _lin(cur.get("car_pct"), 11.0, 18.0)

    components = {"asset quality": asset_quality, "profitability": profitability,
                  "efficiency": efficiency, "provisioning": provisioning,
                  "capital": capital}
    present = {k: v for k, v in components.items() if v is not None}
    score = None
    if present:
        weight_map = {"asset quality": W["asset_quality"],
                      "profitability": W["profitability"],
                      "efficiency": W["efficiency"],
                      "provisioning": W["provisioning"],
                      "capital": W["capital"]}
        total_w = sum(weight_map[k] for k in present)
        score = sum(present[k] * weight_map[k] for k in present) / total_w

    if not pd.isna(gnpa):
        if gnpa > 6:
            flags.append(f"gross NPA {gnpa:.2f}% -- high; asset quality is the "
                         f"thing that sinks lenders")
        elif gnpa > 3:
            flags.append(f"gross NPA {gnpa:.2f}% -- worth watching")
    if not pd.isna(nnpa) and nnpa > 2:
        flags.append(f"net NPA {nnpa:.2f}% -- bad loans not fully provided for")
    if not pd.isna(pcr) and pcr < 50:
        flags.append(f"provision coverage {pcr:.0f}% -- thin; future losses "
                     f"will hit profit directly")
    car = cur.get("car_pct")
    if not pd.isna(car) and car < 12:
        flags.append(f"capital adequacy {car:.1f}% -- close to the ~11.5% "
                     f"regulatory floor")
    if len(rows) > 1:
        prev_g = rows.iloc[-2].get("gnpa_pct")
        if not pd.isna(prev_g) and not pd.isna(gnpa) and gnpa > prev_g + 0.3:
            flags.append(f"gross NPA RISING: {prev_g:.2f}% -> {gnpa:.2f}%")
    if pd.isna(car):
        flags.append("capital adequacy not on file -- add car_pct by hand, it is "
                     "not in NSE's XBRL")

    return Quality(symbol, score, components, flags, str(cur.get("quarter", "")))


def score_nbfc(symbol: str, rows: pd.DataFrame, flags: list[str]) -> Quality:
    """Lenders that file the ordinary schedule: NBFCs, card issuers, HFCs.

    Scored on what still applies -- returns, growth, valuation -- with the two
    metrics that actively mislead for a lender removed.
    """
    cur = rows.iloc[-1]
    profitability = _mean([_lin(cur.get("roe"), 8, 22),
                           _lin(cur.get("roa_pct"), 0.8, 3.5)])
    growth = None
    if len(rows) >= 5:
        r0, r1 = rows.iloc[-5].get("revenue"), cur.get("revenue")
        if not pd.isna(r0) and not pd.isna(r1) and r0 > 0:
            growth = _lin(100 * (r1 / r0 - 1), -5, 25)
    coverage = _lin(cur.get("interest_cover"), 1.2, 3.0)

    promoter = cur.get("promoter_pct")
    no_promoter = not pd.isna(promoter) and promoter < 5
    gov = _mean([None if no_promoter else _lin(promoter, 25, 60),
                 _lin(cur.get("pledge_pct"), 25, 0)])

    components = {"profitability": profitability, "growth": growth,
                  "interest coverage": coverage, "governance": gov}
    score = _mean(list(components.values()))
    flags.append("NBFC / non-bank lender: scored without debt/equity or CFO, "
                 "which do not mean for a lender what they mean elsewhere. "
                 "No NPA data is filed on this schedule -- add gnpa_pct by "
                 "hand for a real asset-quality read.")
    return Quality(symbol, score, components, flags, str(cur.get("quarter", "")))


def score_all(symbols: list[str], sectors: dict[str, str] | None = None,
              path: Path | str = FUNDAMENTALS_PATH) -> dict[str, Quality]:
    df = load(path)
    sectors = sectors or {}
    return {s: score_symbol(df, s, sectors.get(s, "")) for s in symbols}


def passes_gate(q: Quality, floor: float, allow_unknown: bool = True) -> bool:
    """Quality gate for the combined rank.

    `allow_unknown` decides what an unscored stock means. Default True so the
    momentum strategy still works before the sheet is filled -- but the daily
    run says plainly which names are riding on no fundamentals at all.
    """
    if q.score is None:
        return allow_unknown
    return q.score >= floor
