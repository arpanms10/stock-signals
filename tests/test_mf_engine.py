"""MF evaluation: AMFI parsing, fund lookup, peer metrics, verdicts.

The quiet failures that matter: a closed fund dropped from its category (the
backtest then only sees survivors), a regular or IDCW plan mistaken for
Direct Growth, a sell verdict that ignores exit load, and a name query that
lands confidently on the wrong fund.
"""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from mf import amfi, lookup, metrics, verdict

NAVALL = """Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date

Open Ended Schemes(Equity Scheme - Flexi Cap Fund)

PPFAS Mutual Fund

122639;INF879O01027;-;Parag Parikh Flexi Cap Fund - Direct Plan - Growth;Direct Plan;Growth Option;90.8844;16-Oct-2026
122640;INF879O01019;-;Parag Parikh Flexi Cap Fund - Regular Plan - Growth;Regular Plan;Growth Option;80.1;16-Oct-2026
153964;INF879O01AA1;-;Parag Parikh Flexi Cap Fund - Direct Plan - Monthly IDCW Payout;Direct Plan;IDCW Option;20.1;16-Oct-2026

Open Ended Schemes(Equity Schemes - ELSS- Tax Saver Fund)

Axis Mutual Fund

120503;INF846K01EW2;-;Axis ELSS Tax Saver Fund - Direct Plan - Growth;Direct Plan;Growth Option;101.5;16-Oct-2026

Close Ended Schemes(Growth)

Some Mutual Fund

999001;INF000000001;-;Some Closed Equity Fund - Direct Plan - Growth;Direct Plan;Growth Option;12.0;16-Oct-2026
"""

HISTORY = """Scheme Code;NAV Name;Plan;Option;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Net Asset Value;Date

Open Ended Schemes ( Growth )


Sahara Mutual Fund
120288;Sahara Midcap Fund-Growth Option - Direct;;;INF515L01759;;40.809;02-Jan-2019
120289;Sahara Midcap Fund-Dividend- Direct;;;INF515L01760;;40.809;02-Jan-2019

Open Ended Schemes ( Equity Scheme - Large Cap Fund )

UTI Mutual Fund
120716;UTI Nifty 50 Index Fund - Growth Option- Direct;;;INF789F01XA0;;70.1;02-Jan-2019
"""


def test_navall_keeps_only_open_ended_direct_growth_with_category():
    df = amfi.to_frame(amfi.parse(NAVALL))
    assert set(df["code"]) == {122639, 120503}
    cats = dict(zip(df["code"], df["category"]))
    assert cats[122639] == "equity:flexi cap"
    assert cats[120503] == "equity:elss"        # "ELSS- Tax Saver Fund" folded in
    assert df.loc[df.code == 122639, "amc"].item() == "PPFAS Mutual Fund"


def test_history_layout_and_old_label_inferred_from_name():
    df = amfi.to_frame(amfi.parse(HISTORY))
    cats = dict(zip(df["code"], df["category"]))
    # A closed fund still under the pre-2018 "Growth" heading must not vanish.
    assert cats == {120288: "equity:mid cap", 120716: "equity:large cap"}
    assert df["date"].iloc[0] == dt.date(2019, 1, 2)


@pytest.mark.parametrize("heading,expected", [
    ("Equity Scheme - Flexi Cap Fund", "equity:flexi cap"),
    ("Equity Schemes - Large & Mid Cap Fund", "equity:large & mid cap"),
    ("Hybrid Schemes - Balanced Advantage Fund/ Dynamic Asset Allocation", "hybrid:balanced advantage"),
    ("Hybrid Scheme - Dynamic Asset Allocation or Balanced Advantage", "hybrid:balanced advantage"),
    ("Equity Schemes - Sectoral Fund", "equity:sectoral/thematic"),
    ("Income", None),
])
def test_category_spellings(heading, expected):
    assert amfi.category(heading) == expected


SCHEMES = pd.DataFrame({
    "code": [122639, 118955, 120503, 118989, 100001],
    "name": ["Parag Parikh Flexi Cap Fund - Direct Plan - Growth",
             "HDFC Flexi Cap Fund - Growth Option - Direct Plan",
             "Axis ELSS Tax Saver Fund - Direct Plan - Growth",
             "HDFC Mid-Cap Opportunities Fund - Growth Option - Direct Plan",
             "Old Closed Flexi Cap Fund - Direct - Growth"],
    "last_month": ["2026-10"] * 4 + ["2020-01"],
})


