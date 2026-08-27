"""
Read a per-year data centre gas demand series out of a linked spreadsheet.

The data centre lever has two ways to say how much load there is (see the header
block in model.py for what the load then DOES once it is in the model, which is
identical either way):

  * the SIMPLE CELL -- one annual volume for NSW and one for VIC, switched on in
    a chosen year and held flat for the rest of the horizon. A "what if N PJ/yr
    of this showed up" lever, and still the default.

  * a LINKED SPREADSHEET -- this module. A year-by-year series per state, which
    is what an actual build-out looks like: a first site in 2029, a second in
    2032, a plateau once the campus is full. The file is not GARY's to own; it is
    whatever the analyst is keeping the pipeline in, read at solve time.

The file is read, never written. Nothing here modifies the analyst's workbook,
and GARY keeps no copy of it -- which is the point of linking rather than
importing, but also means a scenario solved from a file is only reproducible
while that file still says what it said. The FINGERPRINT below is what stops that
becoming silent: it hashes the parsed series into the scenario key, so editing a
number in the spreadsheet files the next solve under a different key instead of
overwriting a result that came from different numbers.

Accepted layouts
----------------
Wide (one column per state) -- the usual shape::

    Year,NSW,VIC
    2029,2.0,0.0
    2032,7.5,3.0
    2035,12.0,6.5

Long (one row per state-year), for a file that is a database extract::

    Year,State,PJ
    2029,NSW,2.0
    2032,NSW,7.5

Either way: ``Year`` may be a calendar year (2032) or a financial-year label
("2032-33", "FY2032"), from which the LEADING year is taken -- GARY's horizon is
calendar years, so 2032-33 is read as 2032. State columns are matched on name,
so NSW/VIC/"New South Wales"/"Victoria" all work, case-insensitively, and a
column for a state the model has no node for is ignored rather than fatal.
Volumes are PJ/yr unless the column name says TJ (e.g. "NSW (TJ)"), which is
converted.

Excel files may carry the series on any sheet: the first sheet with a usable
Year column is taken, or name one explicitly with ``path.xlsx#SheetName``.

Between and beyond the rows
---------------------------
A pipeline spreadsheet is usually sparse -- a row for the years something
changes, not all 26 of them. So:

  * BEFORE the first row: zero. The first row is when the load switches on.
  * BETWEEN rows: linear interpolation, i.e. a straight ramp between the two
    stated years. A file that means "nothing until it opens" should say so with
    an explicit zero row the year before.
  * AFTER the last row: HELD FLAT at the last value. A series ending at 2040
    means a data centre that is still there in 2050, not one that closes; the
    alternative (zero after the last row) would quietly delete load in the years
    the capacity layer is sizing for.

All three are stated in the dashboard caption and the run header, because they
are assumptions about the analyst's file, not facts in it.
"""
import hashlib
import os
import re

import pandas as pd

# State names accepted in a column header or a State column, mapped to the code
# the rest of the lever uses. Matched case-insensitively after stripping.
STATE_ALIASES = {
    'nsw': 'NSW', 'new south wales': 'NSW',
    'vic': 'VIC', 'victoria': 'VIC',
}

# Column names that hold the year, and the value in a long-format file.
_YEAR_NAMES = {'year', 'years', 'fy', 'financial year', 'financialyear', 'date'}
_STATE_NAMES = {'state', 'region', 'jurisdiction'}
_VALUE_NAMES = {'pj', 'pj/yr', 'value', 'demand', 'volume', 'gas', 'tj', 'tj/yr'}


class DataCentreSeriesError(Exception):
    """The linked file could not be read as a data centre demand series."""


def split_sheet(path):
    """``'book.xlsx#Sheet2'`` -> ``('book.xlsx', 'Sheet2')``; no '#' -> sheet None."""
    if not path:
        return '', None
    text = str(path).strip()
    if '#' in text and not os.path.exists(os.path.expanduser(text)):
        head, _, sheet = text.rpartition('#')
        if head:
            return head.strip(), sheet.strip() or None
    return text, None


