"""PPFAS monthly portfolio disclosure: download and parse.

SEBI makes every AMC publish each scheme's full portfolio within ~10 days of
month-end. PPFAS posts one workbook per scheme per month at a predictable URL:

    https://amc.ppfas.com/downloads/portfolio-disclosure/<year>/
        PPFCF_PPFAS_Monthly_Portfolio_Report_<Month>_<day>_<year>.xlsx

The files were .xls up to Feb 2026 and .xlsx from Mar 2026; the layout inside
did not change. One sheet, read top to bottom as a sequence of sections:

    Equity & Equity related
      (a) Listed / awaiting listing     <- the real stock picks ("core")
      Arbitrage                         <- cash-and-carry: stock long + futures
                                           short, so NO price exposure
      Special Situation                 <- some months: partly-paid rights shares
      (b) Reits
      (c) Unlisted
    Equity & Equity related Foreign Investments   <- Alphabet, Meta, ...
    Money Market Instruments  (CDs, CP, T-bills)
    Others: Mutual Fund Units, Reverse Repo / TREPS
    Net Receivables / (Payables)
    GRAND TOTAL                                   <- net assets (AUM)
    Derivatives  (the short futures leg of the arbitrage book)
    Notes: plan-wise NAV for this and last month-end, turnover ratios, ...

Column 1 carries the section headings and the instrument names, column 2 the
ISIN; holding rows are the ones with an ISIN and a numeric quantity. Values are
in Rs lakh. "% to Net Assets" prints tiny weights as the string "$0.00%", so
the weight is recomputed as value / grand total instead of read.

Anything in a section this parser does not recognise is kept, bucketed as
"other", and listed in `warnings` -- a new kind of holding should show up as a
visible oddity, not vanish.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

BASE_URL = "https://amc.ppfas.com/downloads/portfolio-disclosure"
SCHEME_PREFIX = "PPFCF"        # Parag Parikh Flexi Cap Fund
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "mf" / "ppfas_raw"

# Bucket names used everywhere downstream.
CORE = "core_equity"           # domestic listed stock picks
ARBITRAGE = "arbitrage"        # hedged; behaves like cash
SPECIAL = "special_situation"  # e.g. partly-paid rights shares
REIT = "reit"
UNLISTED = "unlisted"
FOREIGN = "foreign_equity"
MONEY_MARKET = "money_market"  # CDs, commercial paper, T-bills, bonds
MF_UNITS = "mf_units"          # parked in their own liquid fund
TREPS = "treps"                # overnight lending, i.e. cash
OTHER = "other"

# Buckets that are a manager's stock pick, as opposed to cash management.
PICK_BUCKETS = (CORE, SPECIAL, REIT, UNLISTED, FOREIGN)


@dataclass
class Disclosure:
    month_end: dt.date
    holdings: pd.DataFrame            # one row per instrument
    derivatives: pd.DataFrame         # futures legs, quantity negative = short
    net_assets: float                 # Rs lakh, the GRAND TOTAL
    net_receivables: float            # Rs lakh
    nav_direct: float | None          # Direct Growth NAV at month_end
    nav_direct_prev: float | None     # ... and at the previous month-end
    turnover_incl_arb: float | None   # %, as printed in the notes
    turnover_excl_arb: float | None
    source: str = ""
    warnings: list[str] = field(default_factory=list)


# --- download -------------------------------------------------------------

def month_end(year: int, month: int) -> dt.date:
    nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
    return nxt - dt.timedelta(days=1)


def file_stem(d: dt.date, scheme: str = SCHEME_PREFIX) -> str:
    return f"{scheme}_PPFAS_Monthly_Portfolio_Report_{d:%B}_{d.day}_{d.year}"


def download(d: dt.date, dest: Path = RAW_DIR, scheme: str = SCHEME_PREFIX,
             session=None) -> Path | None:
    """Fetch one month's file into `dest`. Returns the path, or None if PPFAS
    has not published it yet. Already-downloaded months are not re-fetched."""
    import requests

    dest.mkdir(parents=True, exist_ok=True)
    stem = file_stem(d, scheme)
    for ext in (".xlsx", ".xls"):
        if (dest / f"{stem}{ext}").exists():
            return dest / f"{stem}{ext}"
    s = session or requests.Session()
    for ext in (".xlsx", ".xls"):
        url = f"{BASE_URL}/{d.year}/{stem}{ext}"
        r = s.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
        # A missing file comes back as an HTML error page, sometimes with 200.
        if r.ok and r.content[:4] in (b"PK\x03\x04", b"\xd0\xcf\x11\xe0"):
            path = dest / f"{stem}{ext}"
            path.write_bytes(r.content)
            return path
    return None


def raw_files(src: Path = RAW_DIR, scheme: str = SCHEME_PREFIX) -> list[Path]:
    return sorted(p for p in src.glob(f"{scheme}_*.xls*")
                  if not p.name.startswith("~$"))


# --- parse ----------------------------------------------------------------

_DATE_RE = re.compile(r"as on\s+([A-Za-z]+\s+\d{1,2},\s*\d{4})", re.I)
_TURNOVER_RE = re.compile(
    r"Turnover Ratio\s*\((Including|Excluding) Equity Arbitrage\)\s*:\s*([\d.]+)", re.I)
_FUTURE_SUFFIX_RE = re.compile(
    r"\s+(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{4}(\s+Future)?\s*$", re.I)


def _num(v) -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _text(v) -> str:
    return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()


def _section(label: str, current_top: str) -> tuple[str, str] | None:
    """Map a heading in column 1 to (top-level section, bucket); None if the
    row is not a heading.

    The lettering is not stable: foreign equity is its own top-level block in
    most months but "(d) Equity & Equity related Foreign Investments" inside
    the domestic block in Feb 2026, and unlisted is "(c)" or "(b)". So the
    "(x) " prefix is dropped before matching."""
    lo = re.sub(r"^\([a-z]\)\s*", "", label.lower())
    if lo.startswith("equity & equity related foreign"):
        return "foreign", FOREIGN
    if lo.startswith("equity & equity related"):
        return "domestic", CORE
    if lo.startswith("special situation"):
        return current_top, SPECIAL
    if lo.startswith("money market") or lo.startswith("debt instruments"):
        return "money", MONEY_MARKET
    if lo in ("others",):
        return "others", OTHER
    if lo.startswith("mutual fund units"):
        return current_top, MF_UNITS
    if lo.startswith("reverse repo") or "treps" in lo:
        return current_top, TREPS
    if lo == "arbitrage":
        return current_top, ARBITRAGE
    if lo.startswith("listed"):
        return current_top, FOREIGN if current_top == "foreign" else CORE
    if lo.startswith("reits") or lo.startswith("invits"):
        return current_top, REIT
    if lo.startswith("unlisted"):
        return current_top, FOREIGN if current_top == "foreign" else UNLISTED
    if current_top == "money" and lo in (
            "certificate of deposit", "commercial paper", "treasury bill",
            "government securities", "corporate bonds"):
        return current_top, MONEY_MARKET
    return None


def parse(path: Path | str) -> Disclosure:
    path = Path(path)
    df = pd.read_excel(path, header=None, sheet_name=0, dtype=object)
    warnings: list[str] = []

    month = None
    for i in range(min(10, len(df))):
        m = _DATE_RE.search(_text(df.iat[i, 1]))
        if m:
            month = dt.datetime.strptime(re.sub(r"\s+", " ", m.group(1)), "%B %d, %Y").date()
            break
    if month is None:
        raise ValueError(f"{path.name}: no 'Monthly Portfolio Statement as on' line")

    rows, derivs = [], []
    top, bucket, sub = "", OTHER, ""
    net_assets = net_recv = None
    in_derivs = False
    nav = nav_prev = None
    turnover = {}

    for i in range(len(df)):
        c = [df.iat[i, j] if j < df.shape[1] else None for j in range(9)]
        label = _text(c[1])
        lo = label.lower()

        m = _TURNOVER_RE.search(label)
        if m:
            turnover[m.group(1).lower()] = float(m.group(2))
            continue
        # The NAV table in the notes. Its own-liquid-fund holding row also reads
        # "Direct Plan- Growth", hence the numeric check on both NAV columns.
        if (nav is None and "direct plan growth" in " ".join(lo.replace("-", " ").split())
                and _num(c[2]) is not None and _num(c[3]) is not None):
            nav_prev, nav = _num(c[2]), _num(c[3])
            continue

        if lo == "derivatives":
            in_derivs = True
            continue
        if in_derivs:
            # Futures rows: name | (Short)/Long | quantity | value | % to AUM.
            if _text(c[2]).lower().strip("()") in ("short", "long") and _num(c[3]) is not None:
                derivs.append({
                    "code": _text(c[0]), "instrument": label,
                    "underlying": _FUTURE_SUFFIX_RE.sub("", label).strip(),
                    "side": _text(c[2]).strip("()").lower(),
                    "quantity": _num(c[3]), "value_lakh": _num(c[4]),
                })
            elif lo.startswith("total") or lo.startswith("#") or lo.startswith("notes"):
                if lo.startswith("#") or lo.startswith("notes"):
                    in_derivs = False
            continue

        if lo.startswith("grand total"):
            net_assets = _num(c[5])
            continue
        if lo.startswith("net receivables"):
            net_recv = _num(c[5])
            continue
        if lo.startswith("sub total") or lo == "total":
            continue

        isin = _text(c[2])
        qty, val = _num(c[4]), _num(c[5])
        is_holding = val is not None and (isin or bucket == TREPS) and net_assets is None
        if not is_holding:
            sec = _section(label, top)
            if sec:
                top, bucket = sec
                sub = label if top == "money" else ""
            continue

        if bucket == OTHER:
            warnings.append(f"{path.name} row {i + 1}: holding in an unrecognised "
                            f"section: {label!r}")
        rows.append({
            "code": _text(c[0]), "name": label, "isin": isin,
            "industry": _text(c[3]).replace("##", "").strip(),
            "quantity": qty, "value_lakh": val, "bucket": bucket,
            "sub_type": sub, "ytm": _num(c[7]),
        })

    if net_assets is None:
        raise ValueError(f"{path.name}: no GRAND TOTAL row")
    h = pd.DataFrame(rows)
    h["weight"] = h["value_lakh"] / net_assets
    h.insert(0, "month_end", month)

    # The parts must add back up to the total, or a section was missed.
    parts = h["value_lakh"].sum() + (net_recv or 0.0)
    if abs(parts - net_assets) > max(1.0, 1e-4 * net_assets):
        warnings.append(f"{path.name}: holdings + receivables = {parts:,.0f} lakh "
                        f"but GRAND TOTAL = {net_assets:,.0f} lakh")
    if nav is None:
        warnings.append(f"{path.name}: Direct Plan Growth NAV not found")

    d = pd.DataFrame(derivs, columns=["code", "instrument", "underlying", "side",
                                      "quantity", "value_lakh"])
    d.insert(0, "month_end", month)
    return Disclosure(
        month_end=month, holdings=h, derivatives=d, net_assets=net_assets,
        net_receivables=net_recv or 0.0, nav_direct=nav, nav_direct_prev=nav_prev,
        turnover_incl_arb=turnover.get("including"),
        turnover_excl_arb=turnover.get("excluding"),
        source=path.name, warnings=warnings,
    )
