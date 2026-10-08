"""F&O analysis: PCR, OI support and resistance, expected range to expiry.

    PYTHONPATH=. .venv/bin/python run_fno.py NIFTY
    PYTHONPATH=. .venv/bin/python run_fno.py NIFTY BANKNIFTY RELIANCE --excel
    PYTHONPATH=. .venv/bin/python run_fno.py RELIANCE --expiry 2026-11-23
    PYTHONPATH=. .venv/bin/python run_fno.py NIFTY --excel reports/nifty.xlsx
    PYTHONPATH=. .venv/bin/python run_fno.py RELIANCE --level 1250 1150
    PYTHONPATH=. .venv/bin/python run_fno.py NIFTY --option 22000PE --lots 2
    PYTHONPATH=. .venv/bin/python run_fno.py --selftest     # are NSE's endpoints still working?

Defaults to the nearest MONTHLY expiry -- NIFTY's weeklies are skipped unless
named with --expiry. Each run saves a snapshot of the chain to the local
database (--no-save to skip), which is what the dashboard's intraday PCR
history reads.

Decision support, not investment advice.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import fno_report
from data.sources.nse_derivatives import NotFnO


def selftest() -> int:
    """Check every live NSE endpoint answers with something usable. NSE
    changed its option-chain API once already (2025) and one retired endpoint
    answers 200 with an empty body -- so 'no error' is not 'working'."""
    import datetime as dt
    from data.sources import nse_derivatives as nsed
    from strategy import fno

    checks = []

    def check(name, fn):
        try:
            ok, detail = fn()
        except Exception as exc:       # noqa: BLE001 -- report, don't crash
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        checks.append(ok)
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}: {detail}")

    def universe():
        u = nsed.fno_underlyings(max_age_days=0)
        return len(u) >= 150 and "NIFTY" in set(u.symbol), f"{len(u)} underlyings"

    def chain(sym, min_strikes):
        def run():
            c = nsed.option_chain(sym)
            s = c.strikes
            oi = s.ce_oi.sum() + s.pe_oi.sum()
            ok = (len(s) >= min_strikes and s.strike.min() < c.spot < s.strike.max()
                  and oi > 0 and fno.straddle(s, c.spot) is not None)
            return ok, (f"{len(s)} strikes, spot {c.spot:,.2f}, OI {oi:,.0f}, "
                        f"expiry {c.expiry}, NSE {c.timestamp:%d-%b %H:%M}")
        return run

    def futures():
        f = nsed.futures("NIFTY")
        return len(f) >= 1 and (f.ltp > 0).all(), f"{len(f)} NIFTY futures"

    def lots():
        n = nsed.lot_size("NIFTY", nsed.nearest_monthly(nsed.expiries("NIFTY"),
                                                        dt.date.today()))
        return bool(n and n > 0), f"NIFTY lot {n}"

    def holidays():
        h = nsed.trading_holidays()
        return len(h) >= 5, f"{len(h)} F&O holidays this year"

    print("F&O self-test against live NSE:")
    check("F&O universe", universe)
    check("NIFTY chain", chain("NIFTY", 50))
    check("RELIANCE chain", chain("RELIANCE", 20))
    check("futures quote", futures)
    check("lot sizes", lots)
    check("holiday calendar", holidays)
    bad = checks.count(False)
    print(f"{len(checks) - bad}/{len(checks)} passed.")
    return 1 if bad else 0


def main(argv=None) -> int:
    if argv is None and sys.argv[1:] == ["--selftest"]:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("symbols", nargs="+", help="NIFTY, BANKNIFTY, RELIANCE, ...")
    ap.add_argument("--expiry", type=dt.date.fromisoformat, default=None,
                    help="YYYY-MM-DD; default: nearest monthly expiry")
    ap.add_argument("--excel", nargs="?", const="", default=None, metavar="PATH",
                    help="also write an Excel report (default reports/fno/fno_<time>.xlsx)")
    ap.add_argument("--level", type=float, nargs="+", default=(), metavar="PRICE",
                    help="how often a move to each price was reached before expiry "
                         "(one symbol only)")
    ap.add_argument("--option", default=None, metavar="STRIKE{CE|PE}",
                    help="check one contract, e.g. 22000PE (one symbol only)")
    ap.add_argument("--lots", type=int, default=1, help="with --option")
    ap.add_argument("--no-save", action="store_true",
                    help="do not store a snapshot of the chain")
    a = ap.parse_args(argv)
    if (a.level or a.option) and len(a.symbols) > 1:
        ap.error("--level/--option need a single symbol: a price only means something for one")
    opt = None
    if a.option:
        import re
        m = re.fullmatch(r"([\d.]+)\s*(CE|PE)", a.option.strip().upper())
        if not m:
            ap.error("--option looks like 22000PE or 1250CE")
        opt = (float(m.group(1)), m.group(2))

    results, failed = [], 0
    for sym in a.symbols:
        try:
            res = fno_report.load(sym, a.expiry, save=not a.no_save, levels=a.level)
        except (NotFnO, ValueError, RuntimeError) as exc:
            print(f"{sym.upper()}: {exc}\n", file=sys.stderr)
            failed += 1
            continue
        results.append(res)
        print(fno_report.text(res.view, res.reaches))
        print()
        if opt:
            from strategy import fno as _fno
            oc = _fno.option_check(res.view, res.chain.strikes, opt[0], opt[1], a.lots,
                                   fno_report.reach_curve(),
                                   _fno.settings(fno_report._cfg())["risk_free_pct"])
            if oc is None:
                print(f"No {opt[0]:g} strike in this chain.")
            else:
                print(fno_report.option_text(oc, res.view.kind))
            print()

    if a.excel is not None and results:
        path = fno_report.write_excel(results, Path(a.excel) if a.excel else None)
        print(f"Excel report: {path}")
    print("Decision support, not investment advice.")
    return 1 if failed and not results else 0


if __name__ == "__main__":
    sys.exit(main())
