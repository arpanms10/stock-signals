"""Instrument classification and the exclusion list."""
import datetime as dt

import pytest

import instruments as ins
from data import bhavcopy as bc


def test_isin_prefixes_classify_correctly():
    assert ins.classify("INE002A01018") == "equity"      # Reliance
    assert ins.classify("INF732E01011") == "fund"        # NIFTYBEES
    assert ins.classify("INF0R8F01034") == "fund"        # LIQUIDCASE
    assert ins.classify("IN9155A01020") == "dvr"         # Tata Motors DVR
    assert ins.classify("") == "other"
    assert ins.classify(None) == "other"


def test_classification_is_case_and_space_insensitive():
    assert ins.classify(" ine002a01018 ") == "equity"


def test_kind_labels_are_browsable():
    assert ins._guess_kind("GOLDBEES") == "gold"
    assert ins._guess_kind("SILVERBEES") == "silver"
    assert ins._guess_kind("LIQUIDCASE") == "liquid/cash"
    assert ins._guess_kind("NIFTYBEES") == "index"
    assert ins._guess_kind("BANKBEES") == "sector"


@pytest.fixture
def market(tmp_path):
    con = bc.connect(tmp_path / "m.db")
    import pandas as pd
    for i in range(40):
        d = dt.date(2026, 1, 1) + dt.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        bc.save_day(con, d, pd.DataFrame(
            [("RELIANCE", 1300.0, 1e6, 1.3e9),
             ("NIFTYBEES", 280.0, 5e6, 1.4e9),
             ("TATAMTRDVR", 400.0, 1e5, 4e7)],
            columns=["symbol", "close", "volume", "turnover"]))
    con.executemany("INSERT OR REPLACE INTO instruments VALUES (?,?)",
                    [("RELIANCE", "INE002A01018"),
                     ("NIFTYBEES", "INF732E01011"),
                     ("TATAMTRDVR", "IN9155A01020")])
    con.commit()
    return con


def test_snapshot_splits_equities_from_everything_else(market, tmp_path):
    out = ins.snapshot(market, tmp_path / "etfs.csv", tmp_path / "eq.csv")
    assert out["equities"] == 1
    assert out["funds"] == 2          # the ETF and the DVR
    excluded = ins.load_excluded(tmp_path / "etfs.csv")
    assert excluded == {"NIFTYBEES", "TATAMTRDVR"}


def test_exclusion_file_records_the_reason(market, tmp_path):
    """A list of excluded symbols with no reason is impossible to audit later."""
    ins.snapshot(market, tmp_path / "etfs.csv", tmp_path / "eq.csv")
    text = (tmp_path / "etfs.csv").read_text()
    assert "near-zero volatility" in text
    assert "second share class" in text


def test_load_excluded_ignores_comment_header(tmp_path):
    p = tmp_path / "e.csv"
    p.write_text("# a comment\n# another\nsymbol,isin\nGOLDBEES,INF123\n")
    assert ins.load_excluded(p) == {"GOLDBEES"}


def test_load_excluded_returns_empty_when_absent(tmp_path):
    assert ins.load_excluded(tmp_path / "nope.csv") == set()


def test_etfs_never_reach_the_tradable_universe(market):
    """The end-to-end guarantee: liquid ETFs outrank stocks on turnover, so
    only the ISIN filter keeps them out."""
    u = bc.universe_on(market, dt.date(2026, 2, 5), top_n=5, min_days=5)
    assert u == ["RELIANCE"]
