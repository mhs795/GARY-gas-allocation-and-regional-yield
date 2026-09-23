"""Compressed, column-oriented storage for GARY scenario results.

Each solved year used to be stored as eight lists of one-dict-per-row records
(``{'Day': 1, 'Node': 'Melbourne', 'Price': 7.35}``, 365 days x 18 nodes, and so
on). Across 27 scenarios x 26 years that is ~11 million dicts, which pickled to
640 MB and cost several GB of RAM to hold. The numbers underneath are the same
few columns repeated, so this module stores each series as a DataFrame and
compresses the stream.

Frames are stored packed -- node/arc names as categoricals, values as float32 --
and are handed to callers still packed, because expanding 13.5M rows back to
object/float64 columns cost more than everything else in the load put together
(19.7s -> 2.5s to stop doing it). ``unpack`` is kept for callers that want the
wider dtypes.

Two things make it safe to compute on the packed frames directly. pandas 3
groupby is ``observed=True``, so a categorical key yields only the groups
actually present rather than the full cartesian product of categories; and
concat/merge across frames with different category sets fall back to plain
strings, preserving values. Day is stored int32 rather than int16 despite
only ever holding 1-365: the dashboard offsets it into a whole-horizon day
number, and an int16 would silently wrap if the horizon ever grew past ~2114.

``load`` sniffs the file, so old uncompressed record-list pickles still open.
"""
import gzip
import os
import pickle
import tempfile

import pandas as pd

try:
    import zstandard as zstd
except ImportError:                                   # optional; gzip is stdlib
    zstd = None

ZSTD_MAGIC = b'\x28\xb5\x2f\xfd'
GZIP_MAGIC = b'\x1f\x8b'

ZSTD_LEVEL = 3          # ~9x on GARY results at ~500 MB/s
GZIP_LEVEL = 6

# The per-day result series emitted by GasMarketModel.get_results(), and how each
# column is packed for storage. Declaring this explicitly rather than inferring
# from dtypes matters: pandas 3 reports text columns as 'str', empty ones as
# 'object', and an inferred rule mis-packs one or the other.
#   'day' -> int32   'name' -> categorical   'val' -> float32
SERIES_SCHEMA = {
    'prices':     {'Day': 'day', 'Node': 'name', 'Price': 'val'},
    'production': {'Day': 'day', 'Node': 'name', 'Potential': 'name', 'Value': 'val'},
    'flow':       {'Day': 'day', 'Arc': 'name', 'From': 'name', 'To': 'name', 'Value': 'val'},
    'storage':    {'Day': 'day', 'Node': 'name', 'Inventory': 'val', 'Injection': 'val',
                   'Withdrawal': 'val'},
    'shortage':   {'Day': 'day', 'Node': 'name', 'Value': 'val'},
    'gpg':        {'Day': 'day', 'Node': 'name', 'Demand': 'val', 'Served': 'val',
                   'Curtailed': 'val'},
    'industrial': {'Day': 'day', 'Node': 'name', 'Demand': 'val', 'Served': 'val',
                   'Curtailed': 'val'},
    # Distribution (mass-market) volume per node-day. The residential/commercial
    # segment's own weight, without which a volume-weighted price could only be
    # weighted by GPG and industrial -- 8-23% of a city node's load. Absent from
    # scenarios cached before it was added; readers must tolerate that.
    'distribution': {'Day': 'day', 'Node': 'name', 'Demand': 'val', 'Served': 'val',
                     'Curtailed': 'val'},
    # LNG exports under netback price formation: planned volume, what cleared at
    # the netback, and what was forgone. Absent with the netback lever off.
    'lng': {'Day': 'day', 'Node': 'name', 'Planned': 'val', 'Foundation': 'val',
            'Spot': 'val', 'Exported': 'val', 'Forgone': 'val'},
}
SERIES_COLUMNS = {k: list(v) for k, v in SERIES_SCHEMA.items()}

_PACKED   = {'day': 'int32',  'name': 'category', 'val': 'float32'}
_UNPACKED = {'day': 'int64',  'name': 'object',   'val': 'float64'}


# ---------------------------------------------------------------------------
# Frame packing
# ---------------------------------------------------------------------------
def _cast(df, series, dtypes):
    schema = SERIES_SCHEMA[series]
    cols = list(schema)
    if isinstance(df, pd.DataFrame):
        df = df.reindex(columns=cols)
    else:
        df = pd.DataFrame(df, columns=cols)
    return pd.DataFrame(
        {c: df[c].astype(dtypes[schema[c]]) for c in cols}, columns=cols)


def pack(df, series):
    """Shrink a result frame for storage: int16 days, categorical names, float32."""
    return _cast(df, series, _PACKED)


def unpack(df, series):
    """Widen a stored frame to object/int64/float64. Not used on the load path --
    see the module docstring -- but kept for callers that want plain dtypes."""
    return _cast(df, series, _UNPACKED)


def frames_from_year(year_results):
    """Convert one get_results() dict in place to packed frames."""
    for series in SERIES_SCHEMA:
        if series in year_results:
            year_results[series] = pack(year_results[series], series)
    return year_results


