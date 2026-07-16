#!/bin/bash
# Bake the pre-solved scenarios into one self-contained HTML file you can email
# or archive -- no server, no network. See src/export_static.py for what carries
# over from the live dashboard and what doesn't.
#
#   ./export_static.sh                 weekly-thinned, ~20 MB (default)
#   ./export_static.sh --stride 1      full daily resolution, ~100 MB
#   ./export_static.sh --no-dispatch   drop the daily dispatch chart
cd "$(dirname "$0")"
[ -d venv ] && PY=venv/bin/python || PY=python3
export PYTHONPATH="$PWD/src:$PYTHONPATH"
"$PY" src/export_static.py "$@"
