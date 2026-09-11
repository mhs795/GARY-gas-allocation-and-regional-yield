#!/usr/bin/env python3
"""Install GARY's Python dependencies into the current interpreter's environment.

Run this once (inside your venv) before running the model:
    python install_requirements.py
"""
import subprocess
import sys
from pathlib import Path

REQUIREMENTS = Path(__file__).parent / "requirements.txt"


def main():
    print(f"Installing dependencies from {REQUIREMENTS} into {sys.executable} ...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS)],
        check=True,
    )
    print(
        "\nDone. If you plan to use the GLPK solver backend (GARY_SOLVER=glpk), "
        "also install the system package:\n"
        "    sudo apt install glpk-utils"
    )


if __name__ == "__main__":
    main()
