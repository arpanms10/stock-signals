"""Turn whatever the user typed -- ticker, company name or ISIN -- into an NSE symbol.

The directory is built from three free sources, each covering a gap in the
others:

  NSE equity master   every listed symbol with its company name and ISIN
  NIFTY 500 list      the sector, which decides whether a stock is scored
                      as a lender
  market.db           symbol -> ISIN for everything the bhavcopy has seen,
                      so lookup still works offline

A query that matches one stock exactly resolves to it. Anything fuzzier
returns candidates instead of a guess: analysing the wrong company with
confidence is worse than asking which one was meant.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MARKET_DB = ROOT / "data" / "market.db"

ISIN_RE = re.compile(r"^IN[A-Z0-9]{9}[0-9]$")

# Words that every Indian company name carries and that say nothing about which
# company it is. "Tata Motors Ltd." and "TATA MOTORS LIMITED" are the same.
_NOISE = {"limited", "ltd", "the", "co", "company", "corp", "corporation",
          "inc", "india", "of", "and"}

COLUMNS = ["symbol", "name", "isin", "sector"]
# `listed`: in NSE's current equity master. A symbol known only from old
# bhavcopies has usually been renamed (ZOMATO -> ETERNAL) or delisted.


@dataclass
class Listing:
    symbol: str
    name: str = ""
    isin: str = ""
    sector: str = ""

    def label(self) -> str:
        bits = [self.symbol]
        if self.name:
            bits.append(self.name)
        if self.isin:
            bits.append(self.isin)
        return " · ".join(bits)


@dataclass
class Resolution:
    """Either one match, or candidates to choose from, or nothing."""
    query: str
    match: Listing | None = None
    candidates: list[Listing] = field(default_factory=list)
    how: str = ""                 # "isin", "symbol", "name" -- how it matched

    @property
    def ok(self) -> bool:
        return self.match is not None


def normalise(name: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", str(name).lower()).split()
    return " ".join(w for w in words if w not in _NOISE)


def clean_query(q: str) -> str:
    """Strip exchange decorations people paste: NSE:INFY, INFY.NS, INFY-EQ."""
    q = str(q).strip()
    q = re.sub(r"^(NSE|BSE)\s*:\s*", "", q, flags=re.I)
    q = re.sub(r"\.(NS|BO)$", "", q, flags=re.I)
    q = re.sub(r"-EQ$", "", q, flags=re.I)
    return q.strip()


def build_directory(master: pd.DataFrame | None = None,
                    index_list: pd.DataFrame | None = None,
                    isin_map: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per symbol: symbol, name, isin, sector. Any source may be None."""
    parts = []
    listed: set[str] = set()
    if master is not None and not master.empty:
        m = master.rename(columns={"ISIN NUMBER": "isin"})
        parts.append(m.reindex(columns=COLUMNS))
        listed = set(m["symbol"].astype(str).str.strip().str.upper())
    if index_list is not None and not index_list.empty:
        parts.append(index_list.reindex(columns=COLUMNS))
    if isin_map is not None and not isin_map.empty:
        parts.append(isin_map.reindex(columns=COLUMNS))
    if not parts:
        return pd.DataFrame(columns=COLUMNS + ["listed"])
    df = pd.concat(parts, ignore_index=True)
    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    for c in ("name", "isin", "sector"):
        df[c] = df[c].fillna("").astype(str).str.strip()
    df["isin"] = df["isin"].str.upper()
    # First non-empty value per column wins, so the master's name survives a
    # blank from the ISIN map and the index list's sector fills the master.
    out = (df.replace("", pd.NA).groupby("symbol", sort=True)
           .first().fillna("").reset_index())
    # With no master at all (offline), nothing can be called unlisted.
    out["listed"] = out["symbol"].isin(listed) if listed else True
    return out[COLUMNS + ["listed"]]


def load_directory() -> pd.DataFrame:
    """The live directory. Each source is optional; offline still works."""
    from data.sources import universe as uni

    master = index_list = isin_map = None
    try:
        master = uni.equity_master()
    except Exception:
        pass
    try:
        index_list = uni.nifty500()
    except Exception:
        pass
    if MARKET_DB.exists():
        try:
            import sqlite3
            con = sqlite3.connect(MARKET_DB)
            isin_map = pd.read_sql_query(
                "SELECT symbol, isin FROM instruments WHERE isin LIKE 'INE%'", con)
            con.close()
        except Exception:
            pass
    return build_directory(master, index_list, isin_map)


def _listing(row) -> Listing:
    return Listing(row["symbol"], row["name"], row["isin"], row["sector"])


def _current(directory: pd.DataFrame, row) -> tuple[pd.Series, bool]:
    """The listed symbol sharing this row's ISIN, if the row itself is not
    listed. A rename keeps the ISIN, so ZOMATO leads to ETERNAL."""
    if row["listed"] or not row["isin"]:
        return row, False
    same = directory[(directory["isin"] == row["isin"]) & directory["listed"]]
    return (same.iloc[0], True) if len(same) == 1 else (row, False)


def resolve(query: str, directory: pd.DataFrame, limit: int = 8) -> Resolution:
    q = clean_query(query)
    res = Resolution(query=query)
    if not q or directory.empty:
        return res
    up = q.upper()

    if ISIN_RE.match(up):
        hit = directory[directory["isin"] == up]
        if len(hit) > 1 and hit["listed"].sum() == 1:
            hit = hit[hit["listed"]]
        if len(hit) == 1:
            res.match, res.how = _listing(hit.iloc[0]), "isin"
        else:
            res.candidates = [_listing(r) for _, r in hit.iterrows()]
        return res

    hit = directory[directory["symbol"] == up]
    if len(hit):
        row, renamed = _current(directory, hit.iloc[0])
        res.match = _listing(row)
        res.how = f"symbol (renamed from {up})" if renamed else "symbol"
        return res

    norm = directory["name"].map(normalise)
    nq = normalise(q)
    if nq:
        exact = directory[norm == nq]
        if len(exact) == 1:
            res.match, res.how = _listing(exact.iloc[0]), "name"
            return res

        # Every word of the query appears in the name, in any order: "tata
        # motors" finds "Tata Motors Ltd." and also "Tata Motors DVR", so with
        # more than one hit the user chooses.
        words = nq.split()
        contains = directory[norm.map(lambda n: all(w in n.split() or w in n
                                                    for w in words))]
        if len(contains) == 1:
            res.match, res.how = _listing(contains.iloc[0]), "name"
            return res
        if len(contains) > 1:
            # Shorter names first: the plain company before its variants.
            order = contains.assign(_n=norm[contains.index].str.len()).sort_values("_n")
            res.candidates = [_listing(r) for _, r in order.head(limit).iterrows()]
            return res

    # Typos: nearest symbols and names. Never auto-picked.
    near_sym = difflib.get_close_matches(up, directory["symbol"].tolist(),
                                         n=limit, cutoff=0.75)
    names = dict(zip(norm, directory["symbol"]))
    near_name = [names[n] for n in difflib.get_close_matches(nq, list(names),
                                                             n=limit, cutoff=0.7)]
    seen, picks = set(), []
    for s in near_sym + near_name:
        if s not in seen:
            seen.add(s)
            picks.append(s)
    by_sym = directory.set_index("symbol", drop=False)
    res.candidates = [_listing(by_sym.loc[s]) for s in picks[:limit]]
    return res
