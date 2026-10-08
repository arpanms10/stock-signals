"""F&O analysis for one or more underlyings: fetch, analyse, print, export.

Shared by run_fno.py (terminal and Excel) and the dashboard's F&O tab, so all
three show the same numbers.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from data.sources import nse_derivatives as nsed
from strategy import fno

REPORTS = Path(__file__).parent / "reports" / "fno"
REACH_PATH = Path(__file__).parent / "config" / "fno_reach.json"


@dataclass
class Result:
    view: fno.FnoView
    chain: nsed.Chain
    reaches: list = field(default_factory=list)     # fno.Reach for asked-for levels


CALIBRATION_PATH = Path(__file__).parent / "config" / "fno_calibration.json"


def _cfg() -> dict:
    """scoring.yaml, with the measured hit rates from the latest validation
    run (config/fno_calibration.json) over the hand-entered ones."""
    import json
    from scoring.timing import load_config
    cfg = load_config()
    if CALIBRATION_PATH.exists():
        cal = json.loads(CALIBRATION_PATH.read_text())
        rates = {kind: {int(k): v for k, v in t.items()}
                 for kind, t in cal.get("range_hit_pct", {}).items()}
        cfg.setdefault("fno", {})["range_hit_pct"] = rates
    return cfg


def reach_curve() -> dict | None:
    return fno.load_reach_curve(REACH_PATH)


def reaches(v: fno.FnoView, levels, curve: dict | None = None) -> list:
    curve = curve if curve is not None else reach_curve()
    out = [fno.reach(v.spot, v.straddle, float(x), v.kind, curve) for x in levels]
    return [x for x in out if x is not None]


def load(symbol: str, expiry: dt.date | None = None, save: bool = True,
         cfg: dict | None = None, levels=(), with_context: bool = True) -> Result:
    """Live chain + futures from NSE, analysed. Saves a snapshot by default."""
    cfg = cfg or _cfg()
    chain = nsed.option_chain(symbol, expiry)
    try:
        fut = nsed.futures(chain.symbol)
    except RuntimeError:
        fut = None      # the chain is the point; futures are context
    try:
        lot = nsed.lot_size(chain.symbol, chain.expiry)
    except RuntimeError:
        lot = None
    ctx = context(chain, cfg) if with_context else {}
    view = fno.analyse(chain.symbol, chain.strikes, chain.spot, chain.expiry,
                       chain.timestamp, today=dt.date.today(), fut=fut,
                       cfg=cfg, lot=lot, now=dt.datetime.now(),
                       curve=(curve := reach_curve()),
                       weekly=chain.expiry not in nsed.monthly_expiries(chain.expiries),
                       **ctx)
    view.conditional = fno.conditional_note(view, _conditional())
    if view.conditional:
        view.reasons.insert(1, view.conditional)
    if save:
        _save_snapshot(chain, fut)
    return Result(view, chain, reaches(view, levels, curve))


def _conditional() -> dict | None:
    import json
    if not CALIBRATION_PATH.exists():
        return None
    return json.loads(CALIBRATION_PATH.read_text()).get("equity_conditional")


def _quiet(fn, default):
    """Context is optional: a failure loses one line of the view, not the view."""
    try:
        return fn()
    except Exception as exc:          # noqa: BLE001 -- NSE fails in many ways
        print(f"warning: {getattr(fn, '__name__', 'context')}: {exc}", file=sys.stderr)
        return default


def history_for(symbol: str, before: dt.date) -> pd.DataFrame:
    """The symbol's daily rows from the F&O history (IV, closes), before a day."""
    from data import fo_bhavcopy as fb
    if not fb.DB_PATH.exists():
        return pd.DataFrame()
    con = fb.connect()
    try:
        return pd.read_sql_query(
            "SELECT date, spot, iv_straddle FROM fo_spot WHERE symbol=? AND date<? "
            "ORDER BY date", con, params=(symbol, before.isoformat()))
    finally:
        con.close()


