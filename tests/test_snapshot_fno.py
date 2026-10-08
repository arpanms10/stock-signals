"""Intraday snapshot job: market-hours gate and the end-of-day report."""
import datetime as dt
import json
from pathlib import Path

import snapshot_fno as sf
from data import store
from data.sources import nse_derivatives as d

FIX = Path(__file__).parent / "fixtures" / "fno"


def test_in_session_gate():
    hol = [dt.date(2026, 10, 20)]
    assert sf.in_session(dt.datetime(2026, 10, 8, 10, 0), hol)
    assert not sf.in_session(dt.datetime(2026, 10, 8, 9, 0), hol)       # before open
    assert not sf.in_session(dt.datetime(2026, 10, 8, 15, 45), hol)     # after close
    assert not sf.in_session(dt.datetime(2026, 10, 10, 11, 0), hol)     # Saturday
    assert not sf.in_session(dt.datetime(2026, 10, 20, 11, 0), hol)     # Diwali


def test_day_report_tracks_first_to_last(tmp_path, monkeypatch):
    con = store.connect(tmp_path / "s.db")
    monkeypatch.setattr(store, "connect", lambda *a, **k: con)
    p = json.loads((FIX / "reliance_chain.json").read_text())
    c1 = d.parse_chain(p, "RELIANCE")
    c2 = d.parse_chain(p, "RELIANCE")
    c2.timestamp = c1.timestamp + dt.timedelta(minutes=30)
    c2.spot = c1.spot * 1.01
    c2.strikes.loc[c2.strikes.strike == 1160, "pe_oi"] += 1_000_000
    store.save_option_snapshot(con, c1)
    store.save_option_snapshot(con, c2)
    summ, timeline = sf.day_frames(c1.timestamp.date())
    r = summ.iloc[0]
    assert r["symbol"] == "RELIANCE" and r["snapshots"] == 2
    assert round(r["spot_chg_pct"], 2) == 1.0
    assert r["pcr_last"] > r["pcr_first"]
    assert len(timeline) == 2


def test_keep_awake_until_close():
    import keep_awake_fno as ka
    assert ka.seconds_until_close(dt.datetime(2026, 10, 9, 9, 20)) == (6 * 60 + 15) * 60
    assert ka.seconds_until_close(dt.datetime(2026, 10, 9, 16, 0)) == 0


def test_awake_agent_runs_weekday_mornings():
    import install_schedule as i
    p = i.build_fno_awake()
    assert p["StartCalendarInterval"] == [{"Weekday": d, "Hour": 9, "Minute": 20}
                                         for d in range(1, 6)]
