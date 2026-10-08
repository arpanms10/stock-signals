"""NSE F&O parsers, run on real responses saved 2026-10-08 (trimmed)."""
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pytest

from data.sources import nse_derivatives as d

FIX = Path(__file__).parent / "fixtures" / "fno"


def load(name):
    return json.loads((FIX / name).read_text())


def test_chain_parses_one_row_per_strike():
    c = d.parse_chain(load("nifty_chain.json"), "NIFTY")
    assert c.expiry == dt.date(2026, 10, 27)
    assert c.spot == pytest.approx(22474.65)
    assert c.timestamp == dt.datetime(2026, 10, 8, 9, 53, 55)
    s = c.strikes
    assert s["strike"].is_monotonic_increasing and s["strike"].is_unique
    row = s.set_index("strike").loc[22550]
    assert row["ce_oi"] == 1958 and row["pe_oi"] == 3579
    assert row["ce_iv"] == pytest.approx(11.96)


def test_zero_premium_and_iv_become_nan():
    p = load("nifty_chain.json")
    raw = {(r["strikePrice"]): r["CE"] for r in p["records"]["data"] if "CE" in r}
    s = d.parse_chain(p, "NIFTY").strikes.set_index("strike")
    zero_iv = [k for k, v in raw.items() if v["impliedVolatility"] == 0]
    zero_ltp = [k for k, v in raw.items() if v["lastPrice"] == 0]
    assert zero_iv and zero_ltp
    assert s.loc[zero_iv, "ce_iv"].isna().all()
    assert s.loc[zero_ltp, "ce_ltp"].isna().all()
    assert (s["ce_oi"] >= 0).all()   # OI stays numeric


def test_missing_leg_is_zero_oi():
    p = load("reliance_chain.json")
    p["records"]["data"][0].pop("PE")
    s = d.parse_chain(p, "RELIANCE").strikes
    assert s.iloc[0]["pe_oi"] == 0 and np.isnan(s.iloc[0]["pe_ltp"])


def test_empty_chain_is_an_error_not_no_oi():
    # The retired option-chain-equities endpoint answers 200 with `{}`.
    with pytest.raises(RuntimeError):
        d.parse_chain({}, "RELIANCE")


def test_futures_only_futures_rows():
    f = d.parse_futures(load("reliance_quote.json"))
    assert len(f) == 3
    assert list(f["expiry"]) == sorted(f["expiry"])
    near = f.iloc[0]
    assert near["expiry"] == dt.date(2026, 10, 27)
    assert near["ltp"] == 1198 and near["prev_close"] == 1210.2


def test_monthly_is_last_expiry_of_each_month():
    exp = d.parse_contract_info(load("nifty_contract_info.json"))
    assert dt.date(2026, 10, 13) in exp            # a weekly
    m = d.monthly_expiries(exp)
    assert m[:3] == [dt.date(2026, 10, 27), dt.date(2026, 11, 23),
                     dt.date(2026, 12, 29)]
    assert d.nearest_monthly(exp, dt.date(2026, 10, 8)) == dt.date(2026, 10, 27)
    # expiry day itself still trades
    assert d.nearest_monthly(exp, dt.date(2026, 10, 27)) == dt.date(2026, 10, 27)
    assert d.nearest_monthly(exp, dt.date(2026, 10, 28)) == dt.date(2026, 11, 23)


def test_underlyings_split_index_and_equity():
    u = d.parse_underlyings(load("underlyings.json"))
    assert u.set_index("symbol").loc["NIFTY", "kind"] == "index"
    assert u.set_index("symbol").loc["ABB", "kind"] == "equity"


def test_lot_sizes_skip_section_headers_and_blank_months():
    lots = d.parse_lot_sizes((FIX / "fo_mktlots.csv").read_text())
    assert "Symbol" not in set(lots["symbol"])
    get = lambda s, m: lots[(lots.symbol == s) & (lots.month == m)]["lot"].tolist()
    assert get("NIFTY", dt.date(2026, 10, 1)) == [65]
    assert get("RELIANCE", dt.date(2026, 11, 1)) == [500]
    assert get("FINNIFTY", dt.date(2027, 3, 1)) == []   # blank: no contract


def test_holidays_and_results_parsing():
    from data.sources import nse_events as ev
    h = d.parse_holidays({"FO": [{"tradingDate": "20-Oct-2026"}, {"tradingDate": "02-Oct-2026"}],
                          "CM": [{"tradingDate": "01-Jan-2026"}]})
    assert h == [dt.date(2026, 10, 2), dt.date(2026, 10, 20)]
    rows = [
        {"bm_symbol": "INFY", "bm_date": "23-Oct-2026", "bm_purpose": "Board Meeting Intimation",
         "bm_desc": "to consider and approve the Unaudited Financial results for the quarter"},
        {"bm_symbol": "BAJFINANCE", "bm_date": "01-Oct-2026", "bm_purpose": "Fund Raising",
         "bm_desc": "To consider Fund Raising"},
        {"bm_symbol": "TCS", "bm_date": "08-Oct-2026", "bm_purpose": "Financial Results/Dividend",
         "bm_desc": ""},
    ]
    df = ev.parse(rows)
    assert list(df["symbol"]) == ["INFY", "TCS"]