def context(chain: nsed.Chain, cfg: dict) -> dict:
    """Everything analyse() needs beyond the chain: holidays, the IV and price
    history behind IV percentile and realised vol, the next monthly's IV for
    term structure, and announced results dates before expiry."""
    today = dt.date.today()
    hol = _quiet(lambda: nsed.holidays_between(today, chain.expiry), [])
    hist = _quiet(lambda: history_for(chain.symbol, today), pd.DataFrame())
    out = {"holidays": hol, "iv_history": None, "closes": None,
           "iv_next": None, "results": None}
    if not hist.empty:
        last = dt.date.fromisoformat(hist["date"].iloc[-1])
        # Both only from a history that reaches the last week (the Saturday
        # job keeps it there). A percentile against a stale year, or realised
        # vol from an old month, describes some other market.
        if (today - last).days <= 7:
            out["iv_history"] = hist["iv_straddle"]
            out["closes"] = pd.concat([hist["spot"], pd.Series([chain.spot])],
                                      ignore_index=True)

    def next_iv():
        nxt = [e for e in nsed.monthly_expiries(chain.expiries) if e > chain.expiry]
        if not nxt:
            return None
        c2 = nsed.option_chain(chain.symbol, nxt[0])
        return fno.straddle_iv(c2.spot, fno.straddle(c2.strikes, c2.spot),
                               (nxt[0] - today).days)
    out["iv_next"] = _quiet(next_iv, None)
    if not chain.is_index:
        from data.sources import nse_events
        out["results"] = _quiet(
            lambda: nse_events.upcoming(chain.symbol, today, chain.expiry), None)
    return out


def _save_snapshot(chain: nsed.Chain, fut: pd.DataFrame | None) -> None:
    # A failed save loses one point of history; it must not lose the analysis.
    from data import store
    try:
        con = store.connect()
        try:
            store.save_option_snapshot(con, chain, fut)
        finally:
            con.close()
    except sqlite3.Error as exc:
        print(f"warning: snapshot not saved ({exc})", file=sys.stderr)


def history(symbol: str, expiry: dt.date, since: dt.datetime | None = None,
            cfg: dict | None = None) -> pd.DataFrame:
    """PCR, walls and range at every stored snapshot of one expiry."""
    from data import store
    con = store.connect()
    try:
        snaps = store.load_option_snapshots(con, symbol, expiry, since)
    finally:
        con.close()
    return fno.history(snaps, cfg or _cfg())


# ------------------------------------------------------------------ text

def _n(x, nd=0) -> str:
    if x is None or pd.isna(x):
        return "-"
    return f"{x:,.{nd}f}"


def _px(x) -> str:
    if x is None or pd.isna(x):
        return "-"
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:,.2f}"


def _wall(w: fno.Wall | None) -> str:
    if w is None:
        return "-"
    chg = w.chg_pct
    return (f"{_px(w.strike)}  (OI {_n(w.oi)}"
            + (f", {chg:+.1f}% today" if chg is not None else "") + ")")


def _pct(p) -> str:
    return "-" if p is None else f"{100 * p:.0f}%"


def reach_line(x: fno.Reach) -> str:
    """One level's reach, in words."""
    s = (f"{_px(x.level)} ({x.distance_pct:+.1f}%, {x.moves:.1f} moves): "
         f"traded there before expiry {_pct(x.hist_touch_intraday)}, "
         f"closed there {_pct(x.hist_touch)}, "
         f"beyond it at expiry {_pct(x.hist_expiry)}")
    return s + f" (model {_pct(x.model_expiry)})"


def vol_line(c: fno.VolContext) -> str:
    """Volatility context in one line."""
    parts = [f"Straddle IV {_n(c.iv, 1)}%"]
    parts.append(f"IV percentile {_n(c.iv_pct, 0)}" if c.iv_pct is not None
                 else "IV percentile - (" + ("F&O history not current: run_fno_backtest.py "
                                             "--ingest" if c.iv_hist_n == 0 else
                                             f"{c.iv_hist_n} days of history; needs 120") + ")")
    parts.append(f"realised 20d {_n(c.rv20, 1)}{'%' if c.rv20 is not None else ''}"
                 + (f" (IV/RV {c.iv_rv:.2f})" if c.iv_rv else ""))
    parts.append(f"skew {c.skew:+.1f} pts" if c.skew is not None else "skew -")
    parts.append(f"next month {c.term:+.1f} pts" if c.term is not None else "next month -")
    return "Volatility: " + "   ".join(parts)