def test_lookup_resolves_names_aliases_and_codes():
    assert lookup.find("parag parikh flexi cap", SCHEMES, "2026-10").code == 122639
    assert lookup.find("PPFAS flexi", SCHEMES, "2026-10").code == 122639
    assert lookup.find("hdfc flexi cap", SCHEMES, "2026-10").code == 118955
    assert lookup.find("118989", SCHEMES).code == 118989


def test_lookup_ambiguous_returns_candidates_not_a_guess():
    m = lookup.find("hdfc", SCHEMES, "2026-10")
    assert not m.resolved
    assert {c for c, _, _ in m.candidates[:2]} == {118955, 118989}


def test_lookup_skips_closed_schemes_by_name():
    m = lookup.find("old closed flexi", SCHEMES, "2026-10")
    assert m.code != 100001


def _panel(n_funds=8, months=72, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.period_range("2020-01", periods=months, freq="M")
    drift = np.linspace(-0.006, 0.006, n_funds)   # fund 0 worst, last best
    R = rng.normal(0.01, 0.03, size=(months, 1)) + drift + rng.normal(0, 0.002, (months, n_funds))
    P = pd.DataFrame(100 * np.cumprod(1 + R, axis=0), index=idx,
                     columns=range(1, n_funds + 1))
    cat = pd.Series("equity:flexi cap", index=P.columns)
    return P, cat


def test_snapshot_ranks_and_excess_order_the_funds():
    P, cat = _panel()
    s = metrics.snapshot(P, cat, P.index[-1])
    assert s["rank_3y"].idxmin() == 1 and s["rank_3y"].idxmax() == 8
    assert s.loc[1, "excess_3y"] < 0 < s.loc[8, "excess_3y"]
    assert s["excess_3y"].median() == pytest.approx(0, abs=1e-12)
    assert s["consistency_3y"].between(0, 1).all()
    assert (s["max_drawdown_3y"] <= 0).all()


def test_young_fund_is_not_ranked_on_a_window_it_lacks():
    P, cat = _panel()
    P.loc[:P.index[-20], 8] = np.nan                # launched 19 months ago
    s = metrics.snapshot(P, cat, P.index[-1])
    assert np.isnan(s.loc[8, "rank_3y"])
    assert s.loc[8, "history_months"] == 18
    st, _ = verdict.state(s.loc[8], verdict.load_rules())
    assert st == verdict.YOUNG


def test_forward_excess_carries_a_closed_fund_at_the_median():
    P, cat = _panel()
    t = P.index[-25]
    P.loc[P.index[-12]:, 1] = np.nan                # fund 1 merged away
    fx = metrics.forward_excess(P, cat, t, 24, list(P.columns))
    assert 1 in fx.index and np.isfinite(fx[1])


RULES = verdict.load_rules()


def _snap(**overrides):
    base = dict(category="equity:flexi cap", history_months=120, peers=30,
                rank_1y=0.5, rank_3y=0.5, rank_5y=0.5,
                excess_1y=0.0, excess_3y=0.0, excess_5y=0.0,
                vs_index_3y=np.nan, consistency_3y=0.5, downside_capture_3y=1.0)
    rows = {}
    for code, o in overrides.items():
        rows[int(code)] = {**base, **o}
    return pd.DataFrame.from_dict(rows, orient="index")


def test_states():
    s = _snap(**{
        "1": dict(rank_3y=0.1, rank_1y=0.3, excess_3y=-0.04),
        "2": dict(rank_3y=0.1, rank_1y=0.8, excess_3y=-0.04),   # recovering: not weak
        "3": dict(rank_3y=0.7, rank_1y=0.1, excess_1y=-0.06),
        "4": dict(rank_3y=0.8, rank_1y=0.6, consistency_3y=0.7, excess_3y=0.03),
        "5": dict(rank_3y=0.8, rank_1y=0.6, consistency_3y=0.7, downside_capture_3y=1.3),
    })
    got = {c: verdict.state(s.loc[c], RULES)[0] for c in s.index}
    assert got == {1: "WEAK", 2: "OK", 3: "SLIPPING", 4: "STRONG", 5: "OK"}


def _book(rows):
    return pd.DataFrame(rows, columns=["code", "weight", "held_over_1y"])


def test_weak_held_fund_exits_with_exit_load_caution_and_alternatives():
    s = _snap(**{"1": dict(rank_3y=0.1, rank_1y=0.3, excess_3y=-0.04),
                 "4": dict(rank_3y=0.8, rank_1y=0.6, consistency_3y=0.7)})
    d = {x.code: x for x in verdict.decide(s, {4: "Good Fund"}, RULES,
                                           _book([(1, 40, "no"), (4, 20, "yes")]))}
    assert d[1].action == "EXIT" and d[1].target == 0
    assert any("exit load" in c for c in d[1].cautions)
    assert any("Good Fund" in a for a in d[1].alternatives)
    assert d[4].action == "ADD" and d[4].target == 25


def test_oversized_fund_trimmed_to_cap_and_unknown_tenure_warns():
    s = _snap(**{"4": dict(rank_3y=0.8, rank_1y=0.6, consistency_3y=0.7)})
    d = verdict.decide(s, {}, RULES, _book([(4, 60, None)]))[0]
    assert (d.action, d.target) == ("TRIM", 25)
    assert any("holding period" in c for c in d.cautions)


def test_category_cap_trims_the_weaker_of_two_small_caps():
    s = _snap(**{"1": dict(category="equity:small cap", rank_3y=0.5),
                 "2": dict(category="equity:small cap", rank_3y=0.9, rank_1y=0.7,
                           consistency_3y=0.8)})
    d = {x.code: x for x in verdict.decide(s, {}, RULES, _book([(1, 20, "yes"), (2, 20, "yes")]))}
    assert d[1].action == "TRIM" and d[1].target == pytest.approx(5)
    assert d[2].action != "TRIM"


def test_near_duplicates_flag_the_weaker():
    s = _snap(**{"1": dict(rank_3y=0.5), "2": dict(rank_3y=0.55)})
    corr = pd.DataFrame([[1, 0.98], [0.98, 1]], index=[1, 2], columns=[1, 2])
    d = {x.code: x for x in verdict.decide(s, {}, RULES, _book([(1, 20, "yes"), (2, 20, "yes")]),
                                           corr=corr)}
    assert d[1].action == "TRIM" and d[2].action == "HOLD"


def test_unheld_funds_get_add_avoid_watch():
    s = _snap(**{"1": dict(rank_3y=0.1, rank_1y=0.3, excess_3y=-0.04),
                 "4": dict(rank_3y=0.8, rank_1y=0.6, consistency_3y=0.7),
                 "6": dict(history_months=12)})
    d = {x.code: x.action for x in verdict.decide(s, {}, RULES, evaluate=[1, 4, 6])}
    assert d == {1: "AVOID", 4: "ADD", 6: "WATCH"}


def test_unranked_held_funds_hold_but_still_obey_category_caps():
    s = _snap(**{"7": dict(category="equity:sectoral/thematic"),
                 "8": dict(category="index:equity")})
    d = {x.code: x for x in verdict.decide(s, {}, RULES, _book([(7, 30, "yes"), (8, 70, "yes")]))}
    assert d[7].action == "TRIM" and d[7].target == pytest.approx(10)
    assert d[8].action == "HOLD" and "yardstick" in d[8].reasons[0]


# --- holdings file: CSV or Excel --------------------------------------------

from mf import holdings as hold   # noqa: E402


def test_xlsx_book_with_loose_headers_and_percent_cells(tmp_path):
    p = tmp_path / "mf_holdings.xlsx"
    pd.DataFrame({"Fund Name": ["Parag Parikh Flexi Cap", "HDFC Mid-Cap Opportunities", 120503],
                  "Allocation %": [0.5, 0.3, 0.2],          # cells formatted as %
                  "Held over 1 year": ["yes", "no", None]}).to_excel(p, index=False)
    book = hold.load(SCHEMES, "2026-10", p)
    t = book.table.set_index("code")
    assert list(t.index) == [122639, 118989, 120503]
    assert t["weight"].tolist() == pytest.approx([50, 30, 20])
    assert t.at[118989, "held_over_1y"] == "no"
    assert book.unresolved == [] and book.warnings == []


def test_csv_book_still_works_and_flags_unknown_funds(tmp_path):
    p = tmp_path / "mf_holdings.csv"
    p.write_text("# comment\nfund,pct,held_over_1y\nPPFAS flexi,60,yes\nhdfc,40,\n")
    book = hold.load(SCHEMES, "2026-10", p)
    assert book.table["code"].tolist() == [122639]
    assert book.unresolved[0][0] == "hdfc"


def test_both_files_present_is_an_error(tmp_path):
    (tmp_path / "mf_holdings.csv").write_text("fund,pct\nx,100\n")
    pd.DataFrame({"fund": ["x"], "pct": [100]}).to_excel(tmp_path / "mf_holdings.xlsx", index=False)
    with pytest.raises(SystemExit):
        hold.find_file(tmp_path)


def test_missing_percentage_column_names_what_was_found(tmp_path):
    p = tmp_path / "mf_holdings.xlsx"
    pd.DataFrame({"Scheme": ["PPFAS flexi"], "Amount": [100000]}).to_excel(p, index=False)
    with pytest.raises(SystemExit, match="amount"):
        hold.load(SCHEMES, "2026-10", p)
