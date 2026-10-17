"""The 200 largest NSE companies on each past date, from the whole market.

The results and picker backtests used to run on TODAY's Nifty 200. That list
holds the companies that grew into it -- GARP's very picks -- and none that
fell out, merged away or delisted. Here the universe is rebuilt on every
date from data that existed on that date:

  1. pool     the most-traded equities by average daily turnover over the
              prior year, from the full-market bhavcopy in data/market.db.
              Wider than 200 so that a large company that trades little
              still gets a market cap.
  2. members  the pool ranked by market cap -- the share count filed by that
              date times that day's raw close -- and cut at 200. A pool name
              with no usable filing has no market cap and fills in by
              turnover only if fewer than 200 have one (it has no filings,
              so neither backtest can use it anyway; it is listed as a gap).

Free-float weighting, which the real index uses, is not reproduced: NSE's
filings carry no shareholding pattern. A company with a large promoter stake
ranks higher here than in the index.

Renamed companies are one name under today's ticker (the ISIN link in
data/bhavcopy.symbol_aliases, and a new ISIN from the same issuer after a
face-value change -- see aliases()), which is also the ticker NSE lists
their filings under.

  data/results/pit_universe.csv   date, symbol, rank, mcap_cr, turnover_rank,
                                  basis (mcap | mcap_early |
                                  turnover), kind (rebalance |
                                  quarter), in_index_now
  data/results/pit_gaps.csv       pool names with no NSE filings to read
"""
from __future__ import annotations

import bisect
import datetime as dt
from pathlib import Path

import pandas as pd

from . import store

MEMBERS = store.DIR / "pit_universe.csv"
GAPS = store.DIR / "pit_gaps.csv"
MARKET_DB = Path(__file__).resolve().parents[1] / "data" / "market.db"
POOL = 250
LOOKBACK_DAYS = 365
MIN_DAYS = 120
SIZE = 200


def quarter_ends(start: dt.date, end: dt.date) -> list[dt.date]:
    out = []
    for y in range(start.year, end.year + 1):
        for m, d in ((3, 31), (6, 30), (9, 30), (12, 31)):
            day = dt.date(y, m, d)
            if start <= day <= end:
                out.append(day)
    return out


def dates(cfg: dict, start: dt.date, end: dt.date) -> list[tuple[dt.date, str]]:
    """Picker rebalance dates and results quarter ends, tagged by kind."""
    from picker import engine as pe
    out = [(d, "rebalance") for d in pe.rebalance_dates(cfg, start, end)]
    out += [(d, "quarter") for d in quarter_ends(start, end)]
    return sorted(out)


def aliases(con) -> dict[str, str]:
    """Old ticker -> today's: bhavcopy.symbol_aliases (same ISIN), plus a new
    ISIN from the same issuer with no overlap in trading. A face-value change
    issues a new ISIN that differs only in the serial digits (ANGELBRKG
    INE732I01013 -> ANGELONE INE732I01021, AMIORG -> ACUTAAS), and NSE then
    lists every filing, old ones included, under the new ticker."""
    from data import bhavcopy as bc
    out = bc.symbol_aliases(con)
    spans = con.execute(
        "SELECT i.isin, s.symbol, s.first, s.last FROM instruments i JOIN ("
        "  SELECT symbol, MIN(date) first, MAX(date) last FROM market "
        "  GROUP BY symbol) s ON s.symbol = i.symbol "
        "WHERE i.isin LIKE 'INE%' ORDER BY s.first").fetchall()
    by_issuer: dict[str, list[tuple]] = {}
    for isin, sym, first, last in spans:
        # INE + issuer (4) + security type (2, "01" = equity) + serial + check.
        if isin[7:9] == "01":
            by_issuer.setdefault(isin[:9], []).append((sym, first, last))
    for links in by_issuer.values():
        links = [x for i, x in enumerate(links) if x[0] not in {y[0] for y in links[:i]}]
        # The new ticker takes over within a month; a long gap is a delisting
        # and a later relisting (HEXAWARE 2020, HEXT 2025), not one series.
        if len(links) < 2 or any(
                b[1] <= a[2] or (dt.date.fromisoformat(b[1])
                                 - dt.date.fromisoformat(a[2])).days > 31
                for a, b in zip(links, links[1:])):
            continue
        final = out.get(links[-1][0], links[-1][0])
        for sym, _, _ in links[:-1]:
            out.setdefault(sym, final)
    return out