def text(v: fno.FnoView, levels: list | None = None) -> str:
    L = []
    head = f"{v.symbol}  spot {_px(v.spot)}"
    if v.futures:
        head += f"  ·  fut {_px(v.futures.price)} ({v.futures.basis:+,.2f})"
    head += (f"  ·  expiry {v.expiry:%d-%b-%Y} ({v.sessions} sessions)"
             f"  ·  NSE {v.timestamp:%d-%b %H:%M}")
    L.append(head)
    since = "2024" if v.kind == "equity" else "2019"
    hit = (f"   held ~{v.range_hit_pct:.0f}% of the time since {since}"
           if v.range_hit_pct is not None else "   (hit rate not measured)")
    L.append(f"Range to expiry: {_px(v.range_low)} – {_px(v.range_high)}"
             f"   = spot ± {v.range_from or '-'} {_px(v.straddle if v.range_from == 'straddle' else v.iv_move)}"
             + hit)
    if v.conditional:
        L.append("  " + v.conditional)
    L.append(f"ATM {_px(v.atm)}   ATM IV {_n(v.atm_iv, 1)}%   "
             f"1σ by IV ±{_px(v.iv_move)}")
    if v.vol:
        L.append(vol_line(v.vol))
    L.append("Positioning (no measured edge -- see docs/f_o/validation.md):")
    L.append(f"  Put wall  (support)     {_wall(v.support)}"
             + (f"   next {_px(v.supports[1].strike)}" if len(v.supports) > 1 else ""))
    L.append(f"  Call wall (resistance)  {_wall(v.resistance)}"
             + (f"   next {_px(v.resistances[1].strike)}" if len(v.resistances) > 1 else ""))
    L.append(f"  PCR (OI) {_n(v.pcr['oi'], 2)} {v.pcr_bias}   "
             f"PCR (ΔOI today) {_n(v.pcr['chg_oi'], 2)} {v.chg_pcr_bias}   "
             f"vs {'stock' if v.kind == 'equity' else 'index'} norms")
    L.append(f"  Max pain {_px(v.max_pain)}")
    if v.futures and v.futures.buildup:
        L.append(f"  Futures: price {v.futures.price_chg:+,.2f}, OI {v.futures.chg_oi:+,.0f}"
                 f" → {v.futures.buildup}")
    walls = [x for x in (v.resistance_reach, v.support_reach) if x is not None]
    if walls or levels:
        L.append("Reach (how often a move this size got there; not a forecast of direction):")
        if v.kind == "index":
            L.append("  (indices: the model has been the better-calibrated column; the "
                     "historical ones ran 4-8 points high out of sample)")
        L += [f"  {'call wall' if x.side == 'up' else 'put wall ':<9}  {reach_line(x)}"
              for x in walls]
        L += [f"  {'level':<9}  {reach_line(x)}" for x in (levels or [])]
    if v.lot:
        L.append(f"Lot size {v.lot}; OI is in contracts.")
    L.append("")
    L += [f"  - {r}" for r in v.reasons]
    if v.warnings:
        L.append("")
        L += [f"  ! {w}" for w in v.warnings]
    return "\n".join(L)


# ----------------------------------------------------------------- tables

def summary_row(v: fno.FnoView) -> dict:
    s, r = v.supports + [None] * 2, v.resistances + [None] * 2
    row = {
        "symbol": v.symbol, "nse_time": v.timestamp, "expiry": v.expiry,
        "sessions_left": v.sessions, "spot": v.spot,
        "futures": v.futures.price if v.futures else None,
        "basis": v.futures.basis if v.futures else None,
        "fut_buildup": v.futures.buildup if v.futures else None,
        "pcr_oi": v.pcr["oi"], "pcr_chg_oi": v.pcr["chg_oi"],
        "pcr_positioning": v.bias,
        "support_1": s[0].strike if s[0] else None,
        "support_2": s[1].strike if s[1] else None,
        "resistance_1": r[0].strike if r[0] else None,
        "resistance_2": r[1].strike if r[1] else None,
        "max_pain": v.max_pain, "atm_iv_pct": v.atm_iv, "straddle": v.straddle,
        "call_wall_reach_pct": (100 * v.resistance_reach.hist_touch
                                if v.resistance_reach and v.resistance_reach.hist_touch
                                is not None else None),
        "call_wall_reach_intraday_pct": (100 * v.resistance_reach.hist_touch_intraday
                                         if v.resistance_reach and v.resistance_reach
                                         .hist_touch_intraday is not None else None),
        "put_wall_reach_pct": (100 * v.support_reach.hist_touch
                               if v.support_reach and v.support_reach.hist_touch
                               is not None else None),
        "put_wall_reach_intraday_pct": (100 * v.support_reach.hist_touch_intraday
                                        if v.support_reach and v.support_reach
                                        .hist_touch_intraday is not None else None),
        "iv_move": v.iv_move, "range_low": v.range_low, "range_high": v.range_high,
        "range_from": v.range_from, "range_hit_pct": v.range_hit_pct,
        "range_hit_note": v.conditional, "lot": v.lot,
        "iv_straddle_pct": v.vol.iv if v.vol else None,
        "iv_percentile": v.vol.iv_pct if v.vol else None,
        "rv20_pct": v.vol.rv20 if v.vol else None,
        "iv_rv": v.vol.iv_rv if v.vol else None,
        "skew_pts": v.vol.skew if v.vol else None,
        "term_pts": v.vol.term if v.vol else None,
        "results_before_expiry": (", ".join(f"{d:%d-%b}" for d in v.vol.results)
                                  if v.vol and v.vol.results else None),
        "warnings": " | ".join(v.warnings),
    }
    return {k: round(x, 2) if isinstance(x, float) else x for k, x in row.items()}


