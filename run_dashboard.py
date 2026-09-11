#!/usr/bin/env python3
"""Cross-platform run script for GARY (equivalent of run_dashboard.bat/.sh).

Assumes your virtual environment is already active and dependencies are
installed (see install_requirements.py). Just sets PYTHONPATH and launches
the dashboard - no shell scripting required.

Usage:
    python run_dashboard.py
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent


def main():
    env = os.environ.copy()
    src_dir = str(ROOT / "src")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [env.get("PYTHONPATH"), src_dir]))

    print("Launching Dashboard...")
    subprocess.run([sys.executable, str(ROOT / "src" / "dashboard.py")], env=env, check=True)


if __name__ == "__main__":
    main()
