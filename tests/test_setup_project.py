"""setup_project.py: the dataset menu, and the steps that only touch files."""
from pathlib import Path

import pytest

import setup_project as sp
from book import userfiles as uf


def test_every_dataset_runs_scripts_that_exist():
    for d in sp.DATASETS:
        assert d.minutes and d.pages and d.what
        for cmd in d.commands:
            assert (sp.ROOT / cmd[0]).exists(), cmd


def test_dataset_choices():
    ui = sp.UI(yes=True)
    starter = [d.key for d in sp.pick_datasets(ui, "starter")]
    assert starter == ["market-recent", "prices", "fundamentals", "mf"]
    everything = [d.key for d in sp.pick_datasets(ui, "all")]
    assert "market" in everything and "market-recent" not in everything
    assert sp.pick_datasets(ui, "none") == []
    assert [d.key for d in sp.pick_datasets(ui, "mf,prices")] == ["prices", "mf"]
    with pytest.raises(SystemExit):
        sp.pick_datasets(ui, "prices,bogus")


def test_ui_takes_defaults_without_a_terminal(monkeypatch):
    ui = sp.UI(yes=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert ui.ask("go?", default=False) is False
    assert ui.choose("which?", [("a", "A"), ("b", "B")], "b") == "b"


def test_templates_step_creates_workbooks_once(tmp_path, monkeypatch, capsys):
    made = sp.templates(sp.UI(yes=True), tmp_path)
    assert sorted(p.name for p in made) == ["holdings.xlsx", "mf_holdings.xlsx",
                                            "watching.xlsx"]
    assert sp.templates(sp.UI(yes=True), tmp_path) == []
    assert "already there" in capsys.readouterr().out
