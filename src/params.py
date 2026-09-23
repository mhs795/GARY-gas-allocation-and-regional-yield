"""
Single source of truth for GARY's scalar PARAMETERS: data/gary_parameters.xlsx.

This workbook holds parameters — the numbers and short lists an analyst tunes. It
is deliberately NOT "all of GARY's inputs": the network itself is structural data
and stays in CSVs, because a node, an arc or an expansion candidate is a ROW in a
table, not a value in a cell, and rows are far easier to diff, review and source
one-per-line in plain text.

GARY's inputs therefore live in three places, by kind:

  data/gary_parameters.xlsx    PARAMETERS — scalars, levers, price anchors,
                               segment weights. Read only through this module.
  data/*.csv (committed)       STRUCTURE — nodes.csv, arcs.csv, supply.csv,
                               expansion_options.csv,
                               demand_profiles.csv, plus the raw GasBB*.CSV and
                               GSOO workbooks the generators read.
  data/*.csv (generated)       DERIVED series written by regenerate_data.py
                               (demand_*.csv, curtailment_params.csv,
                               lng_prices.csv, ...).
                               Gitignored — never edit these by hand.

Anything in the first group is set here rather than as a constant in a module, so
a parameter can be changed, reviewed and diffed in one place without touching
code. The workbook is a **committed source input** — it is not generated and
`regenerate_data.py` does not rewrite it.

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

No fallbacks
------------
Every accessor RAISES ``ParamError`` when the workbook, a sheet or a row is
missing, or a value will not parse. There are no in-code defaults. There used to
be, and they had drifted from the workbook -- the Winter lever defaulted to
1.0/1.5/2.2 against the workbook's 0.91/1.0/1.5, so a machine that could not read
the workbook (openpyxl missing was enough) silently ran the central case as a 1.5x
winter stress. A parameter lives in one place or it is not a parameter.

The workbook is read once and cached. Editing it while the dashboard is running
therefore has no effect until restart, which is deliberate: a scenario solved
half-way through a parameter change would be neither one thing nor the other.
"""
import os
import threading

import pandas as pd

DATA = os.path.join(os.path.dirname(__file__), "data")
WORKBOOK = "gary_parameters.xlsx"

_lock = threading.Lock()
_cache = {}


class ParamError(KeyError):
    """A parameter, sheet or the workbook itself is missing or unreadable."""


def _load():
    """Read every sheet once. Returns {sheet_name: DataFrame}; raises if unreadable."""
    if _cache:
        return _cache
    with _lock:
        if _cache:
            return _cache
        path = os.path.join(DATA, WORKBOOK)
        try:
            _cache.update(pd.read_excel(path, sheet_name=None))
        except (FileNotFoundError, ImportError, ValueError) as exc:
            raise ParamError(
                f"Cannot read the parameters workbook {path}: {exc}. It is a "
                f"committed source input; restore it from git, and make sure "
                f"openpyxl is installed.") from exc
        return _cache


def sheet(name):
    """One sheet as a DataFrame. Raises ParamError if the sheet is absent."""
    book = _load()
    if name not in book:
        raise ParamError(f"Sheet {name!r} is missing from {WORKBOOK}")
    return book[name]


def _raw(name):
    """Raw Value cell for a parameter. Raises ParamError if absent or blank."""
    df = sheet('Parameters')
    hit = df[df['Parameter'].astype(str) == str(name)]
    if hit.empty:
        raise ParamError(f"Parameter {name!r} is missing from the Parameters "
                         f"sheet of {WORKBOOK}")
    v = hit.iloc[0]['Value']
    if v is None or (isinstance(v, float) and pd.isna(v)):
        raise ParamError(f"Parameter {name!r} is blank in {WORKBOOK}")
    return v


def get(name):
    """Parameter as a float."""
    v = _raw(name)
    try:
        return float(v)
    except (TypeError, ValueError) as exc:
        raise ParamError(f"Parameter {name!r} = {v!r} is not a number") from exc


def get_int(name):
    """Parameter as an int."""
    return int(round(get(name)))


def get_str(name):
    """Parameter as a string."""
    return str(_raw(name)).strip()


def get_bool(name):
    """TRUE/FALSE, 1/0 or YES/NO parameter as a bool."""
    v = get_str(name).upper()
    if v in ('TRUE', '1', '1.0', 'YES'):
        return True
    if v in ('FALSE', '0', '0.0', 'NO'):
        return False
    raise ParamError(f"Parameter {name!r} = {v!r} is not TRUE/FALSE")


def get_list(name, cast=str):
    """Comma-separated parameter as a list, e.g. ``APLNG,GLNG,QCLNG``."""
    parts = [p.strip() for p in get_str(name).split(',') if p.strip()]
    try:
        return [cast(p) for p in parts]
    except (TypeError, ValueError) as exc:
        raise ParamError(f"Parameter {name!r} has an unparseable entry") from exc


def get_pairs(name, sep=':'):
    """``A:x,B:y`` parameter as a list of tuples."""
    out = []
    for part in get_str(name).split(','):
        if sep not in part:
            raise ParamError(f"Parameter {name!r}: {part!r} is not 'key{sep}value'")
        a, b = part.split(sep, 1)
        out.append((a.strip(), b.strip()))
    return out


def available():
    """True if the workbook can be read and has a Parameters sheet."""
    try:
        sheet('Parameters')
        return True
    except ParamError:
        return False
