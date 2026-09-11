#!/usr/bin/env python3
"""Cross-platform setup-and-run script for GARY (equivalent of run_dashboard.bat/.sh).

Creates the venv if it doesn't exist, installs requirements, and launches the
dashboard - no shell scripting required.

Usage:
    python run_dashboard.py
"""
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).parent
VENV_DIR = ROOT / "venv"
REQUIREMENTS = ROOT / "requirements.txt"


def venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def main():
    python = venv_python(VENV_DIR)

    if not VENV_DIR.exists():
        print(f"Creating virtual environment at {VENV_DIR} ...")
        venv.EnvBuilder(with_pip=True).create(VENV_DIR)
        print(f"Installing dependencies from {REQUIREMENTS} ...")
        subprocess.run(
            [str(python), "-m", "pip", "install", "-r", str(REQUIREMENTS)],
            check=True,
        )

    env = os.environ.copy()
    src_dir = str(ROOT / "src")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [env.get("PYTHONPATH"), src_dir]))

    print("Launching Dashboard...")
    subprocess.run([str(python), str(ROOT / "src" / "dashboard.py")], env=env, check=True)


if __name__ == "__main__":
    main()