def _year_of(value):
    """Calendar year out of 2032, '2032', '2032-33' or 'FY2032'. None if absent."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        year = int(value)
        return year if 1900 <= year <= 2200 else None
    if hasattr(value, 'year'):                       # a datetime the reader typed
        return int(value.year)
    digits, run = [], ''
    for ch in str(value):
        if ch.isdigit():
            run += ch
            if len(run) == 4:
                digits.append(run)
                run = ''
        else:
            run = ''
    for token in digits:                             # first 4-digit run that is a year
        year = int(token)
        if 1900 <= year <= 2200:
            return year
    return None


def _is_tj(name):
    """True if a column header marks its values as TJ rather than PJ."""
    tokens = re.split(r'[\s/_()\[\],]+', str(name).lower())
    return 'tj' in tokens or 'tjyr' in tokens


def _norm(name):
    return str(name).strip().lower().strip(' ()[]')


def _state_of(name):
    """State code for a column header or cell value, or None if it is not one."""
    text = _norm(name)
    if text in STATE_ALIASES:
        return STATE_ALIASES[text]
    # "NSW (PJ)", "NSW data centres", "Victoria PJ/yr" -- take the leading token(s).
    for alias, code in STATE_ALIASES.items():
        if text.startswith(alias + ' ') or text.startswith(alias + '('):
            return code
    return None


def _read_frames(path, sheet=None):
    """Every candidate table in the file, as a list of DataFrames."""
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.csv', '.txt', '.tsv'):
        sep = '\t' if ext == '.tsv' else None
        # comment='#' so a template can carry its own instructions in the file.
        return [pd.read_csv(path, sep=sep, engine='python', comment='#',
                            skip_blank_lines=True)]
    try:
        book = pd.read_excel(path, sheet_name=sheet)
    except ImportError as exc:                       # openpyxl / odfpy missing
        raise DataCentreSeriesError(
            f"cannot read {os.path.basename(path)}: {exc}") from exc
    except ValueError as exc:                        # named sheet not in the book
        raise DataCentreSeriesError(
            f"{os.path.basename(path)}: {exc}") from exc
    if isinstance(book, dict):
        return list(book.values())
    return [book]


def _series_from_frame(df):
    """``{state: {year: PJ}}`` from one table, or {} if it is not the right shape."""
    if df is None or df.empty:
        return {}
    cols = {c: _norm(c) for c in df.columns}
    year_col = next((c for c, n in cols.items() if n in _YEAR_NAMES), None)
    if year_col is None:                             # no year -> not our table
        return {}
    state_col = next((c for c, n in cols.items() if n in _STATE_NAMES), None)
    out = {}
    if state_col is not None:                        # long format
        value_col = next((c for c, n in cols.items()
                          if n in _VALUE_NAMES and c not in (year_col, state_col)), None)
        if value_col is None:
            return {}
        tj = _is_tj(value_col)
        for _, row in df.iterrows():
            year, state = _year_of(row[year_col]), _state_of(row[state_col])
            if year is None or state is None:
                continue
            pj = pd.to_numeric(row[value_col], errors='coerce')
            if pd.isna(pj):
                continue
            out.setdefault(state, {})[year] = float(pj) / (1000.0 if tj else 1.0)
        return out
    for col in df.columns:                           # wide format
        state = _state_of(col)
        if state is None or col == year_col:
            continue
        tj = _is_tj(col)
        for _, row in df.iterrows():
            year = _year_of(row[year_col])
            if year is None:
                continue
            pj = pd.to_numeric(row[col], errors='coerce')
            if pd.isna(pj):
                continue
            out.setdefault(state, {})[year] = float(pj) / (1000.0 if tj else 1.0)
    return out


def load(path, sheet=None):
    """Read ``path`` and return ``{state: {year: PJ/yr}}``.

    ``path`` may carry a sheet name as ``file.xlsx#SheetName``. Raises
    ``DataCentreSeriesError`` with a message meant for the sidebar if the file is
    missing, unreadable, or has no table with a Year column and a state column.
    """
    path, embedded_sheet = split_sheet(path)
    sheet = sheet or embedded_sheet
    if not path:
        return {}
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        raise DataCentreSeriesError(f"no file at {path}")
    try:
        frames = _read_frames(path, sheet)
    except DataCentreSeriesError:
        raise
    except Exception as exc:                         # unreadable file, bad encoding
        raise DataCentreSeriesError(
            f"could not read {os.path.basename(path)}: {exc}") from exc
    for df in frames:
        series = {st: yrs for st, yrs in _series_from_frame(df).items() if yrs}
        if series:
            return series
    raise DataCentreSeriesError(
        f"{os.path.basename(path)} has no table with a Year column and an "
        f"NSW or VIC column (or Year/State/PJ rows)")


def value_for(year_map, year):
    """PJ in ``year`` from one state's ``{year: PJ}``: zero before, ramp, hold after.

    Linear between stated years, zero before the first, flat at the last value
    after the last -- see the module header for why those three and not others.
    """
    if not year_map:
        return 0.0
    years = sorted(year_map)
    year = int(year)
    if year in year_map:
        return float(year_map[year])
    if year < years[0]:
        return 0.0
    if year > years[-1]:
        return float(year_map[years[-1]])
    lo = max(y for y in years if y < year)
    hi = min(y for y in years if y > year)
    lo_v, hi_v = float(year_map[lo]), float(year_map[hi])
    return lo_v + (hi_v - lo_v) * (year - lo) / (hi - lo)


def fingerprint(series):
    """Short stable hash of a parsed series, for the scenario key.

    Hashes the NUMBERS, not the file: re-saving the workbook, renaming a column
    or reordering rows keeps the key (and the cached result) it already had,
    while changing a volume files the next solve under a new key.
    """
    if not series:
        return ''
    parts = []
    for state in sorted(series):
        for year in sorted(series[state]):
            parts.append(f"{state}:{year}:{round(float(series[state][year]), 6):g}")
    return hashlib.sha1('|'.join(parts).encode()).hexdigest()[:8]


# Repo convention: every linked file is named `datacentre_demand_<something>`,
# so the shared prefix carries no information and is stripped before the label.
_LABEL_PREFIX_RE = re.compile(r'^(?:data[\s_-]*cent(?:re|er)[\s_-]*)?(?:demand[\s_-]*)?', re.I)


def label(path, fallback='Series'):
    """Short label for a linked series file, taken from its NAME.

    ``datacentre_demand_NSW.csv`` -> ``NSW``. This is what goes in the scenario
    key, so a run reads as "the NSW pipeline" rather than as an opaque hash.

    A sheet suffix joins it (``book.xlsx#Q3`` -> ``bookQ3``) so two sheets of one
    workbook do not collide. Sanitised to ``[A-Za-z0-9]``: the scenario key is
    split on ``_`` and parsed by position, so a label carrying a separator would
    break both readers.

    NOTE the trade-off against `fingerprint` below, which this replaced in the
    key: two different versions of one filename now share a key, so editing a
    volume OVERWRITES the cached result rather than filing a new one. That is the
    point -- the file names a scenario you re-run -- but it means the cache is
    only as current as the last solve of that name.
    """
    file_path, sheet = split_sheet(path or '')
    stem = os.path.splitext(os.path.basename(file_path))[0]
    name = _LABEL_PREFIX_RE.sub('', stem)
    if sheet:
        name = f'{name}{sheet}'
    return (re.sub(r'[^A-Za-z0-9]', '', name)
            or re.sub(r'[^A-Za-z0-9]', '', stem)
            or fallback)


def describe(series, max_states=2):
    """One-line summary of a parsed series, e.g. 'NSW 2->12 PJ 2029-2040'."""
    if not series:
        return 'no series'
    bits = []
    for state in sorted(series)[:max_states]:
        years = sorted(series[state])
        first, last = series[state][years[0]], series[state][years[-1]]
        bits.append(f"{state} {first:g}→{last:g} PJ {years[0]}-{years[-1]}")
    return ', '.join(bits)
