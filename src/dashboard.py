import base64
import os, re, sys
import logging
# Background callbacks poll the server twice a second while a solve runs, so
# werkzeug's per-request line buries the solve output. Errors still get through.
logging.getLogger('werkzeug').setLevel(logging.ERROR)
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots
import diskcache
import dash
from dash import dcc, html, Input, Output, State, DiskcacheManager, no_update, ctx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import datacentre_series
import acil_segment_prices
import params as P
import results_io
import supply_curve as sc
from model import RESERVATION_LEVELS, VOLL_PER_GJ, lng_foundation_share
from solve import solve_scenario
from regenerate_data import regenerate_all
from sweep import run_jobs, default_workers

# ---------------------------------------------------------------------------
# Chart-data download (Excel)
# ---------------------------------------------------------------------------
# Charts whose figure can be downloaded as an .xlsx: (button id, graph id, filename).
CHART_DL = [
    ('dl-prod-annual',   'prod-annual-graph',      'production_annual'),
    ('dl-prod-dispatch', 'prod-dispatch-graph',    'production_dispatch'),
    ('dl-flow',          'flow-graph',             'pipeline_flows'),
    ('dl-storage',       'storage-inventory-graph', 'storage_inventory'),
    ('dl-price-high',    'price-high-graph',       'prices_high_demand'),
    ('dl-price-low',     'price-low-graph',        'prices_low_demand'),
    ('dl-price-q-high',  'price-q-high-graph',     'prices_high_demand_quarterly'),
    ('dl-price-q-low',   'price-q-low-graph',      'prices_low_demand_quarterly'),
    ('dl-price-a-high',  'price-a-high-graph',     'prices_high_demand_annual'),
    ('dl-price-a-low',   'price-a-low-graph',      'prices_low_demand_annual'),
    ('dl-ind',           'ind-graph',              'gpg_large_users'),
]


def _trace_array(v):
    """Read one trace's x/y into a plain list, whatever shape it arrives in.

    Plotly 6 no longer puts numeric arrays in the figure as JSON lists: it
    encodes them as {'dtype': 'f4', 'bdata': '<base64>'} typed arrays. Taking
    len() of that dict returns 2 -- the key count -- which is how every chart
    download silently turned into two rows reading "dtype" and "bdata". A
    figure round-tripped through the browser can also come back with a typed
    array serialised as an index-keyed object, so that form is decoded too.
    """
    if v is None:
        return None
    if isinstance(v, dict):
        bdata = v.get('bdata')
        if bdata is not None:
            dtype = str(v.get('dtype') or 'f8')
            shape = v.get('shape')
            if shape and len([d for d in str(shape).split(',') if d.strip()]) > 1:
                return None      # 2-D data has no single column to become
            # Plotly writes little-endian; say so rather than trust the host.
            np_dtype = np.dtype(('<' + dtype) if len(dtype) == 2 else dtype)
            return np.frombuffer(base64.b64decode(bdata), dtype=np_dtype).tolist()
        # {"0": .., "1": ..} -- a JSON-serialised typed array
        if v and all(str(k).lstrip('-').isdigit() for k in v):
            return [v[k] for k in sorted(v, key=lambda k: int(k))]
        return None
    return list(v)


def _fig_dict_to_df(fig):
    """Pull a Plotly figure dict's plotted series into a tidy wide table.

    A shared x (usually the date/year) becomes the first column and each trace's y
    becomes a column named after the trace; long traces are sampled down.
    """
    if not fig or not fig.get('data'):
        return pd.DataFrame()
    series, xref, seen = [], None, {}
    for i, tr in enumerate(fig['data']):
        y = _trace_array(tr.get('y'))
        if not y or all(v is None for v in y):
            continue
        x = _trace_array(tr.get('x'))
        if x is not None and len(x) == len(y) and xref is None:
            xref = x
        if len(y) > 5000:
            step = int(np.ceil(len(y) / 5000))
            # Sample the shared x on the same stride, or the two stop lining up
            # and the x column is dropped for a length mismatch.
            if xref is not None and len(xref) == len(y):
                xref = xref[::step]
            y = y[::step]
        name = str(tr.get('name') or f'series_{i + 1}').strip()
        if name in seen:
            seen[name] += 1
            name = f'{name} ({seen[name]})'
        else:
            seen[name] = 0
        series.append((name, pd.Series(y)))
    if not series:
        return pd.DataFrame()
    # Concat the renamed Series, not a dict: a dict makes pandas sort the column
    # names, which scatters the traces out of the order they are drawn.
    df = pd.concat([s.rename(n) for n, s in series], axis=1)
    if xref is not None and len(xref) == len(df):
        df.insert(0, 'x', xref)
    return df


def _dl_btn(btn_id):
    """Small right-aligned 'download chart data as Excel' button for a chart."""
    return html.Div(
        html.Button('⬇ Data (Excel)', id=btn_id, className='chart-dl-btn'),
        style={'textAlign': 'right', 'margin': '2px 4px 12px'})


# ---------------------------------------------------------------------------
# Background callback manager
# ---------------------------------------------------------------------------
_cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tmp', 'cache')
os.makedirs(_cache_dir, exist_ok=True)
_disk_cache = diskcache.Cache(_cache_dir)
background_callback_manager = DiskcacheManager(_disk_cache)


def _progress(pct, label):
    """(style, children) pair for the solver-progress-bar div's progress= outputs."""
    return {'width': f'{pct}%'}, label


def dataframe_to_table(df, style=None):
    """Plain html.Table equivalent of dbc.Table.from_dataframe (striped/hover via CSS).

    Numeric-looking columns (real number dtypes, or strings that are all digits/
    commas/decimals or the '—' missing-value placeholder) are centre-aligned;
    everything else stays left-aligned.
    """
    def _is_numeric_col(col):
        vals = df[col]
        if pd.api.types.is_numeric_dtype(vals):
            return True
        return all(str(v).strip() == '—' or re.fullmatch(r'-?[\d,]+\.?\d*', str(v).strip())
                   for v in vals)
    centered = {col for col in df.columns if _is_numeric_col(col)}
    center_style = {'textAlign': 'center'}
    return html.Table([
        html.Thead(html.Tr([html.Th(col, style=center_style if col in centered else None)
                            for col in df.columns])),
        html.Tbody([
            html.Tr([html.Td(v, style=center_style if col in centered else None)
                    for col, v in zip(df.columns, row)])
            for row in df.itertuples(index=False)
        ]),
    ], style=style)


def sort_display_df(df, col, ascending=True):
    """Sort a display dataframe by one column, robust to '$M'-formatted strings.

    Table cells are already rendered strings ('1,451.9', '—') by the time a
    table reaches this, so a plain sort_values would order them
    lexicographically ('100' before '99'). Parses each value back to a float
    where possible, sends the '—' placeholder (no CapEx / no cost basis) to
    the end regardless of direction -- the usual spreadsheet convention, via
    na_position='last' -- and falls back to case-insensitive text sorting for
    genuinely non-numeric columns (Project, Type).
    """
    if col not in df.columns:
        return df
    def parse(v):
        s = str(v).strip()
        if s in ('—', '', 'nan', 'None'):
            return np.nan
        try:
            return float(s.replace(',', ''))
        except ValueError:
            return s.lower()
    keys = df[col].map(parse)
    order = keys.sort_values(ascending=ascending, na_position='last', kind='mergesort').index
    return df.loc[order].reset_index(drop=True)

# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------
RESULTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "precalculated_results.pkl")

_results_mem = {'data': None, 'mtime': -1.0}

def load_results():
    try:
        mtime = os.path.getmtime(RESULTS_FILE)
    except FileNotFoundError:
        return {'all_scenarios': {}, 'current_key': None}
    if _results_mem['data'] is None or mtime > _results_mem['mtime']:
        try:
            _results_mem['data'] = results_io.load(RESULTS_FILE)
            _results_mem['mtime'] = mtime
            _map_fig_cache.clear()   # invalidate map cache when data changes
        except Exception:
            pass
    return _results_mem['data'] or {'all_scenarios': {}, 'current_key': None}

def save_results(data):
    results_io.save(data, RESULTS_FILE)

# Map figure cache — keyed by (key, end_year, map_year, options_tuple)
_map_fig_cache: dict = {}

def load_static_data():
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    return {
        'nodes':     pd.read_csv(os.path.join(d, "nodes.csv")),
        'arcs':      pd.read_csv(os.path.join(d, "arcs.csv")),
        'expansion': pd.read_csv(os.path.join(d, "expansion_options.csv")),
        'supply':    pd.read_csv(os.path.join(d, "supply.csv")),
        'gpg_facs':  _safe_csv(os.path.join(d, "gpg_facilities.csv")),
        'ind_bbg':   _safe_csv(os.path.join(d, "industrial_facilities_bbg.csv")),
    }

def _safe_csv(path):
    try:
        return pd.read_csv(path)
    except FileNotFoundError:
        return pd.DataFrame()

static_data = load_static_data()

# ---------------------------------------------------------------------------
# Geographic constants
# ---------------------------------------------------------------------------
# Sidebar switch defaults that are parameters rather than code. Netback pricing is
# ON by default: it is ACIL Allen's methodology, the one behind the 2026 GSOO, and
# the only mode in which the international price disciplines domestic prices --
# with it off the LNG lever scales export VOLUME instead, which at High pushes
# planned exports past physical liquefaction nameplate and reports the excess as
# domestic lost load at VOLL.
NETBACK_DEFAULT = str(P.get_str('netback_pricing_default', 'TRUE')).strip().upper() in ('TRUE', '1', 'YES')
IMPORTS_DEFAULT = str(P.get_str('allow_import_terminals', 'TRUE')).strip().upper() in ('TRUE', '1', 'YES')

# Default contents of the "link a demand series" box. Empty ships the flat-cell
# lever exactly as it was; set datacentre_series_path in the parameters workbook
# to a file an analyst keeps a live pipeline in and the box comes up pointing at
# it. The path is a default, not a lock -- it is editable in the sidebar, and a
# missing file is reported there rather than being fatal at startup.
DC_FILE_DEFAULT = str(P.get_str('datacentre_series_path', 'none') or '').strip()
if DC_FILE_DEFAULT.lower() in ('none', 'nan', '-'):     # workbook's way of saying unset
    DC_FILE_DEFAULT = ''

COORDS = {
    'Surat':          [-27.15, 149.07], 'Moomba':        [-28.1,  140.2],
    'Gippsland':      [-38.5,  147.0],  'Sydney':        [-33.86, 151.2],
    'Melbourne':      [-37.81, 144.96], 'Adelaide':      [-34.92, 138.6],
    'Brisbane':       [-27.47, 153.02], 'Gladstone':     [-23.84, 151.26],
    'APLNG':          [-23.76, 151.20], 'GLNG':          [-23.80, 151.25],
    'QCLNG':          [-23.84, 151.30], 'Port_Kembla':   [-34.45, 150.9],
    'Iona':           [-38.55, 142.9],  'Silver_Springs':[-27.4,  149.2],
    'Geelong':        [-38.10, 144.42],
    # Northern Territory
    'Amadeus':        [-23.85, 132.30], 'Beetaloo':      [-16.55, 133.85],
    'Darwin':         [-12.46, 130.84], 'Tennant_Creek': [-19.65, 134.19],
    'Blacktip':       [-14.23, 129.52], 'Daly_Waters':   [-16.30, 133.37],
}

ARC_WAYPOINTS = {
    'MSP':  [[-28.1097,140.2001],[-28.1166,140.2045],[-28.1309,140.2172],[-28.1768,140.2852],[-28.2480,140.3887],[-28.2821,140.4336],[-28.3949,140.5805],[-28.4980,140.7165],[-28.6402,140.9238],[-28.6894,140.9986],[-28.9991,141.4535],[-29.4139,142.0680],[-29.8628,142.5944],[-30.4452,143.3120],[-31.1498,144.1407],[-31.8720,145.0251],[-32.4138,145.7587],[-32.6850,146.1474],[-33.2834,146.9151],[-33.7892,147.5283],[-34.1256,148.1204],[-34.2388,148.3260],[-34.5505,148.9043],[-34.7186,149.2069],[-34.7551,149.3703],[-34.7298,149.5646],[-34.7253,149.6656],[-34.6957,149.9614],[-34.6471,150.0880],[-34.6063,150.2341],[-34.5353,150.4044],[-34.4908,150.4786],[-34.3596,150.5491],[-34.2916,150.6113],[-34.2329,150.7106],[-34.1551,150.7716],[-34.0117,150.7923],[-33.8879,150.8229],[-33.8322,150.8640]],
    'EGP':  [[-38.2085,147.1644],[-38.1462,147.1487],[-38.0507,147.1633],[-37.9869,147.2180],[-37.9377,147.3783],[-37.8798,147.4731],[-37.8335,147.5533],[-37.8016,147.5861],[-37.7727,147.6626],[-37.7582,147.7683],[-37.7444,148.4173],[-37.7463,148.4035],[-37.7524,148.3187],[-37.7495,148.2859],[-37.7408,148.0162],[-37.7582,147.9323],[-37.5555,148.9200],[-37.3180,149.2007],[-37.1973,149.1901],[-37.1530,149.1825],[-36.8489,149.2736],[-36.5767,149.2845],[-36.4203,149.2407],[-36.2466,149.1679],[-36.0758,149.1642],[-35.8354,149.1642],[-35.7531,149.1631],[-35.6353,149.2158],[-35.5654,149.2595],[-35.5419,149.3256],[-35.4335,149.3970],[-35.2927,149.6357],[-35.2533,149.7533],[-35.2184,149.8357],[-35.1970,149.9086],[-35.1648,149.9994],[-35.0883,150.1191],[-35.0767,150.2175],[-35.1144,150.3050],[-35.0651,150.4180],[-35.0220,150.4765],[-34.9030,150.5310],[-34.8045,150.6367],[-34.7553,150.7095],[-34.7437,150.8152],[-34.6626,150.8152],[-34.5931,150.7970],[-34.5237,150.7783]],
    'VNI':  [[-37.81,144.96],[-37.42,144.99],[-37.03,145.14],[-36.72,145.58],[-36.36,146.32],[-36.08,146.91],[-35.70,147.00],[-35.56,147.03],[-35.34,147.20],[-35.11,147.37],[-34.87,147.58],[-34.65,148.03],[-34.45,148.35],[-34.31,148.30],[-34.55,148.90],[-34.72,149.21],[-34.73,149.56],[-34.70,149.96],[-34.61,150.23],[-34.49,150.48],[-34.36,150.55],[-34.23,150.71],[-34.16,150.77],[-34.01,150.79],[-33.89,150.82],[-33.86,151.2]],
    'SWP':  [[-38.55,142.90],[-38.32,143.07],[-38.23,143.15],[-38.34,143.58],[-38.24,143.99],[-38.15,144.36],[-38.02,144.41],[-37.90,144.66],[-37.81,144.96]],
    'VGP':  [[-38.50,147.00],[-38.16,146.79],[-38.20,146.54],[-38.16,145.93],[-38.07,145.48],[-37.98,145.21],[-37.81,144.96],[-37.90,144.66],[-38.02,144.41],[-38.15,144.36],[-38.24,143.99],[-38.34,143.58],[-38.23,143.15],[-38.55,142.90]],
    'PK2SYD': [[-34.45,150.90],[-34.42,150.85],[-34.35,150.78],[-34.24,150.68],[-34.17,150.61],[-34.07,150.81],[-33.92,150.92],[-33.86,151.20]],
    'MAPS': [[-28.1155,140.2057],[-28.1414,140.1974],[-28.1694,140.1888],[-28.2120,140.1881],[-28.2694,140.1764],[-28.3400,140.1471],[-28.4507,140.1202],[-28.5634,140.0898],[-28.6544,140.0572],[-28.7302,140.0371],[-28.8423,140.0315],[-28.9034,140.0291],[-28.9603,140.0192],[-29.0182,140.0182],[-29.1299,140.0133],[-29.2195,140.0083],[-29.3357,140.0110],[-29.4039,139.9893],[-29.5070,139.9583],[-29.6042,139.9374],[-29.7127,139.9097],[-29.8026,139.8877],[-29.9142,139.8491],[-30.0125,139.8130],[-30.1215,139.7716],[-30.2241,139.7344],[-30.3405,139.7027],[-30.4578,139.6843],[-30.5694,139.6644],[-30.6865,139.6387],[-30.8247,139.5960],[-30.9329,139.5540],[-31.0267,139.5103],[-31.1444,139.4674],[-31.2588,139.4299],[-31.3855,139.3871],[-31.5075,139.3578],[-31.6367,139.3497],[-31.7529,139.3384],[-31.8566,139.3079],[-31.9752,139.2100],[-32.0661,139.1705],[-32.1582,139.1230],[-32.2754,139.0722],[-32.4142,139.0315],[-32.5485,138.9920],[-32.6524,138.9604],[-32.7452,138.9339],[-32.8576,138.9006],[-32.9630,138.8694],[-33.0581,138.8431],[-33.1548,138.8156],[-33.2441,138.8060],[-33.3505,138.7600],[-33.4481,138.7592],[-33.5607,138.7570],[-33.6597,138.7553],[-33.7820,138.7574],[-33.8686,138.7523],[-34.0271,138.6945],[-34.1213,138.6413],[-34.2404,138.6256],[-34.4095,138.6152],[-34.5027,138.6089],[-34.6119,138.6063],[-34.7247,138.5801],[-34.8153,138.5338],[-34.92,138.6]],
    'SWQP': [[-28.1097,140.2001],[-26.9731,143.2067],[-26.9472,143.7501],[-26.9566,145.3210],[-26.9443,145.8589],[-26.7910,145.1567],[-26.7778,145.2706],[-26.7268,145.6739],[-26.7120,145.8284],[-26.6853,146.1427],[-26.6568,146.1417],[-26.6330,146.7375],[-26.6114,146.5394],[-26.6178,146.6066],[-26.6643,147.3402],[-26.6844,147.7525],[-26.6818,147.9063],[-26.6902,148.1651],[-26.6942,148.4101],[-26.6924,148.5289],[-26.6915,148.9213],[-26.6815,149.0941],[-27.15,149.07]],
    'RBP':  [[-27.15,149.07],[-26.6936,149.1862],[-26.7021,149.2396],[-26.7118,149.2914],[-26.7372,149.3972],[-26.7537,149.4610],[-26.7697,149.5203],[-26.7883,149.5908],[-26.8395,149.6474],[-26.8640,149.6713],[-26.8838,149.7226],[-26.8920,149.7830],[-26.9076,149.9121],[-26.9154,149.9992],[-26.9223,150.0825],[-26.9322,150.1205],[-26.9439,150.1985],[-26.9529,150.2585],[-26.9713,150.3290],[-26.9869,150.4177],[-26.9952,150.4822],[-27.0209,150.5979],[-27.0399,150.6769],[-27.0594,150.7495],[-27.0647,150.7936],[-27.0712,150.8341],[-27.0765,150.8918],[-27.0851,150.9375],[-27.1574,150.7734],[-27.1978,151.2089],[-27.47,153.02]],
    'SEA_Gas': [[-38.55,142.9],[-38.5644,143.0605],[-38.4546,142.8872],[-38.3720,142.8599],[-38.2742,142.8387],[-38.1652,142.8077],[-38.1253,142.7523],[-38.1018,142.7214],[-38.0733,142.6726],[-38.0281,142.0157],[-37.9908,141.9621],[-37.9756,141.9313],[-37.9262,141.8819],[-37.9137,141.8379],[-37.8779,141.7985],[-37.8429,141.7696],[-37.8205,141.7237],[-37.7912,141.6199],[-37.7686,141.6016],[-37.7452,141.5813],[-37.7229,141.5408],[-37.7010,141.5340],[-37.6640,141.4900],[-37.6339,141.4505],[-37.6194,141.4252],[-37.6100,141.4015],[-37.6045,141.3742],[-37.5892,141.3587],[-37.5558,141.3608],[-37.5403,141.3456],[-37.5139,141.3361],[-37.5024,141.3333],[-37.4712,141.2954],[-37.4564,141.2747],[-37.4325,141.2518],[-37.4095,141.2414],[-37.3887,141.2146],[-37.3638,141.2122],[-37.3101,141.1770],[-37.2800,141.1435],[-37.2564,141.1189],[-37.2224,141.0863],[-37.1633,141.0267],[-37.1022,140.9416],[-37.0094,140.8580],[-36.8837,140.7308],[-36.7565,140.6389],[-36.6303,140.5222],[-36.5057,140.4024],[-36.3767,140.2814],[-36.2486,140.1173],[-36.1085,139.9910],[-35.9863,139.8731],[-35.8566,139.7570],[-35.7328,139.6307],[-35.5906,139.4674],[-35.4524,139.3512],[-35.3242,139.2158],[-35.1963,139.0913],[-35.0754,138.9669],[-34.92,138.6]],
    'APLNG_Pipe': [[-27.15,149.07],[-26.79,149.15],[-26.13,149.96],[-25.64,149.87],[-25.31,150.32],[-24.71,150.15],[-24.47,150.11],[-24.20,150.37],[-23.90,150.90],[-23.82,151.10],[-23.76,151.20]],
    'GLNG_Pipe':  [[-27.15,149.07],[-26.57,148.79],[-25.85,148.57],[-25.20,148.70],[-24.68,148.85],[-24.60,149.29],[-24.57,149.98],[-24.41,150.50],[-23.95,150.90],[-23.85,151.10],[-23.80,151.25]],
    'WGP_Pipe':   [[-27.15,149.07],[-26.55,149.60],[-26.13,149.96],[-25.50,150.00],[-24.95,150.08],[-24.47,150.11],[-24.41,150.50],[-24.00,150.85],[-23.86,151.10],[-23.84,151.30]],
    'QGP':      [[-27.15,149.07],[-26.55,149.30],[-26.13,149.96],[-25.40,150.05],[-24.95,150.08],[-24.57,149.98],[-24.41,150.50],[-24.00,150.90],[-23.87,151.10],[-23.84,151.26]],
    'Longford': [[-38.50,147.00],[-38.11,147.07],[-38.16,146.79],[-38.20,146.54],[-38.24,146.40],[-38.17,146.27],[-38.16,145.93],[-38.13,145.85],[-38.10,145.72],[-38.07,145.48],[-37.98,145.21],[-37.81,144.96]],
    'SS2Surat': [[-27.4,149.2],[-27.35,149.18],[-27.28,149.15],[-27.20,149.12],[-27.15,149.07]],
    # --- Expansion candidates that are NEW routes, not reversals -------------
    # Bulloo Interlink (APA ECGG Stage 3B): SWQP -> MSP direct across south-west
    # Queensland via the Bulloo Shire (Thargomindah), ~240 km shorter than routing
    # the same gas around through the existing SWQP/Moomba corner.
    'Bulloo':  [[-27.15,149.07],[-27.35,148.20],[-27.55,147.20],[-27.75,146.00],[-27.90,144.90],[-27.99,143.82],[-28.05,142.60],[-28.08,141.40],[-28.10,140.20]],
    # Geelong FSRU (Viva at Refinery Pier / Vopak in Port Phillip Bay) into the
    # DTS via the Lara-Brooklyn corridor -- NOT via Iona and the SWP.
    'GEE2MEL': [[-38.10,144.42],[-38.02,144.41],[-37.95,144.55],[-37.90,144.66],[-37.85,144.80],[-37.81,144.96]],
    # NEAP (APA's proposed North to East Australia Pipeline): a NEW 1561 km corridor
    # from the Beetaloo south-east across the Barkly and western Queensland to the
    # SWQP, deliberately NOT via Mt Isa -- bypassing the NGP and the Carpentaria
    # southbound leg is the whole point of the project. Indicative: APA has a survey
    # permit but no published route.
    'NEAP': [[-16.30,133.40],[-17.20,134.60],[-18.30,136.20],[-19.30,137.90],[-20.20,139.30],[-21.50,140.40],[-23.00,141.20],[-24.60,141.70],[-25.90,141.80],[-26.60,143.50],[-27.10,146.00],[-27.30,147.80],[-27.40,149.20]],
    # --- Northern Territory (Amadeus Basin to Darwin Pipeline + NGP; AEMO gas map v2021) ---
    # AGP: Mereenie (Amadeus Basin) -> Alice Springs -> N along the Stuart Hwy corridor
    # -> Tennant Creek -> Katherine -> Darwin.
    'AGP_S':  [[-23.85,132.30],[-23.70,133.88],[-22.30,134.05],[-20.80,134.15],[-19.65,134.19]],
    'AGP_N':  [[-19.65,134.19],[-18.00,133.55],[-16.30,133.37]],
    'AGP_DW': [[-16.30,133.37],[-14.47,132.26],[-13.20,131.10],[-12.46,130.84]],
    # Bonaparte Gas Pipeline: Blacktip/Yelcherr near Wadeye -> the AGP at Ban Ban
    # Springs, then north on the AGP to Darwin. GARY collapses both legs into one arc.
    'BGP': [[-14.23,129.52],[-14.30,130.40],[-14.20,131.30],[-14.05,131.85],[-13.20,131.10],[-12.46,130.84]],
    'Beetaloo_Pipe': [[-16.55,133.85],[-16.30,133.37]],
    # NGP (Tennant Creek -> Mt Isa) then, as on the AEMO map, gas reaches Moomba via the
    # Carpentaria Pipeline (Mt Isa -> Ballera) and the Ballera -> Moomba line — not a
    # straight run south. Single model arc, traced along the real corridor.
    'NGP':    [[-19.65,134.19],[-19.55,135.80],[-19.90,137.60],[-20.40,138.80],[-20.73,139.49],[-22.50,140.60],[-24.30,142.10],[-25.60,143.10],[-26.40,143.90],[-27.30,142.30],[-27.90,141.00],[-28.10,140.20]],
}
# Override hand-traced routes with real OpenStreetMap geometry where available
# (built by build_pipeline_geometry.py). Arcs absent from the file keep their
# hand-traced fallback. Loaded before the reverse loop so reverses use real geometry.
try:
    import json as _json
    _geo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'pipeline_geometry.json')
    with open(_geo_path) as _gf:
        ARC_WAYPOINTS.update(_json.load(_gf))
