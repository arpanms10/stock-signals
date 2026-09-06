"""Instrument classification: what is tradable, and what is deliberately not.

The universe is filtered by ISIN prefix -- INE is company equity, INF is a
mutual fund or ETF. That filter was not cosmetic. ETFs are highly liquid, so
they pass every turnover screen, and a liquid-fund ETF has near-zero
volatility, which sends risk-adjusted momentum (return / volatility) toward
infinity. A cash equivalent ranked first in the momentum backtest before this
existed.

Excluded instruments are written to config/etfs.csv rather than silently
dropped. They are a legitimate thing to look at later -- index ETFs are a
sensible core holding, and gold ETFs are a real diversifier -- they just must
not compete with stocks in a momentum ranking.
"""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

CONFIG_DIR = Path(__file__).parent / "config"
ETF_PATH = CONFIG_DIR / "etfs.csv"
EQUITY_PATH = CONFIG_DIR / "equities.csv"

EQUITY_PREFIX = "INE"          # company equity
FUND_PREFIXES = ("INF",)       # mutual funds and ETFs
DVR_PREFIX = "IN9"             # differential voting rights share classes

ETF_COLUMNS = ["symbol", "isin", "kind", "first_seen", "last_seen",
               "days_traded", "avg_turnover_cr", "still_trading",
               "excluded_because"]


def classify(isin: str) -> str:
    """equity | fund | dvr | other.

    DVR (differential voting rights) shares are genuine equity, but they are a
    second share class of a company that is already in the universe -- holding
    both is one bet counted twice. All four in ten years of NSE data are
    illiquid besides, so they are excluded and labelled rather than quietly
    lumped in with the ETFs.
    """
    isin = (isin or "").strip().upper()
    if isin.startswith(EQUITY_PREFIX):
        return "equity"
    if any(isin.startswith(p) for p in FUND_PREFIXES):
        return "fund"
    if isin.startswith(DVR_PREFIX):
        return "dvr"
    return "other"


def _guess_kind(symbol: str) -> str:
    """A rough label so the ETF list is browsable at a glance."""
    s = symbol.upper()
    if "GOLD" in s or "GLD" in s:
        return "gold"
    if "SILVER" in s or "SLV" in s:
        return "silver"
    if "LIQUID" in s or "CASH" in s or "OVERNIGHT" in s or "1D" in s:
        return "liquid/cash"
    if "GILT" in s or "GSEC" in s or "SDL" in s or "BOND" in s or "SEC" in s:
        return "debt"
    # Sector before index: BANKBEES tracks Bank Nifty, which is both, but
    # "sector" is the more specific and more useful label.
    if any(k in s for k in ("BANK", "PSU", "PHARMA", "AUTO", "FMCG", "INFRA",
                            "CONSUM", "METAL", "ENERGY", "REALTY", "MEDIA")):
        return "sector"
    if any(k in s for k in ("NIFTY", "SENSEX", "BEES", "N50", "NN50", "500",
                            "MIDCAP", "SMALLCAP", "MOM", "ALPHA", "VALUE")):
        return "index"
    return "fund"


def snapshot(con, path: Path | str = ETF_PATH,
             equity_path: Path | str = EQUITY_PATH) -> dict:
    """Write the excluded-instrument list and the tradable-equity list.

    Both come from the same market data the backtest uses, so the files are a
    faithful record of what the engine did and did not consider.
    """
    rows = con.execute("""
        SELECT i.symbol, i.isin,
               MIN(m.date), MAX(m.date), COUNT(*), AVG(m.turnover)
        FROM instruments i
        JOIN market m ON m.symbol = i.symbol
        GROUP BY i.symbol, i.isin
    """).fetchall()

    latest = con.execute("SELECT MAX(date) FROM market").fetchone()[0]
    cutoff = (dt.date.fromisoformat(latest) - dt.timedelta(days=30)).isoformat() \
        if latest else ""

    funds, equities = [], []
    for sym, isin, first, last, n, turn in rows:
        kind = classify(isin)
        rec = {
            "symbol": sym, "isin": isin,
            "kind": _guess_kind(sym) if kind == "fund" else kind,
            "excluded_because": ("fund/ETF: liquid but near-zero volatility, "
                                 "would top a risk-adjusted ranking"
                                 if kind == "fund" else
                                 "DVR: second share class of a company already "
                                 "in the universe" if kind == "dvr"
                                 else "unrecognised ISIN prefix"),
            "first_seen": first, "last_seen": last, "days_traded": n,
            "avg_turnover_cr": round((turn or 0) / 1e7, 2),
            "still_trading": "yes" if last and last >= cutoff else "no",
        }
        (funds if kind != "equity" else equities).append(rec)

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    funds.sort(key=lambda r: -r["avg_turnover_cr"])
    with Path(path).open("w", newline="", encoding="utf-8") as fh:
        fh.write("# Instruments EXCLUDED from the tradable universe.\n"
                 "# ISIN prefix INF = mutual fund / ETF (INE = company equity).\n"
                 "#\n"
                 "# Excluded because they are highly liquid but near-zero\n"
                 "# volatility, so risk-adjusted momentum ranks a cash-equivalent\n"
                 "# ETF above every real stock. Kept here because they are worth\n"
                 "# looking at on their own terms -- an index ETF is a reasonable\n"
                 "# core holding, a gold ETF a real diversifier -- just not as\n"
                 "# competitors to stocks in a ranking.\n"
                 "#\n"
                 "# DVR share classes are also here: real equity, but a second\n"
                 "# class of a company already in the universe, so holding both\n"
                 "# is one bet counted twice.\n"
                 "#\n"
                 "# avg_turnover_cr is the average daily traded value in crores.\n")
        w = csv.DictWriter(fh, fieldnames=ETF_COLUMNS)
        w.writeheader()
        w.writerows(funds)

    equities.sort(key=lambda r: -r["avg_turnover_cr"])
    with Path(equity_path).open("w", newline="", encoding="utf-8") as fh:
        fh.write("# Company equity (ISIN prefix INE) seen in the market data.\n"
                 "# This is the pool the momentum ranking selects from, before\n"
                 "# the liquidity cut. Includes delisted names -- that is what\n"
                 "# makes the backtest survivorship-free.\n")
        w = csv.DictWriter(fh, fieldnames=ETF_COLUMNS)
        w.writeheader()
        w.writerows(equities)

    return {"funds": len(funds), "equities": len(equities),
            "funds_active": sum(1 for f in funds if f["still_trading"] == "yes")}


def load_excluded(path: Path | str = ETF_PATH) -> set[str]:
    """Symbols in the exclusion list, for anything that wants a belt-and-braces
    check rather than relying on the ISIN lookup alone."""
    path = Path(path)
    if not path.exists():
        return set()
    with path.open(encoding="utf-8") as fh:
        rows = csv.DictReader(r for r in fh if not r.startswith("#"))
        return {r["symbol"].strip().upper() for r in rows if r.get("symbol")}
