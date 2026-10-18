"""Fund name as typed -> AMFI scheme code (Direct Growth).

"parag parikh flexi", "PPFAS flexi cap", "122639" all land on the same
scheme. A query that clearly matches one live scheme resolves to it; anything
closer than that returns candidates instead, the same rule as the stock lookup:
evaluating the wrong fund with confidence is worse than asking.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

import pandas as pd

# Words every scheme name carries that say nothing about which fund it is.
NOISE = {"fund", "direct", "plan", "growth", "option", "the", "scheme", "dir",
         "gr", "mutual", "mf", "-", "&", "and", "of", "open", "ended", "an"}

# What people call an AMC vs what AMFI prints.
ALIASES = {
    "ppfas": "parag parikh", "abslmf": "aditya birla sun life",
    "absl": "aditya birla sun life", "birla": "aditya birla sun life",
    "icici": "icici prudential", "sbi": "sbi", "kotak": "kotak",
    "mirae": "mirae asset", "uti": "uti", "dsp": "dsp",
    "nippon": "nippon india", "reliance": "nippon india",
    "motilal": "motilal oswal", "mosl": "motilal oswal",
    "hdfc": "hdfc", "axis": "axis", "quant": "quant",
}


# Funds renamed in SEBI's push for names that say the category. AMFI's files
# carry only the current name -- even for 2014 dates -- so the old names people
# still use have to be listed. Applied to the query before matching.
FORMER_NAMES = {
    "axis bluechip": "axis large cap",
    "kotak emerging equity": "kotak mid cap",
    "kotak standard multicap": "kotak flexi cap",
    "kotak bluechip": "kotak large cap",
    "kotak equity opportunities": "kotak large & mid cap",
    "mirae asset emerging bluechip": "mirae asset large & midcap",
    "mirae emerging bluechip": "mirae asset large & midcap",
    "sbi bluechip": "sbi large cap",
    "icici prudential bluechip": "icici prudential large cap",
    "icici bluechip": "icici prudential large cap",
    "hdfc top 100": "hdfc large cap",
    "franklin india prima": "franklin india mid cap",
    "aditya birla sun life frontline equity": "aditya birla sun life large cap",
    "absl frontline equity": "aditya birla sun life large cap",
    "canara robeco bluechip": "canara robeco large cap",
    "canara robeco emerging equities": "canara robeco large and mid cap",
    "uti equity fund": "uti flexi cap",
    "invesco india growth opportunities": "invesco india large & mid cap",
}


def _rename(q: str) -> str:
    low = re.sub(r"\s+", " ", q.lower()).strip()
    for old, new in FORMER_NAMES.items():
        if old in low:
            return low.replace(old, new)
    return q


def _tokens(s: str) -> list[str]:
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\b(small|mid|large|flexi|multi|micro)\s+cap\b", r"\1cap", s)
    words = []
    for w in s.split():
        words.extend(ALIASES.get(w, w).split())
    return [w for w in words if w not in NOISE]


@dataclass
class Match:
    code: int | None
    name: str | None
    candidates: list[tuple[int, str, float]] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.code is not None


def _has(w: str, n: list[str]) -> bool:
    return w in n or (len(w) >= 3 and any(x.startswith(w) for x in n))


def _score(q: list[str], name: str) -> float:
    n = _tokens(name)
    if not q or not n:
        return 0.0
    hits = sum(1 for w in q if _has(w, n))
    cover = hits / len(q)
    extra = len(set(n) - set(q)) / max(len(n), 1)
    ratio = difflib.SequenceMatcher(None, " ".join(q), " ".join(n)).ratio()
    return round(cover * 0.7 + ratio * 0.3 - extra * 0.05, 4)


def find(query: str, schemes: pd.DataFrame, latest_month: str | None = None,
         n: int = 6) -> Match:
    """`schemes` is navdb.schemes(). Closed schemes match only by code."""
    q = query.strip()
    if q.isdigit():
        hit = schemes[schemes["code"] == int(q)]
        if not hit.empty:
            return Match(int(q), hit["name"].iloc[0])
        return Match(None, None)
    live = schemes
    if latest_month is not None:
        live = schemes[schemes["last_month"] >= latest_month]
    qt = _tokens(_rename(q))
    scored = sorted(((int(r.code), r.name, _score(qt, r.name))
                     for r in live.itertuples()), key=lambda x: -x[2])[:n]
    if not scored or scored[0][2] < 0.5:
        return Match(None, None, scored)
    # Several names containing every word typed ("hdfc") is a question, not
    # an answer -- unless one of them is nothing but those words.
    full = [x for x in live.itertuples() if all(_has(w, _tokens(x.name)) for w in qt)]
    if len(full) == 1:
        return Match(int(full[0].code), full[0].name, scored)
    exact = [x for x in full if set(_tokens(x.name)) == set(qt)]
    if len(exact) == 1:
        return Match(int(exact[0].code), exact[0].name, scored)
    if not full and len(scored) > 1 and scored[0][2] - scored[1][2] >= 0.1:
        return Match(scored[0][0], scored[0][1], scored)
    return Match(None, None, scored)