except (OSError, ValueError):
    pass
for _fwd, _rev in [('SWQP','SWQP_Rev'),('MSP','MSP_Rev'),('VNI','VNI_Rev'),('PK2SYD','SYD2PK'),
                   ('SS2Surat','Surat2SS'),('NGP','NGP_Rev'),
                   # Reversal candidates: Jemena's EGP stage 1 and SEA Gas's
                   # Port Campbell-Adelaide reverse flow both run an existing
                   # route backwards, so they inherit its real OSM geometry.
                   ('EGP','EGP_Rev'),('SEA_Gas','SEA_Gas_Rev')]:
    if _rev not in ARC_WAYPOINTS and _fwd in ARC_WAYPOINTS:
        ARC_WAYPOINTS[_rev] = list(reversed(ARC_WAYPOINTS[_fwd]))

# ---------------------------------------------------------------------------
# Custom Plotly template – Material Dark
# ---------------------------------------------------------------------------
MD_PRIMARY   = '#1F7AE0'
MD_BG        = '#F5F6F8'
MD_SURFACE   = '#FFFFFF'
MD_SURFACE2  = '#F5F6F8'
MD_TEXT      = '#1A1D21'
MD_TEXT_MED  = '#6B7280'
MD_GRID      = '#E3E6EA'
MD_LINE      = '#E3E6EA'
MD_COLORWAY  = ['#1976D2','#00897B','#F57C00','#E53935','#8E24AA',
                '#039BE5','#F9A825','#E64A19','#43A047','#6D4C41']

pio.templates['material_dark'] = go.layout.Template(
    layout=dict(
        paper_bgcolor=MD_SURFACE,
        plot_bgcolor=MD_SURFACE,
        font=dict(family="Inter, 'Segoe UI', sans-serif", color=MD_TEXT, size=13),
        title=dict(font=dict(size=15, weight=500, color=MD_TEXT),
                   x=0.0, xanchor='left', pad=dict(l=4, t=4)),
        colorway=MD_COLORWAY,
        xaxis=dict(gridcolor=MD_GRID, linecolor=MD_LINE, zerolinecolor=MD_GRID,
                   tickfont=dict(color=MD_TEXT_MED, size=11)),
        yaxis=dict(gridcolor=MD_GRID, linecolor=MD_LINE, zerolinecolor=MD_GRID,
                   tickfont=dict(color=MD_TEXT_MED, size=11)),
        legend=dict(bgcolor='rgba(255,255,255,0.95)', bordercolor=MD_LINE,
                    borderwidth=1, font=dict(size=12, color=MD_TEXT)),
        hoverlabel=dict(bgcolor=MD_SURFACE, bordercolor=MD_LINE,
                        font=dict(family="Inter, 'Segoe UI', sans-serif", size=13, color=MD_TEXT)),
        margin=dict(l=48, r=24, t=48, b=40),
    )
)

CHART_TEMPLATE = 'material_dark'

# Supply-curve palette: one hue per BASIN, in fixed order, so a colour means the
# same gas in every one of the thirty panels whether or not that basin appears in
# a given one. Eight slots, validated for CVD separation and lightness against
# both chart surfaces (worst adjacent pair dE 9.1 light / 8.4 dark, normal-vision
# 19.6 / 19.3) -- checked with a validator rather than by eye, because the eye is
# not a colourimeter. The 2P/2C tranche split rides on hatching instead of a
# ninth and tenth hue, so identity never rests on colour alone.
SUPPLY_COLORS = {
    'light': ['#2a78d6', '#eb6834', '#1baf7a', '#eda100',
              '#e87ba4', '#008300', '#4a3aa7', '#e34948'],
    'dark':  ['#3987e5', '#d95926', '#199e70', '#c98500',
              '#d55181', '#008300', '#9085e9', '#e66767'],
}


def supply_color(family, dark=False):
    """The fixed hue for a source basin, by slot rather than by appearance order."""
    slots = SUPPLY_COLORS['dark' if dark else 'light']
    try:
        return slots[sc.FAMILY_ORDER.index(family) % len(slots)]
    except ValueError:
        return MD_TEXT_MED

# Dark version of the template — NELLY's dark palette
pio.templates['gary_dark'] = go.layout.Template(
    layout=dict(
        paper_bgcolor='#1D2126',
        plot_bgcolor='#1D2126',
        font=dict(family="Inter, 'Segoe UI', sans-serif", color='#ECEFF3', size=13),
        title=dict(font=dict(size=15, weight=500, color='#ECEFF3'),
                   x=0.0, xanchor='left', pad=dict(l=4, t=4)),
        colorway=MD_COLORWAY,
        xaxis=dict(gridcolor='#2C3239', linecolor='#2C3239',
                   zerolinecolor='#2C3239',
                   tickfont=dict(color='#9AA5B1', size=11)),
        yaxis=dict(gridcolor='#2C3239', linecolor='#2C3239',
                   zerolinecolor='#2C3239',
                   tickfont=dict(color='#9AA5B1', size=11)),
        legend=dict(bgcolor='rgba(29,33,38,0.95)', bordercolor='#2C3239',
                    borderwidth=1, font=dict(size=12, color='#ECEFF3')),
        hoverlabel=dict(bgcolor='#14171A', bordercolor='#2C3239',
                        font=dict(family="Inter, 'Segoe UI', sans-serif", size=13, color='#ECEFF3')),
        margin=dict(l=48, r=24, t=48, b=40),
    )
)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = dash.Dash(
    __name__,
    background_callback_manager=background_callback_manager,
    suppress_callback_exceptions=True,
    title='GARY — Gas Allocation and Regional Yield Model',
)
server = app.server

# ---------------------------------------------------------------------------
# Material Design CSS injected into the page head
# ---------------------------------------------------------------------------
MATERIAL_CSS = """
/* ── Variables — NELLY's flat, minimal light/dark theme ───────────────────
   Same variable NAMES the rest of this file already references (so no
   Python changes needed), NELLY's actual values: flat surfaces, thin
   borders instead of drop-shadow elevation, no gradients. ── */
:root {
  --md-bg:          #F5F6F8;
  --md-surface:     #FFFFFF;
  --md-surface-2:   #F5F6F8;
  --md-surface-3:   #E3E6EA;
  --md-primary:     #1F7AE0;
  --md-primary-dim: rgba(31,122,224,0.08);
  --md-secondary:   #6FA8F5;
  --md-error:       #D64545;
  --md-success:     #2E9E5B;
  --md-warning:     #B5762A;
  --md-text:        #1A1D21;
  --md-text-med:    #6B7280;
  --md-text-low:    #9AA5B1;
  --md-divider:     #E3E6EA;
  --md-hover:       rgba(31,122,224,0.05);
  --md-e1: none;
  --md-e4: none;
  --md-e8: none;
  --md-r:    12px;
  --md-r-sm:  8px;
  --md-r-btn: 8px;
  --font: 'Inter', 'Segoe UI', Roboto, system-ui, sans-serif;

  /* Sidebar — flat panel, not a blue drawer; these just alias the main
     text/surface variables so the sidebar reads as part of the same flat
     design instead of a separately-themed component. */
  --sb-bg-top:   var(--md-surface);
  --sb-bg-bot:   var(--md-surface);
  --sb-text:     var(--md-text);
  --sb-text-med: var(--md-text-med);
  --sb-text-low: var(--md-text-low);
  --sb-divider:  var(--md-divider);
}

/* ── Base ───────────────────────────────────────────────────────────────── */
*, *::before, *::after { box-sizing: border-box; }

body, html {
  background-color: var(--md-bg) !important;
  color: var(--md-text) !important;
  font-family: var(--font) !important;
  font-size: 14px;
  margin: 0; padding: 0;
  -webkit-font-smoothing: antialiased;
}

/* ── Scrollbar ──────────────────────────────────────────────────────────── */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: var(--md-surface-2); }
::-webkit-scrollbar-thumb { background: rgba(0,0,0,0.18); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: rgba(0,0,0,0.28); }

/* ── Sidebar — flat panel with a right border, not a blue drawer ─────────── */
.md-sidebar {
  width: 280px;
  flex-shrink: 0;
  background: var(--md-surface);
  min-height: 100vh;
  position: sticky;
  top: 0;
  overflow-y: auto;
  overflow-x: hidden;
  border-right: 1px solid var(--md-divider);
  display: flex;
  flex-direction: column;
}

.md-sidebar-brand {
  padding: 18px 20px 16px;
  border-bottom: 1px solid var(--sb-divider);
  display: flex;
  align-items: center;
  gap: 12px;
}

.md-sidebar-brand-icon {
  width: 34px; height: 34px;
  background: var(--md-primary-dim);
  border: 1px solid var(--md-divider);
  border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
  color: var(--md-primary);
}

.md-sidebar-brand-text {
  font-size: 20px;
  font-weight: 700;
  color: var(--sb-text);
  letter-spacing: -0.3px;
  line-height: 1.2;
}

.md-sidebar-brand-sub {
  font-size: 12px;
  color: var(--sb-text-med);
  font-weight: 400;
  letter-spacing: 0.1px;
}

.md-sidebar-body { padding: 16px 20px; flex: 1; }

.md-section-label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.6px;
  text-transform: uppercase;
  color: var(--sb-text-low);
  margin: 0 0 10px;
  padding-top: 2px;
}

.md-divider {
  border: none;
  border-top: 1px solid var(--sb-divider);
  margin: 16px 0;
}

.md-input-label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.6px;
  font-weight: 600;
  color: var(--sb-text-med);
  margin-bottom: 5px;
  display: block;
}

/* ── Sliders — accent-coloured on the flat panel ──────────────────────── */
.rc-slider-track               { background-color: var(--md-primary) !important; }
.rc-slider-handle              { border-color: var(--md-primary) !important;
                                 background-color: #fff !important;
                                 box-shadow: 0 0 0 3px var(--md-primary-dim) !important; }
.rc-slider-handle:hover,
.rc-slider-handle-dragging     { border-color: var(--md-primary) !important;
                                 box-shadow: 0 0 0 5px var(--md-primary-dim) !important; }
.rc-slider-dot-active          { border-color: var(--md-primary) !important; }
.rc-slider-rail                { background-color: var(--md-surface-3) !important; }
.rc-slider-mark-text           { color: var(--sb-text-low) !important; font-size: 11px !important; }
.rc-slider-mark-text-active    { color: var(--sb-text) !important; }

/* ── Buttons — flat, normal case, no shadow ───────────────────────────── */
.md-btn {
  display: block;
  width: 100%;
  padding: 9px 16px;
  border-radius: var(--md-r-btn);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  border: none;
  transition: background 0.15s, border-color 0.15s;
  text-align: center;
  margin-bottom: 8px;
  outline: none;
}
.md-btn:active    { opacity: 0.85; }
.md-btn:disabled  { opacity: 0.5 !important; cursor: not-allowed !important; }

.md-btn-filled {
  background-color: var(--md-primary);
  color: #fff;
}
.md-btn-filled:hover:not(:disabled) { background-color: var(--md-primary); opacity: 0.9; }

.md-btn-tonal {
  background-color: transparent;
  color: var(--md-text);
  border: 1px solid var(--md-divider);
}
.md-btn-tonal:hover:not(:disabled) { background-color: var(--md-hover); }

.md-btn-text {
  background-color: transparent;
  color: var(--sb-text-med);
  border: 1px solid var(--md-divider);
}
.md-btn-text:hover:not(:disabled) {
  background-color: var(--md-hover);
  color: var(--sb-text);
}

.md-btn-danger {
  background-color: transparent;
  color: var(--md-error);
  border: 1px solid var(--md-divider);
}
.md-btn-danger:hover:not(:disabled) {
  background-color: rgba(214,69,69,0.08);
  border-color: var(--md-error);
}

.chart-dl-btn {
  display: inline-block;
  padding: 4px 10px;
  border-radius: var(--md-r-btn);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  background-color: transparent;
  color: var(--sb-text-med);
  border: 1px solid var(--md-divider);
  transition: background 0.15s, border-color 0.15s;
}
.chart-dl-btn:hover:not(:disabled) { background-color: var(--md-hover); }
.chart-dl-btn:active    { opacity: 0.85; }
.chart-dl-btn:disabled  { opacity: 0.5 !important; cursor: not-allowed !important; }

/* ── Status ─────────────────────────────────────────────────────────────── */
.md-status {
  min-height: 28px;
  font-size: 12px;
  color: var(--sb-text-med);
  padding: 4px 0;
  display: flex;
  align-items: center;
  gap: 6px;
}

/* ── Progress bar — flat, no shimmer animation ────────────────────────── */
.md-progress-wrap { margin: 8px 0 12px; }
.md-progress-wrap .progress {
  height: 6px !important;
  border-radius: 3px !important;
  background-color: var(--md-surface-3) !important;
  overflow: hidden;
}
.md-progress-wrap .progress-bar {
  background: var(--md-primary) !important;
  transition: width 0.3s ease !important;
  font-size: 0 !important;
}

/* ── Dropdown ──────────────────────────────────────────────────────────── */
.Select-control {
  background-color: var(--md-surface) !important;
  border: 1px solid var(--md-divider) !important;
  border-radius: var(--md-r-sm) !important;
  color: var(--md-text) !important;
}
.Select-menu-outer {
  background-color: var(--md-surface) !important;
  border: 1px solid var(--md-divider) !important;
  border-radius: var(--md-r-sm) !important;
}
.Select-option                 { background-color: var(--md-surface) !important;
                                 color: var(--md-text) !important; }
.Select-option.is-focused      { background-color: var(--md-surface-2) !important; }
.Select-option.is-selected     { background-color: var(--md-primary-dim) !important;
                                 color: var(--md-primary) !important; }
.Select-value-label            { color: var(--md-text) !important; }
.Select-placeholder            { color: var(--md-text-low) !important; }
.Select-arrow                  { border-top-color: var(--md-text-med) !important; }

/* ── Checklist ─────────────────────────────────────────────────────────── */
.form-check-input              { background-color: transparent !important;
                                 border-color: var(--md-text-low) !important; }
.form-check-input:checked      { background-color: var(--md-primary) !important;
                                 border-color: var(--md-primary) !important; }
.form-check-label              { color: var(--sb-text-med) !important; font-size: 13px !important; }

/* ── Switch (flat toggle, replaces Bootstrap's .form-switch) ──────────────── */
.md-switch-input {
  appearance: none;
  -webkit-appearance: none;
  width: 34px;
  height: 18px;
  min-width: 34px;
  background-color: var(--md-surface-3);
  border-radius: 999px;
  position: relative;
  cursor: pointer;
  vertical-align: middle;
  margin: 0 8px 0 0;
  transition: background-color 0.15s;
}
.md-switch-input::before {
  content: '';
  position: absolute;
  /* Vertically centred via transform, not a fixed top offset, so the knob
     stays centred in the pill regardless of any box-sizing/border quirks a
     browser's default checkbox styling adds on top of appearance:none. */
  top: 50%;
  left: 2px;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #fff;
  transform: translateY(-50%);
  transition: left 0.15s;
}
.md-switch-input:checked        { background-color: var(--md-primary) !important; }
.md-switch-input:checked::before { left: 18px; }
.md-switch-label { color: var(--sb-text-med); font-size: 13px; vertical-align: middle; }

/* ── Main layout ────────────────────────────────────────────────────────── */
.md-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }

.md-header {
  background: var(--md-surface);
  padding: 14px 22px;
  border-bottom: 1px solid var(--md-divider);
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
}

.md-header-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--md-text);
  letter-spacing: -0.2px;
}

.md-scenario-chip {
  font-size: 11px;
  font-weight: 600;
  padding: 5px 14px;
  border-radius: 14px;
  background-color: var(--md-primary-dim);
  color: var(--md-primary);
  border: 1px solid var(--md-divider);
  max-width: 340px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.md-content { padding: 18px 22px; flex: 1; }

/* ── KPI cards — flat bordered card, no accent bar, no hover shadow ──────── */
.md-kpi-row { display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }

.md-kpi-card {
  flex: 1;
  background-color: var(--md-surface);
  border: 1px solid var(--md-divider);
  border-radius: 10px;
  padding: 12px 16px;
  min-width: 140px;
}

.md-kpi-label {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.6px;
  text-transform: uppercase;
  color: var(--md-text-low);
  margin-bottom: 3px;
}

/* Caption under a sidebar input (e.g. which node a data centre volume lands on). */
.md-input-hint {
  font-size: 10px;
  color: var(--sb-text-med);
  opacity: 0.8;
  margin-top: 3px;
  letter-spacing: 0.02em;
}

/* Qualifier under a KPI value (e.g. reservation take-up): its own line, quieter
   than the number it belongs to, so the value never breaks mid-phrase. */
.md-kpi-sub {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: var(--md-text-low);
  margin-top: 2px;
}

.md-kpi-value {
  font-size: 22px;
  font-weight: 700;
  color: var(--md-text);
  line-height: 1.2;
  /* Wrap rather than ellipsis: the Gas Reserved value carries a "(x% taken up)"
     suffix that was being cut off in the card. Cards stretch to equal height. */
  white-space: normal;
  overflow-wrap: break-word;
}

/* ── Tabs ───────────────────────────────────────────────────────────────── */
.md-tabs-wrap {
  background: transparent;
}

.md-tabs-wrap .nav-tabs {
  border-bottom: 1px solid var(--md-divider) !important;
  background: transparent !important;
  padding: 0;
  flex-wrap: nowrap;
  overflow-x: auto;
}

.md-tabs-wrap .nav-link {
  color: var(--md-text-med) !important;
  border: none !important;
  border-bottom: 2px solid transparent !important;
  border-radius: 0 !important;
  padding: 10px 14px !important;
  font-size: 13px !important;
  font-weight: 600 !important;
  background: transparent !important;
  margin-bottom: -1px !important;
  white-space: nowrap;
  transition: color 0.15s, border-color 0.15s !important;
}

.md-tabs-wrap .nav-link:hover {
  color: var(--md-primary) !important;
}

.md-tabs-wrap .nav-link.active {
  color: var(--md-primary) !important;
  border-bottom: 2px solid var(--md-primary) !important;
  background: transparent !important;
}

/* ── Tab panel — flat card ─────────────────────────────────────────────── */
.md-tab-panel {
  background-color: var(--md-surface);
  border: 1px solid var(--md-divider);
  border-radius: var(--md-r);
  padding: 16px;
  margin-top: 14px;
}

/* ── Map controls ───────────────────────────────────────────────────────── */
.md-map-controls {
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 4px 4px 14px;
}

/* ── Map KPI strip ──────────────────────────────────────────────────────── */
.md-map-kpi-row { display: flex; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; }

.md-map-kpi {
  flex: 1;
  background-color: var(--md-surface-2);
  border: 1px solid var(--md-divider);
  border-radius: var(--md-r-sm);
  padding: 10px 14px;
  min-width: 0;
}

.md-map-kpi-label {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.5px;
  text-transform: uppercase;
  color: var(--md-text-low);
  margin-bottom: 4px;
}

.md-map-kpi-value {
  font-size: 17px;
  font-weight: 700;
  color: var(--md-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ── Expansions table ───────────────────────────────────────────────────── */
.md-table table   { width: 100%; border-collapse: collapse; background: var(--md-surface); }
.md-table thead th {
  background-color: var(--md-surface-2);
  color: var(--md-text-low);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.5px;
  text-transform: uppercase;
  padding: 10px 14px;
  border-bottom: 1px solid var(--md-divider);
}
.md-table tbody tr             { transition: background 0.12s; }
.md-table tbody tr:nth-child(odd) { background-color: rgba(0,0,0,0.025); }
.md-table tbody tr:hover       { background-color: var(--md-hover) !important; }
.md-table tbody td {
  padding: 10px 14px;
  border-bottom: 1px solid var(--md-divider);
  color: var(--md-text);
  font-size: 13px;
}

/* ── Alerts — same flat-card language as NELLY's amber banner ────────────── */
.md-alert {
  border-radius: var(--md-r-sm);
  padding: 10px 14px;
  font-size: 12.5px;
  line-height: 1.5;
  border: 1px solid;
}
.md-alert-success { background-color: rgba(46,158,91,0.08);
                    border-color: rgba(46,158,91,0.3);
                    color: #1E7A45; }
.md-alert-info    { background-color: var(--md-primary-dim);
                    border-color: rgba(31,122,224,0.3);
                    color: var(--md-primary); }
.md-alert-warn    { background-color: #FDF3E3;
                    border-color: #E8B96B;
                    color: #7A4A00; }

/* ── Tooltip ────────────────────────────────────────────────────────────── */
.dash-tooltip { font-family: var(--font) !important; }

/* ── Dark mode overrides — NELLY's dark palette ───────────────────────────
   Toggled on <html> by the existing theme-store clientside callback below;
   unchanged mechanism, new values. ── */
.dark {
  --md-bg:          #14171A;
  --md-surface:     #1D2126;
  --md-surface-2:   #14171A;
  --md-surface-3:   #2C3239;
  --md-primary:     #6FA8F5;
  --md-primary-dim: rgba(111,168,245,0.12);
  --md-text:        #ECEFF3;
  --md-text-med:    #9AA5B1;
  --md-text-low:    #6B7280;
  --md-divider:     #2C3239;
  --md-hover:       rgba(111,168,245,0.08);
  --md-e1: none;
  --md-e4: none;
  --md-e8: none;
}
.dark .Select-control {
  background-color: var(--md-surface-2) !important;
  border-color: var(--md-divider) !important;
  color: var(--md-text) !important;
}
.dark .Select-menu-outer {
  background-color: var(--md-surface) !important;
  border-color: var(--md-divider) !important;
}
.dark .Select-option { background-color: var(--md-surface) !important; color: var(--md-text) !important; }
.dark .Select-option.is-focused { background-color: var(--md-surface-2) !important; }
.dark .Select-option.is-selected { background-color: var(--md-primary-dim) !important; color: var(--md-primary) !important; }
.dark .Select-value-label { color: var(--md-text) !important; }
.dark .Select-placeholder { color: var(--md-text-low) !important; }
.dark .Select-arrow { border-top-color: var(--md-text-med) !important; }
.dark .md-table table { background: var(--md-surface) !important; }
.dark .md-table thead th { background-color: var(--md-surface-2) !important; color: var(--md-text-low) !important; border-color: var(--md-divider) !important; }
.dark .md-table tbody td { color: var(--md-text) !important; border-color: var(--md-divider) !important; }
.dark .md-alert-success { background-color: rgba(46,158,91,0.15) !important; color: #6FCB94 !important; }
.dark .md-alert-info    { background-color: rgba(111,168,245,0.15) !important; color: #6FA8F5 !important; }
.dark .md-alert-warn    { background-color: #3A2E17 !important; border-color: #8A6A2B !important; color: #F0D9A8 !important; }
/* Dark mode – Bootstrap table (expansions tab): flat dark, no stripes */
.dark table { border-color: var(--md-divider) !important; }
.dark table > :not(caption) > * > *,
.dark .table-striped > tbody > tr > * {
  background-color: var(--md-surface) !important;
  --bs-table-striped-bg: var(--md-surface);
  --bs-table-bg: var(--md-surface);
  --bs-table-accent-bg: var(--md-surface);
  color: var(--md-text) !important;
  border-color: var(--md-divider) !important;
}
.dark table thead th {
  background-color: var(--md-surface-2) !important;
  color: var(--md-text-low) !important;
  border-color: var(--md-divider) !important;
}
.dark table tbody tr:hover > * {
  background-color: var(--md-hover) !important;
  color: var(--md-text) !important;
}
/* Dark mode – all sliders */
.dark .rc-slider-mark-text        { color: var(--md-text-low) !important; }
.dark .rc-slider-mark-text-active { color: var(--md-text) !important; }
.dark .md-map-controls .rc-slider-rail   { background-color: var(--md-surface-3) !important; }
.dark .md-map-controls .rc-slider-track  { background-color: var(--md-primary) !important; }
.dark .md-map-controls .rc-slider-handle { border-color: var(--md-primary) !important; background-color: var(--md-surface) !important; }
.dark .md-input-label                        { color: var(--md-text-med) !important; }
.dark .md-input-hint                         { color: var(--md-text-med) !important; }
.theme-toggle {
  display: flex; align-items: center; justify-content: center;
  gap: 8px; margin: 12px 0 4px; padding: 8px 12px;
  border-radius: var(--md-r-btn);
  background: transparent;
  border: 1px solid var(--md-divider);
  color: var(--sb-text-med); font-size: 12.5px; font-weight: 600;
  cursor: pointer; width: 100%;
  transition: background 0.15s;
}
.theme-toggle:hover { background: var(--md-hover); color: var(--sb-text); }
"""

