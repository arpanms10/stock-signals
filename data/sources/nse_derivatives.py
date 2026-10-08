"""F&O data straight from NSE: option chains, futures, expiries, lot sizes.

Endpoints, as of October 2026:

  option-chain-contract-info   expiry dates and strikes for one underlying
  option-chain-v3              the chain for one underlying and ONE expiry
  GetQuoteApi (NextApi)        every futures and options contract of one
                               underlying, with previous close and OI change
  underlying-information       the F&O universe: indices and stocks
  fo_mktlots.csv               lot sizes by contract month

NSE retired `/api/option-chain-indices` (404) and `/api/option-chain-equities`
(200 with an empty `{}` body -- no error) in 2025. The second is the dangerous
one: a parser reading it sees a valid response with no strikes. Everything here
uses the v3 endpoint and treats an empty chain as an error, not as "no OI".

Units: open interest and change in OI are in CONTRACTS on every endpoint here
(cross-checked identifier by identifier between the chain and the quote API).
Multiply by the lot size for shares.

Zeros: NSE reports `lastPrice` and `impliedVolatility` as 0 for a strike that
has not traded. Those become NaN -- a zero IV averaged into an ATM IV, or a
zero premium in a straddle, is a silent wrong number. OI and volume of 0 are
real zeros and stay.
"""
from __future__ import annotations

import datetime as dt
import io
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import nse_client as nse

CONTRACT_INFO_URL = "https://www.nseindia.com/api/option-chain-contract-info"
CHAIN_URL = "https://www.nseindia.com/api/option-chain-v3"
QUOTE_URL = "https://www.nseindia.com/api/NextApi/apiClient/GetQuoteApi"
UNDERLYINGS_URL = "https://www.nseindia.com/api/underlying-information"
LOTS_URL = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"

CACHE_DIR = Path(__file__).resolve().parents[1] / "cache"
NSE_TS_FORMAT = "%d-%b-%Y %H:%M:%S"
EXPIRY_FORMAT = "%d-%b-%Y"

# Fields taken from each side of a chain row, and their column suffix.
_SIDE_FIELDS = {
    "openInterest": "oi",
    "changeinOpenInterest": "chg_oi",
    "totalTradedVolume": "vol",
    "impliedVolatility": "iv",
    "lastPrice": "ltp",
    "buyPrice1": "bid",
    "sellPrice1": "ask",
}
# Zero means "no trade / no quote" for these, not a real value.
_ZERO_IS_MISSING = {"iv", "ltp", "bid", "ask"}


class NotFnO(ValueError):
    """The symbol has no F&O contracts on NSE."""


@dataclass
class Chain:
    """One expiry's option chain, one row per strike."""
    symbol: str
    expiry: dt.date
    spot: float
    timestamp: dt.datetime          # NSE's own, IST, naive
    strikes: pd.DataFrame           # strike, ce_*, pe_*
    expiries: list[dt.date] = field(default_factory=list)

    @property
    def is_index(self) -> bool:
        return self.strikes.attrs.get("kind") == "index"


# ----------------------------------------------------------------- parsing
# Pure functions: payload in, frame out. Tests run these on saved responses.

def parse_expiry(text: str) -> dt.date:
    return dt.datetime.strptime(text, EXPIRY_FORMAT).date()


def parse_contract_info(payload: dict) -> list[dt.date]:
    return sorted(parse_expiry(e) for e in payload.get("expiryDates", []))


def parse_chain(payload: dict, symbol: str) -> Chain:
    """option-chain-v3 response -> Chain. Raises on an empty chain."""
    rec = payload.get("records") or {}
    rows = rec.get("data") or []
    if not rows:
        raise RuntimeError(f"NSE returned an empty option chain for {symbol}")

    out = []
    for r in rows:
        row = {"strike": float(r["strikePrice"])}
        for side in ("CE", "PE"):
            leg = r.get(side) or {}
            for src, name in _SIDE_FIELDS.items():
                v = leg.get(src)
                v = np.nan if v is None else float(v)
                if name in _ZERO_IS_MISSING and v == 0:
                    v = np.nan
                if name in ("oi", "chg_oi", "vol") and np.isnan(v):
                    v = 0.0   # no leg at this strike: nothing open
                row[f"{side.lower()}_{name}"] = v
        out.append(row)

    df = (pd.DataFrame(out).sort_values("strike")
          .drop_duplicates("strike").reset_index(drop=True))
    expiry_text = rows[0].get("expiryDates") or rows[0].get("expiryDate")
    return Chain(
        symbol=symbol,
        expiry=parse_expiry(expiry_text),
        spot=float(rec["underlyingValue"]),
        timestamp=dt.datetime.strptime(rec["timestamp"], NSE_TS_FORMAT),
        strikes=df,
        expiries=sorted(parse_expiry(e) for e in rec.get("expiryDates", [])),
    )


def parse_futures(payload: dict) -> pd.DataFrame:
    """GetQuoteApi derivatives response -> one row per futures contract."""
    rows = [r for r in payload.get("data", [])
            if str(r.get("instrumentType", "")).startswith("FUT")]
    cols = ["expiry", "ltp", "prev_close", "oi", "chg_oi", "volume", "spot"]
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame({
        "expiry": [parse_expiry(r["expiryDate"]) for r in rows],
        "ltp": [float(r["lastPrice"]) for r in rows],
        "prev_close": [float(r.get("prevClose") or np.nan) for r in rows],
        "oi": [float(r.get("openInterest") or 0) for r in rows],
        "chg_oi": [float(r.get("changeinOpenInterest") or 0) for r in rows],
        "volume": [float(r.get("totalTradedVolume") or 0) for r in rows],
        "spot": [float(r.get("underlyingValue") or np.nan) for r in rows],
    })
    return df.sort_values("expiry").reset_index(drop=True)


