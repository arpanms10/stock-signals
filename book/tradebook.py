"""Broker tradebooks: read every file in config/tradebooks/, merge, dedupe.

Zerodha Console > Reports > Tradebook (Equity), one file per date range,
.xlsx or .csv. The header row is found wherever it sits; overlapping files
are merged by trade ID, so downloading a range twice does no harm.

The folder holds your trades: it is listed in .gitignore and also carries its
own .gitignore, so nothing in it is ever committed.
"""
from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

import pandas as pd

from strategy.lots import Trade

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "config" / "tradebooks"
COLS = {"symbol": ("symbol", "tradingsymbol", "scrip"),
        "isin": ("isin",),
        "date": ("trade date", "trade_date", "date"),
        "side": ("trade type", "trade_type", "type", "buy/sell"),
        "qty": ("quantity", "qty"),
        "price": ("price", "trade price", "rate"),
        "trade_id": ("trade id", "trade_id"),
        "segment": ("segment",)}


def ensure_dir(d: Path = DIR) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    gi = d / ".gitignore"
    if not gi.exists():
        gi.write_text("# Your trades. Never committed.\n*\n")
    return d


def _frame(path: Path) -> pd.DataFrame:
    raw = (pd.read_excel(path, header=None) if path.suffix.lower() in (".xlsx", ".xls")
           else pd.read_csv(path, header=None, dtype=str))
    for i in range(min(len(raw), 40)):
        cells = {str(x).strip().lower() for x in raw.iloc[i] if pd.notna(x)}
        if "symbol" in cells and any(c in cells for c in COLS["date"]):
            df = raw.iloc[i + 1:].copy()
            df.columns = [str(x).strip().lower() for x in raw.iloc[i]]
            return df.dropna(how="all")
    raise ValueError(f"{path.name}: no header row with Symbol and Trade Date")


def _col(df: pd.DataFrame, key: str) -> str | None:
    return next((c for c in COLS[key] if c in df.columns), None)


def read(path: Path) -> list[Trade]:
    df = _frame(path)
    need = {k: _col(df, k) for k in COLS}
    missing = [k for k in ("symbol", "date", "side", "qty", "price") if not need[k]]
    if missing:
        raise ValueError(f"{path.name}: no column for {', '.join(missing)}")
    out = []
    for _, r in df.iterrows():
        if need["segment"] and str(r[need["segment"]]).strip().upper() not in ("EQ", "NAN", ""):
            continue
        side = str(r[need["side"]]).strip().lower()
        if side not in ("buy", "sell", "b", "s"):
            continue
        try:
            d = pd.to_datetime(r[need["date"]]).date()
            q, p = float(r[need["qty"]]), float(r[need["price"]])
        except (TypeError, ValueError):
            continue
        out.append(Trade(str(r[need["symbol"]]).strip().upper(), d,
                         "buy" if side.startswith("b") else "sell", q, p,
                         str(r[need["trade_id"]]).strip() if need["trade_id"] else "",
                         str(r[need["isin"]]).strip() if need["isin"] else ""))
    return out


def load(d: Path = DIR) -> tuple[list[Trade], list[dict]]:
    """All trades in the folder, deduplicated; and one summary per file."""
    if not d.exists():
        return [], []
    seen, trades, files = set(), [], []
    for f in sorted(d.iterdir()):
        if f.name.startswith(".") or f.suffix.lower() not in (".xlsx", ".xls", ".csv"):
            continue
        try:
            ts = read(f)
        except Exception as e:                       # noqa: BLE001 -- shown on the page
            files.append({"file": f.name, "error": str(e)})
            continue
        new = 0
        for t in ts:
            key = (t.trade_id, t.symbol, t.date, t.side, t.qty, t.price) if t.trade_id \
                else (t.symbol, t.date, t.side, t.qty, t.price, new)
            if key in seen:
                continue
            seen.add(key)
            trades.append(t)
            new += 1
        files.append({"file": f.name, "trades": len(ts), "new": new,
                      "from": min((t.date for t in ts), default=None),
                      "to": max((t.date for t in ts), default=None)})
    return sorted(trades, key=lambda t: (t.date, t.trade_id)), files


def import_files(paths: list[Path | str], d: Path = DIR) -> list[str]:
    """Copy tradebook files into the folder (after checking each parses)."""
    ensure_dir(d)
    done = []
    for p in map(Path, paths):
        read(p)
        shutil.copy2(p, d / p.name)
        done.append(p.name)
    return done


def save_upload(name: str, data: bytes, d: Path = DIR) -> str:
    ensure_dir(d)
    target = d / Path(name).name
    target.write_bytes(data)
    try:
        read(target)
    except Exception:
        target.unlink()
        raise
    return target.name


def first_date(trades: list[Trade]) -> dt.date | None:
    return min((t.date for t in trades), default=None)
