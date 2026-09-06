"""Shared NSE HTTP session.

NSE rejects requests that do not look like a browser, and its JSON endpoints
additionally require cookies that are only set once you have hit the homepage.
Everything that talks to nseindia.com goes through here so that handshake and
the retry policy exist in exactly one place.
"""
from __future__ import annotations

import io
import time
from pathlib import Path

import pandas as pd
import requests

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept": "application/json, text/csv, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

NIFTY50_LIST_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv"
NIFTY200_LIST_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv"
NIFTY500_LIST_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

_session: requests.Session | None = None
_warmed = False


def session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update(BROWSER_HEADERS)
        _session = s
    return _session


def warm() -> None:
    """Pick up the cookies NSE's JSON endpoints require. Safe to call repeatedly."""
    global _warmed
    if _warmed:
        return
    s = session()
    for url in ("https://www.nseindia.com",
                "https://www.nseindia.com/companies-listing/corporate-filings-actions"):
        try:
            s.get(url, timeout=15)
        except requests.RequestException:
            pass  # the real request may still work; let it be the one that fails
    _warmed = True


def get(url: str, *, params: dict | None = None, tries: int = 3,
        timeout: int = 25) -> requests.Response:
    """GET with a warmed session and linear backoff."""
    warm()
    last: Exception | None = None
    for attempt in range(tries):
        try:
            r = session().get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r
            last = RuntimeError(f"HTTP {r.status_code} for {url}")
        except requests.RequestException as exc:
            last = exc
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"NSE request failed after {tries} tries: {last}")


def fetch_csv(url: str, cache_path: Path | str | None = None,
              max_age_days: int = 7) -> pd.DataFrame:
    """Fetch a CSV, optionally caching it on disk.

    Index membership and the instrument master change rarely, so re-downloading
    them on every run is wasted traffic against an endpoint that rate-limits.
    """
    if cache_path is not None:
        cache_path = Path(cache_path)
        if cache_path.exists():
            age_days = (time.time() - cache_path.stat().st_mtime) / 86400
            if age_days < max_age_days:
                return pd.read_csv(cache_path)
    df = pd.read_csv(io.StringIO(get(url).text))
    df.columns = [c.strip() for c in df.columns]
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(cache_path, index=False)
    return df
