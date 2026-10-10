"""PPFAS disclosure parsing and month-over-month change logic.

The failures that matter here are quiet ones: a section silently dropped, a
split read as a huge buy, or inflow-driven buying read as conviction.
"""
import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from mf import changes as ch
from mf import ppfas

RAW = Path(__file__).resolve().parent.parent / "data" / "mf" / "ppfas_raw"
SEP_2026 = RAW / "PPFCF_PPFAS_Monthly_Portfolio_Report_September_30_2026.xlsx"
FEB_2026 = RAW / "PPFCF_PPFAS_Monthly_Portfolio_Report_February_28_2026.xls"


@pytest.mark.skipif(not SEP_2026.exists(), reason="raw disclosure not present")
def test_parse_reconciles_to_grand_total_and_reads_the_notes():
    d = ppfas.parse(SEP_2026)
    assert d.month_end == dt.date(2026, 9, 30)
    assert d.warnings == []
    assert d.holdings["weight"].sum() + d.net_receivables / d.net_assets == pytest.approx(1, abs=1e-6)
    assert (d.nav_direct_prev, d.nav_direct) == (90.7827, 88.8355)
    assert d.turnover_excl_arb == 17.09
    b = d.holdings.groupby("bucket")["name"].apply(set)
    assert "Alphabet Inc A" in b[ppfas.FOREIGN]
    assert "Reliance Industries Limited" in b[ppfas.ARBITRAGE]
    assert "HDFC Bank Limited" in b[ppfas.CORE]
    assert (d.derivatives["side"] == "short").all()


@pytest.mark.skipif(not FEB_2026.exists(), reason="raw disclosure not present")
def test_foreign_block_found_when_lettered_inside_domestic_section():
    # Feb 2026 has "(d) Equity & Equity related Foreign Investments" instead of
    # its own top-level block, plus a "Special Situation" sub-section.
    d = ppfas.parse(FEB_2026)
    b = d.holdings.groupby("bucket")["weight"].sum()
    assert b[ppfas.FOREIGN] == pytest.approx(0.1051, abs=1e-4)
    assert ppfas.UNLISTED not in b
    assert ppfas.SPECIAL in b


def test_stock_key_survives_face_value_change():
    assert ch.stock_key("INE237A01028") == ch.stock_key("INE237A01036")
    assert ch.stock_key("US02079K3059") == "US02079K3059"


M1, M2 = dt.date(2026, 1, 31), dt.date(2026, 2, 28)


def _h(rows):
    h = pd.DataFrame(rows, columns=["month_end", "name", "isin", "quantity", "value_lakh"])
    h["bucket"], h["industry"], h["weight"] = ppfas.CORE, "X", 0.01
    h["key"] = h["isin"].map(ch.stock_key)
    h["price"] = h["value_lakh"] * 1e5 / h["quantity"]
    return h


def test_split_with_new_isin_detected_even_when_the_fund_also_sold():
    # MCX, Jan 2026: 5:1 split and ~80% sold in the same month.
    h = _h([(M1, "MCX", "INE745G01035", 797_719, 88_833),
            (M2, "MCX", "INE745G01043", 795_472, 20_110)])
    s = ch.detect_splits(h, {})
    assert len(s) == 1 and s.iloc[0]["factor"] in (4.0, 5.0)
    nse = ch.detect_splits(h, {"INE745G01": [(dt.date(2026, 2, 2), 5.0)]})
    assert nse.iloc[0]["factor"] == 5.0 and nse.iloc[0]["source"] == "NSE corporate action"


def test_price_crash_while_buying_is_not_a_bonus():
    # Mahanagar Gas, Mar 2026: price -24%, starter stake grew 11x, same ISIN.
    h = _h([(M1, "MGL", "INE002S01010", 3_045, 37.1),
            (M2, "MGL", "INE002S01010", 35_366, 328.1)])
    assert ch.detect_splits(h, {}).empty


def _summary(flow_factor):
    return pd.DataFrame({"month_end": [M1, M2], "flow_factor": [float("nan"), flow_factor]})


def test_buying_in_line_with_inflows_is_not_conviction():
    h = _h([(M1, "A", "INE000A01011", 1_000_000, 1_000),
            (M2, "A", "INE000A01011", 1_025_000, 1_025)])
    c = ch.monthly_changes(h, _summary(1.025), ch.detect_splits(h, {}))
    r = c.iloc[0]
    assert r["raw_change"] == pytest.approx(0.025)
    assert r["active_change"] == pytest.approx(0.0, abs=1e-9)
    assert r["action"] == "Kept in step with inflows"


def test_untouched_holding_shrinks_against_inflows_and_new_and_exit_labelled():
    h = _h([(M1, "Held", "INE000A01011", 1_000, 10), (M2, "Held", "INE000A01011", 1_000, 10),
            (M1, "Gone", "INE000B01011", 500, 5), (M2, "New", "INE000C01011", 700, 7)])
    c = ch.monthly_changes(h, _summary(1.05), ch.detect_splits(h, {})).set_index("name")
    assert c.at["Held", "action"] == "No trade"
    assert c.at["Held", "active_change"] == pytest.approx(1 / 1.05 - 1)
    assert c.at["Gone", "action"] == "EXIT"
    assert c.at["New", "action"] == "NEW ENTRY"