app.index_string = f"""<!DOCTYPE html>
<html>
  <head>
    {{%metas%}}
    <title>{{%title%}}</title>
    {{%favicon%}}
    {{%css%}}
    <style>{MATERIAL_CSS}</style>
  </head>
  <body style="margin:0;padding:0;">
    {{%app_entry%}}
    <footer>
      {{%config%}}
      {{%scripts%}}
      {{%renderer%}}
    </footer>
  </body>
</html>"""

LEVELS = ['Low', 'Medium', 'High']

# Dashboard opening positions, off the Parameters sheet rather than literals here,
# so the workbook stays the single place a default is set. WINTER OPENS ON LOW: it
# used to open on Medium, which is a 1.5x stress case, so every headline figure was
# a stressed run unless someone moved the slider.
_WINTER_DEFAULT_IX = LEVELS.index(P.get_str('winter_default', 'Medium')) \
    if P.get_str('winter_default', 'Medium') in LEVELS else 1
_LNG_DEFAULT_IX = LEVELS.index(P.get_str('lng_default', 'Medium')) \
    if P.get_str('lng_default', 'Medium') in LEVELS else 1
_MIP_GAP_DEFAULT = P.get('mip_gap_default', 0.005)
_REP_BINS_DEFAULT = P.get_int('rep_bins_per_month', 3)
# Domestic gas reservation shares offered by the slider, as whole percents.
RESERVATION_PCTS = [int(round(x * 100)) for x in RESERVATION_LEVELS]
# Uncontracted share of export volume: the ceiling on a reservation that respects
# foundation SPAs. Shown in the sidebar so the cap is visible before a run rather
# than discovered afterwards.
UNCONTRACTED_PCT = (1.0 - lng_foundation_share()) * 100

# AEMO 2026 GSOO baseline scenarios (label -> slug). The baseline sets the
# underlying demand trajectory; the Winter/LNG levers then layer on top of it.
BASELINES = [
    {'label': 'Step Change (central)', 'value': 'StepChange'},
    {'label': 'Accelerated Transition', 'value': 'Accelerated'},
    {'label': 'Slower Growth', 'value': 'SlowerGrowth'},
]
BASELINE_LABEL = {b['value']: b['label'] for b in BASELINES}

# Compact labels for the scenario dropdown.
_BASE_SHORT = {'StepChange': 'SC', 'Accelerated': 'Acc', 'SlowerGrowth': 'SG'}
_LVL_SHORT = {'Low': 'L', 'Medium': 'M', 'High': 'H'}


# Data centre segment of a scenario key: _DC<nsw>N<vic>V<startyear>, e.g.
# _DC50N30V2030. Both readers below parse it with this one pattern.
_DC_RE = r'_DC([\d.]+)N([\d.]+)V(\d{4})'
# Linked-series segment: _DCS<name>, taken from the linked file's own name (see
# datacentre_series.label). The volumes are not recoverable from it -- the
# per-node split saved with the result is what the map reads -- so it labels the
# run rather than describing it. Still matches the 8-hex fingerprints that
# earlier runs used, so cached results from before the rename keep decoding.
_DCS_RE = r'_DCS([A-Za-z0-9]+)'
# Which node each state's data centre load lands on. Mirrors
# model.DATACENTRE_STATE_NODE, which reads the pair off the parameters workbook; this
# copy exists only to decode scenarios solved before the per-node split was saved.
_DC_KEY_NODES = (('NSW', 'Sydney'), ('VIC', 'Melbourne'))


def datacentre_by_node(res, key):
    """Data centre load at each node for one solved year, ``{node: TJ}``.

    Prefers the split the model now saves. Scenarios solved before that only
    carry the system-wide total, so for those the volumes are read back off the
    scenario key, which encodes the NSW/VIC PJ split and the start year -- the
    same numbers the solve was handed. That fallback is for FLAT-CELL runs only;
    a linked-series key carries a hash rather than volumes, but every run that
    could have one is new enough to have saved the split. The year gate comes
    from the saved total: it is zero before the start year, so a run that has not
    switched on yet reports nothing rather than back-dating the load.
    """
    saved = res.get('datacentre_by_node_tj')
    if saved:
        return {n: float(v) for n, v in saved.items()}
    if not res.get('datacentre_tj'):
        return {}
    mo = re.search(_DC_RE, key or '')
    if not mo:
        return {}
    pj = {'NSW': float(mo.group(1)), 'VIC': float(mo.group(2))}
    return {node: pj[state] * 1000.0
            for state, node in _DC_KEY_NODES if pj.get(state, 0) > 0}


def _base_of(k):
    """Baseline name out of a scenario key."""
    if 'Base_' not in k:
        return ''
    return k.split('Base_', 1)[1].split('_Winter', 1)[0]


def _dr_of(k):
    """Discount rate out of a scenario key, defaulting to the slider's 0.07.

    The key only carries a ``_DR<pct>`` suffix when a run's discount rate
    deviated from the 0.07 default (see the key-building logic around line 1709).
    """
    if '_DR' not in k:
        return 0.07
    pct = k.rsplit('_DR', 1)[1].split('_', 1)[0]
    try:
        return float(pct) / 100.0
    except ValueError:
        return 0.07


def _annualised_capex(capex, life, r):
    """CapEx spread over AssetLife at discount rate ``r``, or None if not costable.

    Capital recovery factor ``CRF = r / (1 - (1+r)^-life)``, the same formula
    capacity_model._asset_residual uses for the model's own NPV treatment of
    capital -- so this reads as "what the model effectively charges per year"
    rather than a separately invented convention. Falls back to straight-line
    (capex / life) when r is 0. Returns None when there's no CapEx or no
    AssetLife to spread it over (the field-development rows, whose capital is
    folded into the gas commodity price rather than charged as a lump CapEx --
    see build_field_developments.derived_capex).
    """
    try:
        capex = float(capex)
        life = float(life)
    except (TypeError, ValueError):
        return None
    if not capex or life != life or life <= 0:
        return None
    if r <= 0:
        return capex / life
    return capex * r / (1.0 - (1.0 + r) ** -life)


def _2c_premium_by_node():
    """``{node: $/GJ}`` the 2C tranche's cost premium over that basin's 2P gas.

    A field-development candidate (Golden Beach, Judith, the Otway fields, the
    Surat/Bowen developments, Cooper_2C, Amadeus_2C, Beetaloo's rows) carries
    zero CapEx by design -- its capital sits inside the 2C tranche's per-GJ
    Cost in supply.csv rather than as a lump sum (see
    build_field_developments.derived_capex). The 2C row's Cost IS AEMO's full
    blended cost (opex + capital + royalty + tax + return), so there is no
    published opex-only baseline to net it against directly. The best proxy
    available is the SAME basin's 2P (developed) Cost: 2P gas is already
    flowing without needing this candidate built, so 2C-minus-2P isolates
    roughly what unlocking the contingent tranche costs on top of business as
    usual -- e.g. Surat 2P $3.65/GJ vs 2C $6.65/GJ, a $3.00/GJ premium.

    A basin with NO 2P row at all (Beetaloo) is not a missing-data case: it
    means there is no developed baseline because the basin isn't producing
    anything yet, so the field is entirely new rather than incremental. The
    2P cost there is treated as $0, and the whole 2C cost -- $9.15/GJ for
    Beetaloo -- is the premium, not a netted-down figure.
    """
    sup = static_data['supply']
    out = {}
    for node, g in sup.groupby('Node'):
        c2 = g.loc[g['Tranche'] == '2C', 'Cost']
        if c2.empty:
            continue
        p2 = g.loc[g['Tranche'] == '2P', 'Cost']
        p2_cost = float(p2.iloc[0]) if not p2.empty else 0.0
        out[node] = float(c2.iloc[0]) - p2_cost
    return out


def _annualised_cost(info, discount_rate, premium_by_node):
    """Annual $ a built project adds, whichever way its capital is carried.

    A project with a real CapEx (a pipeline, an FSRU) spreads it over
    AssetLife via _annualised_capex. A field development that carries zero
    CapEx instead has its capital folded into its basin's 2C gas price -- see
    _2c_premium_by_node -- so here the annualised figure is that basin's
    2C-over-2P premium times this candidate's OWN nameplate capacity run flat
    out for a year: the annual dollars the gas PRICE embeds for exactly the
    gas this candidate is sized to deliver. Not a lump CapEx equivalent and
    not discounted (there's no capital recovery schedule to discount, just an
    ongoing per-GJ charge) -- a straight annual read of what is priced in.
    Returns None only when a node has no 2C row to derive a premium from at
    all, which should not happen for any candidate this function is called on.
    """
    capex = info.get('CapEx')
    if capex:
        return _annualised_capex(capex, info.get('AssetLife'), discount_rate)
    premium = premium_by_node.get(info.get('Target'))
    try:
        capacity = float(info.get('NewCapacity'))
    except (TypeError, ValueError):
        capacity = 0.0
    if premium is None or capacity <= 0:
        return None
    return premium * capacity * 1000.0 * 365.0   # $/GJ * TJ/d->GJ/d * days/yr


def short_key(k):
    """Shorthand scenario label, e.g. 'SC · W-M L-M · Dunk27 · Myopic'."""
    base = _base_of(k)
    winter = k.split('_Winter_', 1)[1].split('_', 1)[0] if '_Winter_' in k else ''
    lng = k.split('_LNG_', 1)[1].split('_', 1)[0] if '_LNG_' in k else ''
    parts = [_BASE_SHORT.get(base, base)]
    if winter or lng:
        parts.append(f"W-{_LVL_SHORT.get(winter, winter[:1])} L-{_LVL_SHORT.get(lng, lng[:1])}")
    if '_Dunkelflaute' in k:
        parts.append('Dunk27')
    mo = re.search(r'_Reserve(\d+)(incl)?', k)
    if mo:
        # 'incl' in the key is short for "including contracted volume", i.e. the
        # reservation overrides the SPAs. '+contract' read as the opposite -- the
        # run that RESPECTS contracts -- and next to a plain 'Res20%' that is
        # exactly backwards, so both halves of the pair now say which they are.
        parts.append(f'Res{mo.group(1)}% '
                     + ('breaks contracts' if mo.group(2) else 'uncontracted only'))
    dcs = re.search(_DCS_RE, k)
    if dcs:
        parts.append(f'DC series {dcs.group(1)}')
    dc = re.search(_DC_RE, k)
    if dc:
        # With a series alongside, only the states still on a cell are non-zero.
        cells = ' / '.join(f'{v} PJ' for v in (dc.group(1), dc.group(2))
                           if float(v) > 0) if dcs else \
                f'{dc.group(1)}/{dc.group(2)} PJ'
        parts.append(('DC + ' if dcs else 'DC ') + f'{cells} @{dc.group(3)}')
    if '_NoImports' in k:
        parts.append('No imports')
    if '_GSOOExp' in k:
        parts.append('GSOO exp')
    if '_Netback' in k:
        parts.append('Netback')
    if '_Myopic' in k:
        parts.append('Myopic')
    if '_DR' in k:
        parts.append('DR' + k.rsplit('_DR', 1)[1].split('_', 1)[0] + '%')
    return '  ·  '.join(p for p in parts if p)


def _dc_cell_phrase(mo, has_series):
    """Label for the cell part of a data centre key segment.

    Alone it names both states. Next to a linked series it names only the states
    still on a cell -- the others read 0 in the key because the file answers for
    them, and "0 PJ NSW" next to a series that puts 18 PJ on Sydney would say the
    opposite of what the run did.
    """
    nsw, vic, year = mo.group(1), mo.group(2), mo.group(3)
    if not has_series:
        return f'  ·  Data centres {nsw} PJ NSW / {vic} PJ VIC from {year}'
    cells = [f'{v} PJ {st}' for v, st in ((nsw, 'NSW'), (vic, 'VIC'))
             if float(v) > 0]
    return f'  ·  plus {" / ".join(cells)} from {year}' if cells else ''


def pretty_key(k):
    """Human-readable label for a scenario key Base_<base>_Winter_<w>_LNG_<l>."""
    base = _base_of(k) or None
    rest = k.split(base, 1)[-1] if base else k
    # Before the chained replaces below, which would otherwise rewrite a trailing
    # _Myopic/_DR into the middle of the reservation percentage.
    rest = re.sub(r'_Reserve(\d+)(incl)?',
                  lambda mo: f'  ·  {mo.group(1)}% reservation'
                             + (' incl. contracted' if mo.group(2) else ''), rest)
    has_series = bool(re.search(_DCS_RE, rest))
    rest = re.sub(_DCS_RE,
                  lambda mo: f'  ·  Data centres from linked series {mo.group(1)}',
                  rest)
    rest = re.sub(_DC_RE, lambda mo: _dc_cell_phrase(mo, has_series), rest)
    rest = (rest.replace('_Winter_', 'Winter ').replace('_LNG_', '  ·  LNG ')
                .replace('_Dunkelflaute', '  ·  SA Dunkelflaute 2027')
                .replace('_GSOOExp', '  ·  GSOO expansions only')
                .replace('_NoImports', '  ·  no import terminals')
                .replace('_Netback', '  ·  LNG netback pricing')
                .replace('_Myopic', '  ·  Myopic'))
    if '_DR' in rest:
        head, dr = rest.rsplit('_DR', 1)
        rest = f'{head}  ·  Discount {dr}%'
    rest = rest.strip('_ ')
    return f'{BASELINE_LABEL.get(base, base)}  ·  {rest}' if base else rest

# ---------------------------------------------------------------------------
# Helper – map arrowhead
# ---------------------------------------------------------------------------
def add_flow_arrows(fig, path, color, width, spacing=1.1, size=0.45):
    """Draw arrowheads at regular intervals along a flow path so direction is clear."""
    segs = []
    for (la1, lo1), (la2, lo2) in zip(path[:-1], path[1:]):
        length = np.sqrt((la2 - la1) ** 2 + (lo2 - lo1) ** 2)
        if length > 1e-9:
            segs.append((la1, lo1, (la2 - la1) / length, (lo2 - lo1) / length, length))
    total = sum(s[4] for s in segs)
    if total == 0:
        return
    n = max(1, int(total / spacing))
    for i in range(1, n + 1):
        target, acc = total * i / (n + 1), 0.0
        for la1, lo1, u_lat, u_lon, length in segs:
            if acc + length >= target:
                dist = target - acc
                c_lat, c_lon = la1 + u_lat * dist, lo1 + u_lon * dist
                p_lat, p_lon = -u_lon, u_lat
                tip_lat, tip_lon = c_lat + 0.5 * size * u_lat, c_lon + 0.5 * size * u_lon
                b1_lat = tip_lat - size * u_lat + size * 0.6 * p_lat
                b1_lon = tip_lon - size * u_lon + size * 0.6 * p_lon
                b2_lat = tip_lat - size * u_lat - size * 0.6 * p_lat
                b2_lon = tip_lon - size * u_lon - size * 0.6 * p_lon
                fig.add_trace(go.Scattermap(
                    lat=[b1_lat, tip_lat, b2_lat, b1_lat],
                    lon=[b1_lon, tip_lon, b2_lon, b1_lon],
                    mode='lines', fill='toself', fillcolor=color,
                    line=dict(width=1, color=color),
                    hoverinfo='skip', showlegend=False,
                ))
                break
            acc += length

# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------
def kpi_card(label, value):
    return html.Div([
        html.Div(label, className='md-kpi-label'),
        html.Div(value, className='md-kpi-value'),
    ], className='md-kpi-card')

def map_kpi(label, value):
    return html.Div([
        html.Div(label, className='md-map-kpi-label'),
        html.Div(value, className='md-map-kpi-value'),
    ], className='md-map-kpi')

def md_alert(text, kind='info'):
    return html.Div(text, className=f'md-alert md-alert-{kind}')