def parse_underlyings(payload: dict) -> pd.DataFrame:
    data = payload.get("data") or {}
    idx = [(d["symbol"], d["underlying"], "index") for d in data.get("IndexList", [])]
    stk = [(d["symbol"], d["underlying"], "equity") for d in data.get("UnderlyingList", [])]
    return pd.DataFrame(idx + stk, columns=["symbol", "name", "kind"])


def parse_lot_sizes(text: str) -> pd.DataFrame:
    """fo_mktlots.csv -> symbol, month (date, 1st of month), lot.

    The file is fixed-width padded with spaces, has a few section-header rows
    with an empty SYMBOL, and leaves months blank where no contract exists.
    """
    raw = pd.read_csv(io.StringIO(text), dtype=str)
    raw.columns = [c.strip() for c in raw.columns]
    raw = raw.apply(lambda s: s.str.strip())
    raw = raw[raw["SYMBOL"].fillna("").ne("") & raw["SYMBOL"].ne("Symbol")]
    months = [c for c in raw.columns if c not in ("UNDERLYING", "SYMBOL")]
    long = raw.melt(id_vars=["SYMBOL"], value_vars=months,
                    var_name="month", value_name="lot")
    long = long[pd.to_numeric(long["lot"], errors="coerce").notna()]
    long["lot"] = long["lot"].astype(int)
    long["month"] = pd.to_datetime(long["month"], format="%b-%y").dt.date
    return (long.rename(columns={"SYMBOL": "symbol"})
            [["symbol", "month", "lot"]].reset_index(drop=True))


def monthly_expiries(expiries: list[dt.date]) -> list[dt.date]:
    """The last expiry in each calendar month.

    Weekly NIFTY expiries sit alongside the monthly one; the monthly is always
    the last of its month. Stocks only have monthlies, so this returns them
    unchanged.
    """
    last: dict[tuple[int, int], dt.date] = {}
    for e in sorted(expiries):
        last[(e.year, e.month)] = e
    return sorted(last.values())


def nearest_monthly(expiries: list[dt.date], today: dt.date) -> dt.date:
    """First monthly expiry on or after today (expiry day itself still trades)."""
    for e in monthly_expiries(expiries):
        if e >= today:
            return e
    raise ValueError("no monthly expiry on or after " + today.isoformat())


# ---------------------------------------------------------------- fetching

def _warm_option_chain() -> None:
    # The JSON endpoints need cookies set by the option-chain page itself.
    nse.warm()
    try:
        nse.session().get("https://www.nseindia.com/option-chain", timeout=15)
    except Exception:
        pass


def _json(url: str, params: dict | None = None) -> dict:
    _warm_option_chain()
    return nse.get(url, params=params).json()


def _cached_text(url: str, name: str, max_age_days: float) -> str:
    path = CACHE_DIR / name
    if path.exists() and (time.time() - path.stat().st_mtime) / 86400 < max_age_days:
        return path.read_text()
    _warm_option_chain()
    text = nse.get(url).text
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return text


def fno_underlyings(max_age_days: float = 7) -> pd.DataFrame:
    import json
    text = _cached_text(UNDERLYINGS_URL, "fno_underlyings.json", max_age_days)
    return parse_underlyings(json.loads(text))


def lot_sizes(max_age_days: float = 7) -> pd.DataFrame:
    return parse_lot_sizes(_cached_text(LOTS_URL, "fo_mktlots.csv", max_age_days))


def lot_size(symbol: str, expiry: dt.date) -> int | None:
    lots = lot_sizes()
    hit = lots[(lots["symbol"] == symbol)
               & (lots["month"] == expiry.replace(day=1))]
    return int(hit["lot"].iloc[0]) if len(hit) else None


def kind_of(symbol: str) -> str:
    """'index' or 'equity'; raises NotFnO for anything outside the F&O list."""
    u = fno_underlyings()
    hit = u[u["symbol"] == symbol.upper()]
    if hit.empty:
        raise NotFnO(f"{symbol} has no F&O contracts on NSE "
                     f"({len(u)} underlyings are F&O-eligible)")
    return str(hit["kind"].iloc[0])


def expiries(symbol: str) -> list[dt.date]:
    return parse_contract_info(_json(CONTRACT_INFO_URL, {"symbol": symbol.upper()}))


def option_chain(symbol: str, expiry: dt.date | None = None,
                 today: dt.date | None = None) -> Chain:
    """The chain for one expiry; defaults to the nearest monthly."""
    symbol = symbol.upper()
    kind = kind_of(symbol)
    all_exp = expiries(symbol)
    if expiry is None:
        expiry = nearest_monthly(all_exp, today or dt.date.today())
    elif expiry not in all_exp:
        raise ValueError(f"{symbol} has no expiry on {expiry}; available: "
                         + ", ".join(e.strftime("%d-%b-%Y") for e in all_exp))
    payload = _json(CHAIN_URL, {
        "type": "Indices" if kind == "index" else "Equity",
        "symbol": symbol,
        "expiry": expiry.strftime(EXPIRY_FORMAT),
    })
    chain = parse_chain(payload, symbol)
    chain.strikes.attrs["kind"] = kind
    chain.expiries = all_exp
    return chain


def futures(symbol: str) -> pd.DataFrame:
    return parse_futures(_json(QUOTE_URL, {
        "functionName": "getSymbolDerivativesData", "symbol": symbol.upper()}))
