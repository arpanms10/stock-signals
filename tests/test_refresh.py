"""Price refresh: what it refreshes, how staleness is counted, how the UI reads it."""
import datetime as dt

import pytest

from data import freshness as fr
from data import store


def test_sessions_since_skips_holidays(monkeypatch):
    import data.sources.nse_derivatives as nd
    monkeypatch.setattr(nd, "holidays_between", lambda a, b: [dt.date(2026, 10, 2)])
    # Thu 1-Oct -> Thu 8-Oct: Fri 2 (holiday), Mon 5, Tue 6, Wed 7, Thu 8
    assert fr.sessions_since(dt.date(2026, 10, 1), dt.date(2026, 10, 8)) == 4


def test_sessions_since_without_calendar_counts_weekdays(monkeypatch):
    import data.sources.nse_derivatives as nd

    def boom(a, b):
        raise RuntimeError("offline")
    monkeypatch.setattr(nd, "holidays_between", boom)
    assert fr.sessions_since(dt.date(2026, 10, 1), dt.date(2026, 10, 8)) == 5


def test_default_symbols_union_and_sources(tmp_path, monkeypatch):
    import portfolio
    import run_backfill as rb
    import watchlist as wl
    con = store.connect(tmp_path / "s.db")
    con.execute("INSERT INTO prices (symbol, date, close) VALUES ('TRACKED', '2026-10-01', 1)")
    monkeypatch.setattr(wl, "all_symbols", lambda: ["WATCHED"])
    monkeypatch.setattr(portfolio, "load_holdings",
                        lambda: [{"tradingsymbol": "held"}, {"tradingsymbol": "TRACKED"}])
    syms, src = rb.default_symbols(con)
    assert syms == ["HELD", "TRACKED", "WATCHED"]
    assert src == {"watchlist": 1, "holdings": 2, "already tracked": 1}


def test_nothing_to_refresh_is_an_error(tmp_path, monkeypatch):
    import portfolio
    import run_backfill as rb
    import watchlist as wl
    real_connect = store.connect
    empty = real_connect(tmp_path / "e.db")          # no prices table rows
    monkeypatch.setattr(wl, "all_symbols", lambda: [])
    monkeypatch.setattr(portfolio, "load_holdings", lambda: [])
    monkeypatch.setattr(store, "connect", lambda *a, **k: empty)
    monkeypatch.setattr("sys.argv", ["run_backfill.py"])
    with pytest.raises(SystemExit) as e:
        rb.main()
    assert e.value.code == 1


def test_exit_code_parsing():
    from ui import service
    assert service.exit_code(["a", "\n[exit code 0]"]) == 0
    assert service.exit_code(["x", "[exit code 2]"]) == 2
    assert service.exit_code(["nothing"]) is None
    assert set(service.FIXES) == {"prices", "market", "fundamentals"}
    assert all(script in service.SCRIPTS for _, script in service.FIXES.values())


def test_price_freshness_uses_the_median_symbol(tmp_path, monkeypatch):
    import data.sources.nse_derivatives as nd
    monkeypatch.setattr(nd, "holidays_between", lambda a, b: [])
    con = store.connect(tmp_path / "p.db")
    # One symbol refreshed to Thu 8-Oct, four still at Thu 1-Oct.
    rows = [("A", "2026-10-08")] + [(s, "2026-10-01") for s in "BCDE"]
    con.executemany("INSERT INTO prices (symbol, date, close) VALUES (?,?,1)", rows)
    f = fr.assess_prices(con, [], today=dt.date(2026, 10, 8))
    assert f.latest == dt.date(2026, 10, 1) and f.label == "stale"