def slider_group(label, slider):
    return html.Div([
        html.Span(label, className='md-input-label'),
        slider,
    ], style={'marginBottom': '20px'})

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
sidebar = html.Div(className='md-sidebar', children=[

    # Brand
    html.Div(className='md-sidebar-brand', children=[
        html.Div('⚡', className='md-sidebar-brand-icon'),
        html.Div([
            html.Div('GARY', className='md-sidebar-brand-text'),
            html.Div('2025–2050', className='md-sidebar-brand-sub'),
        ]),
    ]),

    # Body
    html.Div(className='md-sidebar-body', children=[

        # ── Actions ───────────────────────────────────────────────────────
        html.P('Actions', className='md-section-label'),

        # TWO run buttons, deliberately. One solves the sidebar as it stands; the
        # other solves the standard set, which is a FIXED list in
        # run_standard_set.build() and ignores the sidebar. There used to be a
        # third that swept every reservation level x every baseline; it was a
        # ~12 h cross-product nobody ran, and the standard set already carries the
        # reservation cases worth reading (blocks C, E and F).
        html.Button('▶  Run This Scenario',   id='run-btn',   className='md-btn md-btn-filled'),
        html.Button('⚡  Run Standard Set',    id='batch-btn', className='md-btn md-btn-tonal'),
        html.Button('↺  Regenerate All Data', id='regen-btn', className='md-btn md-btn-text'),
        html.Button('✕  Clear Results',      id='clear-btn', className='md-btn md-btn-danger'),

        html.Div(id='run-status', className='md-status'),
        html.Div(className='md-progress-wrap', children=[
            html.Div(id='solver-progress', className='progress', style={'display': 'none'}, children=[
                html.Div(id='solver-progress-bar', className='progress-bar', style={'width': '0%'}),
            ]),
        ]),

        html.Hr(className='md-divider'),

        # ── Baseline ──────────────────────────────────────────────────────
        html.P('Baseline', className='md-section-label'),

        html.Span('GSOO Baseline Scenario', className='md-input-label'),
        dcc.Dropdown(id='baseline-selector', options=BASELINES, value='StepChange',
                     clearable=False, style={'marginBottom': '20px', 'fontSize': '12px'}),

        html.Hr(className='md-divider'),

        # ── Scenario ──────────────────────────────────────────────────────
        html.P('Scenario', className='md-section-label'),

        slider_group('Southern Winter Stress',
            dcc.Slider(id='winter-slider', min=0, max=2, step=1,
                       marks={i: l for i, l in enumerate(LEVELS)},
                       value=_WINTER_DEFAULT_IX)),
        html.Div('Multiplies Melbourne / Adelaide / Sydney distribution demand over '
                 'the winter window. \u2039Medium\u203a (1.0\u00d7, the default) is the '
                 'CENTRAL GSOO case \u2014 AEMO\u2019s weather-averaged series. '
                 '\u2039Low\u203a (0.91\u00d7) is an unseasonably warm winter, the '
                 'warmest of the seven Bulletin Board winters once the 2.6%/yr '
                 'structural decline is removed. \u2039High\u203a (1.5\u00d7) is a '
                 'deliberate stress beyond observed weather \u2014 the coldest winter '
                 'on record is only 1.08\u00d7.',
                 style={'marginTop': '-14px', 'marginBottom': '18px',
                        'fontSize': '10px', 'color': '#888', 'lineHeight': '1.35'}),

        slider_group('Global LNG Market',
            dcc.Slider(id='lng-slider', min=0, max=2, step=1,
                       marks={i: l for i, l in enumerate(LEVELS)},
                       value=_LNG_DEFAULT_IX)),
        html.Div('Scales export volume when netback pricing is off. With it on, '
                 'it selects the netback price path instead \u2014 Low = Accelerated '
                 'Transition, Medium = this run\'s baseline, High = Slower Growth.',
                 style={'marginTop': '-14px', 'marginBottom': '18px',
                        'fontSize': '10px', 'color': '#888', 'lineHeight': '1.35'}),

        dcc.Checklist(id='dunkelflaute-toggle',
                      options=[{'label': ' SA Dunkelflaute (2027)', 'value': 'on'}],
                      value=[], inputClassName='md-switch-input', labelClassName='md-switch-label',
                      style={'marginBottom': '16px', 'fontSize': '12px'}),

        dcc.Checklist(id='reservation-toggle',
                      options=[{'label': ' Gas reservation', 'value': 'on'}],
                      value=[], inputClassName='md-switch-input', labelClassName='md-switch-label',
                      style={'marginBottom': '8px', 'fontSize': '12px'}),

        html.Div(id='reservation-slider-wrap', style={'display': 'none'}, children=[
            slider_group('Share of LNG exports reserved',
                dcc.Slider(id='reservation-slider', min=0, max=len(RESERVATION_PCTS) - 1,
                           step=None,
                           marks={i: f'{v}%' for i, v in enumerate(RESERVATION_PCTS)},
                           value=1)),
            dcc.Checklist(id='contracts-toggle',
                          options=[{'label': ' Respect LNG foundation contracts',
                                    'value': 'on'}],
                          value=['on'], inputClassName='md-switch-input', labelClassName='md-switch-label',
                          style={'marginBottom': '2px', 'fontSize': '12px'}),
            html.Div(f'On: the reservation may only take UNCONTRACTED export gas, '
                     f'so it is capped at {UNCONTRACTED_PCT:.0f}% however high the '
                     f'slider goes \u2014 which is how the Heads of Agreement works. '
                     f'Off: it takes its share of all export volume, breaking '
                     f'take-or-pay SPAs.',
                     style={'marginBottom': '16px', 'fontSize': '10px',
                            'color': '#888', 'lineHeight': '1.35',
                            'paddingLeft': '38px'}),
        ]),

        # Data centre load. Two volumes rather than one national figure because
        # the whole point is where it lands: Sydney and Melbourne sit at opposite
        # ends of the southbound corridor that binds in every stressed run.
        html.Span('Data centre gas demand (PJ/yr)', className='md-input-label'),
        html.Div(style={'display': 'flex', 'gap': '8px', 'marginBottom': '6px'}, children=[
            html.Div(style={'flex': '1'}, children=[
                dcc.Input(id='dc-nsw-input', type='number', min=0, step=1, value=0,
                          debounce=True, style={'fontSize': '12px'}),
                html.Div('NSW · Sydney', className='md-input-hint'),
            ]),
            html.Div(style={'flex': '1'}, children=[
                dcc.Input(id='dc-vic-input', type='number', min=0, step=1, value=0,
                          debounce=True, style={'fontSize': '12px'}),
                html.Div('VIC · Melbourne', className='md-input-hint'),
            ]),
        ]),
        html.Div('Added to large-industrial demand at Sydney and Melbourne from '
                 'the start year on, spread across the year on each node\'s own '
                 'gas-powered generation shape. Leave at 0 for none.',
                 style={'marginBottom': '14px', 'fontSize': '10px',
                        'color': '#888', 'lineHeight': '1.35'}),

        slider_group('Data centre demand starts',
            dcc.Slider(id='dc-start-slider', min=2025, max=2050, step=1, value=2030,
                       marks={y: str(y) for y in range(2025, 2051, 5)},
                       tooltip={'placement': 'bottom', 'always_visible': True})),

        # The other way to state the same load: a year-by-year series out of the
        # analyst's own spreadsheet, for a build-out with a shape rather than one
        # flat volume. The file is read at solve time and never written to; a
        # state it covers overrides the cell above, a state it does not keeps it.
        html.Span('Or link a demand series (optional)', className='md-input-label'),
        dcc.Input(id='dc-file-input', type='text', debounce=True, value=DC_FILE_DEFAULT,
                  placeholder='/path/to/data_centre_pipeline.xlsx',
                  style={'fontSize': '11px', 'marginBottom': '4px'}),
        html.Div(id='dc-file-status',
                 style={'marginBottom': '4px', 'fontSize': '10px',
                        'lineHeight': '1.35'}),
        html.Div('A .csv or .xlsx with a Year column and NSW and/or VIC columns '
                 'in PJ/yr (add #SheetName for a particular sheet). Zero before '
                 'the first row, a straight ramp between rows, held flat after '
                 'the last. Overrides the box above for the states it covers; '
                 'clear it to go back to the flat volumes.',
                 style={'marginBottom': '16px', 'fontSize': '10px',
                        'color': '#888', 'lineHeight': '1.35'}),

        dcc.Checklist(id='imports-toggle',
                      options=[{'label': ' Allow LNG import terminals', 'value': 'on'}],
                      value=['on'] if IMPORTS_DEFAULT else [], inputClassName='md-switch-input', labelClassName='md-switch-label',
                      style={'marginBottom': '2px', 'fontSize': '12px'}),
        html.Div('On: the capacity model may build Port Kembla, either Geelong '
                 'FSRU, or Outer Harbor. Off: they are dropped from the candidate '
                 'set, so the east coast must be supplied from domestic fields and '
                 'pipe. Field developments such as Golden Beach are unaffected.',
                 style={'marginBottom': '16px', 'fontSize': '10px',
                        'color': '#888', 'lineHeight': '1.35', 'paddingLeft': '38px'}),

        dcc.Checklist(id='gsoo-exp-toggle',
                      options=[{'label': ' GSOO expansions only', 'value': 'on'}],
                      value=[], inputClassName='md-switch-input', labelClassName='md-switch-label',
                      style={'marginBottom': '2px', 'fontSize': '12px'}),
        html.Div('Off: the capacity model may build any candidate in '
                 'expansion_options.csv, including pre-FID projects from GARY\'s '
                 'own market scan (Bulloo Interlink, the Geelong FSRUs, Golden '
                 'Beach, Outer Harbor, the VTS expansion). On: only expansions '
                 'AEMO counts as committed in the 2026 GSOO/VGPR.',
                 style={'marginBottom': '16px', 'fontSize': '10px',
                        'color': '#888', 'lineHeight': '1.35', 'paddingLeft': '38px'}),

        dcc.Checklist(id='netback-toggle',
                      options=[{'label': ' LNG netback pricing (ACIL Allen)', 'value': 'on'}],
                      value=['on'] if NETBACK_DEFAULT else [], inputClassName='md-switch-input', labelClassName='md-switch-label',
                      style={'marginBottom': '2px', 'fontSize': '12px'}),
        html.Div('On (default): trains bid for gas at the export netback and '
                 'imports are priced at ACIL Allen\'s injection cost, so the '
                 'international price disciplines domestic prices. Off: LNG '
                 'exports revert to must-serve demand at any price, and the '
                 'Global LNG lever scales export volume instead of selecting a '
                 'price path.',
                 style={'marginBottom': '16px', 'fontSize': '10px',
                        'color': '#888', 'lineHeight': '1.35', 'paddingLeft': '38px'}),


        dcc.Checklist(id='foresight-toggle',
                      options=[{'label': ' Perfect-foresight capacity build', 'value': 'on'}],
                      value=['on'], inputClassName='md-switch-input', labelClassName='md-switch-label',
                      style={'marginBottom': '20px', 'fontSize': '12px'}),

        slider_group('Discount Rate (capacity NPV)',
            dcc.Slider(id='discount-slider', min=0, max=0.12, step=0.01, value=0.07,
                       marks={0: '0%', 0.05: '5%', 0.07: '7%', 0.1: '10%'},
                       tooltip={'placement': 'bottom', 'always_visible': True})),

        slider_group('Optimality Gap',
            dcc.Slider(id='gap-slider', min=0, max=0.05, step=0.001,
                       value=_MIP_GAP_DEFAULT,
                       marks={0: '0%', 0.01: '1%', 0.02: '2%', 0.05: '5%'},
                       tooltip={'placement': 'bottom', 'always_visible': True})),

        slider_group('Representative days per year',
            dcc.Slider(id='rep-slider', min=1, max=6, step=1,
                       marks={k: str(12 * k + 1) for k in range(1, 7)},
                       value=_REP_BINS_DEFAULT)),
        html.Div('Load bins per month in the capacity layer, marked as the '
                 'representative days a year they produce. ONE bin is a single monthly '
                 'MEAN day, which flattens the load-duration curve inside the month: a '
                 'mild day and a cold snap become one average day, though cheap southern '
                 'gas clears the first and an import terminal the second. More bins keep '
                 'the investment plan in step with what the 365-day dispatch layer '
                 'actually draws, at roughly linear cost in solve time.',
                 style={'fontSize': '11px', 'opacity': 0.7, 'marginBottom': '16px'}),

        html.Hr(className='md-divider'),

        # ── Results ───────────────────────────────────────────────────────
        html.P('Results', className='md-section-label'),

        html.Span('Active Scenario', className='md-input-label'),
        dcc.Dropdown(id='result-selector', placeholder='No results yet…',
                     style={'marginBottom': '20px', 'fontSize': '12px'}),

        html.Span('Analysis Horizon', className='md-input-label'),
        dcc.Slider(id='horizon-slider', min=2025, max=2050, step=1, value=2050,
                   marks={y: str(y) for y in range(2025, 2051, 5)}),
    ]),

    html.Div(style={'padding': '12px 20px 20px', 'borderTop': '1px solid rgba(255,255,255,0.10)', 'marginTop': 'auto'}, children=[
        html.Button('◑  Dark Mode', id='theme-toggle-btn', className='theme-toggle'),
    ]),
])

# ---------------------------------------------------------------------------
# Main content
# ---------------------------------------------------------------------------
main = html.Div(className='md-main', children=[

    # Header
    html.Div(className='md-header', children=[
        html.Span('GARY — Gas Allocation and Regional Yield Model', className='md-header-title'),
        html.Div(id='header-scenario-chip', className='md-scenario-chip',
                 children='No scenario loaded'),
    ]),

    # Content area
    html.Div(className='md-content', children=[

        # KPI row
        html.Div(id='kpi-row', className='md-kpi-row', children=[
            kpi_card('Final Price', '—'),
            kpi_card('System Cost', '—'),
            kpi_card('Total Supply', '—'),
            kpi_card('New Projects', '—'),
        ]),

        # Tabs
        html.Div(className='md-tabs-wrap', children=[
            dcc.Tabs(id='main-tabs', value='tab-map', className='nav-tabs', children=[
                dcc.Tab(label='Network Map',          value='tab-map',    className='nav-link', selected_className='nav-link active'),
                dcc.Tab(label='Production & Dispatch', value='tab-prod',   className='nav-link', selected_className='nav-link active'),
                dcc.Tab(label='Storage Dynamics',      value='tab-storage', className='nav-link', selected_className='nav-link active'),
                dcc.Tab(label='Price Outcomes',        value='tab-price',  className='nav-link', selected_className='nav-link active'),
                dcc.Tab(label='Supply Curves',         value='tab-supply', className='nav-link', selected_className='nav-link active'),
                dcc.Tab(label='Expansions',            value='tab-exp',    className='nav-link', selected_className='nav-link active'),
                dcc.Tab(label='GPG & Large Users',     value='tab-ind',    className='nav-link', selected_className='nav-link active'),
            ]),
        ]),

        # ── Tab panels ──────────────────────────────────────────────────────
        html.Div(className='md-tab-panel', children=[

            # Network Map
            html.Div(id='tab-map-content', children=[
                html.Div(className='md-map-controls', children=[
                    html.Div([
                        html.Span('Map Year', className='md-input-label'),
                        html.Div(dcc.Slider(id='map-year', min=2025, max=2050, step=1, value=2050,
                                   marks={y: str(y) for y in range(2025, 2051, 5)}),
                                   style={'width': '380px'}),
                    ]),
                    dcc.Checklist(id='map-options',
                                  options=[
                                      {'label': ' Labels',   'value': 'labels'},
                                      {'label': ' Capacity', 'value': 'capacity'},
                                  ],
                                  value=['labels', 'capacity'],
                                  inline=True),
                ]),
                html.Div(id='map-kpi-row', className='md-map-kpi-row'),
                dcc.Loading(type='circle', color='#1976D2', children=
                    html.Div(id='map-graph-wrap', style={'height': '720px', 'borderRadius': '10px', 'overflow': 'hidden'})
                ),
            ]),

            # Production & Dispatch
            html.Div(id='tab-prod-content', style={'display': 'none'}, children=[
                dcc.Graph(id='prod-annual-graph',   style={'marginBottom': '4px'}),
                _dl_btn('dl-prod-annual'),
                dcc.Graph(id='prod-dispatch-graph', style={'marginBottom': '4px'}),
                _dl_btn('dl-prod-dispatch'),
                dcc.Graph(id='flow-graph',          style={'marginBottom': '4px'}),
                _dl_btn('dl-flow'),
                html.Div(id='shortage-content'),
            ]),

            # Storage
            html.Div(id='tab-storage-content', style={'display': 'none'}, children=[
                dcc.Graph(id='storage-inventory-graph', style={'marginBottom': '4px'}),
                _dl_btn('dl-storage'),
                html.Div(id='storage-activity-content'),
            ]),

            # Prices
            # The cap-hitting charts live in their own Divs so they can be hidden
            # outright in scenarios where no node ever reaches the cap -- an empty
            # "nodes reaching the cap" chart says nothing the other chart doesn't.
            html.Div(id='tab-price-content', style={'display': 'none'}, children=[
                html.Div(id='price-high-block', children=[
                    dcc.Graph(id='price-high-graph',  style={'marginBottom': '4px'}),
                    _dl_btn('dl-price-high'),
                ]),
                dcc.Graph(id='price-low-graph', style={'marginBottom': '4px'}),
                _dl_btn('dl-price-low'),
                html.Div(id='price-q-high-block', children=[
                    dcc.Graph(id='price-q-high-graph', style={'marginBottom': '4px'}),
                    _dl_btn('dl-price-q-high'),
                ]),
                dcc.Graph(id='price-q-low-graph', style={'marginBottom': '4px'}),
                _dl_btn('dl-price-q-low'),
                html.Div(id='price-a-high-block', children=[
                    dcc.Graph(id='price-a-high-graph', style={'marginBottom': '4px'}),
                    _dl_btn('dl-price-a-high'),
                ]),
                dcc.Graph(id='price-a-low-graph'),
                _dl_btn('dl-price-a-low'),
                html.Hr(style={'margin': '26px 0 14px'}),
                html.Div(id='segment-price-content'),
            ]),

            # Supply curves
            html.Div(id='tab-supply-content', style={'display': 'none'}, children=[
                html.Div(className='md-map-controls', children=[
                    html.Div([
                        html.Span('Curve', className='md-input-label'),
                        dcc.RadioItems(
                            id='supply-mode',
                            options=[
                                {'label': ' Residual (net of exports & other nodes)',
                                 'value': 'residual'},
                                {'label': ' Gross (all gas available that year)',
                                 'value': 'gross'},
                            ],
                            value='residual', inline=True),
                    ]),
                ]),
                dcc.Loading(type='circle', color='#1976D2', children=
                    dcc.Graph(id='supply-curve-graph', style={'marginBottom': '4px'})),
                _dl_btn('dl-supply'),
                html.Div(id='supply-curve-note'),
            ]),

            # Expansions
            html.Div(id='tab-exp-content', style={'display': 'none'}, children=[
                html.Div([
                    html.Span('Sort table by', style={'marginRight': '8px', 'fontSize': '0.85rem',
                                                        'color': 'var(--md-text-med)'}),
                    dcc.Dropdown(
                        id='exp-sort-col',
                        options=[{'label': c, 'value': c} for c in
                                 ['Year', 'Project', 'Type', 'New Capacity (TJ/d)',
                                  'CapEx ($M)', 'Annualised Cost ($M/yr)']],
                        value='Year', clearable=False, searchable=False,
                        style={'width': '230px', 'display': 'inline-block', 'verticalAlign': 'middle'},
                    ),
                    dcc.RadioItems(
                        id='exp-sort-dir',
                        options=[{'label': 'Ascending', 'value': 'asc'},
                                 {'label': 'Descending', 'value': 'desc'}],
                        value='asc', inline=True,
                        style={'marginLeft': '12px', 'fontSize': '0.85rem', 'display': 'inline-block'},
                        labelStyle={'marginRight': '10px'},
                    ),
                ], style={'display': 'flex', 'alignItems': 'center', 'marginTop': '8px'}),
                html.Div(id='expansions-content', className='md-table', style={'marginTop': '8px'}),
            ]),

            # Industrial
            html.Div(id='tab-ind-content', style={'display': 'none'}, children=[
                dcc.Graph(id='ind-graph'),
                _dl_btn('dl-ind'),
            ]),

        ]),
    ]),
])

# ---------------------------------------------------------------------------
# Root layout
# ---------------------------------------------------------------------------
app.layout = html.Div(
    style={'display': 'flex', 'minHeight': '100vh', 'backgroundColor': 'var(--md-bg)'},
    children=[
        dcc.Store(id='refresh-counter', data=0),
        dcc.Store(id='theme-store', storage_type='local', data='light'),
        dcc.Download(id='chart-dl'),          # per-chart Excel download target
        sidebar,
        main,
    ],
)

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------
def reservation_share(toggle_value, slider_index):
    """Sidebar toggle + slider -> reserved share of LNG exports (0 if off)."""
    if not toggle_value or 'on' not in toggle_value:
        return 0.0
    i = 1 if slider_index is None else int(slider_index)
    return RESERVATION_LEVELS[max(0, min(i, len(RESERVATION_LEVELS) - 1))]


def datacentre_spec(nsw_pj, vic_pj, start_year, path=None):
    """Sidebar inputs -> the data centre lever, or None when nothing is set.

    ``path`` optionally links a spreadsheet holding a year-by-year series (see
    datacentre_series.py). A state with a column in that file takes its volumes
    from it; a state without one keeps using its cell, so a file covering only
    NSW leaves the VIC box doing exactly what it did before.

    None rather than a zero-volume dict on purpose: it is what keeps the scenario
    key (and therefore every cached result solved before this lever existed)
    unchanged whenever the boxes are left empty.

    Raises ``DataCentreSeriesError`` if a path is given and cannot be read. That
    is deliberate -- a mistyped path is indistinguishable, in the results, from a
    data centre scenario in which the load happened not to matter.
    """
    nsw = float(nsw_pj or 0)
    vic = float(vic_pj or 0)
    series = datacentre_series.load(path) if (path or '').strip() else {}
    if nsw <= 0 and vic <= 0 and not series:
        return None
    spec = {'NSW': nsw, 'VIC': vic, 'start_year': int(start_year or 2030)}
    if series:
        spec['series'] = series
        spec['source'] = (path or '').strip()
        spec['fingerprint'] = datacentre_series.fingerprint(series)
        spec['label'] = datacentre_series.label(path)
    return spec


def datacentre_segment(datacentre):
    """Data centre part of a scenario key: ``_DC<nsw>N<vic>V<year>``, ``_DCS<name>``.

    A state supplied by a linked series contributes 0 to the cell segment (its
    cell is not being used), and the segment is dropped entirely when no cell is
    in play -- which keeps every key solved before this lever, and before the
    series option, byte-identical to what it was.

    The series contributes its FILE'S NAME, sanitised (datacentre_series.label):
    a run reads as "the NSW pipeline" rather than as an opaque hash. The
    fingerprint is still computed and saved on the spec, but it no longer keys
    the run -- so editing a volume now overwrites that name's cached result
    instead of filing a new one. The file names a scenario you re-run.
    """
    if not datacentre:
        return ''
    series = datacentre.get('series') or {}
    nsw = 0.0 if 'NSW' in series else float(datacentre.get('NSW', 0) or 0)
    vic = 0.0 if 'VIC' in series else float(datacentre.get('VIC', 0) or 0)
    cell = (f"_DC{nsw:g}N{vic:g}V{datacentre['start_year']}") if (nsw or vic) else ''
    if not series:
        return cell
    # Series segment FIRST so both labels below read in that order: the file is
    # the substantive statement, any surviving cell is the "plus" on the end.
    # The two patterns cannot match each other -- _DC wants a digit next, _DCS
    # wants an 'S' -- so their order in the key is free.
    return ('_DCS' + (datacentre.get('label')
                      or datacentre_series.label(datacentre.get('source')))) + cell




def scenario_key(baseline, winter, lng, dunkelflaute=False, reservation=0.0,
                 foresight=True, discount=0.07, datacentre=None,
                 netback=False, respect_contracts=True, gsoo_exp=False,
                 allow_imports=True, rep_bins=None):
    """Cache key for one scenario.

    Segment order is load-bearing — pretty_key parses it and the cached results on
    disk are filed under it — so every caller builds its keys here rather than
    inline, or a sweep silently writes keys the dropdown can't read back.
    """
    dc = datacentre_segment(datacentre)
    return (f'Base_{baseline}_Winter_{winter}_LNG_{lng}'
            + ('_Dunkelflaute' if dunkelflaute else '')
            + ((f'_Reserve{round(reservation * 100)}'
                + ('' if respect_contracts else 'incl')) if reservation else '')
            + dc
            + ('_GSOOExp' if gsoo_exp else '')
            + ('' if allow_imports else '_NoImports')
            + ('_Netback' if netback else '')
            + ('' if foresight else '_Myopic')
            + (f'_DR{round(discount * 100)}' if foresight and abs(discount - 0.07) > 1e-9 else '')
            # Only when it differs from the sheet, so every key solved before this
            # lever existed stays byte-identical.
            + (f'_Rep{rep_bins}' if rep_bins and rep_bins != _REP_BINS_DEFAULT else ''))