def chain_table(res: Result, window_pct: float | None = None) -> pd.DataFrame:
    """The chain around spot, with each strike's role marked."""
    v, df = res.view, res.chain.strikes.copy()
    w = window_pct or fno.settings(_cfg())["wall_window_pct"]
    df = df[(df["strike"] - v.spot).abs() <= v.spot * w / 100]
    roles = {}
    for i, x in enumerate(v.supports, 1):
        roles.setdefault(x.strike, []).append(f"S{i}")
    for i, x in enumerate(v.resistances, 1):
        roles.setdefault(x.strike, []).append(f"R{i}")
    roles.setdefault(v.atm, []).append("ATM")
    if v.max_pain is not None:
        roles.setdefault(v.max_pain, []).append("max pain")
    df.insert(0, "role", df["strike"].map(lambda k: ", ".join(roles.get(k, []))))
    s = fno.settings(_cfg())
    df = fno.add_greeks(df, v.spot, v.days, s["risk_free_pct"])
    return df.reset_index(drop=True)


# ------------------------------------------------------------------ excel

def write_excel(results: list[Result], path: Path | None = None) -> Path:
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    from openpyxl.styles import Font, PatternFill

    path = Path(path or REPORTS / f"fno_{dt.datetime.now():%Y%m%d_%H%M}.xlsx")
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    bold = Font(bold=True)
    fills = {"S": PatternFill("solid", fgColor="D8F0DC"),
             "R": PatternFill("solid", fgColor="F8DADA"),
             "ATM": PatternFill("solid", fgColor="FFF2C4")}

    def autosize(ws):
        for col in ws.columns:
            width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
            ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 8), 60)

    ws = wb.active
    ws.title = "Summary"
    rows = [summary_row(r.view) for r in results]
    cols = list(rows[0])
    ws.append(cols)
    for r in rows:
        ws.append([r[c] for c in cols])
    for c in ws[1]:
        c.font = bold
    ws.freeze_panes = "B2"
    autosize(ws)
    ws.cell(len(rows) + 3, 1, "Decision support, not investment advice. "
            "OI is in contracts. Range = spot ± ATM straddle; range_hit_pct is how often the "
            "expiry close landed inside it since Jan 2024. Walls, PCR and max pain are "
            "positioning: validation found no edge (docs/f_o/validation.md). *_reach_pct: how "
            "often a move this size reached the wall on a daily close before expiry -- "
            "not a forecast of direction.")

    for res in results:
        v = res.view
        ws = wb.create_sheet(v.symbol[:31])
        ws.append([f"{v.symbol} -- expiry {v.expiry:%d-%b-%Y}, NSE {v.timestamp:%d-%b-%Y %H:%M}"])
        ws["A1"].font = Font(bold=True, size=13)
        for reason in v.reasons:
            ws.append([reason])
        for warning in v.warnings:
            ws.append(["! " + warning])
        if v.vol:
            ws.append([vol_line(v.vol)])
        for x in [v.resistance_reach, v.support_reach] + list(res.reaches):
            if x is not None:
                ws.append(["Reach " + reach_line(x)])
        ws.append([])
        top = ws.max_row + 1
        t = chain_table(res)
        ws.append(list(t.columns))
        for c in ws[top]:
            c.font = bold
        for rec in t.itertuples(index=False):
            ws.append([None if (isinstance(x, float) and pd.isna(x)) else x for x in rec])
            role = rec[0] or ""
            fill = fills.get(role[:1]) or (fills["ATM"] if "ATM" in role else None)
            if fill:
                for c in ws[ws.max_row]:
                    c.fill = fill
        ws.freeze_panes = ws.cell(top + 1, 3)
        autosize(ws)
        ws.column_dimensions["A"].width = 12

        # OI by strike: calls vs puts, the picture the walls come from.
        strike_col = list(t.columns).index("strike") + 1
        ce_col = list(t.columns).index("ce_oi") + 1
        pe_col = list(t.columns).index("pe_oi") + 1
        n = len(t)
        chart = BarChart()
        chart.title = f"{v.symbol} open interest by strike"
        chart.y_axis.title = "contracts"
        for col in (ce_col, pe_col):
            chart.add_data(Reference(ws, min_col=col, min_row=top, max_row=top + n),
                           titles_from_data=True)
        chart.set_categories(Reference(ws, min_col=strike_col, min_row=top + 1,
                                       max_row=top + n))
        chart.width, chart.height = 26, 10
        ws.add_chart(chart, ws.cell(2, len(t.columns) + 2).coordinate)

    wb.save(path)
    return path