def pools(con, on: list[dt.date], size: int = POOL) -> dict[dt.date, list[str]]:
    """Turnover-ranked pool per date, today's tickers, most traded first."""
    from backtest import pit_engine as pit
    alias = aliases(con)
    return {d: pit.liquid_universe(con, d, size, lookback_days=LOOKBACK_DAYS,
                                   min_days=MIN_DAYS, aliases=alias)
            for d in on}


def _raw_closes(con, symbols: set[str]) -> dict[str, tuple[list, list]]:
    """Raw (unadjusted) closes per current ticker, renames joined."""
    alias = aliases(con)
    olds = {o for o, n in alias.items() if n in symbols}
    want = sorted(symbols | olds)
    out: dict[str, dict] = {}
    for i in range(0, len(want), 400):
        chunk = want[i:i + 400]
        marks = ",".join("?" * len(chunk))
        rows = con.execute(
            f"SELECT symbol, date, close FROM market WHERE symbol IN ({marks}) "
            f"UNION ALL SELECT symbol, date, close FROM market_t2t "
            f"WHERE symbol IN ({marks})", chunk + chunk).fetchall()
        for sym, d, c in rows:
            out.setdefault(alias.get(sym, sym), {}).setdefault(d, c)
    res = {}
    for sym, by_date in out.items():
        ds = sorted(by_date)
        res[sym] = ([dt.date.fromisoformat(x) for x in ds], [by_date[x] for x in ds])
    return res


def _shares_by_symbol(quarters: pd.DataFrame) -> dict[str, list[tuple]]:
    """(broadcast date, shares in crores) per symbol, oldest first."""
    if quarters.empty or "shares_cr" not in quarters:
        return {}
    q = quarters.dropna(subset=["shares_cr", "broadcast"])
    q = q[q["shares_cr"] > 0]
    out: dict[str, list[tuple]] = {}
    for sym, g in q.groupby("symbol"):
        g = g.sort_values("broadcast")
        out[sym] = [(pd.Timestamp(b).date(), float(s))
                    for b, s in zip(g["broadcast"], g["shares_cr"])]
    return out


def mcap_on(shares: list[tuple] | None, closes: tuple[list, list] | None,
            d: dt.date, actions: list[dt.date] | None = None
            ) -> tuple[float | None, str]:
    """(market cap in crores on `d`, basis): the latest share count NSE had
    published by then, times the raw close on or before `d` (stale beyond a
    week is no price).

    Before the first filing on file (24 quarters reach back to late 2020),
    the first filed count stands in -- basis "mcap_early" -- unless a split
    or bonus (`actions`, ex-dates) falls between `d` and that filing, when
    the count would not match the raw price. (None, "") when not known."""
    if not shares or not closes:
        return None, ""
    filed = [s for b, s in shares if b <= d]
    basis = "mcap"
    if not filed:
        first = shares[0][0]
        if any(d < x <= first for x in actions or []):
            return None, ""
        filed, basis = [shares[0][1]], "mcap_early"
    ds, cs = closes
    i = bisect.bisect_right(ds, d) - 1
    if i < 0 or (d - ds[i]).days > 7 or not cs[i]:
        return None, ""
    return filed[-1] * float(cs[i]), basis


def _action_dates(con) -> dict[str, list[dt.date]]:
    """Split / bonus ex-dates per current ticker."""
    alias = aliases(con)
    out: dict[str, list[dt.date]] = {}
    for sym, ex in con.execute("SELECT symbol, ex_date FROM market_actions"):
        out.setdefault(alias.get(sym, sym), []).append(dt.date.fromisoformat(ex))
    return out


