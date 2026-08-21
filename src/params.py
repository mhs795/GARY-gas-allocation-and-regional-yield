"""
Single source of truth for GARY's model parameters: data/gary_inputs.xlsx.

Every economic and structural parameter the model uses lives on a sheet in that
workbook rather than as a constant in a module, so a parameter can be changed,
reviewed and diffed in one place without touching code. The workbook is a
**committed source input** — it is not generated and `regenerate_data.py` does not
rewrite it.

Sheets
------
``Parameters``       scalars and short lists: one row per parameter, with Group,
                     Value, Unit and Source. ``Value`` is deliberately untyped —
                     accessors below coerce, so a node name and a price can sit in
                     the same column.
``Scenario_Levers``  the Winter and LNG demand multipliers and the years they
                     apply over.
``LNG_Anchors``      ACIL Allen's per-scenario Brent / LNG price / spot share
                     anchors (Tables B.2, B.3, B.4).
``Segment_Weights``  ACIL Allen's contract/spot weights per customer segment.

Fallbacks
---------
Every accessor takes a default and uses it when the workbook, sheet or row is
missing. That is what lets a clone without the workbook still run, and it means a
typo in a parameter name silently returns the default rather than crashing — so
``check()`` exists to list what a caller asked for that the workbook did not have,
and ``build_inputs_workbook.py --check`` runs it.

The workbook is read once and cached. Editing it while the dashboard is running
therefore has no effect until restart, which is deliberate: a scenario solved
half-way through a parameter change would be neither one thing nor the other.
"""
import os
import threading

import pandas as pd

DATA = os.path.join(os.path.dirname(__file__), "data")
WORKBOOK = "gary_inputs.xlsx"

_lock = threading.Lock()
_cache = {}
_missing = set()


def _load():
    """Read every sheet once. Returns {sheet_name: DataFrame}, empty if absent."""
    if _cache:
        return _cache
    with _lock:
        if _cache:
            return _cache
        path = os.path.join(DATA, WORKBOOK)
        try:
            _cache.update(pd.read_excel(path, sheet_name=None))
        except (FileNotFoundError, ImportError, ValueError):
            _cache['__absent__'] = pd.DataFrame()
        return _cache


def sheet(name):
    """One sheet as a DataFrame; empty DataFrame if the workbook or sheet is absent."""
    return _load().get(name, pd.DataFrame())


def _raw(name):
    """Raw Value cell for a parameter, or None."""
    df = sheet('Parameters')
    if df.empty or 'Parameter' not in df.columns:
        return None
    hit = df[df['Parameter'].astype(str) == str(name)]
    if hit.empty:
        return None
    return hit.iloc[0]['Value']


def get(name, default):
    """Parameter as a float. Falls back to ``default`` (and records the miss)."""
    v = _raw(name)
    if v is None or (isinstance(v, float) and pd.isna(v)):
        _missing.add(name)
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        _missing.add(name)
        return default


def get_int(name, default):
    """Parameter as an int."""
    return int(round(get(name, default)))


def get_str(name, default):
    """Parameter as a string."""
    v = _raw(name)
    if v is None or (isinstance(v, float) and pd.isna(v)):
        _missing.add(name)
        return default
    return str(v).strip()


def get_list(name, default, cast=str):
    """Comma-separated parameter as a list, e.g. ``APLNG,GLNG,QCLNG``."""
    v = _raw(name)
    if v is None or (isinstance(v, float) and pd.isna(v)):
        _missing.add(name)
        return list(default)
    parts = [p.strip() for p in str(v).split(',') if p.strip()]
    try:
        return [cast(p) for p in parts]
    except (TypeError, ValueError):
        _missing.add(name)
        return list(default)


def get_pairs(name, default, sep=':'):
    """``A:x,B:y`` parameter as a list of tuples."""
    v = _raw(name)
    if v is None or (isinstance(v, float) and pd.isna(v)):
        _missing.add(name)
        return list(default)
    out = []
    for part in str(v).split(','):
        if sep in part:
            a, b = part.split(sep, 1)
            out.append((a.strip(), b.strip()))
    return out or list(default)


def available():
    """True if the workbook was found and has a Parameters sheet."""
    return not sheet('Parameters').empty


def missing():
    """Parameter names that were asked for but not found, so far this process."""
    return sorted(_missing)
