"""Launch the dashboard.

    python run_ui.py

Starts Streamlit on http://localhost:8501. The UI imports the framework
directly -- there is no API server and no second process to keep running.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    app = ROOT / "ui" / "app.py"
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    cmd = [sys.executable, "-m", "streamlit", "run", str(app),
           "--server.headless", "false",
           "--browser.gatherUsageStats", "false",
           # Local only. This shows a real portfolio; it has no business
           # listening on anything but the loopback interface.
           "--server.address", "localhost"]
    try:
        subprocess.run(cmd, cwd=str(ROOT), env=env, check=False)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
