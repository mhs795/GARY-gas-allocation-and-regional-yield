#!/usr/bin/env python3
"""Install GARY's Python dependencies.

If you're not already inside a virtual environment, this creates one at
./venv (reusing it if it already exists) and installs into that, instead of
touching your system Python.

Run:
    python install_requirements.py

If a venv gets created, activate it afterwards before running the model:
    source venv/bin/activate      (Linux/macOS)
    venv\\Scripts\\activate         (Windows)
"""
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).parent
REQUIREMENTS = ROOT / "requirements.txt"
VENV_DIR = ROOT / "venv"


def in_virtualenv() -> bool:
    return sys.prefix != sys.base_prefix


def venv_python(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def ensure_venv() -> Path:
    python = venv_python(VENV_DIR)
    if python.exists():
        print(f"Using existing virtual environment at {VENV_DIR}")
        return python

    print(f"No active virtual environment detected; creating one at {VENV_DIR} ...")
    try:
        venv.create(VENV_DIR, with_pip=True)
    except Exception as exc:
        sys.exit(
            f"Failed to create a virtual environment at {VENV_DIR}: {exc}\n"
            "On Debian/Ubuntu you may need: sudo apt install python3-venv\n"
            "Alternatively, activate a venv yourself first and re-run this script."
        )
    return python


def ensure_pip(python: Path):
    check = subprocess.run([str(python), "-c", "import pip"], capture_output=True)
    if check.returncode != 0:
        print("pip not found in this interpreter; bootstrapping with ensurepip ...")
        subprocess.run([str(python), "-m", "ensurepip", "--upgrade"], check=True)


def main():
    if in_virtualenv():
        python = Path(sys.executable)
    else:
        python = ensure_venv()

    ensure_pip(python)

    print(f"Installing dependencies from {REQUIREMENTS} into {python} ...")
    subprocess.run(
        [str(python), "-m", "pip", "install", "-r", str(REQUIREMENTS)],
        check=True,
    )
    print(
        "\nDone. If you plan to use the GLPK solver backend (GARY_SOLVER=glpk), "
        "also install the system package:\n"
        "    sudo apt install glpk-utils"
    )

    if not in_virtualenv():
        activate_hint = (
            f"{VENV_DIR}\\Scripts\\activate"
            if sys.platform == "win32"
            else f"source {VENV_DIR}/bin/activate"
        )
        print(
            f"\nA virtual environment was created at {VENV_DIR}.\n"
            f"Activate it before running the model:\n    {activate_hint}"
        )


if __name__ == "__main__":
    main()
