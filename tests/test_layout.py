"""Every script the dashboard, the jobs and setup call by path exists, and
the root holds only the entry points."""
import re
from pathlib import Path

from ops import jobs

ROOT = Path(jobs.ROOT)


def _paths_in(text: str) -> set[str]:
    return set(re.findall(r'"((?:scripts|research|book)/\w+\.py)"', text))


def test_every_called_script_exists():
    called = set()
    for f in ["ui/service.py", "ui/picker_tab.py", "ui/results_tab.py", "setup_project.py",
              "ops/jobs.py", "scripts/run_weekly.py", "scripts/run_swing_daily.py"]:
        called |= _paths_in((ROOT / f).read_text())
    assert len(called) > 15
    missing = sorted(p for p in called if not (ROOT / p).exists())
    assert not missing, missing


def test_root_keeps_only_entry_points():
    assert sorted(p.name for p in ROOT.glob("*.py")) == ["run_ui.py", "setup_project.py"]


def test_scripts_run_without_pythonpath():
    """Each script puts the project folder on the import path itself."""
    for folder in ("scripts", "research"):
        for p in (ROOT / folder).glob("*.py"):
            if p.name != "__init__.py":
                assert "run from anywhere" in p.read_text(), p
