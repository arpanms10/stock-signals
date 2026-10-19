"""Scheduled jobs: the entries each scheduler gets, built without installing."""
import xml.etree.ElementTree as ET
from pathlib import Path

from ops import jobs

ROOT = Path("/opt/stock signals")          # a space, to test quoting


def test_every_job_has_plain_words_and_a_script_that_exists():
    here = jobs.ROOT
    for j in jobs.JOBS.values():
        assert j.what and j.feeds and j.when.describe()
        assert (here / j.script).exists(), j.script
    assert [n for n, j in jobs.JOBS.items() if j.recommended] == \
        ["daily", "weekly", "monthly", "quarterly"]


def test_intraday_times_run_every_half_hour_through_the_close():
    t = jobs.JOBS["fno-intraday"].when.intraday_times()
    assert t[0] == (9, 30) and t[-1] == (15, 30) and len(t) == 13


def test_launchd_calendar_entries():
    p = jobs.plist(jobs.JOBS["weekly"], ROOT, ["--portfolio", "2000000"])
    assert p["Label"] == "local.stock-signals.weekly"
    assert p["StartCalendarInterval"] == {"Weekday": 6, "Hour": 9, "Minute": 30}
    assert p["ProgramArguments"][-2:] == ["--portfolio", "2000000"]
    q = jobs.plist(jobs.JOBS["quarterly"], ROOT)["StartCalendarInterval"]
    assert [e["Month"] for e in q] == [2, 5, 8, 11] and q[0]["Day"] == 15
    d = jobs.plist(jobs.JOBS["daily"], ROOT)["StartCalendarInterval"]
    assert {e["Weekday"] for e in d} == {1, 2, 3, 4, 5}


def test_cron_lines_are_marked_and_quoted():
    lines = jobs.cron_lines(jobs.JOBS["monthly"], ROOT)
    assert len(lines) == 1 and lines[0].startswith("0 10 5 * * cd '/opt/stock signals'")
    assert lines[0].endswith("# stock-signals:monthly")
    intra = jobs.cron_lines(jobs.JOBS["fno-intraday"], ROOT)
    assert intra[0].startswith("0 10,11,12,13,14,15 * * 1-5")
    assert intra[1].startswith("30 9,10,11,12,13,14,15 * * 1-5")
    assert jobs.cron_lines(jobs.JOBS["weekly"], ROOT)[0].startswith("30 9 * * 6 ")


def test_merge_crontab_replaces_only_our_lines():
    cur = "0 1 * * * backup.sh\n5 5 * * * old # stock-signals:daily\n"
    new = jobs.merge_crontab(cur, "daily", ["30 19 * * 1-5 run # stock-signals:daily"])
    assert new == "0 1 * * * backup.sh\n30 19 * * 1-5 run # stock-signals:daily\n"
    assert jobs.merge_crontab(new, "daily", None) == "0 1 * * * backup.sh\n"
    assert jobs.merge_crontab("", "daily", None) == ""


def _xml(job):
    raw = jobs.task_xml(job, ROOT / "reports" / "tasks" / f"{job.name}.cmd")
    return ET.fromstring(raw.split("?>", 1)[1])


def test_windows_task_xml_triggers():
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    t = _xml(jobs.JOBS["quarterly"])
    months = [m.tag.split("}")[1] for m in t.find(".//t:Months", ns)]
    assert months == ["February", "May", "August", "November"]
    assert t.find(".//t:StartWhenAvailable", ns).text == "true"
    i = _xml(jobs.JOBS["fno-intraday"])
    assert i.find(".//t:Repetition/t:Interval", ns).text == "PT30M"
    assert i.find(".//t:Repetition/t:Duration", ns).text == "PT361M"
    days = [d.tag.split("}")[1] for d in i.find(".//t:DaysOfWeek", ns)]
    assert days == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def test_windows_wrapper_sets_pythonpath_and_logs():
    cmd = jobs.windows_cmd(jobs.JOBS["monthly"], ROOT)
    assert f'set "PYTHONPATH={ROOT}"' in cmd and ">> " in cmd and "--refresh" in cmd


def test_legacy_agents_are_found_by_new_name(tmp_path):
    for n in ("com.someone.stock-signals.weekly", "com.someone.stock-signals.swing-daily",
              "local.stock-signals.daily", "com.other.thing.weekly"):
        (tmp_path / f"{n}.plist").write_text("")
    found = jobs.legacy_plists(tmp_path)
    assert set(found) == {"weekly", "daily"}
    assert found["daily"].name == "com.someone.stock-signals.swing-daily.plist"
