"""AMFI NAV files: the live scheme master and the historical NAV report.

Two free files, no key:

  NAVAll.txt          every live scheme's latest NAV, under its SEBI category
  NAV history report  every scheme's NAV over a date range -- closed and merged
                      schemes included, which is what makes a backtest free of
                      survivorship bias

Both group rows under heading lines rather than carrying the category on the
row, and the history report has a different column order from NAVAll. The
history report also labels every scheme that still exists with its *current*
category, even on 2014 dates, while schemes that died before SEBI's 2017-18
recategorisation keep the old labels ("Growth", "ELSS"). So a scheme's
category here is its latest one; there is no point-in-time category.

Only Direct Growth schemes are kept: direct plans started in January 2013,
carry no distributor commission, and growth NAVs compound without payouts --
the only series a return can be read straight off.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

import pandas as pd
import requests

NAVALL_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"
HISTORY_URL = ("https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"
               "?frmdt={start:%d-%b-%Y}&todt={end:%d-%b-%Y}")
TIMEOUT = 120

HEADING_RE = re.compile(r"^\s*(Open Ended|Close Ended|Interval Fund)[^(]*\((.*)\)\s*$",
                        re.IGNORECASE)

# The group half of a heading, as AMFI has spelled it over the years.
GROUPS = {
    "equity scheme": "equity", "equity schemes": "equity",
    "hybrid scheme": "hybrid", "hybrid schemes": "hybrid",
    "debt scheme": "debt", "income/debt oriented schemes": "debt",
    "solution oriented scheme": "solution", "solution oriented schemes": "solution",
    "other scheme": "other", "index funds": "index",
    "exchange traded funds (etfs)": "etf",
    "fund of funds scheme (domestic)": "fof", "overseas fund of funds": "fof",
    "life cycle funds": "solution", "children's fund": "solution",
}

# Pre-2018 single-word headings that still turn up: schemes that closed before
# AMFI re-labelled them keep the label they were filed under. ELSS is
# unambiguous; "Growth" covered every equity style, so the name has to say.
OLD_LABELS = {"elss": "equity:elss"}
NAME_HINTS = [
    (r"small ?cap", "equity:small cap"),
    (r"mid ?cap|emerging", "equity:mid cap"),
    (r"large (&|and) mid|equity opportunit", "equity:large & mid cap"),
    (r"blue ?chip|large ?cap|top ?(100|200)|frontline", "equity:large cap"),
    (r"flexi ?cap|multi ?cap|diversified", "equity:flexi cap"),
    (r"tax ?(saver|saving|relief|advantage)|elss", "equity:elss"),
    (r"focus", "equity:focused"),
    (r"value", "equity:value"),
    (r"contra", "equity:contra"),
    (r"dividend yield", "equity:dividend yield"),
    (r"bank|financ|infra|pharma|health|tech|consum|fmcg|energy|power|psu|"
     r"auto|resource|realty|real estate|manufactur|commodit|esg|mnc|"
     r"international|global|world|us |nasdaq", "equity:sectoral/thematic"),
]

# The same category under different wordings. Keys are after _clean().
ALIASES = {
    "dynamic asset allocation or balanced advantage": "balanced advantage",
    "balanced advantage fund/ dynamic asset allocation": "balanced advantage",
    "balanced advantage/ dynamic asset allocation": "balanced advantage",
    "multi asset allocation": "multi asset allocation",
    "sectoral/ thematic": "sectoral/thematic",
    "sectoral": "sectoral/thematic",
    "thematic": "sectoral/thematic",
    "equity funds": "index funds",
    "other etfs": "etfs",
    "equity etf": "etfs",
    "fund of funds investing overseas": "overseas",
    "fof overseas": "overseas",
    "fund of funds scheme (domestic)": "domestic",
    "fof domestic": "domestic",
    "elss- tax saver": "elss",
    "elss - tax saver": "elss",
    "index": "index funds",
}


@dataclass(frozen=True)
class Row:
    code: int
    name: str
    amc: str
    category: str | None     # "equity:flexi cap"; None when nothing says
    nav: float
    date: dt.date


def _clean(s: str) -> str:
    s = s.lower().replace("’", "'").replace("**", "")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*\bfunds?\b$", "", s).strip()
    return s


def _group(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().replace("’", "'").replace("**", "")).strip()


def category(heading: str, name: str = "") -> str | None:
    """'Equity Schemes - Flexi Cap Fund' -> 'equity:flexi cap'.

    Old single-word labels ('Growth', 'Balanced') predate SEBI's categories.
    For 'Growth' the scheme name is the only clue; anything else old is None.
    """
    if " - " not in heading:
        old = _group(heading)
        if old in OLD_LABELS:
            return OLD_LABELS[old]
        if old == "growth" and name:
            n = name.lower()
            for pat, cat in NAME_HINTS:
                if re.search(pat, n):
                    return cat
        return None
    group, sub = heading.split(" - ", 1)
    g = GROUPS.get(_group(group))
    if g is None:
        return None
    sub = _clean(sub)
    sub = ALIASES.get(sub, sub)
    return f"{g}:{sub}"


def is_direct_growth(name: str, plan: str = "", option: str = "") -> bool:
    text = f"{name} {plan} {option}".lower()
    if "direct" not in text:
        return False
    if re.search(r"idcw|dividend|bonus|payout|reinvest", f"{name} {option}".lower()):
        return False
    return "growth" in text


def _num(s: str) -> float | None:
    try:
        v = float(s.replace(",", ""))
    except ValueError:
        return None
    return v if v > 0 else None


def parse(text: str) -> list[Row]:
    """Either file -> Direct Growth rows. The header line decides the layout."""
    lines = text.splitlines()
    header = [h.strip().lower() for h in lines[0].split(";")] if lines else []
    navall = len(header) > 3 and header[3].startswith("scheme name")
    heading, open_ended, amc, out = "", False, "", []
    for raw in lines[1:]:
        line = raw.strip()
        if not line:
            continue
        m = HEADING_RE.match(line)
        if m:
            # Close-ended and interval schemes cannot be bought or sold at
            # NAV on demand, so they are no one's alternative to a fund.
            open_ended = m.group(1).lower() == "open ended"
            heading = m.group(2).strip()
            continue
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < 8:
            amc = line
            continue
        if navall:
            code, _, _, name, plan, option, nav, date = parts[:8]
        else:
            code, name, plan, option, _, _, nav, date = parts[:8]
        if (not open_ended or not code.isdigit()
                or not is_direct_growth(name, plan, option)):
            continue
        v = _num(nav)
        try:
            d = dt.datetime.strptime(date, "%d-%b-%Y").date()
        except ValueError:
            continue
        if v is None:
            continue
        out.append(Row(int(code), name, amc, category(heading, name), v, d))
    return out


def to_frame(rows: list[Row]) -> pd.DataFrame:
    return pd.DataFrame([r.__dict__ for r in rows],
                        columns=["code", "name", "amc", "category", "nav", "date"])


def _get(url: str) -> str:
    r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.content.decode("utf-8", errors="replace")


def fetch_latest() -> pd.DataFrame:
    return to_frame(parse(_get(NAVALL_URL)))


def fetch_history(start: dt.date, end: dt.date) -> pd.DataFrame:
    return to_frame(parse(_get(HISTORY_URL.format(start=start, end=end))))