def _walk_years(obj, fn):
    """Apply fn to every per-year result dict in a saved results structure."""
    scenarios = obj.get('all_scenarios', {}) if isinstance(obj, dict) else {}
    for years in scenarios.values():
        for yr in years or []:
            if isinstance(yr, dict):
                fn(yr)
    return obj


def _normalise_in_place(year_results, expand):
    """Leave stored frames as they are; convert legacy record lists to frames."""
    for series in SERIES_SCHEMA:
        if series not in year_results:
            continue
        val = year_results[series]
        if expand:
            year_results[series] = unpack(val, series)
        elif not isinstance(val, pd.DataFrame):
            year_results[series] = pack(val, series)   # legacy record list


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------
def _umask():
    m = os.umask(0)      # no way to read it without setting it
    os.umask(m)
    return m


def _sniff(path):
    with open(path, 'rb') as f:
        head = f.read(4)
    if head.startswith(ZSTD_MAGIC):
        return 'zstd'
    if head.startswith(GZIP_MAGIC):
        return 'gzip'
    return 'raw'


def save(obj, path):
    """Write results compressed, atomically (a half-written cache is worse than none)."""
    _walk_years(obj, lambda yr: frames_from_year(yr))
    blob = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)

    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.results-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as f:
            if zstd is not None:
                f.write(zstd.ZstdCompressor(level=ZSTD_LEVEL).compress(blob))
            else:
                f.write(gzip.compress(blob, compresslevel=GZIP_LEVEL))
        # mkstemp makes the temp file 0600; the cache should keep normal file
        # permissions so anything else running as the user can still read it.
        os.chmod(tmp, 0o666 & ~_umask())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return path


def load(path, expand=False):
    """Read a results cache written in any format this project has used.

    Frames come back packed (categorical names, float32 values) unless
    ``expand=True``, which widens them to object/int64/float64.
    """
    kind = _sniff(path)
    if kind == 'zstd':
        if zstd is None:
            raise RuntimeError(
                f"{path} is zstd-compressed but the 'zstandard' package is not "
                "installed. Run: pip install zstandard")
        with open(path, 'rb') as f:
            obj = pickle.loads(zstd.ZstdDecompressor().decompress(
                f.read(), max_output_size=0))
    elif kind == 'gzip':
        with gzip.open(path, 'rb') as f:
            obj = pickle.load(f)
    else:
        with open(path, 'rb') as f:
            obj = pickle.load(f)

    _walk_years(obj, lambda yr: _normalise_in_place(yr, expand))
    return obj


# --- Provenance -------------------------------------------------------------
# A cached scenario is filed under a key built from the SCENARIO settings, which
# says nothing about the inputs or the code it was solved with. Edit supply.csv or
# the workbook and the dashboard would keep serving the old answer under the same
# key, indistinguishable from a fresh one. So every solve stamps its results with
# a fingerprint of both, and the dashboard flags a scenario whose stamp no longer
# matches the working tree.
_SRC = os.path.dirname(os.path.abspath(__file__))
# Modules whose code changes the numbers. The dashboard and the build scripts are
# left out on purpose: editing a chart must not mark every result stale.
MODEL_MODULES = ('model.py', 'capacity_model.py', 'solve.py', 'params.py',
                 'solvers.py', 'datacentre_series.py')


def _digest(paths):
    import hashlib
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(os.path.basename(p).encode())
        with open(p, 'rb') as f:
            h.update(f.read())
    return h.hexdigest()[:12]


def provenance():
    """``{'inputs': sha, 'code': sha}`` for the working tree as it stands now.

    ``inputs`` covers every CSV and the parameters workbook in src/data -- source
    and derived alike, since the derived files are what the model actually reads.
    """
    data = os.path.join(_SRC, 'data')
    inputs = [os.path.join(data, f) for f in os.listdir(data)
              if f.lower().endswith(('.csv', '.xlsx'))]
    code = [os.path.join(_SRC, f) for f in MODEL_MODULES]
    # Hashing ~64 MB takes ~0.2 s, and the dashboard asks on every header
    # refresh, so the answer is memoised on the files' sizes and mtimes.
    stamp = tuple((p, os.stat(p).st_mtime_ns, os.stat(p).st_size)
                  for p in sorted(inputs + code))
    if _PROV_MEMO.get('stamp') != stamp:
        _PROV_MEMO.update(stamp=stamp, value={'inputs': _digest(inputs),
                                              'code': _digest(code)})
    return dict(_PROV_MEMO['value'])


_PROV_MEMO = {}


def stale_reason(results, current=None):
    """Why a cached scenario no longer matches the working tree, or '' if it does."""
    meta = next((r.get('run_meta') for r in results if r.get('run_meta')), None)
    if not meta:
        return 'solved before provenance was recorded'
    current = current or provenance()
    why = [k for k in ('inputs', 'code') if meta.get(k) != current[k]]
    return ('solved with different ' + ' and '.join(why)) if why else ''
