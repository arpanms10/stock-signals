"""OHLCV for NSE equities and indices.

Primary source is jugaad-data, which reads NSE's own historical endpoints: free,
no API key, no auth, and therefore usable by an unattended nightly job.

One trap handled here: NSE returns each trading day as IST midnight expressed in
UTC, i.e. 18:30 on the *previous* calendar day. Taking .date() naively shifts
every bar back one day and silently misaligns prices against index data and
corporate-action ex-dates. We add the 5:30 offset before reducing to a date.
"""
from __future__ import annotations

import datetime as dt
import socket
import warnings

import pandas as pd

# jugaad-data issues its requests without a timeout. A single socket that
# stalls therefore hangs the whole nightly job indefinitely, with no output and
# no alert -- the exact failure mode where silence gets mistaken for "no
# signals today". urllib3 falls back to the default socket timeout when none is
# given, so setting it here bounds every underlying fetch.
socket.setdefaulttimeout(30)

warnings.filterwarnings("ignore", message="no explicit representation of timezones")

IST_OFFSET = pd.Timedelta(hours=5, minutes=30)

EMPTY_STOCK = pd.DataFrame(columns=["date", "open", "high", "low", "close",
                                    "volume", "symbol"])
EMPTY_INDEX = pd.DataFrame(columns=["date", "open", "high", "low", "close",
                                    "symbol"])

STOCK_COLUMNS = {
    "OPEN": "open", "HIGH": "high", "LOW": "low", "CLOSE": "close",
    "PREV. CLOSE": "prev_close", "VWAP": "vwap", "VOLUME": "volume",
    "NO OF TRADES": "trades", "DELIVERY QTY": "delivery_qty",
    "DELIVERY %": "delivery_pct", "SYMBOL": "symbol", "SERIES": "series",
}


def _to_trade_date(series: pd.Series) -> pd.Series:
    """NSE timestamps -> the calendar day the trade actually happened (IST)."""
    return (pd.to_datetime(series) + IST_OFFSET).dt.date


def stock_history(symbol: str, from_date: dt.date, to_date: dt.date) -> pd.DataFrame:
    """Raw (unadjusted) daily bars for one symbol, oldest first."""
    from jugaad_data.nse import stock_df

    try:
        raw = stock_df(symbol=symbol.upper(), from_date=from_date,
                       to_date=to_date, series="EQ")
    except (KeyError, ValueError):
        # NSE returns an empty payload for ranges with no trading data -- a
        # weekend, a holiday, or today before the close is published. The
        # library raises on the empty frame; for us it just means "no bars".
        return EMPTY_STOCK.copy()
    if raw is None or len(raw) == 0:
        return EMPTY_STOCK.copy()
    df = raw.rename(columns=STOCK_COLUMNS)
    df["date"] = _to_trade_date(df["DATE"])

    # NSE returns every series listed under the symbol, not just the equity
    # one: NTPC comes back with its bonds (quoted near 1360 on ~100 shares of
    # volume) interleaved with the actual stock near 100. Deduplicating by date
    # without this filter keeps whichever row happens to come first, which
    # silently builds a price series that alternates between two instruments.
    if "series" in df.columns:
        df = df[df["series"].astype(str).str.strip().str.upper() == "EQ"]
    keep = ["date"] + [c for c in STOCK_COLUMNS.values()
                       if c in df.columns and c != "series"]
    df = df[keep].drop_duplicates(subset="date").sort_values("date")
    df["symbol"] = symbol.upper()
    return df.reset_index(drop=True)


def index_history(index: str, from_date: dt.date, to_date: dt.date) -> pd.DataFrame:
    """Daily bars for an index, e.g. 'NIFTY 500' for the regime filter."""
    from jugaad_data.nse import index_df

    try:
        raw = index_df(symbol=index, from_date=from_date, to_date=to_date)
    except (KeyError, ValueError):
        return EMPTY_INDEX.copy()
    if raw is None or len(raw) == 0:
        return EMPTY_INDEX.copy()
    df = raw.rename(columns={"OPEN": "open", "HIGH": "high",
                             "LOW": "low", "CLOSE": "close"})
    # Index history returns a plain date string, not an IST-shifted timestamp.
    df["date"] = pd.to_datetime(df["HistoricalDate"]).dt.date
    df = df[["date", "open", "high", "low", "close"]]
    df = df.drop_duplicates(subset="date").sort_values("date")
    df["symbol"] = index
    return df.reset_index(drop=True)
