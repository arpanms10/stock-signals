"""Watchlist: the one file you maintain. Plain CSV so it opens in Excel.

Source of truth is config/watchlist.csv. Every interface (Excel, the Streamlit
grid, Telegram commands) reads and writes through this module so validation and
formatting stay identical whichever way a stock gets added.
"""
from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass, asdict
from pathlib import Path

WATCHLIST_PATH = Path(__file__).parent / "config" / "watchlist.csv"
COLUMNS = ["symbol", "name", "sector", "active", "added_on", "notes"]


@dataclass
class Entry:
    symbol: str
    name: str = ""
    sector: str = ""
    active: str = "yes"
    added_on: str = ""
    notes: str = ""

    @property
    def is_active(self) -> bool:
        return str(self.active).strip().lower() in ("yes", "y", "true", "1")


def load(path: Path | str = WATCHLIST_PATH) -> list[Entry]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        sym = (r.get("symbol") or "").strip().upper()
        if not sym:
            continue
        out.append(Entry(
            symbol=sym,
            name=(r.get("name") or "").strip(),
            sector=(r.get("sector") or "").strip(),
            active=(r.get("active") or "yes").strip(),
            added_on=(r.get("added_on") or "").strip(),
            notes=(r.get("notes") or "").strip(),
        ))
    return out


def save(entries: list[Entry], path: Path | str = WATCHLIST_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = sorted(entries, key=lambda e: e.symbol)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for e in entries:
            w.writerow(asdict(e))


def active_symbols(path: Path | str = WATCHLIST_PATH) -> list[str]:
    """Symbols the engine should signal on today."""
    return [e.symbol for e in load(path) if e.is_active]


def all_symbols(path: Path | str = WATCHLIST_PATH) -> list[str]:
    """Every symbol ever tracked, retired ones included.

    The backtest uses this rather than active_symbols: dropping retired names
    would quietly erase past losers and flatter every result (survivorship bias).
    """
    return [e.symbol for e in load(path)]


def add(symbol: str, name: str = "", sector: str = "", notes: str = "",
        path: Path | str = WATCHLIST_PATH) -> tuple[bool, str]:
    """Add a symbol. Returns (changed, message). Reactivates if already retired."""
    symbol = symbol.strip().upper()
    if not symbol:
        return False, "Empty symbol."
    entries = load(path)
    for e in entries:
        if e.symbol == symbol:
            if e.is_active:
                return False, f"{symbol} is already on the watchlist."
            e.active = "yes"
            save(entries, path)
            return True, f"{symbol} reactivated."
    entries.append(Entry(
        symbol=symbol, name=name, sector=sector, notes=notes,
        active="yes", added_on=dt.date.today().isoformat(),
    ))
    save(entries, path)
    return True, f"{symbol} added."


def set_active(symbol: str, active: bool, path: Path | str = WATCHLIST_PATH) -> tuple[bool, str]:
    """Retire or resume a symbol. Never deletes the row -- history is kept
    so the backtest still sees it."""
    symbol = symbol.strip().upper()
    entries = load(path)
    for e in entries:
        if e.symbol == symbol:
            e.active = "yes" if active else "no"
            save(entries, path)
            return True, f"{symbol} {'resumed' if active else 'retired'}."
    return False, f"{symbol} is not on the watchlist."