def members(pool_by_date: dict[dt.date, list[str]], quarters: pd.DataFrame,
            con, size: int = SIZE, kinds: dict | None = None,
            index_now: set[str] | None = None) -> pd.DataFrame:
    """The top `size` of each date's pool by market cap (see module doc)."""
    shares = _shares_by_symbol(quarters)
    closes = _raw_closes(con, {s for p in pool_by_date.values() for s in p})
    actions = _action_dates(con)
    rows = []
    for d, pool in sorted(pool_by_date.items()):
        caps = [(s, *mcap_on(shares.get(s), closes.get(s), d, actions.get(s)), i + 1)
                for i, s in enumerate(pool)]
        chosen = sorted([c for c in caps if c[1]], key=lambda c: -c[1])[:size]
        if len(chosen) < size:
            chosen += [(s, None, "turnover", t) for s, m, _, t in caps
                       if not m][:size - len(chosen)]
        for r, (s, m, basis, t) in enumerate(chosen, 1):
            rows.append({"date": d, "symbol": s, "rank": r,
                         "mcap_cr": round(m) if m else None, "turnover_rank": t,
                         "basis": basis, "kind": (kinds or {}).get(d, ""),
                         "in_index_now": s in (index_now or set())})
    return pd.DataFrame(rows)


def load(path: Path = MEMBERS) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def on(df: pd.DataFrame, d: dt.date) -> set[str]:
    """Members on the latest universe date on or before `d`."""
    if df.empty:
        return set()
    known = sorted(df["date"].unique())
    i = bisect.bisect_right(known, d) - 1
    if i < 0:
        return set()
    return set(df.loc[df["date"] == known[i], "symbol"])


def ever(df: pd.DataFrame) -> set[str]:
    return set(df["symbol"]) if not df.empty else set()


class MarketPrices:
    """Daily closes from the full-market bhavcopy, for past members that the
    price store (data/signals.db, today's Nifty 500 and holdings) never
    tracked: the delisted, the merged, the fallen.

    Close only -- the bhavcopy store keeps no open, high or low. `high` and
    `low` are set to the close, so an ATR computed on it is the close-to-close
    range, and `open` is left blank. Renames are joined under today's ticker;
    `adjusted` divides prices before each split or bonus by its factor.
    """

    def __init__(self, con=None):
        from data import bhavcopy as bc
        self.con = con if con is not None else bc.connect(MARKET_DB)
        self.aliases = aliases(self.con)
        self._actions = None

    def load(self, symbol: str, adjusted: bool = True) -> pd.DataFrame:
        names = [symbol] + [o for o, n in self.aliases.items() if n == symbol]
        marks = ",".join("?" * len(names))
        df = pd.read_sql_query(
            f"SELECT date, close, 0 AS t2t FROM market WHERE symbol IN ({marks}) "
            f"UNION ALL SELECT date, close, 1 FROM market_t2t "
            f"WHERE symbol IN ({marks})", self.con, params=names + names)
        if df.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close"])
        df = (df.sort_values("t2t").drop_duplicates("date").sort_values("date")
              .reset_index(drop=True))
        df["date"] = pd.to_datetime(df["date"]).dt.date
        if adjusted:
            if self._actions is None:
                from data import bhavcopy as bc
                acts = bc.load_actions(self.con)
                if not acts.empty:
                    acts["symbol"] = acts["symbol"].map(lambda x: self.aliases.get(x, x))
                self._actions = acts
            acts = self._actions
            if not acts.empty:
                for a in acts[acts["symbol"] == symbol].itertuples():
                    before = df["date"] < dt.date.fromisoformat(a.ex_date)
                    df.loc[before, "close"] = df.loc[before, "close"] / a.factor
        df["open"] = float("nan")
        df["high"] = df["low"] = df["close"]
        return df[["date", "open", "high", "low", "close"]]


def price_loader(price_con, market: MarketPrices | None = None):
    """load(symbol, adjusted) -> prices: the price store first (OHLC), the
    full-market closes when the store has nothing for the symbol."""
    from data import store as price_store
    market = market or MarketPrices()

    def load(symbol: str, adjusted: bool = True) -> pd.DataFrame:
        df = price_store.load_prices(price_con, symbol, adjusted=adjusted)
        if df.empty:
            df = market.load(symbol, adjusted)
            df.attrs["close_only"] = True
        return df
    return load
