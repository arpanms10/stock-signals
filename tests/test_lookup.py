"""Stock lookup by ticker, company name or ISIN."""
import pandas as pd

from analysis import lookup as lk


def directory():
    master = pd.DataFrame({
        "symbol": ["TATAMOTORS", "TATAMTRDVR", "INFY", "SBIN", "HDFCBANK"],
        "name": ["Tata Motors Limited", "Tata Motors Limited DVR",
                 "Infosys Limited", "State Bank of India", "HDFC Bank Limited"],
        "ISIN NUMBER": ["INE155A01022", "IN9155A01020", "INE009A01021",
                        "INE062A01020", "INE040A01034"],
    })
    nifty = pd.DataFrame({
        "symbol": ["INFY", "SBIN"], "name": ["Infosys Ltd.", "State Bank of India"],
        "sector": ["Information Technology", "Financial Services"],
        "isin": ["INE009A01021", "INE062A01020"],
    })
    # Seen in the bhavcopy but missing from the master: delisted, or renamed
    # (ZOMATO is ETERNAL now, same ISIN).
    master.loc[len(master)] = ["ETERNAL", "Eternal Limited", "INE758T01015"]
    isin_map = pd.DataFrame({"symbol": ["OLDCO", "ZOMATO", "ETERNAL"],
                             "isin": ["INE999Z01011", "INE758T01015",
                                      "INE758T01015"]})
    return lk.build_directory(master, nifty, isin_map)


def test_directory_merges_sources():
    d = directory().set_index("symbol")
    assert d.loc["INFY", "name"] == "Infosys Limited"        # master wins
    assert d.loc["INFY", "sector"] == "Information Technology"  # index fills
    assert d.loc["OLDCO", "isin"] == "INE999Z01011"
    assert d.loc["OLDCO", "name"] == ""


def test_exact_symbol_any_case_and_decorations():
    d = directory()
    for q in ("infy", "INFY", "NSE:INFY", "INFY.NS", " infy-eq "):
        r = lk.resolve(q, d)
        assert r.ok and r.match.symbol == "INFY" and r.how == "symbol", q


def test_isin():
    r = lk.resolve("ine062a01020", directory())
    assert r.ok and r.match.symbol == "SBIN" and r.how == "isin"


def test_unknown_isin_is_not_guessed():
    r = lk.resolve("INE000A01000", directory())
    assert not r.ok and r.candidates == []


def test_exact_name_ignores_limited_and_punctuation():
    r = lk.resolve("Infosys Ltd.", directory())
    assert r.ok and r.match.symbol == "INFY" and r.how == "name"


def test_ambiguous_name_returns_candidates_plain_first():
    r = lk.resolve("tata motors limited dvr", directory())
    assert r.ok and r.match.symbol == "TATAMTRDVR"
    r = lk.resolve("tata", directory())
    assert not r.ok
    assert [c.symbol for c in r.candidates] == ["TATAMOTORS", "TATAMTRDVR"]


def test_unique_partial_name_resolves():
    r = lk.resolve("state bank", directory())
    assert r.ok and r.match.symbol == "SBIN"


def test_typo_offers_candidates_never_a_match():
    r = lk.resolve("INFYY", directory())
    assert not r.ok
    assert "INFY" in [c.symbol for c in r.candidates]


def test_empty_query():
    r = lk.resolve("   ", directory())
    assert not r.ok and r.candidates == []


def test_renamed_symbol_leads_to_current_one():
    d = directory()
    r = lk.resolve("zomato", d)
    assert r.ok and r.match.symbol == "ETERNAL" and "renamed" in r.how
    r = lk.resolve("INE758T01015", d)
    assert r.ok and r.match.symbol == "ETERNAL"


def test_delisted_symbol_still_resolves_to_itself():
    r = lk.resolve("OLDCO", directory())
    assert r.ok and r.match.symbol == "OLDCO" and r.how == "symbol"
