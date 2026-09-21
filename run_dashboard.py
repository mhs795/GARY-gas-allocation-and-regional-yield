#!/usr/bin/env python3
"""Cross-platform run script for GARY (equivalent of run_dashboard.bat/.sh).

By default, assumes your virtual environment is already active and
dependencies are installed (see install_requirements.py). Set
AUTO_CREATE_VENV to True below if you'd rather this script create/reuse
./venv and install requirements into it automatically.

Like ./gas on Linux, this stops a GARY left running on the port before it
starts, so you always end up talking to the code in this folder rather than to
an older server that never shut down.

Usage:
    python run_dashboard.py
"""
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

# Set True to auto-create/reuse ./venv and install requirements into it when
# no virtual environment is active. Leave False to just use whatever
# interpreter is currently active (e.g. one you activated yourself).
AUTO_CREATE_VENV = False

ROOT = Path(__file__).parent
HOST = os.environ.get('GARY_HOST', '127.0.0.1')
PORT = int(os.environ.get('GARY_PORT', '8050'))
URL = f'http://{HOST}:{PORT}'


def port_in_use(host=HOST, port=PORT):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def stop_existing_server():
    """Stop a GARY still listening on the port, so this run serves this code.

    A server left over from a previous session keeps the port; the new process
    then dies on bind while the browser carries on talking to the old one, which
    looks like GARY ignoring every change you made. Returns True if the port is
    free by the end.
    """
    if not port_in_use():
        return True

    try:
        import psutil
    except ImportError:
        psutil = None

    killed = []
    if psutil is not None:
        for conn in psutil.net_connections(kind='inet'):
            if conn.status == psutil.CONN_LISTEN and conn.laddr.port == PORT and conn.pid:
                try:
                    proc = psutil.Process(conn.pid)
                    print(f"Stopping existing GARY server on :{PORT} (pid {conn.pid})...", flush=True)
                    proc.terminate()
                    killed.append(proc)
                except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                    print(f"Could not stop pid {conn.pid} on :{PORT}: {exc}", flush=True)
        if killed:
            gone, alive = psutil.wait_procs(killed, timeout=5)
            for proc in alive:
                try:
                    proc.kill()
                except psutil.NoSuchProcess:
                    pass

    for _ in range(20):
        if not port_in_use():
            return True
        time.sleep(0.25)

    print(f"\nPort {PORT} is still in use: something else is serving {URL}.", flush=True)
    print("Close it yourself, or set GARY_PORT to a free port and run this again.", flush=True)
    return False


def open_browser_when_ready():
    for _ in range(60):
        if port_in_use():
            webbrowser.open(URL)
            return
        time.sleep(0.5)


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

    if not stop_existing_server():
        sys.exit(1)

    env = os.environ.copy()
    src_dir = str(ROOT / "src")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [env.get("PYTHONPATH"), src_dir]))
    env.setdefault("GARY_HOST", HOST)
    env["GARY_PORT"] = str(PORT)

    print(f"Launching Dashboard from {ROOT.resolve()} ...", flush=True)
    proc = subprocess.Popen([python, str(ROOT / "src" / "dashboard.py")], env=env)
    open_browser_when_ready()
    sys.exit(proc.wait())


if __name__ == "__main__":
    main()