def get_filtered(key, end_year):
    data = load_results()
    return [r for r in data['all_scenarios'].get(key, []) if r['Year'] <= end_year]


def served_volume(res, nodes):
    """Gas that physically arrived at each of `nodes` on each day, TJ.

    Net inflow -- arrivals less what left again -- so gas that only passed
    through on its way somewhere else (Melbourne -> Sydney, Sydney -> Port
    Kembla) is not counted as consumed at the transit node. For Demand and LNG
    nodes, which carry no production and no storage, net inflow is exactly the
    gas consumed or exported there, which is the weight any delivered-price
    average wants.

    Returns a Node/Day-indexed 'Vol' Series, or None when the year has no flow
    data at all.
    """
    _flow = res['flow']
    if _flow.empty:
        return None
    # Names are stored as categoricals with per-frame category sets, so From/To
    # and Node do not align as indexes. Compare and group on plain strings.
    nodes = {str(n) for n in nodes}
    f = _flow[['From', 'To', 'Day', 'Value']].astype({'From': str, 'To': str})
    inflow  = (f[f['To'].isin(nodes)]
               .groupby(['To', 'Day'])['Value'].sum())
    outflow = (f[f['From'].isin(nodes)]
               .groupby(['From', 'Day'])['Value'].sum())
    inflow.index.names = outflow.index.names = ['Node', 'Day']
    served = inflow.sub(outflow, fill_value=0.0).clip(lower=0).rename('Vol')
    return None if served.empty else served


def delivered_price(res):
    """Volume-weighted delivered price across the demand centres, $/GJ.

    The headline price has to be the one buyers face, so it is taken at the
    Demand nodes -- not at the fields, and not at the LNG trains. The Price
    Outcomes chart PLOTS the trains as well, but its average line uses this same
    demand-node set, so the card and the line agree. Weighting an average price by *production* instead answers a
    different question: it lands on the wellhead, where Surat's volume swamps
    everything and the transport differential to Melbourne (~$10/GJ in a tight
    year) never appears at all.

    The weight is what physically arrived at the node each day, net of gas that
    only passed through on its way somewhere else (Melbourne -> Sydney, Sydney ->
    Port Kembla). Demand nodes carry no production and no storage, so net inflow
    is exactly the gas consumed there. Weighting per node-DAY rather than per
    node is what lets winter count properly: that is when both the volumes and
    the prices are high, and a flat average over days would wash it out.

    Returns None when a year has no priced delivery at all, so the caller can
    fall back rather than divide by zero.
    """
    _prices = res['prices']
    if _prices.empty:
        return None
    demand_nodes = set(static_data['nodes']
                       .loc[static_data['nodes']['Type'] == 'Demand', 'Name'])
    served = served_volume(res, demand_nodes)
    if served is None:
        return None
    merged = (_prices.astype({'Node': str})
              .merge(served.reset_index(), on=['Node', 'Day']))
    total = merged['Vol'].sum()
    if merged.empty or total <= 0:
        return None
    return float((merged['Price'] * merged['Vol']).sum() / total)


def headline_price(res):
    """The one price figure the dashboard quotes for a year, $/GJ.

    Every card that shows "the price" goes through here, so the header KPI and
    the map KPI cannot drift apart again -- which is exactly what had happened
    when one weighted by production and the other by throughput.

    Falls back to a mean over the priced demand nodes when a year delivered
    nothing to weight. A plain mean over ALL nodes would sweep in undeveloped
    ones, whose nodal dual sits at the $300 value of lost load and is a shadow
    price rather than a market price.
    """
    p_avg = delivered_price(res)
    if p_avg is not None:
        return p_avg
    _prices = res['prices']
    if _prices.empty:
        return float('nan')
    demand_names = static_data['nodes'].loc[
        static_data['nodes']['Type'] == 'Demand', 'Name'].tolist()
    _dem_p = _prices[_prices['Node'].astype(str).isin(demand_names)]
    return float(_dem_p['Price'].mean() if not _dem_p.empty
                 else _prices['Price'].mean())


def exp_label(name):
    """Display name for an expansion project.

    Reads the `Label` column of expansion_options.csv so the readable name lives
    with the data rather than in a dict in the UI, and falls back to the raw name
    with underscores stripped — a project added to the CSV without a Label still
    renders, just less prettily.
    """
    row = static_data['expansion']
    hit = row[row['Name'] == name]
    if not hit.empty and 'Label' in hit.columns:
        val = hit.iloc[0]['Label']
        if isinstance(val, str) and val.strip():
            return val.strip()
    return str(name).replace('_', ' ')


def build_summary(filtered_results, discount_rate=0.07):
    rows, prices_trend, builds_timeline, total_cost = [], [], [], 0
    exp_lookup = static_data['expansion'].set_index('Name').to_dict('index')
    premium_by_node = _2c_premium_by_node()
    for res in filtered_results:
        y = res['Year']
        _prices = res['prices']
        _prod   = res['production']
        p_avg   = headline_price(res)
        rows.append({'Year': y, 'Production_PJ': _prod['Value'].sum() / 1000,
                     'Shortage_TJ': res['shortage']['Value'].sum(),
                     'Avg_Price': p_avg})
        total_cost += res['total_cost']
        if not _prices.empty:
            prices_trend.append(_prices[['Node', 'Price']].assign(Year=y))
        for b in res['builds']:
            if not any(bt['Project'] == b for bt in builds_timeline):
                info = exp_lookup.get(b, {})
                ann = _annualised_cost(info, discount_rate, premium_by_node)
                builds_timeline.append({
                    'Year': y,
                    # Project stays the RAW name: it is the join key back to
                    # expansion_options.csv and to res['builds'], and several
                    # callers look up by it. Label is the display string.
                    'Project': b,
                    'Label': exp_label(b),
                    'Type': info.get('Type', '—'),
                    'New Capacity (TJ/d)': info.get('NewCapacity', '—'),
                    'CapEx ($M)': f"{info['CapEx']/1e6:,.0f}" if info.get('CapEx') else '—',
                    'Annualised Cost ($M/yr)': f"{ann/1e6:,.1f}" if ann is not None else '—',
                })
    return (
        pd.DataFrame(rows) if rows else pd.DataFrame(columns=['Year','Production_PJ','Shortage_TJ','Avg_Price']),
        pd.concat(prices_trend, ignore_index=True)[['Year', 'Node', 'Price']]
        if prices_trend else pd.DataFrame(columns=['Year', 'Node', 'Price']),
        pd.DataFrame(builds_timeline) if builds_timeline else pd.DataFrame(columns=['Year','Project','Label','Type','New Capacity (TJ/d)','CapEx ($M)','Annualised Cost ($M/yr)']),
        total_cost,
    )

def blank_fig(tmpl=CHART_TEMPLATE):
    fig = go.Figure()
    fig.update_layout(template=tmpl,
                      paper_bgcolor=MD_SURFACE, plot_bgcolor=MD_SURFACE)
    return fig

# ---------------------------------------------------------------------------
# Tab show / hide
# ---------------------------------------------------------------------------
@app.callback(
    [Output('tab-map-content',     'style'),
     Output('tab-prod-content',    'style'),
     Output('tab-storage-content', 'style'),
     Output('tab-price-content',   'style'),
     Output('tab-supply-content',  'style'),
     Output('tab-exp-content',     'style'),
     Output('tab-ind-content',     'style')],
    Input('main-tabs', 'value'),
)
def show_tab(active):
    order = ['tab-map','tab-prod','tab-storage','tab-price','tab-supply','tab-exp','tab-ind']
    return [{'display': 'block'} if t == active else {'display': 'none'} for t in order]

# ---------------------------------------------------------------------------
# Run Scenario (background)
# ---------------------------------------------------------------------------
@app.callback(
    Output('refresh-counter', 'data',     allow_duplicate=True),
    Output('run-status',      'children', allow_duplicate=True),
    Input('run-btn', 'n_clicks'),
    State('winter-slider', 'value'),
    State('lng-slider',    'value'),
    State('gap-slider',    'value'),
    State('rep-slider',    'value'),
    State('baseline-selector', 'value'),
    State('dunkelflaute-toggle', 'value'),
    State('reservation-toggle', 'value'),
    State('reservation-slider', 'value'),
    State('discount-slider', 'value'),
    State('foresight-toggle', 'value'),
    State('netback-toggle', 'value'),
    State('gsoo-exp-toggle', 'value'),
    State('imports-toggle', 'value'),
    State('contracts-toggle', 'value'),
    State('dc-nsw-input', 'value'),
    State('dc-vic-input', 'value'),
    State('dc-start-slider', 'value'),
    State('dc-file-input', 'value'),
    State('refresh-counter', 'data'),
    background=True,
    running=[
        (Output('run-btn',          'disabled'), True,  False),
        (Output('batch-btn',        'disabled'), True,  False),
        (Output('solver-progress',  'style'),
         {'display': 'block'}, {'display': 'none'}),
        (Output('run-status', 'children'), '⏳  Solving…', ''),
    ],
    progress=[Output('solver-progress-bar', 'style'), Output('solver-progress-bar', 'children')],
    prevent_initial_call=True,
)
def run_scenario(set_progress, n_clicks, wi, li, gap, rep_bins, baseline, dunkel, resv_on, resv_i,
                 discount, foresight_v, netback_v, gsoo_exp_v, imports_v, contracts_v,
                 dc_nsw, dc_vic, dc_start, dc_file, refresh):
    w, l = LEVELS[wi], LEVELS[li]
    baseline = baseline or 'StepChange'
    dunkelflaute = bool(dunkel) and 'on' in dunkel
    reservation = reservation_share(resv_on, resv_i)
    foresight = 'on' in (foresight_v or [])
    dr = 0.07 if discount is None else float(discount)
    netback = 'on' in (netback_v or [])
    gsoo_exp = 'on' in (gsoo_exp_v or [])
    allow_imports = 'on' in (imports_v or [])
    respect_contracts = 'on' in (contracts_v or [])
    try:
        datacentre = datacentre_spec(dc_nsw, dc_vic, dc_start, dc_file)
    except datacentre_series.DataCentreSeriesError as exc:
        # Stop rather than solve without the load: a run that silently dropped a
        # linked series would be indistinguishable from one where it did nothing.
        return no_update, f'✗  Data centre series — {exc}'
    def _cb(yr, p):
        pct = int(p * 100)
        set_progress(_progress(pct, f'Solving {yr}… {pct}%'))
    # Built before the solve so it can also title the terminal log.
    key = scenario_key(baseline, w, l, dunkelflaute, reservation, foresight, dr,
                       datacentre, netback, respect_contracts, gsoo_exp,
                       allow_imports, rep_bins)
    result = solve_scenario(w, l, mip_gap=gap, rep_bins=rep_bins, callback=_cb,
                            baseline=baseline, dunkelflaute=dunkelflaute,
                            discount_rate=dr, foresight=foresight,
                            reservation=reservation,
                            datacentre=datacentre, netback_pricing=netback,
                            respect_contracts=respect_contracts,
                            gsoo_expansions_only=gsoo_exp,
                            allow_import_terminals=allow_imports,
                            title=pretty_key(key))
    data = load_results()
    data['all_scenarios'][key] = result
    data['current_key'] = key
    save_results(data)
    return (refresh or 0) + 1, f'✓  {pretty_key(key)}'

@app.callback(
    Output('reservation-slider-wrap', 'style'),
    Input('reservation-toggle', 'value'),
)
def toggle_reservation_slider(v):
    return {'display': 'block'} if v and 'on' in v else {'display': 'none'}


@app.callback(
    Output('dc-file-status', 'children'),
    Output('dc-file-status', 'style'),
    Input('dc-file-input', 'value'),
)
def check_datacentre_file(path):
    """Read the linked series as soon as it is typed and say what it found.

    Here rather than only at solve time because the alternative is finding out
    that a path is wrong, or that a workbook's NSW column is called something
    the reader does not recognise, several minutes into a run. It also names
    which states the file answers for, since those are the ones whose cell above
    has stopped mattering.
    """
    base = {'marginBottom': '4px', 'fontSize': '10px', 'lineHeight': '1.35'}
    if not (path or '').strip():
        return '', {**base, 'display': 'none'}
    try:
        series = datacentre_series.load(path)
    except datacentre_series.DataCentreSeriesError as exc:
        return f'✗  {exc}', {**base, 'color': '#E53935'}
    cells = [st for st in ('NSW', 'VIC') if st not in series]
    note = (f'  ·  {" and ".join(cells)} still from the box above'
            if cells else '')
    return (f'✓  {datacentre_series.describe(series)}{note}',
            {**base, 'color': '#00897B'})


def _run_sweep(jobs, data, set_progress):
    """Solve a list of (key, title, kwargs) scenarios and cache each as it lands.

    Saving inside the completion callback rather than at the end means an
    interrupted sweep keeps every scenario that finished, and the dropdown fills
    up as it goes. Scenarios finish out of order once there is more than one
    worker, so each is filed under the key that came back with it.
    """
    if not jobs:
        set_progress(_progress(100, 'Nothing to solve — every scenario already cached'))
        return
    workers = min(default_workers(), len(jobs))
    note = f' on {workers} workers' if workers > 1 else ''

    def _done(i, n, key, results, secs):
        data['all_scenarios'][key] = results
        data['current_key'] = key
        save_results(data)
        pct = int(i / n * 100)
        set_progress(_progress(pct, f'{i}/{n} scenarios complete{note} — {pct}%'))

    def _year(i, n, yr, frac):
        pct = int((i - 1 + frac) / n * 100)
        set_progress(_progress(pct, f'Scenario {i}/{n} · Year {yr} — {pct}%'))

    set_progress(_progress(0, f'Solving {len(jobs)} scenarios{note}…'))
    run_jobs(jobs, workers=workers, on_done=_done, on_year=_year)


# ---------------------------------------------------------------------------
# Run the standard set (background)
# ---------------------------------------------------------------------------
@app.callback(
    Output('refresh-counter', 'data',     allow_duplicate=True),
    Output('run-status',      'children', allow_duplicate=True),
    Input('batch-btn', 'n_clicks'),
    State('gap-slider', 'value'),
    State('rep-slider', 'value'),
    State('refresh-counter', 'data'),
    background=True,
    running=[
        (Output('run-btn',         'disabled'), True,  False),
        (Output('batch-btn',       'disabled'), True,  False),
        (Output('solver-progress', 'style'),
         {'display': 'block'}, {'display': 'none'}),
        (Output('run-status', 'children'), '\u23f3  Standard set running\u2026', ''),
    ],
    progress=[Output('solver-progress-bar', 'style'), Output('solver-progress-bar', 'children')],
    prevent_initial_call=True,
)
def run_batch(set_progress, n_clicks, gap, rep_bins, refresh):
    """Solve the standard scenario set -- the same 17 runs as `python src/run_standard_set.py`.

    THE SIDEBAR IS DELIBERATELY IGNORED except for the two numerical solve settings
    (MIP gap, representative-day bins). The set is a fixed, documented list in
    `run_standard_set.build()`, and its whole value is that it is the same list every
    time: six blocks -- outlook, weather, structural, data centres, and the two
    reservation blocks that differ only by whether foundation contracts are respected.
    Letting the sidebar bend it would mean the cache held seventeen runs whose identity
    depended on what the sliders happened to be set to when somebody pressed the button.

    Use "Run This Scenario" for anything the sidebar describes. That is the division:
    one button for the standard set, one for whatever you are looking at.

    Already-cached keys are skipped, so a re-run after adding a block costs one solve
    rather than seventeen. Clear Results to force a full rebuild.
    """
    import run_standard_set

    try:
        spec = run_standard_set.build()
    except datacentre_series.DataCentreSeriesError as exc:
        # Stop rather than solve without the load: a run that silently dropped the
        # data centre series would be indistinguishable from one where it did nothing.
        return no_update, f'\u2717  Data centre series \u2014 {exc}'

    data = load_results()
    jobs, blocks = [], []
    for block, kw in spec:
        key = run_standard_set.key_for(kw)
        if block not in blocks:
            blocks.append(block)
        # The dunkelflaute case is always re-solved so edits to the event flow
        # through; everything else is skipped if it is already in the cache.
        if kw.get('dunkelflaute') or key not in data['all_scenarios']:
            jobs.append((key, pretty_key(key),
                         dict(kw, mip_gap=gap, rep_bins=rep_bins)))

    if not jobs:
        return no_update, f'\u2713  Standard set \u2014 all {len(spec)} scenarios already cached'

    _run_sweep(jobs, data, set_progress)
    skipped = len(spec) - len(jobs)
    note = f' ({skipped} already cached)' if skipped else ''
    return (refresh or 0) + 1, (f'\u2713  Standard set complete \u2014 {len(spec)} scenarios, '
                                f'{len(blocks)} blocks{note}')

# ---------------------------------------------------------------------------
# Clear
# ---------------------------------------------------------------------------
@app.callback(
    Output('refresh-counter', 'data',     allow_duplicate=True),
    Output('run-status',      'children', allow_duplicate=True),
    Input('clear-btn', 'n_clicks'),
    State('refresh-counter', 'data'),
    prevent_initial_call=True,
)
def clear_results(n, refresh):
    if os.path.exists(RESULTS_FILE):
        os.remove(RESULTS_FILE)
    return (refresh or 0) + 1, 'Results cleared'

# ---------------------------------------------------------------------------
# Regen demand
# ---------------------------------------------------------------------------
@app.callback(
    Output('run-status', 'children', allow_duplicate=True),
    Input('regen-btn', 'n_clicks'),
    background=True,
    running=[
        (Output('run-btn',     'disabled'), True, False),
        (Output('batch-btn',   'disabled'), True, False),
        (Output('regen-btn',   'disabled'), True, False),
        (Output('solver-progress', 'style'),
         {'display': 'block'}, {'display': 'none'}),
        (Output('run-status', 'children'), '⏳  Regenerating data…', ''),
    ],
    progress=[Output('solver-progress-bar', 'style'), Output('solver-progress-bar', 'children')],
    prevent_initial_call=True,
)
def regen_demand(set_progress, n):
    if not n:
        return no_update
    def _cb(label, frac):
        pct = int(frac * 100)
        set_progress(_progress(pct, f'{label} — {pct}%'))
    regenerate_all(progress=_cb)
    set_progress(_progress(100, 'Complete — 100%'))
    return '✓  All data regenerated from source'

# ---------------------------------------------------------------------------
# Result selector + horizon max
# ---------------------------------------------------------------------------
@app.callback(
    Output('result-selector',  'options'),
    Output('result-selector',  'value'),
    Output('horizon-slider',   'max'),
    Input('refresh-counter', 'data'),
    State('result-selector',   'value'),
)
def update_selector(refresh, current):
    data = load_results()
    keys = list(data['all_scenarios'].keys())
    if not keys:
        return [], None, 2050
    # After a solve (refresh > 0), jump to the newly run scenario.
    # On initial load (refresh is None/0), honour the current dropdown value.
    if refresh and data.get('current_key') and data['current_key'] in keys:
        selected = data['current_key']
    elif current and current in keys:
        selected = current
    else:
        selected = data.get('current_key') or keys[0]
    results  = data['all_scenarios'].get(selected, [])
    max_year = max((r['Year'] for r in results), default=2050)
    return [{'label': short_key(k), 'value': k} for k in keys], selected, max_year

# ---------------------------------------------------------------------------
# Header chip + KPI row
# ---------------------------------------------------------------------------
@app.callback(
    Output('header-scenario-chip', 'children'),
    Output('kpi-row',              'children'),
    Input('result-selector', 'value'),
    Input('horizon-slider',  'value'),
)
def update_header_kpis(key, end_year):
    empty = [kpi_card(t, '—') for t in ['Final Price', 'System Cost', 'Total Supply', 'New Projects']]
    if not key:
        return 'No scenario loaded', empty
    filtered = get_filtered(key, end_year)
    if not filtered:
        return key, empty
    summary, _, builds_df, total_cost = build_summary(filtered)
    final_price = f"${summary['Avg_Price'].iloc[-1]:.2f}/GJ" if not summary.empty else '—'
    final_year = int(summary['Year'].iloc[-1]) if not summary.empty else end_year
    reserved_pj = sum(r.get('lng_reserved_tj', 0) for r in filtered) / 1000
    # How much of the reserved volume the domestic market actually absorbed. It is
    # offered at $0 so it is taken up wherever it can physically reach a buyer;
    # a gap means the market could not absorb it, not that it was uneconomic.
    served_pj = sum(r.get('reserved_served_tj', 0) for r in filtered) / 1000
    netback_run = any(r.get('netback_pricing') for r in filtered)
    respects = next((r.get('reservation_respects_contracts', True) for r in filtered), True)
    caveats = []
    if reserved_pj:
        if not netback_run:
            caveats.append('no export revenue counted')
        elif respects:
            caveats.append('incl. export revenue forgone')
        else:
            caveats.append('export revenue counted on spot tail only')
        caveats.append('reserved gas at $0')
    cost_caveat = ' · '.join(caveats)
    chips = [
        kpi_card('Final Price', [final_price,
                                 html.Span(f'demand-weighted, {final_year}',
                                           className='md-kpi-sub')]),
        # What the objective does and does not count, spelled out under the number
        # rather than in the title, because it depends on three switches at once.
        #
        # Export revenue exists ONLY under netback pricing: model.py initialises
        # LNGNodes to an empty set otherwise, so lng_benefit is identically zero
        # and a reservation looks free because it removes demand nothing was
        # paying for. With netback on, the reservation shrinks the spot ceiling by
        # the full applied share, so the revenue it forgoes IS costed -- but only
        # on the contestable tail. Foundation volume is written back into
        # node_demand as must-serve and carries no revenue, so a reservation that
        # breaks contracts still loses export value the objective never sees.
        #
        # The reserved tranche is priced at $0/GJ in every mode, so reserving more
        # always lowers the number regardless. It is the cost of serving what was
        # served, never a welfare number.
        kpi_card('System Cost',
                 [f"${total_cost/1e6:,.0f}M"]
                 + ([html.Span(cost_caveat, className='md-kpi-sub')]
                    if cost_caveat else [])),
        kpi_card('Total Supply', f"{summary['Production_PJ'].sum():,.0f} PJ"),
        kpi_card('New Projects', str(len(builds_df))),
    ]
    if reserved_pj:
        # Requested vs applied: with foundation contracts respected the applied
        # share is capped at the uncontracted volume, so a 20% slider can deliver
        # 7%. Showing only the volume would hide that entirely.
        requested = next((r.get('reservation_share', 0) for r in filtered), 0)
        applied = next((r.get('reservation_share_applied', requested)
                        for r in filtered), requested)
        note = f"{served_pj/reserved_pj*100:.0f}% taken up"
        if applied and abs(applied - requested) > 1e-9:
            note += f" \u00b7 {requested*100:.0f}% asked, {applied*100:.0f}% allowed"
        chips.insert(3, kpi_card('Gas Reserved',
                                 [f"{reserved_pj:,.0f} PJ",
                                  html.Span(note, className='md-kpi-sub')]))
    # Data centre load carried over the horizon. Absent from any run solved
    # before this lever existed, and from any run with both boxes at zero.
    # LNG netback pricing: the export volume that priced itself out. Under
    # must-serve exports this is structurally zero, so the card only appears on a
    # netback run and only when the netback actually bound somewhere.
    if any(r.get('netback_pricing') for r in filtered):
        planned_pj = sum(r.get('lng_planned_tj', 0) for r in filtered) / 1000
        exported_pj = sum(r.get('lng_exported_tj', 0) for r in filtered) / 1000
        last_nb = next((r.get('netback_aud_gj', 0) for r in reversed(filtered)
                        if r.get('netback_aud_gj')), 0)
        spot_pj = sum(r.get('lng_spot_tj', 0) for r in filtered) / 1000
        sub = html.Span((f"{exported_pj/planned_pj*100:.0f}% of planned \u00b7 "
                         f"{spot_pj:,.0f} PJ contestable spot") if planned_pj else '',
                        className='md-kpi-sub')
        chips.insert(3, kpi_card('LNG Exported', [f"{exported_pj:,.0f} PJ", sub]))
        chips.insert(3, kpi_card('LNG Netback', f"${last_nb:.2f}/GJ"))
    dc_pj = sum(r.get('datacentre_tj', 0) for r in filtered) / 1000
    if dc_pj:
        chips.insert(3, kpi_card('Data Centre Load', f"{dc_pj:,.0f} PJ"))
    return pretty_key(key), chips

