"""Store an intraday snapshot of the F&O basket's chains. Meant for launchd.

The change-in-OI PCR is the one F&O read the end-of-day history cannot test:
it is about what writers did during the session. These snapshots, taken every
half hour by the agent install_schedule.py --fno-intraday installs, build the
intraday history that run_fno_backtest.py tests once there is enough of it.

Does nothing outside 09:15-15:30 IST on a trading day. Light: no next-month
chain, no results lookup -- just the chain and futures, saved with NSE's own
timestamp so a run between NSE updates replaces rather than duplicates.

    PYTHONPATH=. .venv/bin/python snapshot_fno.py
    PYTHONPATH=. .venv/bin/python snapshot_fno.py --symbols NIFTY BANKNIFTY --force

How the day went, from the stored snapshots (default: today):

    PYTHONPATH=. .venv/bin/python snapshot_fno.py --report
    PYTHONPATH=. .venv/bin/python snapshot_fno.py --report --date 2026-10-08 --excel
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys


def in_session(now: dt.datetime, holidays) -> bool:
    return (now.weekday() < 5 and now.date() not in set(holidays)
            and dt.time(9, 15) <= now.time() <= dt.time(15, 30))


def day_frames(day: dt.date):
    """(summary, timeline) for one day's snapshots, monthly expiry only.

    Per underlying, the expiry with the most snapshots that day is used:
    that is the nearest monthly the job records, and it keeps the odd
    manual run on another expiry out of the comparison."""
    import pandas as pd
    from data import store
    from strategy import fno

    con = store.connect()
    try:
        snaps = pd.read_sql_query(
            "SELECT * FROM option_snapshots WHERE ts >= ? AND ts < ? ORDER BY ts, strike",
            con, params=(day.isoformat(), (day + dt.timedelta(days=1)).isoformat()))
    finally:
        con.close()
    if snaps.empty:
        return pd.DataFrame(), pd.DataFrame()
    snaps["ts"] = pd.to_datetime(snaps["ts"])
    main_exp = (snaps.groupby(["symbol", "expiry"])["ts"].nunique()
                .reset_index().sort_values("ts").groupby("symbol").tail(1))
    timeline, rows = [], []
    for r in main_exp.itertuples():
        g = snaps[(snaps["symbol"] == r.symbol) & (snaps["expiry"] == r.expiry)]
        h = fno.history(g)
        if h.empty:
            continue
        h.insert(0, "symbol", r.symbol)
        h.insert(1, "expiry", r.expiry)
        timeline.append(h)
        a, b = h.iloc[0], h.iloc[-1]

        def chg(x, y):
            return None if x is None or y is None or pd.isna(x) or pd.isna(y) else y - x

        rows.append({
            "symbol": r.symbol, "expiry": r.expiry, "snapshots": len(h),
            "first": a["ts"].strftime("%H:%M"), "last": b["ts"].strftime("%H:%M"),
            "spot": b["spot"], "spot_chg_pct": 100 * (b["spot"] / a["spot"] - 1),
            "pcr_first": a["pcr_oi"], "pcr_last": b["pcr_oi"],
            "pcr_chg": chg(a["pcr_oi"], b["pcr_oi"]),
            "chg_oi_pcr_last": b["pcr_chg_oi"],
            "put_wall": f"{a['support']:g} -> {b['support']:g}"
            if pd.notna(a["support"]) and pd.notna(b["support"]) else None,
            "call_wall": f"{a['resistance']:g} -> {b['resistance']:g}"
            if pd.notna(a["resistance"]) and pd.notna(b["resistance"]) else None,
            "walls_moved": bool(a["support"] != b["support"] or a["resistance"] != b["resistance"]),
            "straddle_chg_pct": (100 * (b["straddle"] / a["straddle"] - 1)
                                 if a["straddle"] and b["straddle"] else None),
        })
    summ = pd.DataFrame(rows)
    order = {s: i for i, s in enumerate(["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"])}
    summ = summ.sort_values(by="symbol", key=lambda x: x.map(lambda s: (order.get(s, 9), s)))
    return summ.reset_index(drop=True), pd.concat(timeline, ignore_index=True)


def report(day: dt.date, excel: bool = False) -> int:
    from pathlib import Path
    summ, timeline = day_frames(day)
    if summ.empty:
        print(f"No snapshots stored for {day}.")
        return 1
    times = sorted(timeline["ts"].dt.strftime("%H:%M").unique())
    print(f"F&O intraday, {day:%a %d-%b-%Y}: {len(summ)} underlyings, NSE update times "
          f"{times[0]}-{times[-1]} ({len(times)} distinct)")
    print("Change-in-OI PCR is untested (docs/f_o/validation.md) -- this is the data "
          "that will test it, not a signal.\n")
    cols = ["symbol", "snapshots", "first", "last", "spot", "spot_chg_pct", "pcr_first",
            "pcr_last", "chg_oi_pcr_last", "put_wall", "call_wall", "straddle_chg_pct"]
    print(summ[cols].round(2).to_string(index=False))
    moved = summ[summ["walls_moved"]]
    if len(moved):
        print(f"\nWalls moved during the day: {', '.join(moved['symbol'])}")
    if excel:
        out = Path(__file__).parent / "reports" / "fno" / f"intraday_{day:%Y%m%d}.xlsx"
        out.parent.mkdir(parents=True, exist_ok=True)
        import pandas as pd
        with pd.ExcelWriter(out) as xw:
            summ.round(3).to_excel(xw, sheet_name="Summary", index=False)
            t = timeline.copy()
            t["ts"] = t["ts"].dt.strftime("%Y-%m-%d %H:%M:%S")
            t.round(3).to_excel(xw, sheet_name="Timeline", index=False)
        print(f"\nExcel: {out}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--symbols", nargs="+", default=None,
                    help="default: the record_fno.py basket")
    ap.add_argument("--force", action="store_true", help="run outside market hours")
    ap.add_argument("--report", action="store_true",
                    help="summarise a day's stored snapshots instead of taking one")
    ap.add_argument("--date", type=dt.date.fromisoformat, default=None,
                    help="with --report: YYYY-MM-DD (default today)")
    ap.add_argument("--excel", action="store_true", help="with --report: also write Excel")
    a = ap.parse_args(argv)
    if a.report:
        return report(a.date or dt.date.today(), a.excel)

    import fno_report
    import record_fno
    from data.sources import nse_derivatives as nsed

    now = dt.datetime.now()
    try:
        hol = nsed.trading_holidays(now.year)
    except RuntimeError:
        hol = []
    if not a.force and not in_session(now, hol):
        print(f"{now:%Y-%m-%d %H:%M}: market closed, nothing to do.")
        return 0
    symbols = [s.upper() for s in (a.symbols or record_fno.default_symbols())]
    ok = 0
    for sym in symbols:
        try:
            fno_report.load(sym, save=True, with_context=False)
            ok += 1
        except (nsed.NotFnO, ValueError, RuntimeError) as exc:
            print(f"  {sym}: {exc}", file=sys.stderr)
    print(f"{now:%Y-%m-%d %H:%M}: {ok}/{len(symbols)} snapshots stored.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
