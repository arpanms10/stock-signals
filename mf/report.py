"""The month-by-month comparison workbook, written for a human reader.

Sheet order is reading order: the year in plain English first, then the
monthly story, then the tables that back every sentence, raw data last.
Every observation sentence is generated from the numbers on the later sheets;
nothing here is typed in by hand, so re-running next month rewrites it.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from mf import changes as ch
from mf.ppfas import ARBITRAGE, FOREIGN, PICK_BUCKETS

HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
HEADER_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, size=14, color="1F3A5F")
SUB_FONT = Font(bold=True, size=11, color="1F3A5F")
WRAP = Alignment(wrap_text=True, vertical="top")
THIN = Border(bottom=Side(style="thin", color="D9D9D9"))

# One colour per decision, used on every sheet that shows actions.
ACTION_FILL = {
    "NEW ENTRY": "1E7B34", "Added (big)": "57B36A", "Added": "B7E1C1",
    "Kept in step with inflows": "EDEDED", "No trade": "FFFFFF",
    "Bought less than inflows": "FCE4C4", "Trimmed": "F8B26A",
    "Trimmed (big)": "E06C4C", "EXIT": "A61B1B",
}
DARK_ACTIONS = {"NEW ENTRY", "EXIT", "Trimmed (big)", "Added (big)"}

PCT = "0.00%"
PCT1 = "0.0%"
CR = '#,##0" cr"'
NUM = "#,##0"


def _base_action(a: str) -> str:
    return a.split(" (was")[0].split(" (still")[0]


def _fill_for(a: str) -> PatternFill | None:
    c = ACTION_FILL.get(_base_action(a))
    return PatternFill("solid", fgColor=c) if c else None


# --- sheet helpers ------------------------------------------------------

def _write_table(ws, df: pd.DataFrame, start_row: int = 1, formats: dict | None = None,
                 widths: dict | None = None, wrap: tuple = (), freeze: str | None = "A2",
                 autofilter: bool = True) -> int:
    formats, widths = formats or {}, widths or {}
    for j, col in enumerate(df.columns, 1):
        c = ws.cell(row=start_row, column=j, value=str(col))
        c.fill, c.font = HEADER_FILL, HEADER_FONT
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for i, row in enumerate(df.itertuples(index=False), start_row + 1):
        for j, (col, v) in enumerate(zip(df.columns, row), 1):
            if isinstance(v, float) and np.isnan(v):
                v = None
            elif isinstance(v, (np.floating, np.integer)):
                v = v.item()
            c = ws.cell(row=i, column=j, value=v)
            if col in formats:
                c.number_format = formats[col]
            if col in wrap:
                c.alignment = WRAP
    for j, col in enumerate(df.columns, 1):
        ws.column_dimensions[get_column_letter(j)].width = widths.get(
            col, max(10, min(40, len(str(col)) + 2)))
    if freeze:
        ws.freeze_panes = freeze
    if autofilter and len(df):
        ws.auto_filter.ref = (f"A{start_row}:{get_column_letter(len(df.columns))}"
                              f"{start_row + len(df)}")
    return start_row + len(df) + 1


def _lines(ws, lines: list[tuple[str, str]], start_row: int = 1, width: int = 140) -> int:
    """Write (style, text) lines in column A. style: title | sub | text | bullet | blank."""
    ws.column_dimensions["A"].width = width
    r = start_row
    for style, text in lines:
        c = ws.cell(row=r, column=1, value=("•  " + text) if style == "bullet" else text)
        c.alignment = WRAP
        if style == "title":
            c.font = TITLE_FONT
        elif style == "sub":
            c.font = SUB_FONT
        if style in ("bullet", "text") and len(text) > width:
            ws.row_dimensions[r].height = 15 * (len(text) // width + 1)
        r += 1
    return r


# --- the year in plain English -------------------------------------------

def _names(df: pd.DataFrame, fmt) -> str:
    return "; ".join(fmt(r) for r in df.itertuples())


def year_observations(summary: pd.DataFrame, journeys: pd.DataFrame,
                      changes: pd.DataFrame, sectors: pd.DataFrame) -> list[tuple[str, str]]:
    s0, s1 = summary.iloc[0], summary.iloc[-1]
    m0, m1 = ch.month_label(s0.month_end), ch.month_label(s1.month_end)
    flows = summary["net_inflow_cr"].dropna()
    early, late = flows.iloc[:3].mean(), flows.iloc[-3:].mean()
    nav_ret = s1.nav_direct / s0.nav_direct - 1
    L: list[tuple[str, str]] = [
        ("title", f"Parag Parikh Flexi Cap Fund: what the manager did, {m0} to {m1}"),
        ("text", "Every line below is computed from the monthly portfolio disclosures. "
                 "Share counts, not weights, are used to judge buying and selling, and "
                 "they are adjusted for investor inflows and stock splits (see 'Read Me')."),
        ("blank", ""),
        ("sub", "1. Size and money flows"),
        ("bullet", f"Net assets went from Rs {s0.net_assets_cr:,.0f} cr to Rs "
                   f"{s1.net_assets_cr:,.0f} cr. Direct-plan NAV went {s0.nav_direct:.2f} -> "
                   f"{s1.nav_direct:.2f} ({nav_ret:+.1%})."),
        ("bullet", f"Investors added an estimated Rs {flows.sum():,.0f} cr of new money over "
                   f"the year. Inflows slowed: about Rs {early:,.0f} cr/month in the first three "
                   f"months vs about Rs {late:,.0f} cr/month in the last three."),
    ]

    L += [("blank", ""), ("sub", "2. Where the money sits (share of the fund)")]
    for col, label in [("domestic_equity", "Indian stocks"), ("foreign_equity", "Foreign stocks"),
                       ("reits", "REITs"), ("arbitrage", "Arbitrage (hedged, cash-like)"),
                       ("debt_money_market", "Debt & money market"),
                       ("cash_treps_liquid", "Cash / TREPS / liquid fund")]:
        L.append(("bullet", f"{label}: {s0[col]:.1%} -> {s1[col]:.1%} "
                            f"({(s1[col] - s0[col]) * 100:+.1f} pp)"))
    L.append(("bullet", f"Cash-like money (arbitrage + debt + cash) fell from "
                        f"{s0.cash_like_total:.1%} to {s1.cash_like_total:.1%}: the manager "
                        f"has been putting the cash cushion to work."
              if s1.cash_like_total < s0.cash_like_total - 0.02 else
              f"Cash-like money (arbitrage + debt + cash): {s0.cash_like_total:.1%} -> "
              f"{s1.cash_like_total:.1%}."))
    L.append(("bullet", f"Number of stock positions: {s0.n_stocks} -> {s1.n_stocks}. "
                        f"Top-10 holdings: {s0.top10_weight:.1%} -> {s1.top10_weight:.1%} "
                        f"of the fund."))

    held = journeys[journeys["status"].str.startswith("Held all year")]
    new = journeys[journeys["status"].str.startswith("New")].sort_values(
        "weight_end", ascending=False)
    gone = journeys[journeys["status"].str.startswith(("Exited", "In and out"))
                    & ~journeys["name"].str.contains("Partly Paid")]
    L += [("blank", ""), ("sub", "3. New stocks bought this year (still held)")]
    for r in new.itertuples():
        L.append(("bullet", f"{r.name} ({r.industry}): first bought {r.first_month}, now "
                            f"{r.weight_end:.2%} of the fund (Rs {r.value_end_cr:,.0f} cr). "
                            f"Price since first seen: {r.price_return_while_held:+.0%}."))
    L += [("blank", ""), ("sub", "4. Stocks sold out completely")]
    for r in gone.itertuples():
        L.append(("bullet", f"{r.name}: was {r.weight_start:.2%} of the fund in {r.first_month}"
                            f"{', peak ' + format(r.weight_peak, '.2%') if r.weight_peak > r.weight_start + 1e-4 else ''}"
                            f"; fully sold by {r.exit_month}. Price while held: "
                            f"{r.price_return_while_held:+.0%}."))
    if gone.empty:
        L.append(("bullet", "None."))

    acc = held[held["active_change_cum"] >= 0.10].sort_values("weight_change_pp", ascending=False)
    L += [("blank", ""), ("sub", "5. Existing stocks the manager bought much more of "
                                 "(beyond what inflows required)")]
    for r in acc.itertuples():
        L.append(("bullet", f"{r.name}: shares {r.shares_change:+.0%} ({r.active_change_cum:+.0%} "
                            f"beyond inflows), weight {r.weight_start:.2%} -> {r.weight_end:.2%}. "
                            f"Added in {r.months_added} of {len(summary) - 1} months; price {r.price_return_while_held:+.0%}."))

    frozen = held[held["shares_change"].abs() < 0.005]
    under = held[(held["active_change_cum"] <= -0.10) & (held["shares_change"].abs() >= 0.005)]
    L += [("blank", ""), ("sub", "6. Stocks the manager stopped adding to")]
    if len(frozen):
        L.append(("bullet", "Not a single share traded all year (weight drifts only with price "
                            "and shrinks as the fund grows): "
                            + _names(frozen.sort_values("weight_end", ascending=False),
                                     lambda r: f"{r.name} ({r.weight_start:.2%} -> {r.weight_end:.2%})")
                            + "."))
    if len(under):
        L.append(("bullet", "Bought far less than inflows, so their share of the fund was let "
                            "shrink: " + _names(under.sort_values("active_change_cum"),
                                                lambda r: f"{r.name} (shares {r.shares_change:+.0%}, "
                                                          f"{r.active_change_cum:+.0%} vs inflows)")
                            + "."))
    fx = held[held["bucket"] == FOREIGN]
    if len(fx) and (fx["shares_change"].abs() < 0.02).all():
        L.append(("bullet", "Foreign holdings (" + ", ".join(fx["name"]) + ") were essentially "
                            "untouched. Their weight changes are price and rupee moves, not "
                            "decisions. Indian funds' fresh overseas buying is capped by "
                            "industry-wide regulatory limits, which likely explains the freeze."))

    adds = changes[changes["action"].isin(["Added", "Added (big)"])]
    fell = (adds["price_return"] < 0).sum()
    L += [("blank", ""), ("sub", "7. Patterns in how the manager buys")]
    if len(adds):
        L.append(("bullet", f"Of {len(adds)} 'added' decisions on existing stocks, {fell} "
                            f"({fell / len(adds):.0%}) came in a month when that stock's price "
                            f"fell. Median price move in an 'added' month: "
                            f"{adds['price_return'].median():+.1%}."))
    trims = changes[changes["action"].str.startswith(("Trimmed", "EXIT"))]
    L.append(("bullet", f"Selling is rare: {len(trims)} trim/exit decisions in {len(summary) - 1} months, "
                        f"versus {len(adds)} adds and "
                        f"{(changes['action'].str.startswith('NEW')).sum()} new entries."))
    via_arb = changes[changes["action"] == "NEW ENTRY (was in arbitrage book)"]
    if len(via_arb):
        L.append(("bullet", "Some positions started life in the hedged arbitrage book before "
                            "becoming real stock bets: " + ", ".join(
                                f"{r.name} ({ch.month_label(r.month_end)})"
                                for r in via_arb.itertuples()) + "."))
    to = summary["turnover_excl_arb"].dropna()
    if len(to):
        L.append(("bullet", f"Portfolio turnover excluding arbitrage (as reported) ranged "
                            f"{to.min():.1f}% to {to.max():.1f}%: a low-churn, buy-and-hold style."))

    d = (sectors.iloc[:, -1] - sectors.iloc[:, 0]) * 100
    L += [("blank", ""), ("sub", "8. Sector shifts (share of the fund, start -> end)")]
    for sec, v in d.sort_values(ascending=False).head(4).items():
        L.append(("bullet", f"Up: {sec} {sectors.loc[sec].iloc[0]:.1%} -> "
                            f"{sectors.loc[sec].iloc[-1]:.1%} ({v:+.1f} pp)"))
    for sec, v in d.sort_values().head(4).items():
        if v < 0:
            L.append(("bullet", f"Down: {sec} {sectors.loc[sec].iloc[0]:.1%} -> "
                                f"{sectors.loc[sec].iloc[-1]:.1%} ({v:+.1f} pp)"))
    L += [("blank", ""),
          ("text", "These are observations about what the fund did, not recommendations. "
                   "Disclosures are month-end snapshots published ~10 days later, so trades "
                   "inside a month are invisible.")]
    return L


# --- the monthly story ------------------------------------------------------

STORY_ORDER = ["NEW ENTRY", "EXIT", "Added (big)", "Trimmed (big)", "Trimmed", "Added"]


def monthly_story(summary: pd.DataFrame, changes: pd.DataFrame) -> pd.DataFrame:
    rows = []
    prev = None
    for s in summary.itertuples():
        if prev is None:
            prev = s
            continue
        c = changes[changes["month_end"] == s.month_end].copy()
        c["base"] = c["action"].map(_base_action)
        n_new, n_exit = (c["base"] == "NEW ENTRY").sum(), (c["base"] == "EXIT").sum()
        headline = (f"Fund Rs {s.net_assets_cr:,.0f} cr, NAV {s.nav_return:+.1%}, est. "
                    f"inflow Rs {s.net_inflow_cr:,.0f} cr. Stocks {s.total_equity:.1%} "
                    f"(India {s.domestic_equity:.1%}, foreign {s.foreign_equity:.1%}), REITs "
                    f"{s.reits:.1%}, cash-like {s.cash_like_total:.1%} "
                    f"({(s.cash_like_total - prev.cash_like_total) * 100:+.1f} pp). "
                    f"{n_new} new, {n_exit} exits, "
                    f"{c['base'].str.startswith('Added').sum()} adds, "
                    f"{c['base'].str.startswith('Trimmed').sum()} trims, "
                    f"{(c['base'] == 'No trade').sum()} untouched.")
        rows.append({"Month": ch.month_label(s.month_end), "Action": "MONTH SUMMARY",
                     "Stock": "", "Weight before": None, "Weight after": None,
                     "What happened": headline})
        c["order"] = c["base"].map({a: i for i, a in enumerate(STORY_ORDER)})
        c = c[c["order"].notna()].sort_values(["order", "weight"], ascending=[True, False])
        for r in c.itertuples():
            rows.append({"Month": ch.month_label(s.month_end), "Action": r.action,
                         "Stock": r.name, "Weight before": r.weight_prev,
                         "Weight after": r.weight, "What happened": r.note})
        prev = s
    return pd.DataFrame(rows)


# --- workbook ---------------------------------------------------------------

def _matrix(changes: pd.DataFrame, h: pd.DataFrame, journeys: pd.DataFrame,
            value: str) -> pd.DataFrame:
    picks = h[h["bucket"].isin(PICK_BUCKETS)]
    order = list(journeys["key"])
    names = dict(zip(journeys["key"], journeys["name"]))
    if value == "weight":
        t = picks.pivot_table(index="key", columns="month_end", values="weight", aggfunc="sum")
    else:
        t = changes.pivot_table(index="key", columns="month_end", values=value, aggfunc="first")
    t = t.reindex(order)
    t.columns = [ch.month_label(c) for c in t.columns]
    t.insert(0, "Stock", [names[k] for k in t.index])
    t.insert(1, "Status", list(journeys.set_index("key").loc[t.index, "status"]))
    return t.reset_index(drop=True)


def build(path: Path, discs, h: pd.DataFrame, summary: pd.DataFrame,
          changes: pd.DataFrame, journeys: pd.DataFrame, splits: pd.DataFrame,
          sectors: pd.DataFrame) -> Path:
    wb = Workbook()
    m0, m1 = ch.month_label(summary.month_end.iloc[0]), ch.month_label(summary.month_end.iloc[-1])

    # 1. Year summary
    ws = wb.active
    ws.title = "Year Summary"
    _lines(ws, year_observations(summary, journeys, changes, sectors))

    # 2. Monthly story
    ws = wb.create_sheet("Monthly Story")
    story = monthly_story(summary, changes)
    _write_table(ws, story, formats={"Weight before": PCT, "Weight after": PCT},
                 widths={"Month": 10, "Action": 26, "Stock": 34, "Weight before": 9,
                         "Weight after": 9, "What happened": 120},
                 wrap=("What happened",), freeze="D2")
    for i, r in enumerate(story.itertuples(), 2):
        if r.Action == "MONTH SUMMARY":
            for j in range(1, 7):
                cell = ws.cell(row=i, column=j)
                cell.fill = PatternFill("solid", fgColor="DCE6F1")
                cell.font = Font(bold=True)
            ws.row_dimensions[i].height = 32
        else:
            f = _fill_for(r.Action)
            if f:
                ws.cell(row=i, column=2).fill = f
                if _base_action(r.Action) in DARK_ACTIONS:
                    ws.cell(row=i, column=2).font = Font(color="FFFFFF", bold=True)

    # 3. Asset mix + charts
    ws = wb.create_sheet("Asset Mix")
    mix = pd.DataFrame({
        "Month": [ch.month_label(m) for m in summary.month_end],
        "Net assets (Rs cr)": summary.net_assets_cr,
        "Direct NAV": summary.nav_direct,
        "NAV return (month)": summary.nav_return,
        "Est. net inflow (Rs cr)": summary.net_inflow_cr,
        "Indian stocks": summary.domestic_equity,
        "Foreign stocks": summary.foreign_equity,
        "REITs": summary.reits,
        "Arbitrage (hedged)": summary.arbitrage,
        "Debt & money market": summary.debt_money_market,
        "Cash / TREPS / liquid": summary.cash_treps_liquid,
        "Total stocks (India + foreign)": summary.total_equity,
        "Total cash-like": summary.cash_like_total,
        "No. of stocks": summary.n_stocks,
        "Top-10 weight": summary.top10_weight,
        "Short futures (% of fund)": summary.short_futures,
        "Turnover incl. arbitrage (%)": summary.turnover_incl_arb,
        "Turnover excl. arbitrage (%)": summary.turnover_excl_arb,
    })
    fm = {c: PCT1 for c in mix.columns[5:13]} | {
        "Net assets (Rs cr)": CR, "Est. net inflow (Rs cr)": CR, "Direct NAV": "0.00",
        "NAV return (month)": "+0.0%;-0.0%", "Top-10 weight": PCT1,
        "Short futures (% of fund)": PCT, "Turnover incl. arbitrage (%)": "0.00",
        "Turnover excl. arbitrage (%)": "0.00"}
    end = _write_table(ws, mix, formats=fm, widths={c: 13 for c in mix.columns}, freeze="B2",
                       autofilter=False)
    ws.row_dimensions[1].height = 45
    n = len(mix)
    bar = BarChart()
    bar.type, bar.grouping, bar.overlap = "col", "percentStacked", 100
    bar.title, bar.height, bar.width = "Where the money sits", 9, 22
    bar.add_data(Reference(ws, min_col=6, max_col=11, min_row=1, max_row=n + 1),
                 titles_from_data=True)
    bar.set_categories(Reference(ws, min_col=1, min_row=2, max_row=n + 1))
    ws.add_chart(bar, f"A{end + 2}")
    line = BarChart()
    line.title, line.height, line.width = "Estimated net investor inflow (Rs cr)", 9, 22
    line.add_data(Reference(ws, min_col=5, min_row=1, max_row=n + 1), titles_from_data=True)
    line.set_categories(Reference(ws, min_col=1, min_row=2, max_row=n + 1))
    line.legend = None
    ws.add_chart(line, f"A{end + 21}")
    aum = LineChart()
    aum.title, aum.height, aum.width = "Net assets (Rs cr)", 9, 22
    aum.add_data(Reference(ws, min_col=2, min_row=1, max_row=n + 1), titles_from_data=True)
    aum.set_categories(Reference(ws, min_col=1, min_row=2, max_row=n + 1))
    aum.legend = None
    ws.add_chart(aum, f"L{end + 2}")

    # 4. Stock journeys
    ws = wb.create_sheet("Stock Journeys")
    jt = journeys.rename(columns={
        "name": "Stock", "industry": "Industry", "status": "Status", "verdict": "The year in one line",
        "first_month": "First seen", "exit_month": "Gone by", "months_held": "Months held",
        "weight_start": f"Weight {m0}", "weight_end": f"Weight {m1}", "weight_peak": "Peak weight",
        "weight_change_pp": "Weight change (pp)", "shares_change": "Shares change",
        "active_change_cum": "Change beyond inflows", "months_added": "Months added",
        "months_trimmed": "Months trimmed", "months_no_trade": "Months untouched",
        "price_return_while_held": "Price move while held", "value_end_cr": f"Value {m1} (Rs cr)"})
    jt["Type"] = jt["bucket"].map({FOREIGN: "Foreign", "reit": "REIT",
                                   "special_situation": "Special situation"}).fillna("Indian stock")
    jt = jt[["Stock", "Type", "Industry", "Status", "The year in one line", "First seen", "Gone by",
             "Months held", f"Weight {m0}", f"Weight {m1}", "Peak weight", "Weight change (pp)",
             "Shares change", "Change beyond inflows", "Months added", "Months trimmed",
             "Months untouched", "Price move while held", f"Value {m1} (Rs cr)"]]
    _write_table(ws, jt, formats={f"Weight {m0}": PCT, f"Weight {m1}": PCT, "Peak weight": PCT,
                                  "Weight change (pp)": "+0.00;-0.00", "Shares change": "+0%;-0%",
                                  "Change beyond inflows": "+0%;-0%",
                                  "Price move while held": "+0%;-0%", f"Value {m1} (Rs cr)": CR},
                 widths={"Stock": 34, "Type": 12, "Industry": 24, "Status": 22,
                         "The year in one line": 80},
                 wrap=("The year in one line",), freeze="B2")
    last = len(jt) + 1
    for col in ("Change beyond inflows", "Weight change (pp)"):
        L = get_column_letter(list(jt.columns).index(col) + 1)
        ws.conditional_formatting.add(f"{L}2:{L}{last}", ColorScaleRule(
            start_type="percentile", start_value=5, start_color="F8696B",
            mid_type="num", mid_value=0, mid_color="FFFFFF",
            end_type="percentile", end_value=95, end_color="63BE7B"))

    # 5. Decisions matrix (colour = action)
    ws = wb.create_sheet("Decisions Grid")
    grid = _matrix(changes, h, journeys, "action")
    _write_table(ws, grid, widths={"Stock": 34, "Status": 24} | {c: 15 for c in grid.columns[2:]},
                 freeze="C2", autofilter=False)
    for i in range(len(grid)):
        for j in range(2, len(grid.columns)):
            v = grid.iat[i, j]
            cell = ws.cell(row=i + 2, column=j + 1)
            if isinstance(v, str):
                f = _fill_for(v)
                if f:
                    cell.fill = f
                if _base_action(v) in DARK_ACTIONS:
                    cell.font = Font(color="FFFFFF", bold=True)
                cell.alignment = Alignment(wrap_text=True)
    r = len(grid) + 3
    ws.cell(row=r, column=1, value="Legend").font = SUB_FONT
    for k, (a, col) in enumerate(ACTION_FILL.items(), 1):
        c = ws.cell(row=r + k, column=1, value=a)
        c.fill = PatternFill("solid", fgColor=col)
        if a in DARK_ACTIONS:
            c.font = Font(color="FFFFFF", bold=True)

    # 6. Active change % grid
    ws = wb.create_sheet("Buying vs Inflows %")
    act = _matrix(changes, h, journeys, "active_change")
    _write_table(ws, act, formats={c: "+0.0%;-0.0%;0.0%" for c in act.columns[2:]},
                 widths={"Stock": 34, "Status": 24}, freeze="C2", autofilter=False)
    rng = f"C2:{get_column_letter(len(act.columns))}{len(act) + 1}"
    ws.conditional_formatting.add(rng, ColorScaleRule(
        start_type="num", start_value=-0.3, start_color="F8696B", mid_type="num", mid_value=0,
        mid_color="FFFFFF", end_type="num", end_value=0.3, end_color="63BE7B"))

    # 7. Weights by month
    ws = wb.create_sheet("Weights by Month")
    wt = _matrix(changes, h, journeys, "weight")
    _write_table(ws, wt, formats={c: PCT for c in wt.columns[2:]},
                 widths={"Stock": 34, "Status": 24}, freeze="C2", autofilter=False)
    ws.conditional_formatting.add(f"C2:{get_column_letter(len(wt.columns))}{len(wt) + 1}",
                                  ColorScaleRule(start_type="num", start_value=0,
                                                 start_color="FFFFFF", end_type="max",
                                                 end_color="5B9BD5"))

    # 8. Shares by month (restated to today's share basis)
    ws = wb.create_sheet("Shares by Month")
    sh = _shares_matrix(h, splits, journeys)
    _write_table(ws, sh, formats={c: NUM for c in sh.columns[2:]},
                 widths={"Stock": 34, "Status": 24} | {c: 14 for c in sh.columns[2:]},
                 freeze="C2", autofilter=False)

    # 9. Sectors
    ws = wb.create_sheet("Sectors")
    sec = sectors.copy()
    sec.columns = [ch.month_label(c) for c in sec.columns]
    sec["Change (pp)"] = (sec.iloc[:, -1] - sec.iloc[:, 0]) * 100
    sec = sec.reset_index().rename(columns={"sector": "Sector"})
    _write_table(ws, sec, formats={c: PCT1 for c in sec.columns[1:-1]} | {"Change (pp)": "+0.0;-0.0"},
                 widths={"Sector": 38}, freeze="B2", autofilter=False)
    ws.conditional_formatting.add(f"B2:{get_column_letter(len(sec.columns) - 1)}{len(sec) + 1}",
                                  ColorScaleRule(start_type="num", start_value=0,
                                                 start_color="FFFFFF", end_type="max",
                                                 end_color="5B9BD5"))

    # 10. Every change, filterable
    ws = wb.create_sheet("All Changes")
    ac = changes.assign(Month=changes["month_end"].map(ch.month_label)).rename(columns={
        "name": "Stock", "industry": "Industry", "action": "Action", "note": "What happened",
        "weight_prev": "Weight before", "weight": "Weight after",
        "weight_change_pp": "Weight change (pp)", "qty_prev_adj": "Shares before (split-adj.)",
        "qty": "Shares after", "shares_change": "Shares bought (+) / sold (-)",
        "raw_change": "Share count change", "active_change": "Change beyond inflows",
        "price_return": "Price move", "traded_value_cr": "Approx. traded value (Rs cr)",
        "value_cr": "Position value (Rs cr)", "split_factor": "Split/bonus factor",
        "arb_qty": "Also in arbitrage book (shares)"})
    ac = ac[["Month", "Stock", "Industry", "Action", "What happened", "Weight before", "Weight after",
             "Weight change (pp)", "Shares before (split-adj.)", "Shares after",
             "Shares bought (+) / sold (-)", "Share count change", "Change beyond inflows",
             "Price move", "Approx. traded value (Rs cr)", "Position value (Rs cr)",
             "Split/bonus factor", "Also in arbitrage book (shares)"]]
    _write_table(ws, ac, formats={
        "Weight before": PCT, "Weight after": PCT, "Weight change (pp)": "+0.00;-0.00",
        "Shares before (split-adj.)": NUM, "Shares after": NUM,
        "Shares bought (+) / sold (-)": "+#,##0;-#,##0;0", "Share count change": "+0.0%;-0.0%",
        "Change beyond inflows": "+0.0%;-0.0%", "Price move": "+0.0%;-0.0%",
        "Approx. traded value (Rs cr)": '+#,##0;-#,##0;0', "Position value (Rs cr)": CR,
        "Also in arbitrage book (shares)": NUM},
        widths={"Stock": 32, "Industry": 22, "Action": 24, "What happened": 90}, freeze="C2")
    for i, a in enumerate(ac["Action"], 2):
        f = _fill_for(a)
        if f:
            ws.cell(row=i, column=4).fill = f
            if _base_action(a) in DARK_ACTIONS:
                ws.cell(row=i, column=4).font = Font(color="FFFFFF", bold=True)

    # 11. Foreign
    ws = wb.create_sheet("Foreign Holdings")
    fx = h[h["bucket"] == FOREIGN]
    fq = fx.pivot_table(index="name", columns="month_end", values="quantity", aggfunc="sum")
    fw = fx.pivot_table(index="name", columns="month_end", values="weight", aggfunc="sum")
    fq.columns = fw.columns = [ch.month_label(c) for c in fq.columns]
    ws.cell(row=1, column=1, value="Shares held").font = SUB_FONT
    r = _write_table(ws, fq.reset_index().rename(columns={"name": "Stock"}), start_row=2,
                     formats={c: NUM for c in fq.columns}, widths={"Stock": 36} |
                     {c: 13 for c in fq.columns}, freeze="B3", autofilter=False)
    ws.cell(row=r + 1, column=1, value="Share of the fund").font = SUB_FONT
    _write_table(ws, fw.reset_index().rename(columns={"name": "Stock"}), start_row=r + 2,
                 formats={c: PCT for c in fw.columns}, freeze=None, autofilter=False)

    # 12. Arbitrage & futures
    ws = wb.create_sheet("Arbitrage & Futures")
    arb = h[h["bucket"] == ARBITRAGE]
    der = pd.concat([d.derivatives for d in discs], ignore_index=True)
    at = pd.DataFrame({
        "Month": [ch.month_label(m) for m in summary.month_end],
        "Arbitrage stock longs (% of fund)": summary.arbitrage.values,
        "Short futures (% of fund)": summary.short_futures.values,
        "Names in arbitrage book": [arb[arb.month_end == m]["key"].nunique()
                                    for m in summary.month_end],
        "Futures contracts (lines)": [int((der.month_end == m).sum()) for m in summary.month_end],
    })
    ws.cell(row=1, column=1, value=(
        "Arbitrage = buy the stock, short its future: the price risk cancels and the fund earns "
        "the spread, so it behaves like cash. Tracked for completeness only.")).alignment = WRAP
    ws.row_dimensions[1].height = 30
    r = _write_table(ws, at, start_row=2, formats={
        "Arbitrage stock longs (% of fund)": PCT, "Short futures (% of fund)": PCT},
        widths={c: 18 for c in at.columns}, freeze="B3", autofilter=False)
    aq = arb.assign(Month=arb.month_end.map(ch.month_label)).pivot_table(
        index="name", columns="month_end", values="weight", aggfunc="sum")
    aq.columns = [ch.month_label(c) for c in aq.columns]
    aq = aq.loc[aq.notna().sum(axis=1).sort_values(ascending=False).index]
    ws.cell(row=r + 1, column=1, value="Arbitrage book by stock (% of fund)").font = SUB_FONT
    _write_table(ws, aq.reset_index().rename(columns={"name": "Stock"}), start_row=r + 2,
                 formats={c: PCT for c in aq.columns}, freeze=None, autofilter=False)
    ws.column_dimensions["A"].width = 40

    # 13. Splits
    ws = wb.create_sheet("Splits & Bonuses")
    spl = splits.assign(Month=splits.month_end.map(ch.month_label))[
        ["Month", "name", "factor", "source", "old_isin", "new_isin", "price_before", "price_after"]
    ].rename(columns={"name": "Stock", "factor": "Factor", "source": "Source",
                      "old_isin": "Old ISIN", "new_isin": "New ISIN",
                      "price_before": "Price before", "price_after": "Price after"})
    _write_table(ws, spl, formats={"Price before": "#,##0.00", "Price after": "#,##0.00"},
                 widths={"Stock": 36, "Source": 32, "Old ISIN": 15, "New ISIN": 15})

    # 14. Raw holdings
    ws = wb.create_sheet("Raw Holdings")
    raw = h.assign(Month=h.month_end.map(ch.month_label), value_cr=h.value_lakh / 100)[
        ["Month", "bucket", "sub_type", "name", "isin", "industry", "quantity", "value_cr",
         "weight", "ytm"]]
    _write_table(ws, raw, formats={"quantity": NUM, "value_cr": "#,##0.00", "weight": "0.000%",
                                   "ytm": "0.00%"},
                 widths={"name": 50, "industry": 30, "bucket": 16, "sub_type": 20, "isin": 15})

    # 15. Read me
    ws = wb.create_sheet("Read Me", 0)
    _lines(ws, read_me(discs, splits, m0, m1))
    wb.active = 1

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def _shares_matrix(h, splits, journeys) -> pd.DataFrame:
    picks = h[h["bucket"].isin(PICK_BUCKETS)]
    t = picks.pivot_table(index="key", columns="month_end", values="quantity", aggfunc="sum")
    for r in splits.itertuples():
        if r.key in t.index:
            t.loc[r.key, [c for c in t.columns if c < r.month_end]] *= r.factor
    t = t.reindex(list(journeys["key"]))
    names = dict(zip(journeys["key"], journeys["name"]))
    t.columns = [ch.month_label(c) for c in t.columns]
    t.insert(0, "Stock", [names[k] for k in t.index])
    t.insert(1, "Status", list(journeys.set_index("key").loc[t.index, "status"]))
    return t.reset_index(drop=True)


def read_me(discs, splits, m0, m1) -> list[tuple[str, str]]:
    return [
        ("title", "How to read this workbook"),
        ("text", f"Parag Parikh Flexi Cap Fund (Direct Growth ISIN INF879O01027, AMFI scheme "
                 f"code 122639). {len(discs)} monthly portfolio disclosures, {m0} to {m1}, "
                 f"downloaded from amc.ppfas.com. Holdings are the same for every plan; only the "
                 f"NAV differs, and the Direct Growth NAV is used throughout."),
        ("text", f"Generated {dt.date.today():%d %b %Y}. Re-run: "
                 f"PYTHONPATH=. .venv/bin/python scripts/run_mf_ppfas.py"),
        ("blank", ""),
        ("sub", "Sheets"),
        ("bullet", "Year Summary: the whole year in plain English. Start here."),
        ("bullet", "Monthly Story: month by month, the fund-level picture, then every notable "
                   "decision (new, exit, big adds/trims) with a sentence each."),
        ("bullet", "Asset Mix: size, inflows, and the split between Indian stocks, foreign "
                   "stocks, REITs, arbitrage, debt and cash, plus charts."),
        ("bullet", "Stock Journeys: one row per stock ever held, with the whole year in one line."),
        ("bullet", "Decisions Grid: stock x month, coloured by what the manager did. The fastest "
                   "way to see patterns."),
        ("bullet", "Buying vs Inflows %: the same grid as numbers (see 'beyond inflows' below)."),
        ("bullet", "Weights by Month / Shares by Month: the raw history, shares restated for splits."),
        ("bullet", "Sectors, Foreign Holdings, Arbitrage & Futures, Splits & Bonuses: supporting detail."),
        ("bullet", "All Changes: every stock x month row, filterable. Raw Holdings: every line "
                   "of every disclosure, including debt."),
        ("blank", ""),
        ("sub", "Three adjustments that make the numbers honest"),
        ("bullet", "Shares, not weights. A stock going from 3% to 4% of the fund may be pure "
                   "price movement. The share count is what the manager controls."),
        ("bullet", "Inflows. When investors add money, the fund buys a bit of everything just to "
                   "stay invested. The fund's units grew by a known factor each month "
                   "(net-assets growth / NAV growth). 'Change beyond inflows' = share-count "
                   "change after removing that factor. Positive = the manager deliberately "
                   "bought more; negative = let it shrink or sold."),
        ("bullet", "Splits and bonuses. A 5-for-1 split multiplies the share count by 5 with no "
                   "buying. Detected and undone (see the 'Splits & Bonuses' sheet)"
                   + (": " + "; ".join(f"{r.name} {r.factor:g}:1 in {ch.month_label(r.month_end)}"
                                       for r in splits.itertuples()) if len(splits) else "")
                   + "."),
        ("blank", ""),
        ("sub", "Action labels (share count vs last month)"),
        ("bullet", "NEW ENTRY / EXIT: appeared / disappeared from the stock picks. '(was in "
                   "arbitrage book)' means the stock was held only as a hedged position before."),
        ("bullet", f"Added (big): >= +{ch.BIG:.0%} beyond inflows. Added: +{ch.SMALL:.0%} to "
                   f"+{ch.BIG:.0%}. Kept in step with inflows: within +/-{ch.SMALL:.0%}."),
        ("bullet", "Bought less than inflows: bought some shares, but fewer than inflows "
                   "required, so the stake shrank relative to the fund."),
        ("bullet", f"Trimmed: shares actually sold. Trimmed (big): {ch.BIG:.0%} or more sold. "
                   "No trade: share count identical."),
        ("blank", ""),
        ("sub", "Buckets"),
        ("bullet", "Indian stocks: the 'Listed' section, the manager's domestic picks."),
        ("bullet", "Arbitrage: stock bought AND its future sold. No price exposure, so it is "
                   "treated as cash-like, never as a stock pick."),
        ("bullet", "Foreign stocks: US-listed holdings (Alphabet, Meta, Microsoft, Amazon). "
                   "Their rupee value includes currency moves."),
        ("bullet", "REITs: listed real-estate trusts, counted as picks."),
        ("bullet", "Cash-like: arbitrage + CDs/commercial paper/T-bills + TREPS + the liquid "
                   "fund units + net receivables."),
        ("blank", ""),
        ("sub", "Limits"),
        ("bullet", "Month-end snapshots only. A stock bought and sold within a month never shows up."),
        ("bullet", "'Price move' is the per-share value implied by the disclosure (value / "
                   "shares), split-adjusted, and excludes dividends."),
        ("bullet", "Approx. traded value = shares bought or sold x month-end price; the actual "
                   "trade prices are not disclosed."),
        ("bullet", "Description of past behaviour, not investment advice."),
    ]
