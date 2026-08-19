import os, re, sys
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
import diskcache
import dash
from dash import dcc, html, Input, Output, State, DiskcacheManager, no_update, ctx
import dash_bootstrap_components as dbc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import results_io
from model import RESERVATION_LEVELS
from solve import solve_scenario
from regenerate_data import regenerate_all

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
    ('dl-ind',           'ind-graph',              'gpg_large_users'),
]


def _fig_dict_to_df(fig):
    """Pull a Plotly figure dict's plotted series into a tidy wide table.

    A shared x (usually the date/year) becomes the first column and each trace's y
    becomes a column named after the trace; long traces are sampled down.
    """
    if not fig or not fig.get('data'):
        return pd.DataFrame()
    series, xref, seen = [], None, {}
    for i, tr in enumerate(fig['data']):
        y = tr.get('y')
        if y is None or len(y) == 0:
            continue
        y = list(y)
        if all(v is None for v in y):
            continue
        x = tr.get('x')
        if x is not None and len(x) == len(y) and xref is None:
            xref = list(x)
        if len(y) > 5000:
            step = int(np.ceil(len(y) / 5000))
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
    df = pd.concat({n: s for n, s in series}, axis=1)
    if xref is not None and len(xref) == len(df):
        df.insert(0, 'x', xref)
    return df


def _dl_btn(btn_id):
    """Small right-aligned 'download chart data as Excel' button for a chart."""
    return html.Div(
        dbc.Button('⬇ Data (Excel)', id=btn_id, size='sm', color='secondary',
                   outline=True, className='chart-dl-btn'),
        style={'textAlign': 'right', 'margin': '2px 4px 12px'})


# ---------------------------------------------------------------------------
# Background callback manager
# ---------------------------------------------------------------------------
_cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tmp', 'cache')
os.makedirs(_cache_dir, exist_ok=True)
_disk_cache = diskcache.Cache(_cache_dir)
background_callback_manager = DiskcacheManager(_disk_cache)

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
COORDS = {
    'Surat':          [-27.15, 149.07], 'Moomba':        [-28.1,  140.2],
    'Gippsland':      [-38.5,  147.0],  'Sydney':        [-33.86, 151.2],
    'Melbourne':      [-37.81, 144.96], 'Adelaide':      [-34.92, 138.6],
    'Brisbane':       [-27.47, 153.02], 'Gladstone':     [-23.84, 151.26],
    'APLNG':          [-23.76, 151.20], 'GLNG':          [-23.80, 151.25],
    'QCLNG':          [-23.84, 151.30], 'Port_Kembla':   [-34.45, 150.9],
    'Iona':           [-38.55, 142.9],  'Silver_Springs':[-27.4,  149.2],
    # Northern Territory
    'Amadeus':        [-23.85, 132.30], 'Beetaloo':      [-16.30, 133.40],
    'Darwin':         [-12.46, 130.84], 'Tennant_Creek': [-19.65, 134.19],
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
    # --- Northern Territory (Amadeus Basin to Darwin Pipeline + NGP; AEMO gas map v2021) ---
    # AGP: Mereenie (Amadeus Basin) -> Alice Springs -> N along the Stuart Hwy corridor
    # -> Tennant Creek -> Katherine -> Darwin.
    'AGP_S':  [[-23.85,132.30],[-23.70,133.88],[-22.30,134.05],[-20.80,134.15],[-19.65,134.19]],
    'AGP_N':  [[-19.65,134.19],[-18.00,133.55],[-16.30,133.37],[-14.47,132.26],[-13.20,131.10],[-12.46,130.84]],
    'Beetaloo_Pipe': [[-16.30,133.40],[-17.10,133.60],[-18.00,133.90],[-19.65,134.19]],
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
for _fwd, _rev in [('SWQP','SWQP_Rev'),('MSP','MSP_Rev'),('VNI','VNI_Rev'),('PK2SYD','SYD2PK'),('SS2Surat','Surat2SS'),('NGP','NGP_Rev')]:
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
# App – Bootstrap base (we override everything with Material CSS)
# ---------------------------------------------------------------------------
app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP],
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