# ---------------------------------------------------------------------------
# Network Map
# ---------------------------------------------------------------------------
_MAP_CONFIG = {'scrollZoom': True, 'displayModeBar': True, 'modeBarButtonsToRemove': ['lasso2d', 'select2d']}

@app.callback(
    Output('map-kpi-row',    'children'),
    Output('map-graph-wrap', 'children'),
    Input('result-selector', 'value'),
    Input('horizon-slider',  'value'),
    Input('map-year',        'value'),
    Input('map-options',     'value'),
    Input('theme-store',     'data'),
)
def update_map(key, end_year, map_year, options, theme):
    dark = (theme == 'dark')
    cache_key = (key, end_year, map_year, tuple(sorted(options or [])), dark)
    try:
        if cache_key not in _map_fig_cache:
            _map_fig_cache[cache_key] = _update_map_inner(key, end_year, map_year, options, dark=dark)
            if len(_map_fig_cache) > 36:   # cap at ~36 combos
                _map_fig_cache.pop(next(iter(_map_fig_cache)))
        kpis, fig = _map_fig_cache[cache_key]
    except Exception as e:
        import traceback
        traceback.print_exc()
        fig = go.Figure()
        fig.update_layout(map=dict(style='carto-darkmatter' if dark else 'carto-positron', center=dict(lat=-24,lon=140), zoom=3.6),
                          margin=dict(l=0,r=0,t=0,b=0), height=720, paper_bgcolor='#1E1E2E' if dark else 'white')
        return [html.Span(f'Map error: {e}', style={'color':'red'})], dcc.Graph(
            id='map-graph-err', figure=fig, style={'height':'720px'}, config=_MAP_CONFIG)
    # Unique id per (key, map_year) forces React to fully remount the Plotly canvas
    graph_id = f'map-graph-{key or "none"}-{map_year}'
    return kpis, dcc.Graph(
        id=graph_id, figure=fig,
        style={'height': '720px', 'borderRadius': '10px', 'overflow': 'hidden'},
        config=_MAP_CONFIG,
    )

