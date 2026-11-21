"""NSE corporate announcements for one company (Phase 8 monitoring).

Source: /api/corporate-announcements, the same feed as the "Corporate
announcements" tab on a quote page. One row per filing: the category NSE
assigns (`desc`), a one-line summary (`attchmntText`), the time it was
disseminated and a link to the PDF on nsearchives.
"""
from __future__ import annotations

import datetime as dt
import time

from . import nse_client as nse

URL = "https://www.nseindia.com/api/corporate-announcements"


def parse(symbol: str, payload) -> list[dict]:
    out = []
    for r in payload or []:
        if not isinstance(r, dict) or not r.get("seq_id"):
            continue
        at = str(r.get("sort_date") or "").strip()
        if not at:
            try:
                at = dt.datetime.strptime(r["an_dt"], "%d-%b-%Y %H:%M:%S").isoformat(sep=" ")
            except (KeyError, ValueError):
                continue
        link = str(r.get("attchmntFile") or "").strip()
        out.append({"seq_id": str(r["seq_id"]), "symbol": symbol,
                    "at": at, "category": str(r.get("desc") or "").strip(),
                    "text": str(r.get("attchmntText") or "").strip(),
                    "link": "" if link in ("", "-", "None") else link})
    return out


def fetch(symbol: str, start: dt.date, end: dt.date) -> list[dict]:
    """Every announcement for `symbol` between two dates, newest first."""
    nse.warm()
    r = nse.get(URL, params={"index": "equities", "symbol": symbol,
                             "from_date": start.strftime("%d-%m-%Y"),
                             "to_date": end.strftime("%d-%m-%Y")}, timeout=60)
    time.sleep(0.3)
    return parse(symbol, r.json())
