"""What kind of security a holding is, and which sleeve of the book it sits in.

A broker file mixes company shares with REITs, InvITs, ETFs, bonds, gold
bonds and rights entitlements. Each needs different treatment: a REIT is
judged on occupancy and distributions, not ROCE; a bond has no Quality score;
a rights entitlement expires. So nothing is assumed to be an equity share.

Two independent clues, cross-checked:

  ISIN      characters 1-3 say the issuer kind (INE company, INF fund) and
            characters 8-9 the security type: 01 equity shares, 23 InvIT
            units, 25 REIT units; others (07, 08 ...) are debt.
  suffix    how brokers write a non-EQ series: EMBASSY-RR (REIT),
            PGINVIT-IV (InvIT), SGBJUN31-GB (gold bond), XYZ-N1 (bond),
            XYZ-RE (rights entitlement), XYZ-E1 (partly paid).

When the two disagree, or a suffix is one we do not know, the holding is
flagged for you to verify rather than silently given a type.

Pure: no I/O.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

EQUITY, REIT, INVIT = "equity", "reit", "invit"
ETF, BOND, GOLD_BOND = "etf", "bond", "gold_bond"
RIGHTS, PARTLY_PAID, UNKNOWN = "rights", "partly_paid", "unknown"

KIND_LABEL = {
    EQUITY: "Equity share", REIT: "REIT", INVIT: "InvIT", ETF: "ETF / fund",
    BOND: "Bond / debenture", GOLD_BOND: "Sovereign gold bond",
    RIGHTS: "Rights entitlement", PARTLY_PAID: "Partly paid share",
    UNKNOWN: "Unidentified",
}

# Sleeves of the book. Only "operating" takes part in the 70:30 Core-Satellite
# split; REITs and InvITs are a separate sleeve (listed real assets) that still
# counts towards the size of the book.
OPERATING, REAL_ASSETS, FUNDS, DEBT, OTHER = (
    "operating", "real_assets", "funds", "debt", "other")
SLEEVE_OF = {EQUITY: OPERATING, PARTLY_PAID: OPERATING, REIT: REAL_ASSETS,
             INVIT: REAL_ASSETS, ETF: FUNDS, BOND: DEBT, GOLD_BOND: DEBT,
             RIGHTS: OTHER, UNKNOWN: OTHER}
SLEEVE_LABEL = {OPERATING: "Operating equity",
                REAL_ASSETS: "Alternative income / listed real assets",
                FUNDS: "ETFs and funds", DEBT: "Bonds and gold bonds",
                OTHER: "Other / to verify"}

# Series that are still the ordinary equity share, traded under another
# settlement type (trade-for-trade, SME platform ...).
EQUITY_SERIES = {"EQ", "BE", "BZ", "BL", "BT", "SM", "ST"}
SUFFIX_KIND = {"RR": REIT, "IV": INVIT, "GB": GOLD_BOND, "RE": RIGHTS,
               "E1": PARTLY_PAID, "E2": PARTLY_PAID, "F": ETF}
# NSE debt series: N0-N9, Y0-Y9, Z0-Z9, NA-NZ ...
BOND_SUFFIX = re.compile(r"^(N[0-9A-Z]|Y[0-9A-Z]|Z[0-9A-Z]|D[0-9])$")
# A 1-2 character limit keeps real symbols with a hyphen (BAJAJ-AUTO) out.
SUFFIX = re.compile(r"^(.+)-([A-Z0-9]{1,2})$")

ISIN_TYPE_KIND = {"01": EQUITY, "23": INVIT, "25": REIT}


@dataclass(frozen=True)
class Security:
    symbol: str            # as held, e.g. EMBASSY-RR
    base: str              # the exchange symbol, e.g. EMBASSY
    series: str            # EQ, RR, IV, GB ...
    kind: str
    isin: str = ""
    review: str = ""       # why you should verify the type; "" when clear

    @property
    def sleeve(self) -> str:
        return SLEEVE_OF[self.kind]

    @property
    def label(self) -> str:
        return KIND_LABEL[self.kind]

    @property
    def needs_review(self) -> bool:
        return bool(self.review)


def kind_from_isin(isin: str) -> str | None:
    isin = (isin or "").strip().upper()
    if len(isin) != 12:
        return None
    if isin.startswith("INF"):
        return ETF
    if isin.startswith("IN0"):
        return None               # government security: SGB or G-sec, the suffix decides
    if isin.startswith(("INE", "IN9")):
        return ISIN_TYPE_KIND.get(isin[7:9], BOND)
    return None


def split_symbol(symbol: str) -> tuple[str, str | None]:
    """'EMBASSY-RR' -> ('EMBASSY', 'RR'); 'BAJAJ-AUTO' -> ('BAJAJ-AUTO', None)."""
    sym = (symbol or "").strip().upper()
    m = SUFFIX.match(sym)
    return (m.group(1), m.group(2)) if m else (sym, None)


def classify(symbol: str, isin: str = "", sector: str = "") -> Security:
    sym = (symbol or "").strip().upper()
    base, suffix = split_symbol(sym)
    isin = (isin or "").strip().upper()
    by_isin = kind_from_isin(isin)

    if suffix in EQUITY_SERIES:
        by_suffix = EQUITY
    elif suffix in SUFFIX_KIND:
        by_suffix = SUFFIX_KIND[suffix]
    elif suffix and BOND_SUFFIX.match(suffix):
        by_suffix = BOND
    elif suffix:
        by_suffix = UNKNOWN
    elif sym.startswith("SGB"):
        by_suffix = GOLD_BOND
    else:
        by_suffix = None          # a plain symbol: equity, or an ETF

    if sector.strip().lower() == "debt" and by_suffix is None and by_isin is None:
        by_suffix = BOND

    review = ""
    if by_suffix == UNKNOWN:
        kind = by_isin or UNKNOWN
        review = f"unrecognised series suffix -{suffix}"
    elif by_isin and by_suffix and by_isin != by_suffix:
        kind = by_isin            # the ISIN is the issuer's own registration
        review = (f"symbol suffix says {KIND_LABEL[by_suffix]}, "
                  f"ISIN {isin} says {KIND_LABEL[by_isin]}")
    else:
        kind = by_isin or by_suffix or EQUITY
    if isin.startswith("IN0") and kind == EQUITY:
        kind = BOND
        review = f"government ISIN {isin} on a plain symbol"
    if kind in (RIGHTS, PARTLY_PAID) and not review:
        review = f"{KIND_LABEL[kind].lower()}: check its terms and expiry"

    series = (suffix if suffix else
              {REIT: "RR", INVIT: "IV", GOLD_BOND: "GB"}.get(kind, "EQ"))
    if kind == EQUITY and suffix not in EQUITY_SERIES:
        series = "EQ"
    # The exchange symbol of a plain equity share is the symbol itself; of
    # an equity traded in another series, the bare symbol.
    if kind == EQUITY and suffix in EQUITY_SERIES:
        sym = base
    return Security(sym, base, series, kind, isin, review)
