#!/usr/bin/env python3
"""Cross-platform run script for GARY (equivalent of run_dashboard.bat/.sh).

By default, assumes your virtual environment is already active and
dependencies are installed (see install_requirements.py). Set
AUTO_CREATE_VENV to True below if you'd rather this script create/reuse
./venv and install requirements into it automatically.

Usage:
    python run_dashboard.py
"""
import os
import subprocess
import sys
from pathlib import Path

# Set True to auto-create/reuse ./venv and install requirements into it when
# no virtual environment is active. Leave False to just use whatever
# interpreter is currently active (e.g. one you activated yourself).
AUTO_CREATE_VENV = False

ROOT = Path(__file__).parent


def main():
    python = sys.executable

    if AUTO_CREATE_VENV:
        import install_requirements as setup

        if not setup.in_virtualenv():
            python = str(setup.ensure_venv())
            setup.ensure_pip(Path(python))
            print(f"Installing dependencies from {setup.REQUIREMENTS} into {python} ...")
            subprocess.run(
                [python, "-m", "pip", "install", "-r", str(setup.REQUIREMENTS)],
                check=True,
            )

    env = os.environ.copy()
    src_dir = str(ROOT / "src")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [env.get("PYTHONPATH"), src_dir]))

    print("Launching Dashboard...")
    subprocess.run([python, str(ROOT / "src" / "dashboard.py")], env=env, check=True)


if __name__ == "__main__":
    main()