.md-kpi-value {
  font-size: 22px;
  font-weight: 700;
  color: var(--md-text);
  line-height: 1.1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
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
# Domestic gas reservation shares offered by the slider, as whole percents.
RESERVATION_PCTS = [int(round(x * 100)) for x in RESERVATION_LEVELS]

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


def _base_of(k):
    """Baseline name out of a scenario key."""
    if 'Base_' not in k:
        return ''
    return k.split('Base_', 1)[1].split('_Winter', 1)[0]


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
    if '_Reserve' in k:
        parts.append('Res' + k.split('_Reserve', 1)[1].split('_', 1)[0] + '%')
    if '_Elastic' in k:
        parts.append('Elastic')
    if '_Myopic' in k:
        parts.append('Myopic')
    if '_DR' in k:
        parts.append('DR' + k.rsplit('_DR', 1)[1].split('_', 1)[0] + '%')
    return '  ·  '.join(p for p in parts if p)


def pretty_key(k):
    """Human-readable label for a scenario key Base_<base>_Winter_<w>_LNG_<l>."""
    base = _base_of(k) or None
    rest = k.split(base, 1)[-1] if base else k
    # Before the chained replaces below, which would otherwise rewrite a trailing
    # _Myopic/_DR into the middle of the reservation percentage.
    rest = re.sub(r'_Reserve(\d+)', r'  ·  \1% reservation', rest)
    rest = (rest.replace('_Winter_', 'Winter ').replace('_LNG_', '  ·  LNG ')
                .replace('_Dunkelflaute', '  ·  SA Dunkelflaute 2027')
                .replace('_Elastic', '  ·  Elastic demand')
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

        html.Button('▶  Run Scenario',       id='run-btn',   className='md-btn md-btn-filled'),
        html.Button('⚡  Run All Scenarios',  id='batch-btn', className='md-btn md-btn-tonal'),
        html.Button('↺  Regenerate All Data', id='regen-btn', className='md-btn md-btn-text'),
        html.Button('✕  Clear Results',      id='clear-btn', className='md-btn md-btn-danger'),

        html.Div(id='run-status', className='md-status'),
        html.Div(className='md-progress-wrap', children=[
            dbc.Progress(id='solver-progress', value=0, label='',
                         striped=False, animated=False,
                         style={'display': 'none'}),
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
                       marks={i: l for i, l in enumerate(LEVELS)}, value=1)),

        slider_group('Global LNG Demand',
            dcc.Slider(id='lng-slider', min=0, max=2, step=1,
                       marks={i: l for i, l in enumerate(LEVELS)}, value=1)),

        dbc.Checklist(id='dunkelflaute-toggle',
                      options=[{'label': ' SA Dunkelflaute (2027)', 'value': 'on'}],
                      value=[], switch=True,
                      style={'marginBottom': '16px', 'fontSize': '12px'}),

        dbc.Checklist(id='reservation-toggle',
                      options=[{'label': ' Gas reservation', 'value': 'on'}],
                      value=[], switch=True,
                      style={'marginBottom': '8px', 'fontSize': '12px'}),

        html.Div(id='reservation-slider-wrap', style={'display': 'none'}, children=[
            slider_group('Share of LNG exports reserved',
                dcc.Slider(id='reservation-slider', min=0, max=len(RESERVATION_PCTS) - 1,
                           step=None,
                           marks={i: f'{v}%' for i, v in enumerate(RESERVATION_PCTS)},
                           value=1)),
        ]),

        # Covers every tier and both directions now, not just mass-market shedding,
        # so the label says "demand" rather than naming one tier.
        dbc.Checklist(id='elastic-toggle',
                      options=[{'label': ' Price-responsive demand', 'value': 'on'}],
                      value=[], switch=True,
                      style={'marginBottom': '2px', 'fontSize': '12px'}),
        html.Div('Off: fixed volumes, GPG and industrial curtailment only. '
                 'On: demand sheds above its willingness to pay and rises when '
                 'gas is cheap.',
                 style={'marginBottom': '16px', 'fontSize': '10px',
                        'color': '#888', 'lineHeight': '1.35', 'paddingLeft': '38px'}),

        dbc.Checklist(id='foresight-toggle',
                      options=[{'label': ' Perfect-foresight capacity build', 'value': 'on'}],
                      value=['on'], switch=True,
                      style={'marginBottom': '20px', 'fontSize': '12px'}),

        slider_group('Discount Rate (capacity NPV)',
            dcc.Slider(id='discount-slider', min=0, max=0.12, step=0.01, value=0.07,
                       marks={0: '0%', 0.05: '5%', 0.07: '7%', 0.1: '10%'},
                       tooltip={'placement': 'bottom', 'always_visible': True})),

        slider_group('Optimality Gap',
            dcc.Slider(id='gap-slider', min=0, max=0.05, step=0.001, value=0.01,
                       marks={0: '0%', 0.01: '1%', 0.02: '2%', 0.05: '5%'},
                       tooltip={'placement': 'bottom', 'always_visible': True})),

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
            dbc.Tabs(id='main-tabs', active_tab='tab-map', children=[
                dbc.Tab(label='Network Map',          tab_id='tab-map'),
                dbc.Tab(label='Production & Dispatch', tab_id='tab-prod'),
                dbc.Tab(label='Storage Dynamics',      tab_id='tab-storage'),
                dbc.Tab(label='Price Outcomes',        tab_id='tab-price'),
                dbc.Tab(label='Expansions',            tab_id='tab-exp'),
                dbc.Tab(label='GPG & Large Users',     tab_id='tab-ind'),
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
                    dbc.Checklist(id='map-options',
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
            html.Div(id='tab-price-content', style={'display': 'none'}, children=[
                dcc.Graph(id='price-high-graph',  style={'marginBottom': '4px'}),
                _dl_btn('dl-price-high'),
                dcc.Graph(id='price-low-graph'),
                _dl_btn('dl-price-low'),
            ]),

            # Expansions
            html.Div(id='tab-exp-content', style={'display': 'none'}, children=[
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


def get_filtered(key, end_year):
    data = load_results()
    return [r for r in data['all_scenarios'].get(key, []) if r['Year'] <= end_year]


def build_summary(filtered_results):
    rows, prices_trend, builds_timeline, total_cost = [], [], [], 0
    exp_lookup = static_data['expansion'].set_index('Name').to_dict('index')
    for res in filtered_results:
        y = res['Year']
        _prices = res['prices']
        _prod   = res['production']
        merged  = pd.merge(_prices, _prod, on=['Node', 'Day'])
        p_avg   = ((merged['Price'] * merged['Value']).sum() / merged['Value'].sum()
                   if not merged.empty else _prices['Price'].mean())
        rows.append({'Year': y, 'Production_PJ': _prod['Value'].sum() / 1000,
                     'Shortage_TJ': res['shortage']['Value'].sum(),
                     'Avg_Price': p_avg})
        total_cost += res['total_cost']
        if not _prices.empty:
            prices_trend.append(_prices[['Node', 'Price']].assign(Year=y))
        for b in res['builds']:
            if not any(bt['Project'] == b for bt in builds_timeline):
                info = exp_lookup.get(b, {})
                builds_timeline.append({
                    'Year': y,
                    'Project': b,
                    'Type': info.get('Type', '—'),
                    'New Capacity (TJ/d)': info.get('NewCapacity', '—'),
                    'CapEx ($M)': f"{info['CapEx']/1e6:,.0f}" if info.get('CapEx') else '—',
                })
    return (
        pd.DataFrame(rows) if rows else pd.DataFrame(columns=['Year','Production_PJ','Shortage_TJ','Avg_Price']),
        pd.concat(prices_trend, ignore_index=True)[['Year', 'Node', 'Price']]
        if prices_trend else pd.DataFrame(columns=['Year', 'Node', 'Price']),
        pd.DataFrame(builds_timeline) if builds_timeline else pd.DataFrame(columns=['Year','Project','Type','New Capacity (TJ/d)','CapEx ($M)']),
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
     Output('tab-exp-content',     'style'),
     Output('tab-ind-content',     'style')],
    Input('main-tabs', 'active_tab'),
)
def show_tab(active):
    order = ['tab-map','tab-prod','tab-storage','tab-price','tab-exp','tab-ind']
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
    State('baseline-selector', 'value'),
    State('dunkelflaute-toggle', 'value'),
    State('reservation-toggle', 'value'),
    State('reservation-slider', 'value'),
    State('discount-slider', 'value'),
    State('foresight-toggle', 'value'),
    State('elastic-toggle', 'value'),
    State('refresh-counter', 'data'),
    background=True,
    running=[
        (Output('run-btn',          'disabled'), True,  False),
        (Output('batch-btn',        'disabled'), True,  False),
        (Output('solver-progress',  'style'),
         {'display': 'block'}, {'display': 'none'}),
        (Output('run-status', 'children'), '⏳  Solving…', ''),
    ],
    progress=[Output('solver-progress', 'value'), Output('solver-progress', 'label')],
    prevent_initial_call=True,
)
def run_scenario(set_progress, n_clicks, wi, li, gap, baseline, dunkel, resv_on, resv_i,
                 discount, foresight_v, elastic_v, refresh):
    w, l = LEVELS[wi], LEVELS[li]
    baseline = baseline or 'StepChange'
    dunkelflaute = bool(dunkel) and 'on' in dunkel
    reservation = reservation_share(resv_on, resv_i)
    foresight = 'on' in (foresight_v or [])
    elastic = 'on' in (elastic_v or [])
    dr = 0.07 if discount is None else float(discount)
    def _cb(yr, p):
        pct = int(p * 100)
        set_progress((pct, f'Solving {yr}… {pct}%'))
    result = solve_scenario(w, l, mip_gap=gap, callback=_cb,
                            baseline=baseline, dunkelflaute=dunkelflaute,
                            discount_rate=dr, foresight=foresight,
                            reservation=reservation, elastic_demand=elastic)
    key = (f'Base_{baseline}_Winter_{w}_LNG_{l}'
           + ('_Dunkelflaute' if dunkelflaute else '')
           + (f'_Reserve{round(reservation * 100)}' if reservation else '')
           + ('_Elastic' if elastic else '')
           + ('' if foresight else '_Myopic')
           + (f'_DR{round(dr*100)}' if foresight and abs(dr - 0.07) > 1e-9 else ''))
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


# ---------------------------------------------------------------------------
# Run All Scenarios (background)
# ---------------------------------------------------------------------------
@app.callback(
    Output('refresh-counter', 'data',     allow_duplicate=True),
    Output('run-status',      'children', allow_duplicate=True),
    Input('batch-btn', 'n_clicks'),
    State('gap-slider', 'value'),
    State('baseline-selector', 'value'),
    State('discount-slider', 'value'),
    State('foresight-toggle', 'value'),
    State('reservation-toggle', 'value'),
    State('reservation-slider', 'value'),
    State('refresh-counter', 'data'),
    background=True,
    running=[
        (Output('run-btn',         'disabled'), True,  False),
        (Output('batch-btn',       'disabled'), True,  False),
        (Output('solver-progress', 'style'),
         {'display': 'block'}, {'display': 'none'}),
        (Output('run-status', 'children'), '⏳  Batch running…', ''),
    ],
    progress=[Output('solver-progress', 'value'), Output('solver-progress', 'label')],
    prevent_initial_call=True,
)
def run_batch(set_progress, n_clicks, gap, baseline, discount, foresight_v,
              resv_on, resv_i, refresh):
    # Every combination: all GSOO baselines x Winter x LNG (dunkelflaute
    # off) -> 27 runs, plus one Step Change + SA Dunkelflaute (2027) case at the
    # central Winter/LNG so it sits alongside its plain Step Change counterpart.
    #
    # The batch honours the sidebar reservation setting: with the toggle on it
    # solves the whole sweep at that reserved share and writes _Reserve<pct>
    # keys, which land in the result dropdown ALONGSIDE the plain runs rather
    # than replacing them. It does not loop over reservation levels itself --
    # one batch, one share, deliberately (the full cross-product is ~12 h).
    foresight = 'on' in (foresight_v or [])
    reservation = reservation_share(resv_on, resv_i)
    dr = 0.07 if discount is None else float(discount)
    # Segment order must match the single-run key builder in run_scenario:
    # Dunkelflaute, then Reserve, then Myopic/DR.
    resv_suffix = f'_Reserve{round(reservation * 100)}' if reservation else ''
    suffix = ('' if foresight else '_Myopic') + (f'_DR{round(dr * 100)}' if foresight and abs(dr - 0.07) > 1e-9 else '')
    jobs = [(b['value'], w, l, False) for b in BASELINES for w in LEVELS for l in LEVELS]
    jobs.append(('StepChange', 'Medium', 'Medium', True))
    data = load_results()
    for i, (b, w, l, dunkel) in enumerate(jobs):
        key = f'Base_{b}_Winter_{w}_LNG_{l}' + ('_Dunkelflaute' if dunkel else '') + resv_suffix + suffix
        # Skip already-computed base combos, but always recompute the dunkelflaute
        # case so edits to the event flow through on a re-run.
        if dunkel or key not in data['all_scenarios']:
            def _cb(yr, p, _i=i, _n=len(jobs), _b=b, _w=w, _l=l, _d=dunkel):
                overall = int((_i + p) / _n * 100)
                tag = ' · SA Dunkelflaute 2027' if _d else ''
                tag += f' · {round(reservation * 100)}% reservation' if reservation else ''
                set_progress((overall, f'{BASELINE_LABEL.get(_b, _b)} · Winter {_w} · LNG {_l}{tag} · Year {yr} — {overall}%'))
            data['all_scenarios'][key] = solve_scenario(w, l, mip_gap=gap, callback=_cb, baseline=b, dunkelflaute=dunkel, discount_rate=dr, foresight=foresight, reservation=reservation)
            data['current_key'] = key
            save_results(data)
        pct = int((i + 1) / len(jobs) * 100)
        set_progress((pct, f'Scenario {i+1}/{len(jobs)} complete — {pct}%'))
    resv_note = f' at {round(reservation * 100)}% reservation' if reservation else ''
    return (refresh or 0) + 1, f'✓  Batch complete — {len(jobs)} scenarios (all baselines + SA dunkelflaute){resv_note}'

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
        (Output('run-btn',   'disabled'), True, False),
        (Output('batch-btn', 'disabled'), True, False),
        (Output('regen-btn', 'disabled'), True, False),
        (Output('solver-progress', 'style'),
         {'display': 'block'}, {'display': 'none'}),
        (Output('run-status', 'children'), '⏳  Regenerating data…', ''),
    ],
    progress=[Output('solver-progress', 'value'), Output('solver-progress', 'label')],
    prevent_initial_call=True,
)
def regen_demand(set_progress, n):
    if not n:
        return no_update
    def _cb(label, frac):
        pct = int(frac * 100)
        set_progress((pct, f'{label} — {pct}%'))
    regenerate_all(progress=_cb)
    set_progress((100, 'Complete — 100%'))
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
    reserved_pj = sum(r.get('lng_reserved_tj', 0) for r in filtered) / 1000
    # How much of the reserved volume the domestic market actually absorbed. It is
    # offered at $0 so it is taken up wherever it can physically reach a buyer;
    # a gap means the market could not absorb it, not that it was uneconomic.
    served_pj = sum(r.get('reserved_served_tj', 0) for r in filtered) / 1000
    # Mass-market load that priced itself out. Absent from scenarios solved before
    # the elastic lever existed, and from any run with it switched off.
    mm_shed_pj = sum(float(r['massmarket']['Curtailed'].sum())
                     for r in filtered if len(r.get('massmarket', ()))) / 1000
    # Demand taken up because gas was cheap (GPG expansion and industrial uptake).
    raised_pj = sum(float(r['demand_raise']['Value'].sum())
                    for r in filtered if len(r.get('demand_raise', ()))) / 1000
    elastic_run = any(r.get('elastic_demand') for r in filtered)
    chips = [
        kpi_card('Final Price',  final_price),
        # Not comparable across runs in two separate ways. Under a reservation the
        # objective carries no export revenue, and the reserved gas is costed at
        # zero, so both removing export demand and reserving more always lower it.
        # Under elastic demand it also carries a negative benefit term for demand
        # taken up cheaply, which is a surplus, not a cost. Either way it is the
        # cost of serving what was served, not a welfare number.
        kpi_card('System Cost' + (' (served gas only)' if reserved_pj else '')
                 + (' (net of demand benefit)' if elastic_run and not reserved_pj else ''),
                 f"${total_cost/1e6:,.0f}M"),
        kpi_card('Total Supply', f"{summary['Production_PJ'].sum():,.0f} PJ"),
        kpi_card('New Projects', str(len(builds_df))),
    ]
    if reserved_pj:
        take_up = f" ({served_pj/reserved_pj*100:.0f}% taken up)" if reserved_pj else ""
        chips.insert(3, kpi_card('Gas Reserved', f"{reserved_pj:,.0f} PJ{take_up}"))
    if raised_pj:
        chips.insert(3, kpi_card('Demand Raised', f"{raised_pj:,.0f} PJ"))
    if mm_shed_pj:
        chips.insert(3, kpi_card('Demand Shed', f"{mm_shed_pj:,.0f} PJ"))
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

    # Per-node throughput (production + inflow, TJ/yr). Used to (a) volume-weight the
    # system average price and (b) mask "phantom" nodes: an undeveloped/disconnected
    # potential node (e.g. Beetaloo before it is built) carries no gas, so its nodal
    # balance dual sits at the value-of-lost-load ($300) — a real shadow price, but
    # meaningless as a market price. It must not drag the average or paint the node hot.
    PRICE_MIN_TP = 1.0   # TJ/yr; below this a node is treated as unpriced
    _infl   = _flow.groupby('To')['Value'].sum() if not _flow.empty else pd.Series(dtype=float)
    _prod_n = _prod.groupby('Node')['Value'].sum() if not _prod.empty else pd.Series(dtype=float)
    throughput = _prod_n.add(_infl, fill_value=0.0)
    _pw = throughput.reindex(price_map.index).fillna(0.0).clip(lower=0)
    avg_price = float((price_map * _pw).sum() / _pw.sum()) if _pw.sum() > 0 else float(price_map.mean())

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
        tt += _fac_block(static_data.get('ind_bbg'), 'Large industrial', '🏭', ind_serv.get(node, 0), ind_cur.get(node, 0))
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
            exp_row = import_exp[import_exp['Target'] == row_t['Node']]
            e_cap = int(exp_row.iloc[0]['NewCapacity']) if not exp_row.empty else '?'
            e_capex = f"${exp_row.iloc[0]['CapEx']/1e6:,.0f}M" if not exp_row.empty else '?'
            proj_name = exp_row.iloc[0]['Name'] if not exp_row.empty else row_t['Node']
            built_yr = builds_df[builds_df['Project'] == proj_name]['Year'].iloc[0] if not builds_df.empty and proj_name in builds_df['Project'].values else '?'
            tip = (f"<b>⚓ {row_t['Node']} — LNG Import Terminal</b><br>"
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
            if e['Type'] == 'Terminal':
                continue  # terminals rendered separately above
            target = e['Target']
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
            exp_labels.append(proj.replace('_', ' '))
            exp_tips.append(f"<b>✦ {proj}</b><br>Type: {e['Type']}<br>Built: {built_yr}<br>+{e['NewCapacity']} TJ/d<br>CapEx: ${e['CapEx']/1e6:,.0f}M")
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
    Input('main-tabs',       'active_tab'),
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
    Input('main-tabs',       'active_tab'),
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
    Output('price-high-graph', 'figure'),
    Output('price-low-graph',  'figure'),
    Input('result-selector', 'value'),
    Input('horizon-slider',  'value'),
    Input('main-tabs',       'active_tab'),
    Input('theme-store',     'data'),
)
def update_prices(key, end_year, active_tab, theme):
    if active_tab != 'tab-price':
        return no_update, no_update
    tmpl = 'gary_dark' if theme == 'dark' else CHART_TEMPLATE
    b = blank_fig(tmpl)
    if not key:
        return b, b
    filtered = get_filtered(key, end_year)
    if not filtered:
        return b, b

    # Daily nodal prices for the demand centres over the selected horizon.
    frames = [r['prices'].assign(Year=r['Year']) for r in filtered if not r['prices'].empty]
    if not frames:
        return b, b
    price_nodes = static_data['nodes'][static_data['nodes']['Type'].isin(['Demand', 'LNG'])]['Name'].tolist()
    dpr = pd.concat(frames)
    dpr = dpr[dpr['Node'].isin(price_nodes)].copy()
    dpr['Date'] = pd.to_datetime(dpr['Year'].astype(str) + dpr['Day'].astype(int).astype(str).str.zfill(3),
                                 format='%Y%j')
    # Monthly-average price (~12 points/year): smooths daily noise while keeping
    # the seasonal (winter) signal.
    dpr['Month'] = dpr['Date'].dt.to_period('M').dt.to_timestamp()
    q = dpr.groupby(['Month', 'Node'])['Price'].mean().reset_index()
    # Split centres by the highest quarterly price they reach (cap-bound vs low)
    # so each chart auto-scales to its own group.
    ann    = q.groupby('Node')['Price'].max()
    hit    = [n for n in price_nodes if ann.get(n, 0) >= 150]
    no_hit = [n for n in price_nodes if n not in hit]

    def _price_fig(nodes, title):
        sub = q[q['Node'].isin(nodes)].sort_values('Month')
        if sub.empty:
            f = blank_fig(tmpl)
            f.update_layout(title=title)
            return f
        f = px.line(sub, x='Month', y='Price', color='Node', title=title,
                    template=tmpl, labels={'Price': '$/GJ', 'Month': ''},
                    render_mode='svg')
        f.update_yaxes(rangemode='tozero')
        return f

    fig_high = _price_fig(hit, 'Demand & LNG nodes reaching the $300 cap — monthly avg ($/GJ)')
    fig_low  = _price_fig(no_hit, 'Demand & LNG nodes staying below the cap — monthly avg ($/GJ)')
    return fig_high, fig_low

# ---------------------------------------------------------------------------
# Expansions
# ---------------------------------------------------------------------------
@app.callback(
    Output('expansions-content', 'children'),
    Input('result-selector', 'value'),
    Input('horizon-slider',  'value'),
    Input('main-tabs',       'active_tab'),
    Input('theme-store',     'data'),
)
def update_expansions(key, end_year, active_tab, theme):
    if active_tab != 'tab-exp':
        return no_update
    tmpl = 'gary_dark' if theme == 'dark' else CHART_TEMPLATE
    if not key:
        return md_alert('No results loaded.', 'info')
    filtered = get_filtered(key, end_year)
    if not filtered:
        return md_alert('No results in this horizon.', 'info')
    _, _, builds_df, _ = build_summary(filtered)
    if builds_df.empty:
        return md_alert('No new infrastructure built in this scenario.', 'success')

    sorted_df = builds_df.sort_values('Year').reset_index(drop=True)

    # Gantt-style bar chart
    type_colors = {'Pipeline': '#1976D2', 'Terminal': '#E65100', 'LNG': '#2E7D32'}
    fig = go.Figure()
    for _, row in sorted_df.iterrows():
        color = type_colors.get(row.get('Type', ''), '#78909C')
        label = f"<b>{row['Project']}</b><br>Built: {row['Year']}<br>Type: {row.get('Type','—')}<br>Capacity: {row.get('New Capacity (TJ/d)','—')} TJ/d<br>CapEx: ${row.get('CapEx ($M)','—')}M"
        fig.add_trace(go.Bar(
            x=[end_year - row['Year'] + 1],
            y=[row['Project']],
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

    table = dbc.Table.from_dataframe(
        sorted_df.drop(columns=['Type'], errors='ignore') if 'Type' not in sorted_df.columns else sorted_df,
        striped=True, bordered=False, hover=True, size='sm',
        className='table-light',
        style={'fontSize': '0.85rem'},
    )
    return html.Div([dcc.Graph(figure=fig, config={'displayModeBar': False}), table])

# ---------------------------------------------------------------------------
# Industrial
# ---------------------------------------------------------------------------
@app.callback(
    Output('ind-graph', 'figure'),
    Input('result-selector', 'value'),
    Input('horizon-slider',  'value'),
    Input('main-tabs',       'active_tab'),
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
