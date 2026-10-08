"""Quarterly-results dates from NSE's board-meeting announcements.

A results announcement inside an expiry cycle is the one scheduled event that
moves a single stock's volatility: implied volatility is bid up into it and
collapses after ("IV crush"). The F&O view flags it, and the validation
checks whether ranges behave differently across it.

Source: /api/corporate-board-meetings, one row per announced meeting, with
the purpose in free text. A meeting counts as a results date when its
purpose or description mentions financial results. That catches "Financial
Results", "Financial Results/Dividend", "to consider and approve the
Unaudited Financial results ..." -- and errs towards including a meeting
that also covers results among other business, which is what matters here.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
from pathlib import Path

import pandas as pd

from . import nse_client as nse

URL = "https://www.nseindia.com/api/corporate-board-meetings"
CACHE_DIR = Path(__file__).resolve().parents[1] / "cache"
RESULTS = re.compile(r"financial\s+results?", re.I)


def parse(payload: list) -> pd.DataFrame:
    """Board-meeting rows -> symbol, date, purpose; results meetings only."""
    rows = []
    for r in payload or []:
        text = f"{r.get('bm_purpose', '')} {r.get('bm_desc', '')}"
        if not RESULTS.search(text):
            continue
        try:
            d = dt.datetime.strptime(r["bm_date"], "%d-%b-%Y").date()
        except (KeyError, ValueError):
            continue
        rows.append({"symbol": str(r.get("bm_symbol", "")).strip(), "date": d,
                     "purpose": str(r.get("bm_purpose", "")).strip()})
    df = pd.DataFrame(rows, columns=["symbol", "date", "purpose"])
    return df.drop_duplicates(["symbol", "date"]).reset_index(drop=True)


def fetch(start: dt.date, end: dt.date) -> pd.DataFrame:
    """Results meetings between two dates, a month per request (the endpoint
    returns ~1 MB for a busy month; whole quarters time out)."""
    nse.warm()
    nse.session().get("https://www.nseindia.com/option-chain", timeout=15)
    out, d = [], start.replace(day=1)
    while d <= end:
        nxt = (d + dt.timedelta(days=32)).replace(day=1)
        lo, hi = max(d, start), min(nxt - dt.timedelta(days=1), end)
        r = nse.get(URL, params={"index": "equities",
                                 "from_date": lo.strftime("%d-%m-%Y"),
                                 "to_date": hi.strftime("%d-%m-%Y")}, timeout=60)
        out.append(parse(r.json()))
        time.sleep(0.4)
        d = nxt
    df = pd.concat(out, ignore_index=True) if out else parse([])
    return df.drop_duplicates(["symbol", "date"]).reset_index(drop=True)


def upcoming(symbol: str, today: dt.date, until: dt.date,
             max_age_hours: float = 12) -> list[dt.date]:
    """Announced results dates for one stock between today and `until`.

    Cached for half a day: the whole window is one or two requests shared by
    every symbol looked up that day."""
    path = CACHE_DIR / f"results_{today:%Y%m%d}_{until:%Y%m%d}.json"
    if path.exists() and (time.time() - path.stat().st_mtime) / 3600 < max_age_hours:
        rows = json.loads(path.read_text())
    else:
        df = fetch(today, until)
        rows = [{"symbol": s, "date": d.isoformat()} for s, d in zip(df["symbol"], df["date"])]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows))
    return sorted(dt.date.fromisoformat(r["date"]) for r in rows
                  if r["symbol"] == symbol.upper())