def _update_map_inner(key, end_year, map_year, options, dark=False):
    show_labels   = 'labels'   in (options or [])
    show_capacity = 'capacity' in (options or [])

    def empty():
        fig = go.Figure()
        fig.update_layout(
            map=dict(style='carto-darkmatter' if dark else 'carto-positron', center=dict(lat=-24, lon=140), zoom=3.6),
            margin=dict(l=0, r=0, t=0, b=0), height=720,
            paper_bgcolor='#1E1E2E' if dark else 'white',
        )
        return [], fig

    if not key:
        return empty()

    filtered = get_filtered(key, end_year)
    if not filtered:
        return empty()

    _, _, builds_df, _ = build_summary(filtered)
    res     = next((r for r in filtered if r['Year'] == map_year), filtered[-1])
    _prices = res['prices']
    _prod   = res['production']
    _flow   = res['flow']

    price_map = _prices.groupby('Node')['Price'].mean()
    prod_map  = _prod.groupby('Node')['Value'].sum() / 1000
    flow_map  = _flow.groupby(['From','To','Arc'])['Value'].sum().reset_index()
    flow_map['Value'] /= 1000

    # Per-node throughput (production + inflow, TJ/yr). Used to mask "phantom"
    # nodes: an undeveloped/disconnected potential node (e.g. Beetaloo before it is
    # built) carries no gas, so its nodal balance dual sits at the value-of-lost-load
    # ($300) — a real shadow price, but meaningless as a market price. It must not
    # paint the node hot.
    PRICE_MIN_TP = 1.0   # TJ/yr; below this a node is treated as unpriced
    _infl   = _flow.groupby('To')['Value'].sum() if not _flow.empty else pd.Series(dtype=float)
    _prod_n = _prod.groupby('Node')['Value'].sum() if not _prod.empty else pd.Series(dtype=float)
    throughput = _prod_n.add(_infl, fill_value=0.0)
    # Same measure as the header's Final Price card, taken for the year the map
    # is showing rather than the last year of the horizon, so stepping the year
    # slider moves this figure with the map.
    avg_price = headline_price(res)

    map_kpis = [
        map_kpi(f'Avg Price ({map_year})', f"${avg_price:.2f}/GJ"),
        map_kpi('Total Production',        f"{_prod['Value'].sum()/1000:.1f} PJ"),
        map_kpi('Total Shortage',
                f"{res['shortage']['Value'].sum():.1f} TJ"),
        map_kpi('Active Pipelines',        str(_flow[_flow['Value'] > 10]['Arc'].nunique())),
    ]

    arc_caps   = static_data['arcs'].set_index('Name')['Capacity'].to_dict()
    exp_info   = static_data['expansion']
    built_now  = builds_df[builds_df['Year'] <= map_year]['Project'].tolist()

    fig = go.Figure()
    PIPE_CASING = '#0d0d0d';  PIPE_FILL = '#B8860B';  PIPE_W = 3

    # Which arcs have been expanded by map_year?
    expanded_arcs = set(
        exp_info[(exp_info['Name'].isin(built_now)) & (exp_info['Type'] == 'Pipeline')]['Target'].tolist()
    )

    for _, arc_row in static_data['arcs'].iterrows():
        arc  = arc_row['Name']
        cap  = (arc_caps.get(arc, 0) + exp_info[
                    (exp_info['Target'] == arc) &
                    (exp_info['Name'].isin(built_now))]['NewCapacity'].sum()) * 365 / 1000
        path = ARC_WAYPOINTS.get(arc, [COORDS[arc_row['From']], COORDS[arc_row['To']]])
        lats = [p[0] for p in path];  lons = [p[1] for p in path]
        if show_capacity and cap > 0:
            is_exp = arc in expanded_arcs
            fill   = '#FFD600' if is_exp else PIPE_FILL
            exp_tag = ' ✦ EXPANDED' if is_exp else ''
            hover = f"<b>{arc}</b>{exp_tag}<br>{arc_row['From']} → {arc_row['To']}<br>Capacity: {cap:.0f} PJ/yr"
            fig.add_trace(go.Scattermap(lat=lats, lon=lons, mode='lines',
                line=dict(width=PIPE_W+3, color=PIPE_CASING), opacity=0.85, hoverinfo='skip', showlegend=False))
            fig.add_trace(go.Scattermap(lat=lats, lon=lons, mode='lines',
                line=dict(width=PIPE_W, color=fill), opacity=0.7 if is_exp else 0.55,
                text=hover, hoverinfo='text', showlegend=False))

    for _, row in flow_map.iterrows():
        arc  = row['Arc']
        cap  = (arc_caps.get(arc, 0) + exp_info[
                    (exp_info['Target'] == arc) &
                    (exp_info['Name'].isin(built_now))]['NewCapacity'].sum()) * 365 / 1000
        util   = (row['Value'] / cap) if cap > 0 else 0
        is_exp = arc in expanded_arcs
        path   = ARC_WAYPOINTS.get(arc, [COORDS[row['From']], COORDS[row['To']]])
        lats   = [p[0] for p in path];  lons = [p[1] for p in path]
        color  = '#ef4444' if util > 0.9 else ('#f97316' if util > 0.7 else '#22c55e')
        casing = '#7f1d1d' if util > 0.9 else ('#7c2d12' if util > 0.7 else '#14532d')
        # Lighter tint of the utilisation colour so arrows contrast against the pipe fill
        arrow_c = '#fca5a5' if util > 0.9 else ('#fdba74' if util > 0.7 else '#86efac')
        fw     = max(3, min(9, 2 + np.log1p(row['Value']) * 1.5))
        exp_tag = ' ✦ EXPANDED' if is_exp else ''
        hover  = (f"<b>{arc}</b>{exp_tag}<br>Flow: {row['Value']:.1f} PJ"
                  f"<br>Utilisation: {util:.1%}<br>Capacity: {cap:.0f} PJ/yr")
        # Draw gold outer glow for expanded pipelines
        if is_exp:
            fig.add_trace(go.Scattermap(lat=lats, lon=lons, mode='lines',
                line=dict(width=fw+8, color='#FFD600'), opacity=0.35, hoverinfo='skip', showlegend=False))
        fig.add_trace(go.Scattermap(lat=lats, lon=lons, mode='lines',
            line=dict(width=fw+3, color=casing), opacity=1.0, hoverinfo='skip', showlegend=False))
        fig.add_trace(go.Scattermap(lat=lats, lon=lons, mode='lines',
            line=dict(width=fw, color=color), opacity=0.9, text=hover, hoverinfo='text', showlegend=False))
        add_flow_arrows(fig, path, arrow_c, fw)

    node_types  = static_data['nodes'].set_index('Name')['Type'].to_dict()
    def _node_sum(stream, col):
        df = res[stream]
        return (df.groupby('Node')[col].sum() / 1000).to_dict() if not df.empty else {}
    gpg_serv, gpg_cur = _node_sum('gpg', 'Served'), _node_sum('gpg', 'Curtailed')
    ind_serv, ind_cur = _node_sum('industrial', 'Served'), _node_sum('industrial', 'Curtailed')
    dc_load = datacentre_by_node(res, key)
    map_nodes = []
    for node, c in COORDS.items():
        n_t = node_types.get(node, 'Hub')
        # A node is "unpriced" only if nothing flows there AND its dual is a phantom
        # near-VoLL price — i.e. an undeveloped/disconnected potential node (Beetaloo
        # before it is built). A real scarcity price ($300 with actual demand/flow)
        # keeps its colour as a genuine signal; an idle but connected node keeps its
        # normal arbitrage price.
        _tp, _pr = float(throughput.get(node, 0.0)), float(price_map.get(node, 0.0))
        priced = not (_tp < PRICE_MIN_TP and _pr >= 250.0)
        p_v = _pr if priced else float('nan')
        s_v = float(prod_map.get(node, 0))
        price_line = (f"Price: ${p_v:.2f}/GJ<br>" if priced
                      else "Price: n/a — undeveloped, no gas flow<br>")
        tt  = f"<b>{node} ({n_t})</b><br>" + price_line
        def _fac_block(df, label, icon, served, shed):
            if df is None or df.empty:
                return ''
            rows = df[df['Node'] == node].sort_values('MeanDemand', ascending=False)
            static_tot = (rows['MeanDemand'] * 365 / 1000).sum() if not rows.empty else 0
            demand_pj = served + shed          # actual scenario-year gas call at this node
            if static_tot < 0.01 or demand_pj < 0.01:
                return ''
            # Headline = this scenario-year's served (+shed) gas at the node; the
            # static per-facility split (GBB shares) is scaled to that total, so the
            # facility stickers move with the scenario/year (incl. the dunkelflaute).
            scale = demand_pj / static_tot
            s = f"{icon} {label}: {demand_pj:.1f} PJ/yr" + (f" <i>({shed:.1f} shed)</i>" if shed > 0.01 else "") + "<br>"
            for _, fr in rows.iterrows():
                s += f"&nbsp;&nbsp;· {fr['FacilityName']}: {fr['MeanDemand'] * 365 / 1000 * scale:.1f} PJ/yr<br>"
            return s
        tt += _fac_block(static_data.get('gpg_facs'), 'GPG', '⚡', gpg_serv.get(node, 0), gpg_cur.get(node, 0))
        # Data centre load is demand ADDITIONAL to GPG and to the existing large
        # industrial facilities -- model.py adds dc_demand on top of ind_demand
        # rather than reallocating within it. It rides inside the industrial tier
        # only so that it faces the same curtailment terms, so the two are separated
        # again here: the industrial line keeps the existing facilities it is
        # itemising, and the data centres get a line of their own. Left combined,
        # the tier total is scaled across the facility rows and the lever's volume
        # is attributed to plant that is not consuming it -- Melbourne 2040 read as
        # a Viva Energy refinery on 43.8 PJ/yr against 3.8 PJ the year before.
        ind_s, ind_c = ind_serv.get(node, 0), ind_cur.get(node, 0)
        dc_pj_node = dc_load.get(node, 0.0) / 1000
        dc_s, dc_c = dc_pj_node, 0.0
        ind_tot = ind_s + ind_c
        if dc_pj_node > 0.01 and ind_tot > 0.01:
            # Data centre load is firm in the model: ind_curtail_cap bars the
            # industrial tier from shedding more than its non-data-centre demand.
            # So this is not the dashboard picking a shed order -- it reads back
            # what the solver did. Whatever was curtailed belongs to the
            # facilities, and the data centre line carries no shed.
            #
            # The exception is a result cached before that constraint existed,
            # where the solver COULD shed the data centre along with the rest of
            # the tier. Those are reported as they were solved rather than quietly
            # re-read as firm: curtailment beyond the facilities' own demand shows
            # against the data centres and is labelled, so an old number on screen
            # cannot be mistaken for what the model does now.
            shed_tot = ind_c
            ind_dem  = max(0.0, ind_tot - dc_pj_node)
            ind_c    = min(shed_tot, ind_dem)
            dc_c     = shed_tot - ind_c
            ind_s    = ind_dem - ind_c
            dc_s     = dc_pj_node - dc_c
        tt += _fac_block(static_data.get('ind_bbg'), 'Large industrial', '🏭', ind_s, ind_c)
        # A bulk state volume, not a facility list, so nothing to itemise under it.
        if dc_pj_node > 0.01:
            note = ('firm' if dc_c <= 0.01 else
                    f"{dc_c:.1f} shed \u2014 solved before data centres were firm")
            tt += (f"🖥️ Data centres: {dc_s + dc_c:.1f} PJ/yr "
                   f"<i>({note} · scenario lever)</i><br>")

        map_nodes.append({'Node': node, 'Lat': c[0], 'Lon': c[1],
                          'Type': n_t, 'Price': p_v, 'Supply': s_v, 'Tooltip': tt,
                          'Priced': priced})

    n_df  = pd.DataFrame(map_nodes)
    max_p = 300

    # Which Import nodes have a terminal expansion built by map_year?
    import_exp = exp_info[exp_info['Type'] == 'Terminal']
    built_terminals = set(
        import_exp[import_exp['Name'].isin(built_now)]['Target'].tolist()
    )

    # All node types — symbol varies by type, colour reflects price, Supply nodes scale with production
    styling = {
        'Supply':  ('circle',      None, 4),   # size scaled by production
        'Demand':  ('square',      None, 0),
        'Storage': ('diamond',     None, 0),
        'LNG':     ('triangle',    None, 0),
        'Hub':     ('circle-open', None, 0),
    }
    show_colorbar = True
    for nt, (sym, _, sm) in styling.items():
        df_t = n_df[(n_df['Type'] == nt) & (n_df['Priced'])]
        if df_t.empty:
            continue
        size = df_t['Supply'].apply(lambda x: 14 + np.sqrt(x) * sm) if sm > 0 else 16
        fig.add_trace(go.Scattermap(
            lat=df_t['Lat'], lon=df_t['Lon'],
            mode='markers+text' if show_labels else 'markers',
            marker=dict(
                size=size, symbol=sym,
                color=df_t['Price'],
                colorscale='Plasma',
                cmin=0, cmax=max_p,
                showscale=show_colorbar,
                colorbar=dict(title='Price ($/GJ)', thickness=14, x=1.0,
                              y=0.3, len=0.4, tickformat='.0f',
                              bgcolor='rgba(30,30,46,0.85)' if dark else 'rgba(255,255,255,0.85)',
                              tickfont=dict(color='rgba(255,255,255,0.87)' if dark else '#333')) if show_colorbar else None,
            ),
            text=df_t['Node'] if show_labels else None,
            textposition='top center',
            textfont=dict(size=11, color='black', family='Arial Black'),
            hovertemplate='%{customdata}<extra></extra>',
            customdata=df_t['Tooltip'],
            name=nt,
        ))
        show_colorbar = False  # only show colourbar once

    # Undeveloped / zero-flow nodes (excl. Import, handled below): neutral grey, off
    # the price colour scale, so a phantom VoLL dual never paints a node hot.
    df_unpriced = n_df[(~n_df['Priced']) & (n_df['Type'] != 'Import')]
    if not df_unpriced.empty:
        fig.add_trace(go.Scattermap(
            lat=df_unpriced['Lat'], lon=df_unpriced['Lon'],
            mode='markers+text' if show_labels else 'markers',
            marker=dict(size=13, symbol='circle', color='#9E9E9E'),
            opacity=0.6,
            text=df_unpriced['Node'] if show_labels else None,
            textposition='top center',
            textfont=dict(size=10, color='#9E9E9E', family='Arial'),
            hovertemplate='%{customdata}<extra></extra>',
            customdata=df_unpriced['Tooltip'],
            name='Undeveloped (no flow)',
        ))

    # Import terminal nodes — split into proposed (not yet built) and active (built)
    df_import = n_df[n_df['Type'] == 'Import']
    df_proposed = df_import[~df_import['Node'].isin(built_terminals)]
    df_active   = df_import[df_import['Node'].isin(built_terminals)]

    if not df_proposed.empty:
        fig.add_trace(go.Scattermap(
            lat=df_proposed['Lat'], lon=df_proposed['Lon'],
            mode='markers+text' if show_labels else 'markers',
            marker=dict(size=14, symbol='circle-open', color='#9E9E9E'),
            opacity=0.55,
            text=df_proposed['Node'] if show_labels else None,
            textposition='top center',
            textfont=dict(size=10, color='#9E9E9E', family='Arial'),
            hovertemplate='%{customdata}<extra></extra>',
            customdata=df_proposed['Node'] + ' (proposed — not yet built)',
            name='Import Terminal (proposed)',
        ))

    if not df_active.empty:
        for _, row_t in df_active.iterrows():
            # Rival terminals can share a landing point (Viva and Vopak both front
            # Geelong), so pick the one that was actually built rather than the
            # first row for the node -- otherwise the tooltip names the loser.
            _at_node = import_exp[import_exp['Target'] == row_t['Node']]
            _built = _at_node[_at_node['Name'].isin(built_now)]
            exp_row = _built if not _built.empty else _at_node
            e_cap = int(exp_row.iloc[0]['NewCapacity']) if not exp_row.empty else '?'
            e_capex = f"${exp_row.iloc[0]['CapEx']/1e6:,.0f}M" if not exp_row.empty else '?'
            proj_name = exp_row.iloc[0]['Name'] if not exp_row.empty else row_t['Node']
            proj_label = exp_label(proj_name) if not exp_row.empty else row_t['Node']
            built_yr = builds_df[builds_df['Project'] == proj_name]['Year'].iloc[0] if not builds_df.empty and proj_name in builds_df['Project'].values else '?'
            tip = (f"<b>⚓ {proj_label} — LNG Import Terminal</b><br>"
                   f"Status: <b>OPERATIONAL</b> (built {built_yr})<br>"
                   f"Capacity: {e_cap} TJ/d<br>CapEx: {e_capex}")
        # Outer glow ring
        fig.add_trace(go.Scattermap(
            lat=df_active['Lat'], lon=df_active['Lon'],
            mode='markers',
            marker=dict(size=38, symbol='circle', color='#00BCD4'),
            opacity=0.18,
            hoverinfo='skip', showlegend=False,
        ))
        # Middle ring
        fig.add_trace(go.Scattermap(
            lat=df_active['Lat'], lon=df_active['Lon'],
            mode='markers',
            marker=dict(size=26, symbol='circle', color='#00BCD4'),
            opacity=0.40,
            hoverinfo='skip', showlegend=False,
        ))
        # Core marker
        fig.add_trace(go.Scattermap(
            lat=df_active['Lat'], lon=df_active['Lon'],
            mode='markers+text' if show_labels else 'markers',
            marker=dict(size=16, symbol='star', color='#00BCD4'),
            text=df_active['Node'] if show_labels else None,
            textposition='top center',
            textfont=dict(size=12, color='#006064', family='Arial Black'),
            hovertemplate='%{customdata}<extra></extra>',
            customdata=[tip],
            name='Import Terminal (operational)',
        ))

    # Overlay gold star markers for built pipeline expansions (not terminals — handled above)
    if built_now:
        exp_lats, exp_lons, exp_tips, exp_labels = [], [], [], []
        for proj in built_now:
            row_e = exp_info[exp_info['Name'] == proj]
            if row_e.empty:
                continue
            e = row_e.iloc[0]
            target = e['Target']
            if e['Type'] == 'Terminal':
                # Import-node terminals get their own cyan anchor above. A terminal
                # landing on a Supply or Demand node (Golden Beach at Gippsland,
                # Outer Harbor at Adelaide) has no anchor there, so star the node --
                # without this a built project is simply invisible on the map.
                if target in set(n_df[n_df['Type'] == 'Import']['Node']) or target not in COORDS:
                    continue
                lat, lon = COORDS[target]
                built_yr = builds_df[builds_df['Project'] == proj]['Year'].iloc[0] if not builds_df.empty else '?'
                exp_lats.append(lat); exp_lons.append(lon)
                exp_labels.append(exp_label(proj))
                exp_tips.append(f"<b>✦ {exp_label(proj)}</b><br>Type: Terminal<br>Built: {built_yr}<br>"
                                f"+{e['NewCapacity']} TJ/d<br>CapEx: ${e['CapEx']/1e6:,.0f}M")
                continue
            arc_row_e = static_data['arcs'][static_data['arcs']['Name'] == target]
            if arc_row_e.empty:
                continue
            path_e = ARC_WAYPOINTS.get(target)
            if path_e:
                mid = path_e[len(path_e) // 2]
                lat, lon = mid[0], mid[1]
            else:
                from_n = arc_row_e.iloc[0]['From']
                to_n   = arc_row_e.iloc[0]['To']
                lat = (COORDS[from_n][0] + COORDS[to_n][0]) / 2
                lon = (COORDS[from_n][1] + COORDS[to_n][1]) / 2
            built_yr = builds_df[builds_df['Project'] == proj]['Year'].iloc[0] if not builds_df.empty else '?'
            exp_lats.append(lat); exp_lons.append(lon)
            exp_labels.append(exp_label(proj))
            exp_tips.append(f"<b>✦ {exp_label(proj)}</b><br>Type: {e['Type']}<br>Built: {built_yr}<br>+{e['NewCapacity']} TJ/d<br>CapEx: ${e['CapEx']/1e6:,.0f}M")
        if exp_lats:
            fig.add_trace(go.Scattermap(
                lat=exp_lats, lon=exp_lons,
                mode='markers+text' if show_labels else 'markers',
                marker=dict(size=20, symbol='star', color='#FFD600'),
                text=exp_labels if show_labels else None,
                textposition='top center',
                textfont=dict(size=11, color='#B8860B', family='Arial Black'),
                hovertemplate='%{customdata}<extra></extra>',
                customdata=exp_tips,
                name='Pipeline Expansion',
            ))

    scenario_label = pretty_key(key)
    fig.update_layout(
        map=dict(style='carto-darkmatter' if dark else 'carto-positron', center=dict(lat=-24, lon=140), zoom=3.6),
        margin=dict(l=0, r=0, t=40, b=0), height=720,
        title=dict(text=f'<b>{scenario_label}</b>  |  Year {map_year}',
                   x=0.5, xanchor='center',
                   font=dict(size=13, color='#90CAF9' if dark else '#1976D2')),
        legend=dict(yanchor='top', y=0.99, xanchor='left', x=0.01,
                    bgcolor='rgba(30,30,46,0.92)' if dark else 'rgba(255,255,255,0.88)',
                    font=dict(color='rgba(255,255,255,0.87)' if dark else '#111', size=11)),
        paper_bgcolor='#1E1E2E' if dark else 'white',
    )
    return map_kpis, fig


# ---------------------------------------------------------------------------
# Production & Dispatch
# ---------------------------------------------------------------------------
@app.callback(
    Output('prod-annual-graph',   'figure'),
    Output('prod-dispatch-graph', 'figure'),
    Output('flow-graph',          'figure'),
    Output('shortage-content',    'children'),
    Input('result-selector', 'value'),
    Input('horizon-slider',  'value'),
    Input('main-tabs',       'value'),
    Input('theme-store',     'data'),
)
def update_prod(key, end_year, active_tab, theme):
    if active_tab != 'tab-prod':
        return no_update, no_update, no_update, no_update
    tmpl = 'gary_dark' if theme == 'dark' else CHART_TEMPLATE
    b = blank_fig(tmpl)
    if not key:
        return b, b, b, html.Div()
    filtered = get_filtered(key, end_year)
    if not filtered:
        return b, b, b, html.Div()

    prod_frames = [r['production'].assign(Year=r['Year'])
                   for r in filtered if not r['production'].empty]
    if not prod_frames:
        return b, b, b, html.Div()
    all_prod = pd.concat(prod_frames)

    ann_prod = all_prod.groupby(['Year','Node'])['Value'].sum().reset_index()
    ann_prod['Value'] /= 1000
    fig_ann = px.area(ann_prod, x='Year', y='Value', color='Node',
                      title='Annual Production (PJ)', template=tmpl,
                      labels={'Value': 'PJ'})
    fig_ann.update_yaxes(rangemode='tozero')

    daily = []
    for r in filtered:
        if not r['production'].empty:
            # assign(), not in-place: these frames are the cached results themselves,
            # not throwaway copies rebuilt from records on each callback.
            daily.append(r['production'].assign(
                GlobalDay=r['production']['Day'] + (r['Year'] - 2025) * 365))
    if daily:
        df_d = pd.concat(daily)
        fig_disp = px.area(df_d, x='GlobalDay', y='Value', color='Node',
                           title='Continuous Dispatch (TJ/d)', template=tmpl,
                           labels={'GlobalDay': 'Days from 2025', 'Value': 'TJ/d'})
        fig_disp.update_layout(xaxis_rangeslider_visible=True)
    else:
        fig_disp = b

    flow_frames = [r['flow'].assign(Year=r['Year']) for r in filtered if not r['flow'].empty]
    if flow_frames:
        all_flow = pd.concat(flow_frames)
        major    = ['MSP','EGP','VNI','WGP_Pipe','APLNG_Pipe','GLNG_Pipe']
        df_flow = all_flow[all_flow['Arc'].isin(major)].copy()
        df_flow['Date'] = pd.to_datetime(df_flow['Year'].astype(str) + df_flow['Day'].astype(int).astype(str).str.zfill(3), format='%Y%j')
        # Reindex to every modelled day so idle (zero-flow) days are NaN; the line
        # then breaks at those gaps instead of interpolating across them.
        years = sorted(df_flow['Year'].unique())
        full  = pd.to_datetime([f'{y}{d:03d}' for y in years for d in range(1, 366)], format='%Y%j')
        pivot = df_flow.pivot_table(index='Date', columns='Arc', values='Value').reindex(full)
        pivot.index.name = 'Date'
        long  = pivot.reset_index().melt(id_vars='Date', var_name='Arc', value_name='Value')
        fig_flow = px.line(long, x='Date', y='Value', color='Arc',
                           title='Major Pipeline Flows (daily, TJ/d)', template=tmpl,
                           labels={'Value': 'TJ/d', 'Date': ''}, render_mode='svg')
        fig_flow.update_traces(connectgaps=False)
        fig_flow.update_yaxes(rangemode='tozero')
    else:
        fig_flow = b

    short_frames = [r['shortage'].assign(Year=r['Year'])
                    for r in filtered if not r['shortage'].empty]
    if short_frames:
        df_s = pd.concat(short_frames).groupby(['Year','Node'])['Value'].sum().reset_index()
        df_s['Value'] /= 1000
        fig_s = px.bar(df_s, x='Year', y='Value', color='Node',
                       title='Annual Shortages (PJ)', template=tmpl,
                       labels={'Value': 'PJ'})
        fig_s.update_yaxes(rangemode='tozero')
        shortage = dcc.Graph(figure=fig_s)
    else:
        shortage = md_alert('No shortages detected in this scenario.', 'success')

    return fig_ann, fig_disp, fig_flow, shortage

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
@app.callback(
    Output('storage-inventory-graph',  'figure'),
    Output('storage-activity-content', 'children'),
    Input('result-selector', 'value'),
    Input('horizon-slider',  'value'),
    Input('main-tabs',       'value'),
    Input('theme-store',     'data'),
)
def update_storage(key, end_year, active_tab, theme):
    if active_tab != 'tab-storage':
        return no_update, no_update
    tmpl = 'gary_dark' if theme == 'dark' else CHART_TEMPLATE
    b = blank_fig(tmpl)
    if not key:
        return b, html.Div()
    filtered = get_filtered(key, end_year)
    if not filtered:
        return b, html.Div()

    frames = []
    for r in filtered:
        if not r['storage'].empty:
            df = r['storage'].copy()
            df['Date'] = pd.to_datetime(str(r['Year']) + df['Day'].astype(int).astype(str).str.zfill(3), format='%Y%j')
            frames.append(df)

    if not frames:
        return b, md_alert('No storage data available.', 'info')

    df_s = pd.concat(frames)
    fig_inv = px.line(df_s, x='Date', y='Inventory', color='Node',
                      title='Storage Inventory (TJ)', template=tmpl,
                      labels={'Date': '', 'Inventory': 'TJ'}, render_mode='svg')
    fig_inv.update_xaxes(tickformat='%Y', dtick='M12')
    fig_inv.update_layout(xaxis_rangeslider_visible=True)

    if 'Injection' in df_s.columns and 'Withdrawal' in df_s.columns:
        df_s['RelFlow'] = df_s['Injection'] - df_s['Withdrawal']
        fig_act = px.bar(df_s, x='Date', y='RelFlow', color='Node',
                         title='Net Storage Activity (TJ/d)', template=tmpl,
                         labels={'Date': '', 'RelFlow': 'TJ/d'})
        fig_act.update_xaxes(tickformat='%Y', dtick='M12')
        activity = dcc.Graph(figure=fig_act)
    else:
        activity = md_alert('Injection/Withdrawal data not in saved results — clear and re-run.', 'warn')

    return fig_inv, activity

# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------
@app.callback(
    Output('price-high-graph',   'figure'),
    Output('price-low-graph',    'figure'),
    Output('price-q-high-graph', 'figure'),
    Output('price-q-low-graph',  'figure'),
    Output('price-a-high-graph', 'figure'),
    Output('price-a-low-graph',  'figure'),
    Output('price-high-block',   'style'),
    Output('price-q-high-block', 'style'),
    Output('price-a-high-block', 'style'),
    Input('result-selector', 'value'),
    Input('horizon-slider',  'value'),
    Input('main-tabs',       'value'),
    Input('theme-store',     'data'),
)
def update_prices(key, end_year, active_tab, theme):
    HIDE = {'display': 'none'}
    SHOW = {}
    if active_tab != 'tab-price':
        return (no_update,) * 9
    tmpl = 'gary_dark' if theme == 'dark' else CHART_TEMPLATE
    b = blank_fig(tmpl)
    if not key:
        return (b,) * 6 + (HIDE,) * 3
    filtered = get_filtered(key, end_year)
    if not filtered:
        return (b,) * 6 + (HIDE,) * 3

    # Daily nodal prices for the demand centres over the selected horizon.
    frames = [r['prices'].assign(Year=r['Year']) for r in filtered if not r['prices'].empty]
    if not frames:
        return (b,) * 6 + (HIDE,) * 3
    price_nodes = static_data['nodes'][static_data['nodes']['Type'].isin(['Demand', 'LNG'])]['Name'].tolist()
    # The average line is domestic-only; see _weighted_avg.
    avg_nodes = set(static_data['nodes']
                    .loc[static_data['nodes']['Type'] == 'Demand', 'Name'])
    dpr = pd.concat(frames)
    dpr = dpr[dpr['Node'].isin(price_nodes)].copy()
    dpr['Node'] = dpr['Node'].astype(str)
    dpr['Day']  = dpr['Day'].astype(int)
    dpr['Date'] = pd.to_datetime(dpr['Year'].astype(str) + dpr['Day'].astype(int).astype(str).str.zfill(3),
                                 format='%Y%j')
    # Monthly-average price (~12 points/year): smooths daily noise while keeping
    # the seasonal (winter) signal. Quarterly (~4 points/year) and annual (1 point)
    # sit underneath for the longer-run trend, where the winter spike is averaged
    # into its period and only the trajectory is left.
    dpr['Month']   = dpr['Date'].dt.to_period('M').dt.to_timestamp()
    dpr['Quarter'] = dpr['Date'].dt.to_period('Q').dt.to_timestamp()
    dpr['Annual']  = dpr['Date'].dt.to_period('Y').dt.to_timestamp()
    q  = dpr.groupby(['Month',   'Node'])['Price'].mean().reset_index()
    qq = dpr.groupby(['Quarter', 'Node'])['Price'].mean().reset_index()
    qa = dpr.groupby(['Annual',  'Node'])['Price'].mean().reset_index()
    # Split centres into those that actually reach the price cap and those that
    # never do, so each chart auto-scales to its own group. The split is taken on
    # the DAILY prices, which is where the cap binds: any averaging (monthly,
    # quarterly, annual) pulls a spike below the cap, so a period-average test
    # would move nodes between groups and the three charts would no longer show
    # the same node sets.
    day_max = dpr.groupby('Node')['Price'].max()
    hit     = [n for n in price_nodes if day_max.get(n, 0) >= VOLL_PER_GJ - 1e-6]
    no_hit  = [n for n in price_nodes if n not in hit]

    # Weights for the average line: gas actually consumed (Demand) or exported
    # (LNG) at each node each day, so the average lands on the price buyers
    # actually paid for the gas that moved. Per node-DAY, not per node, is what
    # lets winter count properly -- that is when both the volumes and the prices
    # are high, and a flat average over days would wash it out.
    vframes = []
    for r in filtered:
        v = served_volume(r, price_nodes)
        if v is None:
            continue
        vframes.append(v.reset_index().assign(Year=r['Year']))
    if vframes:
        vol = pd.concat(vframes, ignore_index=True)
        vol['Node'] = vol['Node'].astype(str)
        vol['Day']  = vol['Day'].astype(int)
        dpr = dpr.merge(vol, on=['Node', 'Day', 'Year'], how='left')
        dpr['Vol'] = dpr['Vol'].fillna(0.0)
    # Scenarios solved before flows were saved (and, in principle, a horizon in
    # which nothing flows) leave no weights to use. Rather than drop the line,
    # fall back to equal weights and say so in its name, so the chart is never
    # silently showing a plain mean labelled as weighted.
    weighted = bool(vframes) and float(dpr['Vol'].sum()) > 0
    if not weighted:
        dpr['Vol'] = 1.0
    wavg_name = ('Domestic volume-weighted avg' if weighted
                 else 'Unweighted avg \u2014 no flow data')
    wavg_all_name = ('Volume-weighted avg incl. LNG exports' if weighted
                     else 'Unweighted avg incl. LNG exports')
    # Neutral, deliberately outside the series colourway: an average is a summary
    # of the lines, not another node. The export-inclusive one is the same hue,
    # lighter and dotted, so it reads as a variant of the domestic line rather
    # than as a separate series.
    wavg_colour = '#ECEFF3' if theme == 'dark' else '#1A1D21'
    wavg_all_colour = '#8B939C' if theme == 'dark' else '#8A9199'

    def _weighted_avg(period, nodes, domestic_only=True):
        """Volume-weighted mean DOMESTIC price per period, from DAILY rows.

        Taken off the daily prices rather than off the plotted period means, so
        a node-day with no delivery carries no weight and a big winter day
        carries its full one.

        ``domestic_only`` restricts the weights to demand nodes, which is what the
        headline KPI card does, so the two agree. With it False the three LNG
        trains are included and the figure becomes a whole-market average.

        BOTH ARE DRAWN, because they answer different questions and the gap between
        them is itself informative. Export volume is roughly four times domestic
        consumption -- the trains are 78% of the weight in 2050 -- so the
        export-inclusive line sits below every domestic node on the chart. That is
        not an error; it is what it means for most of the gas to leave the country
        at a price below what domestic buyers pay.
        """
        sub = dpr[dpr['Node'].isin(set(nodes) & set(avg_nodes) if domestic_only
                                   else set(nodes))]
        if sub.empty:
            return None
        g = (sub.assign(_pv=sub['Price'] * sub['Vol'])
                .groupby(period)[['_pv', 'Vol']].sum())
        g = g[g['Vol'] > 0]
        if g.empty:
            return None
        return (g['_pv'] / g['Vol']).rename('Price').reset_index()

    def _price_fig(df, period, nodes, title):
        sub = df[df['Node'].isin(nodes)].sort_values(period)
        if sub.empty:
            f = blank_fig(tmpl)
            f.update_layout(title=title)
            return f
        f = px.line(sub, x=period, y='Price', color='Node', title=title,
                    template=tmpl, labels={'Price': '$/GJ', period: ''},
                    render_mode='svg')
        w = _weighted_avg(period, nodes)
        if w is not None and len(w) > 1:
            f.add_scatter(x=w[period], y=w['Price'], mode='lines',
                          name=wavg_name,
                          line=dict(color=wavg_colour, width=3, dash='dash'),
                          hovertemplate='%{x}<br>' + wavg_name
                                        + ': $%{y:.2f}/GJ<extra></extra>')
        # Only worth a second line where this chart actually carries an LNG node --
        # otherwise it would duplicate the domestic line exactly.
        if set(nodes) - set(avg_nodes):
            wa = _weighted_avg(period, nodes, domestic_only=False)
            if wa is not None and len(wa) > 1:
                f.add_scatter(x=wa[period], y=wa['Price'], mode='lines',
                              name=wavg_all_name,
                              line=dict(color=wavg_all_colour, width=2, dash='dot'),
                              hovertemplate='%{x}<br>' + wavg_all_name
                                            + ': $%{y:.2f}/GJ<extra></extra>')
        f.update_yaxes(rangemode='tozero')
        return f

    # With nothing at the cap there is no split to make: the "below the cap"
    # chart holds every node, so it is titled as the whole set and the cap-hitting
    # charts are hidden rather than drawn empty.
    cap = f'${VOLL_PER_GJ:,.0f}'
    lo_label = (f'Demand & LNG nodes staying below the {cap} cap' if hit
                else 'Demand & LNG node prices')
    hi_label = f'Demand & LNG nodes reaching the {cap} cap'

    fig_high = _price_fig(q, 'Month', hit,
                          f'{hi_label} — monthly avg ($/GJ)')
    fig_low  = _price_fig(q, 'Month', no_hit,
                          f'{lo_label} — monthly avg ($/GJ)')
    fig_q_high = _price_fig(qq, 'Quarter', hit,
                            f'{hi_label} — quarterly avg ($/GJ)')
    fig_q_low  = _price_fig(qq, 'Quarter', no_hit,
                            f'{lo_label} — quarterly avg ($/GJ)')
    fig_a_high = _price_fig(qa, 'Annual', hit,
                            f'{hi_label} — annual avg ($/GJ)')
    fig_a_low  = _price_fig(qa, 'Annual', no_hit,
                            f'{lo_label} — annual avg ($/GJ)')
    hi_style = SHOW if hit else HIDE
    return (fig_high, fig_low, fig_q_high, fig_q_low, fig_a_high, fig_a_low,
            hi_style, hi_style, hi_style)

@app.callback(
    Output('segment-price-content', 'children'),
    Input('result-selector', 'value'),
    Input('horizon-slider',  'value'),
    Input('main-tabs',       'value'),
    Input('theme-store',     'data'),
)
def update_segment_prices(key, end_year, active_tab, theme):
    """ACIL Allen-style customer-segment prices for the selected scenario.

    A post-processing layer over duals the solve already produced: it adds no
    constraint and re-solves nothing. See acil_segment_prices.py, and the README
    section "What a GARY price is, and when it is not a wholesale price" for why
    a dual is not a price a customer pays.
    """
    if active_tab != 'tab-price' or not key:
        return no_update
    filtered = get_filtered(key, end_year)
    if not filtered:
        return no_update
    tmpl = 'gary_dark' if theme == 'dark' else CHART_TEMPLATE
    try:
        seg = acil_segment_prices.segment_prices(filtered)
    except Exception as exc:                       # never take the tab down
        return md_alert(f'Segment prices unavailable: {exc}', 'warning')
    if seg.empty:
        return md_alert('No segment prices for this scenario.', 'info')

    demand_nodes = sorted(static_data['nodes']
                          .loc[static_data['nodes']['Type'] == 'Demand', 'Name'])
    seg = seg[seg['Node'].isin(demand_nodes)]
    if seg.empty:
        return md_alert('No segment prices at the demand nodes.', 'info')

    LABEL = {'ResidentialCommercial': 'Residential / commercial',
             'Industrial': 'Industrial', 'GPG_CCGT': 'GPG — CCGT',
             'GPG_OCGT': 'GPG — OCGT'}
    seg = seg.assign(Segment=seg['Segment'].map(lambda x: LABEL.get(x, x)))
    fig = px.line(seg.sort_values('Year'), x='Year', y='Price',
                  color='Segment', facet_col='Node',
                  facet_col_wrap=2, template=tmpl, render_mode='svg',
                  labels={'Price': '$/GJ'},
                  title='Customer-segment prices (ACIL Allen weighting) — $/GJ')
    fig.for_each_annotation(lambda a: a.update(text=a.text.split('=')[-1]))
    fig.update_yaxes(rangemode='tozero', matches=None)
    fig.update_layout(height=680)

    two_run = bool(seg['TwoRun'].any()) if 'TwoRun' in seg.columns else False
    caveat = (
        'These are GARY\u2019s nodal duals reweighted onto ACIL Allen\u2019s '
        'contract/spot split per customer segment \u2014 100/0 residential and '
        'commercial, 90/10 industrial, 80/20 CCGT, 20/80 OCGT plus a $1.00/GJ '
        'short-notice premium. The contract leg is weighted by total delivered '
        'volume at the node; each segment\u2019s spot leg is weighted by that '
        'segment\u2019s own daily profile. '
        + ('Run 1 / Run 2 are separate solves. '
           if two_run else
           'Both legs come from ONE solve, so ACIL Allen\u2019s uncapped Run 2 is '
           'approximated by the capped run \u2014 identical wherever the $12 Code '
           'cap does not bind, which is all of Step Change and all of Accelerated. ')
        + 'ACIL Allen\u2019s Step 2 overlay \u2014 vertical integration, gentailer '
          'portfolio effects, MARKET POWER, and inflating new supply costs toward '
          'netback \u2014 is NOT reproduced: it is judgement applied outside their '
          'model, per generator and per contract, and is not reproducible from '
          'published material. These are their mechanical layer only and will sit '
          'BELOW their published forecasts wherever that overlay adds to them. '
          'The $1.00/GJ OCGT premium is GARY\u2019s number, not theirs.')
    return html.Div([
        dcc.Graph(figure=fig, style={'marginBottom': '4px'}),
        md_alert(caveat, 'warning'),
    ])


# ---------------------------------------------------------------------------
# Supply curves
# ---------------------------------------------------------------------------
SUPPLY_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
SUPPLY_YEAR_STEP = 5

# (key, end_year, residual) -> panels. Rebuilding a grid walks every solved year
# to re-accumulate depletion, which is worth doing once per scenario rather than
# once per theme toggle.
_supply_cache: dict = {}


def supply_demand_nodes():
    """The demand centres the grid has a row for, in nodes.csv order."""
    n = static_data['nodes']
    return [str(x) for x in n.loc[n['Type'] == 'Demand', 'Name']]


def supply_curve_panels(key, end_year, residual):
    """{(node, year): (blocks, demand PJ/yr, price $/GJ)} plus the years drawn.

    Every solved year is walked, not just the ones with a column, because
    depletion is cumulative: what the 2040 panel has left to produce depends on
    what 2025-39 took out of the ground.
    """
    cache_key = (key, end_year, residual)
    if cache_key in _supply_cache:
        return _supply_cache[cache_key]

    years_all = sorted(get_filtered(key, end_year), key=lambda r: r['Year'])
    if not years_all:
        return {}, []
    nodes = supply_demand_nodes()
    drawn = [r for r in years_all
             if (r['Year'] - years_all[0]['Year']) % SUPPLY_YEAR_STEP == 0]

    supply_df = pd.read_csv(os.path.join(SUPPLY_DATA_DIR, 'supply.csv'))
    arcs_df, exp_df = static_data['arcs'], static_data['expansion']

    panels, cum, wanted = {}, {}, {r['Year'] for r in drawn}
    for res in years_all:
        if res['Year'] in wanted:
            year_panels = sc.curves_for_year(
                res, nodes, supply_df, arcs_df, exp_df, nodes, cum=cum,
                data_dir=SUPPLY_DATA_DIR, residual=residual)
            for node, panel in year_panels.items():
                panels[(node, res['Year'])] = panel
        cum = sc.accumulate(cum, res)

    out = (panels, [r['Year'] for r in drawn])
    _supply_cache[cache_key] = out
    if len(_supply_cache) > 8:
        _supply_cache.pop(next(iter(_supply_cache)))
    return out


def _panel_ranges(panels, nodes, years):
    """(y max for the whole grid, {node: x max}) -- the window worth looking at.

    Both axes are cut back to the part of the curve the year's demand actually
    reaches. A stack whose dear tail runs to $40/GJ on a route nobody would use
    is real, but drawn in full it squashes the $6-12 band where every price in
    this model lives into the bottom fifth of the panel.
    """
    y_top, x_max = 0.0, {}
    for node in nodes:
        x_node = 0.0
        for year in years:
            df, dem, price, _ = panels.get((node, year),
                                           (pd.DataFrame(), 0.0, None, {}))
            x_node = max(x_node, dem * 1.9)
            if price:
                # p90, not the median: the band has to fit in the panel or the
                # winter days it exists to show are cropped out of it.
                y_top = max(y_top, price['p90'])
            if df.empty:
                continue
            cut = df[df['CumPJ'] >= dem]
            row = cut.iloc[0] if not cut.empty else df.iloc[-1]
            y_top = max(y_top, float(row['cost']))
            x_node = max(x_node, float(row['CumPJ']) * 1.25)
        x_max[node] = max(x_node, 1.0)
    return y_top * 1.3, x_max


def build_supply_figure(key, end_year, residual, dark):
    """The grid: a row per demand node, a column every five years."""
    tmpl = 'gary_dark' if dark else CHART_TEMPLATE
    panels, years = supply_curve_panels(key, end_year, residual)
    nodes = supply_demand_nodes()
    if not panels or not years:
        return blank_fig(tmpl)

    ncol = len(years)
    titles = [f'<b>{y}</b>' if i == 0 else ''
              for i in range(len(nodes)) for y in years]
    fig = make_subplots(rows=len(nodes), cols=ncol, subplot_titles=titles,
                        horizontal_spacing=0.012, vertical_spacing=0.055)

    y_top, x_max = _panel_ranges(panels, nodes, years)
    ink   = '#ECEFF3' if dark else MD_TEXT
    muted = '#9AA5B1' if dark else MD_TEXT_MED
    hatch = 'rgba(255,255,255,0.60)' if dark else 'rgba(0,0,0,0.42)'
    # Texture, not more hues, carries what a block IS: solid gas out of a
    # producing field, hatched gas behind a project that has to be built, dotted
    # gas carved out of the export stream and offered at nothing.
    # Dots need to be bigger and denser than diagonals to read at all at the
    # size a panel in a thirty-panel grid gives them.
    HATCH = {'developed': dict(shape=''),
             'potential': dict(shape='/', size=6, solidity=0.28),
             'reserved':  dict(shape='.', size=9, solidity=0.42)}
    # The two lines sit on top of the bars, so their labels need the surface
    # behind them to stay readable over a dark step.
    label_bg = 'rgba(29,33,38,0.72)' if dark else 'rgba(255,255,255,0.78)'
    band = 'rgba(154,165,177,0.22)' if dark else 'rgba(107,114,128,0.15)'
    seen = set()
    # Built as plain dicts and attached in one pass at the end. add_vline/add_hline
    # re-validate every shape already on the figure each time they are called, so
    # sixty of them on a thirty-panel grid cost twenty seconds on their own.
    shapes, notes = [], []

    for r, node in enumerate(nodes, start=1):
        for c, year in enumerate(years, start=1):
            # Plotly numbers the first subplot's axes 'x'/'y', not 'x1'/'y1',
            # and its layout keys 'xaxis'/'yaxis'.
            n = (r - 1) * ncol + c
            sfx = '' if n == 1 else str(n)
            xref, yref = f'x{sfx}', f'y{sfx}'
            df, dem, price, meta = panels.get((node, year),
                                              (pd.DataFrame(), 0.0, None, {}))
            for _, b in df.iterrows():
                fam, first = b['family'], b['family'] not in seen
                seen.add(fam)
                fig.add_trace(go.Bar(
                    x=[b['StartPJ'] + b['PJ'] / 2], y=[b['cost']], width=[b['PJ']],
                    marker=dict(
                        color=supply_color(fam, dark),
                        # Texture, not a ninth and tenth hue, for what the block
                        # IS -- see HATCH above. fillmode='overlay' or the
                        # pattern REPLACES the fill: marker.color is ignored
                        # under the default 'replace' and the bar comes out
                        # white.
                        pattern=dict(fillmode='overlay', fgcolor=hatch,
                                     **HATCH.get(b['kind'], HATCH['developed'])),
                        line=dict(width=0)),
                    name=fam, legendgroup=fam, showlegend=first,
                    # Ranked by the palette's own slot order, not by the order a
                    # basin happens to first appear in the grid.
                    legendrank=sc.FAMILY_ORDER.index(fam) if fam in sc.FAMILY_ORDER else 99,
                    hovertemplate=(f"<b>{b['label']}</b><br>"
                                   f"${b['cost']:.2f}/GJ delivered to {node}<br>"
                                   f"{b['PJ']:.1f} PJ  (cumulative {b['CumPJ']:.1f} PJ)<br>"
                                   f"field ${b['field_cost']:.2f} + transport "
                                   f"${b['tariff']:.2f}<br>via {b['route']}"
                                   "<extra></extra>"),
                ), row=r, col=c)

            if dem > 0:
                shapes.append(dict(type='line', xref=xref, yref=f'{yref} domain',
                                   x0=dem, x1=dem, y0=0, y1=1, layer='above',
                                   line=dict(color=ink, width=1.4, dash='dot')))
                notes.append(dict(xref=xref, yref=f'{yref} domain', x=dem, y=1.0,
                                  text=f'{dem:.0f} PJ', showarrow=False,
                                  xanchor='left', yanchor='top', xshift=3,
                                  bgcolor=label_bg, borderpad=1,
                                  font=dict(size=9, color=muted)))
            if price:
                # The band is where this node's daily prices actually sat; the
                # line is the typical day, which is the like-for-like comparison
                # with an annual-average curve. A band riding well above the
                # curve is the year's dear days -- winter, or a congestion rent
                # no supply block carries -- and saying so beats hiding it
                # inside a mean.
                shapes.append(dict(type='rect', xref=f'{xref} domain', yref=yref,
                                   x0=0, x1=1, y0=price['p10'], y1=price['p90'],
                                   layer='below', line=dict(width=0),
                                   fillcolor=band))
                shapes.append(dict(type='line', xref=f'{xref} domain', yref=yref,
                                   x0=0, x1=1, y0=price['median'], y1=price['median'],
                                   layer='above',
                                   line=dict(color=ink, width=1.4, dash='dash')))
                notes.append(dict(xref=f'{xref} domain', yref=yref, x=0.0,
                                  y=price['median'], text=f"${price['median']:.2f}",
                                  showarrow=False, xanchor='left', yanchor='bottom',
                                  xshift=3, bgcolor=label_bg, borderpad=1,
                                  font=dict(size=9, color=muted)))

            # Where the price line rides above the steps and an inbound
            # corridor ran at its limit all year, say so: that difference is a
            # congestion rent, which the LP's dual carries and a stack of field
            # costs and tariffs cannot.
            if price and not df.empty and meta.get('binding'):
                cut = df[df['CumPJ'] >= dem]
                top = float((cut.iloc[0] if not cut.empty else df.iloc[-1])['cost'])
                if price['median'] - top > 0.25:
                    arc, days = meta['binding'][0]
                    tr = meta.get('transit') or 0.0
                    text = f'{arc} full {days} d'
                    if tr > 1:
                        # Say how much of that full pipe was only passing
                        # through: the cheap block is not all this node's.
                        text += f' · {tr:.0f} TJ/d transit'
                    notes.append(dict(
                        xref=f'{xref} domain', yref=f'{yref} domain',
                        x=0.0, y=0.0, text=text, showarrow=False,
                        xanchor='left', yanchor='bottom', xshift=3, yshift=3,
                        bgcolor=label_bg, borderpad=1,
                        font=dict(size=8, color=muted)))

            fig.layout[f'xaxis{sfx}'].update(
                range=[0, x_max[node]], showgrid=False, tickfont=dict(size=9),
                title=dict(text='PJ/yr' if r == len(nodes) else None,
                           font=dict(size=10, color=muted)))
            # One y scale for the whole grid, with ticks only down the first
            # column: the point of the grid is comparing where the price lands
            # between nodes and across years, which a per-panel scale would break.
            fig.layout[f'yaxis{sfx}'].update(
                range=[0, y_top], tickfont=dict(size=9), showticklabels=(c == 1),
                title=dict(text=f'<b>{node}</b><br>$/GJ' if c == 1 else None,
                           font=dict(size=11, color=ink)))

    # Two grey swatches naming what the hatching means, so the tranche encoding is
    # in the legend rather than only in a caption.
    swatches = [('Developed (2P)', 'developed'), ('Undeveloped (2C) / import', 'potential')]
    if any(panel[0]['kind'].eq('reserved').any()
           for panel in panels.values() if not panel[0].empty):
        swatches.append(('Reserved — offered at $0 at the field', 'reserved'))
    for i, (label, kind) in enumerate(swatches):
        fig.add_trace(go.Bar(
            x=[None], y=[None], name=label, legendgroup='tranche',
            legendrank=200 + i, legendgrouptitle_text='Tranche' if i == 0 else None,
            marker=dict(color=muted, line=dict(width=0),
                        # Denser than on the bars: a 12px legend swatch shows
                        # nothing at the density that reads well across a panel.
                        pattern=dict(fillmode='overlay', fgcolor=hatch, size=4,
                                     solidity=0.55,
                                     shape=HATCH[kind].get('shape', ''))),
            showlegend=True, hoverinfo='skip'), row=1, col=1)

    for a in fig.layout.annotations:          # the year headings
        a.font.size = 12
    mode = ('Residual — net of LNG exports and the other demand centres'
            if residual else 'Gross — every tranche available that year')
    fig.update_layout(
        template=tmpl, barmode='overlay', bargap=0,
        height=230 * len(nodes) + 160,
        shapes=shapes, annotations=list(fig.layout.annotations) + notes,
        title=(f'Delivered supply curves by demand node  ·  {mode}'
               '<br><sup>Each step is a tranche of gas at its delivered cost '
               '($/GJ, y) against the volume it can supply (PJ/yr, x). Dotted '
               'vertical line = that year’s demand. Dashed horizontal line = '
               'the price GARY reported on a typical (median) day at the node, '
               'inside a band spanning its 10th–90th percentile daily price.'
               '</sup>'),
        legend=dict(orientation='h', yanchor='top', y=-0.045, x=0,
                    groupclick='toggleitem', font=dict(size=11)),
        margin=dict(l=76, r=24, t=104, b=120),
        hovermode='closest',
    )
    return fig


def supply_curve_table(key, end_year, residual):
    """The grid as one tidy table -- the view that reads without the colours."""
    panels, years = supply_curve_panels(key, end_year, residual)
    rows = []
    for (node, year), (df, dem, price, meta) in sorted(panels.items(),
                                                       key=lambda kv: kv[0][::-1]):
        for _, b in df.iterrows():
            rows.append({
                'Node': node, 'Year': year, 'Source': b['label'],
                'Basin': b['family'],
                'Tranche': {'developed': '2P', 'potential': '2C/import',
                            'reserved': 'Reserved'}.get(b['kind'], ''),
                'Delivered $/GJ': round(float(b['cost']), 3),
                'Field $/GJ': round(float(b['field_cost']), 3),
                'Transport $/GJ': round(float(b['tariff']), 3),
                'PJ/yr': round(float(b['PJ']), 2),
                'Cumulative PJ/yr': round(float(b['CumPJ']), 2),
                'Route': b['route'],
                'Node demand PJ/yr': round(float(dem), 2),
                'Price, typical day $/GJ': None if not price else round(price['median'], 2),
                'Price, annual mean $/GJ': None if not price else round(price['mean'], 2),
                'Price, p10 $/GJ': None if not price else round(price['p10'], 2),
                'Price, p90 $/GJ': None if not price else round(price['p90'], 2),
            })
    return pd.DataFrame(rows)


@app.callback(
    Output('supply-curve-graph', 'figure'),
    Output('supply-curve-note',  'children'),
    Input('result-selector', 'value'),
    Input('horizon-slider',  'value'),
    Input('main-tabs',       'value'),
    Input('supply-mode',     'value'),
    Input('theme-store',     'data'),
)
def update_supply_curves(key, end_year, active_tab, mode, theme):
    if active_tab != 'tab-supply':
        return no_update, no_update
    tmpl = 'gary_dark' if theme == 'dark' else CHART_TEMPLATE
    if not key:
        return blank_fig(tmpl), html.Div()
    residual = mode != 'gross'
    fig = build_supply_figure(key, end_year, residual, theme == 'dark')
    note = md_alert(
        'Reconstructed from the solved year, not re-solved. Each tranche is '
        'stacked at its delivered cost — field cost plus the year’s scarcity '
        'rent, plus the tariff on the cheapest route that still had capacity — '
        'so a basin reappears further up the curve once its cheap corridor '
        'fills. The residual view strips the gas the LNG trains and the other '
        'demand centres took, cheapest first, which is what brings the curve '
        'onto GARY’s own nodal price; the gross view leaves it in. A panel is '
        'a typical-day construction — capacity and demand are both annual '
        'average flat rates — so the price line is the median day’s, which the '
        'curve meets to the cent in about half the grid; the band around it is '
        'the 10th–90th percentile of the year’s daily prices, and where it '
        'rides above the curve those are days an annual average cannot hold, '
        'a winter peak or a congestion rent no supply block carries. Each '
        'panel also treats its node as the only buyer of what is left.', 'info')
    return fig, note


@app.callback(
    Output('chart-dl', 'data', allow_duplicate=True),
    Input('dl-supply', 'n_clicks'),
    State('result-selector', 'value'),
    State('horizon-slider',  'value'),
    State('supply-mode',     'value'),
    prevent_initial_call=True,
)
def download_supply_curves(n, key, end_year, mode):
    """The supply-curve grid as a table, block by block.

    Its own download rather than the shared figure-scraping one: the grid is
    hundreds of one-bar traces, which that route would turn into hundreds of
    columns, and the numbers worth having (route, field cost, tariff) are in the
    hover rather than in the geometry.
    """
    if not key:
        return no_update
    df = supply_curve_table(key, end_year, mode != 'gross')
    if df.empty:
        return no_update
    return dcc.send_data_frame(df.to_excel, 'supply_curves.xlsx',
                               sheet_name='supply_curves', index=False)

# ---------------------------------------------------------------------------
# Expansions
# ---------------------------------------------------------------------------
@app.callback(
    Output('expansions-content', 'children'),
    Input('result-selector', 'value'),
    Input('horizon-slider',  'value'),
    Input('main-tabs',       'value'),
    Input('theme-store',     'data'),
    Input('exp-sort-col',    'value'),
    Input('exp-sort-dir',    'value'),
)
def update_expansions(key, end_year, active_tab, theme, sort_col, sort_dir):
    if active_tab != 'tab-exp':
        return no_update
    tmpl = 'gary_dark' if theme == 'dark' else CHART_TEMPLATE
    if not key:
        return md_alert('No results loaded.', 'info')
    filtered = get_filtered(key, end_year)
    if not filtered:
        return md_alert('No results in this horizon.', 'info')
    _, _, builds_df, _ = build_summary(filtered, discount_rate=_dr_of(key))
    if builds_df.empty:
        return md_alert('No new infrastructure built in this scenario.', 'success')

    sorted_df = builds_df.sort_values('Year').reset_index(drop=True)

    # Gantt-style bar chart
    type_colors = {'Pipeline': '#1976D2', 'Terminal': '#E65100', 'LNG': '#2E7D32'}
    fig = go.Figure()
    for _, row in sorted_df.iterrows():
        color = type_colors.get(row.get('Type', ''), '#78909C')
        name = row.get('Label') or row['Project']
        label = f"<b>{name}</b><br>Built: {row['Year']}<br>Type: {row.get('Type','—')}<br>Capacity: {row.get('New Capacity (TJ/d)','—')} TJ/d<br>CapEx: ${row.get('CapEx ($M)','—')}M"
        fig.add_trace(go.Bar(
            x=[end_year - row['Year'] + 1],
            y=[name],
            base=[row['Year']],
            orientation='h',
            marker_color=color,
            marker_line_width=0,
            text=f"  {row['Year']}",
            textposition='inside',
            hovertext=label,
            hoverinfo='text',
            showlegend=False,
        ))
    fig.update_layout(
        template=tmpl,
        title='Infrastructure Build Timeline',
        xaxis=dict(title='Year', range=[2025, end_year]),
        yaxis=dict(title=''),
        barmode='overlay',
        height=max(200, 80 + len(sorted_df) * 55),
        margin=dict(l=0, r=20, t=40, b=40),
    )

    # Show the readable Label under the "Project" heading and drop the raw name --
    # it is a join key, not something to read in a table.
    table_df = (sorted_df.drop(columns=['Project'])
                         .rename(columns={'Label': 'Project'}))
    table_df = table_df[['Year', 'Project'] + [c for c in table_df.columns
                                               if c not in ('Year', 'Project')]]
    # The Gantt chart above stays chronological (its x-axis already encodes
    # year, and the y-axis row order reads best that way) -- only the table
    # itself follows the user's chosen sort.
    table_df = sort_display_df(table_df, sort_col or 'Year', ascending=(sort_dir != 'desc'))
    table = dataframe_to_table(table_df, style={'fontSize': '0.85rem'})
    return html.Div([dcc.Graph(figure=fig, config={'displayModeBar': False}), table])

# ---------------------------------------------------------------------------
# Industrial
# ---------------------------------------------------------------------------
@app.callback(
    Output('ind-graph', 'figure'),
    Input('result-selector', 'value'),
    Input('horizon-slider',  'value'),
    Input('main-tabs',       'value'),
    Input('theme-store',     'data'),
)
def update_industrial(key, end_year, active_tab, theme):
    if active_tab != 'tab-ind':
        return no_update
    tmpl = 'gary_dark' if theme == 'dark' else CHART_TEMPLATE
    b = blank_fig(tmpl)
    if not key:
        return b
    filtered = get_filtered(key, end_year)
    if not filtered:
        return b
    # Curtailable large-user demand (GPG + industrial): served vs shed, monthly.
    parts = []
    for r in filtered:
        yr = r['Year']
        for stream, lbl in (('gpg', 'GPG'), ('industrial', 'Large Industrial')):
            df = r[stream]
            if df.empty:
                continue
            df = df.copy()
            df['Date'] = pd.to_datetime(str(yr) + df['Day'].astype(int).astype(str).str.zfill(3), format='%Y%j')
            df['Tier_base'] = lbl
            parts.append(df[['Date', 'Tier_base', 'Served', 'Curtailed']])
    if not parts:
        return b
    allr = pd.concat(parts)
    allr['Month'] = allr['Date'].dt.to_period('M').dt.to_timestamp()
    g = allr.groupby(['Month', 'Tier_base'])[['Served', 'Curtailed']].sum().reset_index()
    rows = []
    for _, x in g.iterrows():
        rows.append({'Month': x['Month'], 'Tier': f"{x['Tier_base']} served", 'PJ': x['Served'] / 1000})
        if x['Curtailed'] / 1000 > 0.0001:
            rows.append({'Month': x['Month'], 'Tier': f"{x['Tier_base']} curtailed", 'PJ': x['Curtailed'] / 1000})
    dd = pd.DataFrame(rows)
    cmap = {'GPG served': '#2563eb', 'GPG curtailed': '#93c5fd',
            'Large Industrial served': '#b45309', 'Large Industrial curtailed': '#fcd34d'}
    fig = px.area(dd, x='Month', y='PJ', color='Tier', color_discrete_map=cmap,
                  title='GPG & Large-Industrial Gas — served vs curtailed (monthly, PJ)',
                  template=tmpl)
    fig.update_yaxes(rangemode='tozero')
    return fig

# ---------------------------------------------------------------------------
# Theme toggle
# ---------------------------------------------------------------------------
app.clientside_callback(
    """
    function(theme) {
        if (theme === 'dark') {
            document.documentElement.classList.add('dark');
        } else {
            document.documentElement.classList.remove('dark');
        }
        return theme === 'dark' ? '◑  Light Mode' : '◑  Dark Mode';
    }
    """,
    Output('theme-toggle-btn', 'children'),
    Input('theme-store', 'data'),
)

@app.callback(
    Output('theme-store', 'data'),
    Input('theme-toggle-btn', 'n_clicks'),
    State('theme-store', 'data'),
    prevent_initial_call=True,
)
def toggle_theme(n, current):
    return 'dark' if current != 'dark' else 'light'

# ---------------------------------------------------------------------------
# Downloads: per-chart data as Excel
# ---------------------------------------------------------------------------
@app.callback(
    Output('chart-dl', 'data'),
    [Input(b, 'n_clicks') for b, _, _ in CHART_DL],
    [State(g, 'figure') for _, g, _ in CHART_DL],
    prevent_initial_call=True,
)
def download_chart_data(*args):
    """Send the clicked chart's plotted data as an .xlsx (built from its figure)."""
    n = len(CHART_DL)
    figs = args[n:]
    idx = next((i for i, (b, _, _) in enumerate(CHART_DL) if b == ctx.triggered_id), None)
    if idx is None:
        return no_update
    df = _fig_dict_to_df(figs[idx])
    if df.empty:
        return no_update
    return dcc.send_data_frame(df.to_excel, f'{CHART_DL[idx][2]}.xlsx',
                               sheet_name='data', index=False)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    import argparse
    import solvers
    _parser = argparse.ArgumentParser(description="Run the GARY dashboard.")
    solvers.add_solver_argument(_parser)
    _args, _ = _parser.parse_known_args()
    if _args.solver:
        solvers.set_solver_name(_args.solver)
    print(f"Solver: {solvers.describe()}")
    solvers.require_available()
    app.run(debug=False, host='127.0.0.1', port=8050)
