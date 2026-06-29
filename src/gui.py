import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import numpy as np
import os
import pickle
import time
import subprocess
from model import GasMarketModel
from solve import solve_scenario, get_lng_mult
from regenerate_data import regenerate_all

st.set_page_config(page_title="Gas Market 2050 Explorer", layout="wide")

# --- Consistent Dark Theme CSS ---
st.markdown("""
    <style>
    .stMetric {
        background-color: #1e2130;
        padding: 15px;
        border-radius: 10px;
        border: 1px solid #3e4150;
    }
    [data-testid="stSidebar"] {
        background-color: #11141c;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 10px;
    }
    .stTabs [data-baseweb="tab"] {
        padding: 10px 20px;
        background-color: #1e2130;
        border-radius: 5px 5px 0 0;
        margin-right: 4px;
    }
    /* Green Progress Bar */
    .stProgress > div > div > div > div {
        background-color: #28a745;
    }
    </style>
""", unsafe_allow_html=True)

st.title("🚀 Gas Market Explorer")

# --- Static Data Loader ---
@st.cache_data
def load_static_data():
    base_path = os.path.dirname(__file__)
    data_dir = os.path.join(base_path, "data")
    return {
        'nodes': pd.read_csv(os.path.join(data_dir, "nodes.csv")),
        'arcs': pd.read_csv(os.path.join(data_dir, "arcs.csv")),
        'demand': pd.read_csv(os.path.join(data_dir, "demand_2050.csv")),
        'supply': pd.read_csv(os.path.join(data_dir, "supply.csv")).dropna(subset=['Node', 'Capacity', 'Cost']),
        'ind_facs': pd.read_csv(os.path.join(data_dir, "industrial_facilities.csv")),
        'expansion': pd.read_csv(os.path.join(data_dir, "expansion_options.csv"))
    }

def add_arrowhead(fig, lat1, lon1, lat2, lon2, color, width, size=0.45):
    d_lat, d_lon = lat2 - lat1, lon2 - lon1
    length = np.sqrt(d_lat**2 + d_lon**2)
    if length < 0.05: return
    u_lat, u_lon = d_lat / length, d_lon / length
    p_lat, p_lon = -u_lon, u_lat
    tip_lat, tip_lon = lat1 + 0.7 * d_lat, lon1 + 0.7 * d_lon
    b1_lat = tip_lat - size * u_lat + (size * 0.6) * p_lat
    b1_lon = tip_lon - size * u_lon + (size * 0.6) * p_lon
    b2_lat = tip_lat - size * u_lat - (size * 0.6) * p_lat
    b2_lon = tip_lon - size * u_lon - (size * 0.6) * p_lon
    fig.add_trace(go.Scattermapbox(lat=[b1_lat, tip_lat, b2_lat, b1_lat], lon=[b1_lon, tip_lon, b2_lon, b1_lon], mode='lines', fill="toself", fillcolor=color, line=dict(width=1, color=color), hoverinfo='skip', showlegend=False))

# --- Persistent Results Logic ---
RESULTS_FILE = os.path.join(os.path.dirname(__file__), "data", "precalculated_results.pkl")

def save_results():
    data = {'all_scenarios': st.session_state['all_scenarios'], 'current_key': st.session_state.get('current_key')}
    with open(RESULTS_FILE, 'wb') as f: pickle.dump(data, f)

if 'all_scenarios' not in st.session_state:
    if os.path.exists(RESULTS_FILE):
        try:
            with open(RESULTS_FILE, 'rb') as f:
                data = pickle.load(f)
                st.session_state['all_scenarios'] = data.get('all_scenarios', {})
                st.session_state['current_key'] = data.get('current_key')
        except: st.session_state['all_scenarios'] = {}
    else: st.session_state['all_scenarios'] = {}

# --- Sidebar ---
st.sidebar.header("Scenario Configuration")
winter_level = st.sidebar.select_slider("Southern Winter Stress", options=["Low", "Medium", "High"], value="Medium")
lng_level = st.sidebar.select_slider("Global LNG Demand", options=["Low", "Medium", "High"], value="Medium")
# adgsm_enabled = st.sidebar.toggle("ADGSM Domestic Reservation", value=False)
adgsm_enabled = False

st.sidebar.markdown("---")
st.sidebar.header("Solver Settings")
mip_gap = st.sidebar.slider("Solver Optimality Gap", 0.0, 0.05, 0.005, step=0.001)
if mip_gap == 0.0: st.sidebar.warning("⚠️ 0.0 gap is VERY slow for full batches.")

st.sidebar.markdown("---")
st.sidebar.header("Data Management")
if st.sidebar.button("Regenerate All Data"):
    regen_status = st.sidebar.empty()
    with st.spinner("Rebuilding all data from source (GBB + GSOO)..."):
        regenerate_all(progress=lambda label, frac: regen_status.text(f"{int(frac*100)}% — {label}"))
        st.cache_data.clear() # Clear cache
        st.success("All data regenerated from source!")

if st.sidebar.button("🚀 Run Current Scenario"):
    status_text = st.sidebar.empty()
    progress_bar = st.sidebar.progress(0)
    
    def annual_callback(year, progress):
        status_text.text(f"Processing Year: {year}")
        progress_bar.progress(progress)

    try:
        res = solve_scenario(winter_level, lng_level, adgsm_enabled=adgsm_enabled, mip_gap=mip_gap, callback=annual_callback)
        key = f"ADGSM_{adgsm_enabled}_Winter_{winter_level}_LNG_{lng_level}"
        st.session_state['all_scenarios'][key] = res
        st.session_state['current_key'] = key
        save_results(); st.rerun()
    except Exception as e: st.error(f"Error: {e}")

if st.sidebar.button("📊 Run All Scenarios (Batch)"):
    adgsm_opts, winters, lngs = [False], ["Low", "Medium", "High"], ["Low", "Medium", "High"]
    total_scenarios = len(adgsm_opts) * len(winters) * len(lngs)
    
    st.sidebar.markdown("### Batch Progress")
    overall_status = st.sidebar.empty()
    overall_progress = st.sidebar.progress(0)
    
    st.sidebar.markdown("### Current Scenario")
    annual_status = st.sidebar.empty()
    annual_progress = st.sidebar.progress(0)
    
    def batch_annual_callback(year, progress):
        annual_status.text(f"Processing Year: {year}")
        annual_progress.progress(progress)

    count = 0
    for a_opt in adgsm_opts:
        for w in winters:
            for l in lngs:
                count += 1
                key = f"ADGSM_{a_opt}_Winter_{w}_LNG_{l}"
                
                overall_status.text(f"Scenario {count}/{total_scenarios}")
                overall_progress.progress(count / total_scenarios)

                if key not in st.session_state['all_scenarios']:
                    try:
                        res = solve_scenario(w, l, adgsm_enabled=a_opt, mip_gap=mip_gap, callback=batch_annual_callback)
                        st.session_state['all_scenarios'][key] = res
                        save_results()
                    except Exception as e: st.error(f"Error in {key}: {e}"); st.stop()
                else:
                    # Mark current scenario as done for the annual bar
                    batch_annual_callback(2050, 1.0)
    
    st.sidebar.success("All scenarios complete!"); st.rerun()

if st.sidebar.button("🗑️ Clear All Results"):
    st.session_state['all_scenarios'] = {}
    if os.path.exists(RESULTS_FILE): os.remove(RESULTS_FILE)
    st.rerun()

if not st.session_state['all_scenarios']:
    st.info("👈 Set parameters and click 'Run Current Scenario'.")
    st.stop()

available_keys = list(st.session_state['all_scenarios'].keys())
if st.session_state.get('current_key') not in available_keys: st.session_state['current_key'] = available_keys[0]
selected_key = st.sidebar.selectbox("Selected Result", options=available_keys, index=available_keys.index(st.session_state['current_key']))
if selected_key != st.session_state['current_key']:
    st.session_state['current_key'] = selected_key; save_results()

full_results = st.session_state['all_scenarios'][selected_key]
end_year = st.sidebar.slider("Analysis Horizon", 2025, max(res['Year'] for res in full_results), max(res['Year'] for res in full_results))
filtered_results = [res for res in full_results if res['Year'] <= end_year]

# --- Geographic Data ---
static_data = load_static_data()
COORDS = {'Surat': [-27.15, 149.07], 'Moomba': [-28.1, 140.2], 'Gippsland': [-38.5, 147.0], 'Sydney': [-33.86, 151.2], 'Melbourne': [-37.81, 144.96], 'Adelaide': [-34.92, 138.6], 'Brisbane': [-27.47, 153.02], 'Gladstone': [-23.84, 151.26], 'APLNG': [-23.76, 151.20], 'GLNG': [-23.80, 151.25], 'QCLNG': [-23.84, 151.30], 'Port_Kembla': [-34.45, 150.9], 'Iona': [-38.55, 142.9], 'Silver_Springs': [-27.4, 149.2]}
ARC_WAYPOINTS = {
    'MSP': [[-28.1097, 140.2001], [-28.1166, 140.2045], [-28.1309, 140.2172], [-28.1768, 140.2852], [-28.2480, 140.3887], [-28.2821, 140.4336], [-28.3949, 140.5805], [-28.4980, 140.7165], [-28.6402, 140.9238], [-28.6894, 140.9986], [-28.9991, 141.4535], [-29.4139, 142.0680], [-29.8628, 142.5944], [-30.4452, 143.3120], [-31.1498, 144.1407], [-31.8720, 145.0251], [-32.4138, 145.7587], [-32.6850, 146.1474], [-33.2834, 146.9151], [-33.7892, 147.5283], [-34.1256, 148.1204], [-34.2388, 148.3260], [-34.5505, 148.9043], [-34.7186, 149.2069], [-34.7551, 149.3703], [-34.7298, 149.5646], [-34.7253, 149.6656], [-34.6957, 149.9614], [-34.6471, 150.0880], [-34.6063, 150.2341], [-34.5353, 150.4044], [-34.4908, 150.4786], [-34.3596, 150.5491], [-34.2916, 150.6113], [-34.2329, 150.7106], [-34.1551, 150.7716], [-34.0117, 150.7923], [-33.8879, 150.8229], [-33.8322, 150.8640]],
    'EGP': [[-38.2085, 147.1644], [-38.1462, 147.1487], [-38.0507, 147.1633], [-37.9869, 147.2180], [-37.9377, 147.3783], [-37.8798, 147.4731], [-37.8335, 147.5533], [-37.8016, 147.5861], [-37.7727, 147.6626], [-37.7582, 147.7683], [-37.7444, 148.4173], [-37.7463, 148.4035], [-37.7524, 148.3187], [-37.7495, 148.2859], [-37.7408, 148.0162], [-37.7582, 147.9323], [-37.5555, 148.9200], [-37.3180, 149.2007], [-37.1973, 149.1901], [-37.1530, 149.1825], [-36.8489, 149.2736], [-36.5767, 149.2845], [-36.4203, 149.2407], [-36.2466, 149.1679], [-36.0758, 149.1642], [-35.8354, 149.1642], [-35.7531, 149.1631], [-35.6353, 149.2158], [-35.5654, 149.2595], [-35.5419, 149.3256], [-35.4335, 149.3970], [-35.2927, 149.6357], [-35.2533, 149.7533], [-35.2184, 149.8357], [-35.1970, 149.9086], [-35.1648, 149.9994], [-35.0883, 150.1191], [-35.0767, 150.2175], [-35.1144, 150.3050], [-35.0651, 150.4180], [-35.0220, 150.4765], [-34.9030, 150.5310], [-34.8045, 150.6367], [-34.7553, 150.7095], [-34.7437, 150.8152], [-34.6626, 150.8152], [-34.5931, 150.7970], [-34.5237, 150.7783]],
    'VNI': [[-37.81, 144.96], [-37.36, 145.13], [-36.75, 145.56], [-36.36, 146.31], [-36.07, 146.91], [-35.12, 147.37], [-34.84, 148.11], [-34.41, 148.91], [-34.23, 150.71], [-33.86, 151.2]],
    'SWP': [[-38.55, 142.9], [-38.34, 143.58], [-38.15, 144.35], [-37.95, 144.75], [-37.81, 144.96]],
    'VGP': [[-38.5, 147.0], [-38.25, 146.4], [-38.12, 145.3], [-37.95, 144.7], [-38.15, 144.3], [-38.55, 142.9]],
    'PK2SYD': [[-34.45, 150.9], [-34.32, 150.85], [-34.15, 150.82], [-33.86, 151.2]],
    'MAPS': [[-28.1155, 140.2057], [-28.1414, 140.1974], [-28.1694, 140.1888], [-28.2120, 140.1881], [-28.2694, 140.1764], [-28.3400, 140.1471], [-28.4507, 140.1202], [-28.5634, 140.0898], [-28.6544, 140.0572], [-28.7302, 140.0371], [-28.8423, 140.0315], [-28.9034, 140.0291], [-28.9603, 140.0192], [-29.0182, 140.0182], [-29.1299, 140.0133], [-29.2195, 140.0083], [-29.3357, 140.0110], [-29.4039, 139.9893], [-29.5070, 139.9583], [-29.6042, 139.9374], [-29.7127, 139.9097], [-29.8026, 139.8877], [-29.9142, 139.8491], [-30.0125, 139.8130], [-30.1215, 139.7716], [-30.2241, 139.7344], [-30.3405, 139.7027], [-30.4578, 139.6843], [-30.5694, 139.6644], [-30.6865, 139.6387], [-30.8247, 139.5960], [-30.9329, 139.5540], [-31.0267, 139.5103], [-31.1444, 139.4674], [-31.2588, 139.4299], [-31.3855, 139.3871], [-31.5075, 139.3578], [-31.6367, 139.3497], [-31.7529, 139.3384], [-31.8566, 139.3079], [-31.9752, 139.2100], [-32.0661, 139.1705], [-32.1582, 139.1230], [-32.2754, 139.0722], [-32.4142, 139.0315], [-32.5485, 138.9920], [-32.6524, 138.9604], [-32.7452, 138.9339], [-32.8576, 138.9006], [-32.9630, 138.8694], [-33.0581, 138.8431], [-33.1548, 138.8156], [-33.2441, 138.8060], [-33.3505, 138.7600], [-33.4481, 138.7592], [-33.5607, 138.7570], [-33.6597, 138.7553], [-33.7820, 138.7574], [-33.8686, 138.7523], [-34.0271, 138.6945], [-34.1213, 138.6413], [-34.2404, 138.6256], [-34.4095, 138.6152], [-34.5027, 138.6089], [-34.6119, 138.6063], [-34.7247, 138.5801], [-34.8153, 138.5338], [-34.92, 138.6]],
    'SWQP': [[-28.1097, 140.2001], [-26.9731, 143.2067], [-26.9472, 143.7501], [-26.9566, 145.3210], [-26.9443, 145.8589], [-26.7910, 145.1567], [-26.7778, 145.2706], [-26.7268, 145.6739], [-26.7120, 145.8284], [-26.6853, 146.1427], [-26.6568, 146.1417], [-26.6330, 146.7375], [-26.6114, 146.5394], [-26.6178, 146.6066], [-26.6643, 147.3402], [-26.6844, 147.7525], [-26.6818, 147.9063], [-26.6902, 148.1651], [-26.6942, 148.4101], [-26.6924, 148.5289], [-26.6915, 148.9213], [-26.6815, 149.0941], [-27.15, 149.07]],
    'RBP': [[-27.15, 149.07], [-26.6936, 149.1862], [-26.7021, 149.2396], [-26.7118, 149.2914], [-26.7372, 149.3972], [-26.7537, 149.4610], [-26.7697, 149.5203], [-26.7883, 149.5908], [-26.8395, 149.6474], [-26.8640, 149.6713], [-26.8838, 149.7226], [-26.8920, 149.7830], [-26.9076, 149.9121], [-26.9154, 149.9992], [-26.9223, 150.0825], [-26.9322, 150.1205], [-26.9439, 150.1985], [-26.9529, 150.2585], [-26.9713, 150.3290], [-26.9869, 150.4177], [-26.9952, 150.4822], [-27.0209, 150.5979], [-27.0399, 150.6769], [-27.0594, 150.7495], [-27.0647, 150.7936], [-27.0712, 150.8341], [-27.0765, 150.8918], [-27.0851, 150.9375], [-27.1574, 150.7734], [-27.1978, 151.2089], [-27.47, 153.02]],
    'SEA_Gas': [[-38.55, 142.9], [-38.5644, 143.0605], [-38.4546, 142.8872], [-38.3720, 142.8599], [-38.2742, 142.8387], [-38.1652, 142.8077], [-38.1253, 142.7523], [-38.1018, 142.7214], [-38.0733, 142.6726], [-38.0281, 142.0157], [-37.9908, 141.9621], [-37.9756, 141.9313], [-37.9262, 141.8819], [-37.9137, 141.8379], [-37.8779, 141.7985], [-37.8429, 141.7696], [-37.8205, 141.7237], [-37.7912, 141.6199], [-37.7686, 141.6016], [-37.7452, 141.5813], [-37.7229, 141.5408], [-37.7010, 141.5340], [-37.6640, 141.4900], [-37.6339, 141.4505], [-37.6194, 141.4252], [-37.6100, 141.4015], [-37.6045, 141.3742], [-37.5892, 141.3587], [-37.5558, 141.3608], [-37.5403, 141.3456], [-37.5139, 141.3361], [-37.5024, 141.3333], [-37.4712, 141.2954], [-37.4564, 141.2747], [-37.4325, 141.2518], [-37.4095, 141.2414], [-37.3887, 141.2146], [-37.3638, 141.2122], [-37.3101, 141.1770], [-37.2800, 141.1435], [-37.2564, 141.1189], [-37.2224, 141.0863], [-37.1633, 141.0267], [-37.1022, 140.9416], [-37.0094, 140.8580], [-36.8837, 140.7308], [-36.7565, 140.6389], [-36.6303, 140.5222], [-36.5057, 140.4024], [-36.3767, 140.2814], [-36.2486, 140.1173], [-36.1085, 139.9910], [-35.9863, 139.8731], [-35.8566, 139.7570], [-35.7328, 139.6307], [-35.5906, 139.4674], [-35.4524, 139.3512], [-35.3242, 139.2158], [-35.1963, 139.0913], [-35.0754, 138.9669], [-34.92, 138.6]],
    'APLNG_Pipe': [[-27.15, 149.07], [-26.2, 149.8], [-25.1, 150.3], [-24.3, 150.8], [-23.76, 151.20]],
    'GLNG_Pipe': [[-27.15, 149.07], [-26.4, 150.0], [-25.3, 150.5], [-24.5, 151.0], [-23.80, 151.25]],
    'WGP_Pipe': [[-27.15, 149.07], [-26.6, 150.2], [-25.5, 150.7], [-24.7, 151.2], [-23.84, 151.30]],
    'QGP': [[-27.15, 149.07], [-26.85, 149.20], [-26.50, 149.55], [-26.10, 150.00], [-25.50, 150.35], [-24.90, 150.65], [-24.40, 150.90], [-23.84, 151.26]],
    'Longford': [[-38.50, 147.00], [-38.47, 146.70], [-38.42, 146.30], [-38.28, 145.95], [-38.10, 145.55], [-37.95, 145.20], [-37.85, 145.00], [-37.81, 144.96]],
}
# Auto-generate reverse-direction waypoints for bidirectional arcs
for _fwd, _rev in [('SWQP', 'SWQP_Rev'), ('MSP', 'MSP_Rev'), ('VNI', 'VNI_Rev'), ('PK2SYD', 'SYD2PK')]:
    if _rev not in ARC_WAYPOINTS and _fwd in ARC_WAYPOINTS:
        ARC_WAYPOINTS[_rev] = list(reversed(ARC_WAYPOINTS[_fwd]))

yearly_summary, prices_trend, builds_timeline, total_system_cost = [], [], [], 0
for res in filtered_results:
    y = res['Year']
    p_pj, s_tj = sum(p['Value'] for p in res['production']) / 1000, sum(s['Value'] for s in res['shortage']) if res['shortage'] else 0
    _prices, _prod = pd.DataFrame(res['prices']), pd.DataFrame(res['production'])
    merged = pd.merge(_prices, _prod, on=['Node', 'Day'])
    p_avg = (merged['Price'] * merged['Value']).sum() / merged['Value'].sum() if not merged.empty else _prices['Price'].mean()
    yearly_summary.append({'Year': y, 'Production_PJ': p_pj, 'Shortage_TJ': s_tj, 'Avg_Price': p_avg})
    total_system_cost += res['total_cost']
    for p in res['prices']: prices_trend.append({'Year': y, 'Node': p['Node'], 'Price': p['Price']})
    for b in res['builds']:
        if not any(bt['Project'] == b for bt in builds_timeline): builds_timeline.append({'Year': y, 'Project': b})

summary_df = pd.DataFrame(yearly_summary)
if summary_df.empty: summary_df = pd.DataFrame(columns=['Year', 'Production_PJ', 'Shortage_TJ', 'Avg_Price'])
prices_df = pd.DataFrame(prices_trend)
if prices_df.empty: prices_df = pd.DataFrame(columns=['Year', 'Node', 'Price'])
builds_df = pd.DataFrame(builds_timeline)
if builds_df.empty: builds_df = pd.DataFrame(columns=['Year', 'Project'])

# --- Dashboard Layout ---
col1, col2, col3, col4 = st.columns(4)
col1.metric("Final Price", f"${summary_df['Avg_Price'].iloc[-1]:.2f}/GJ" if not summary_df.empty else "$0.00")
col2.metric("System Cost", f"${total_system_cost/1e6:,.0f}M")
col3.metric("Total Supply", f"{summary_df['Production_PJ'].sum():,.0f} PJ")
col4.metric("New Projects", len(builds_timeline))

tab_map, tab_prod, tab_storage, tab_price, tab_exp, tab_ind = st.tabs(["🌐 Network Map", "🚜 Production & Dispatch", "🔋 Storage Dynamics", "💰 Price Outcomes", "🏗️ Expansions", "🏭 Industrial Use"])

with tab_map:
    map_year = st.select_slider("Select Map Year", options=range(2025, end_year + 1), value=end_year)
    res = next((r for r in filtered_results if r['Year'] == map_year), filtered_results[-1])
    _prices, _prod, _flow = pd.DataFrame(res['prices']), pd.DataFrame(res['production']), pd.DataFrame(res['flow'])
    ms1, ms2, ms3, ms4 = st.columns(4)
    ms1.metric(f"Avg Price ({map_year})", f"${_prices['Price'].mean():.2f}/GJ")
    ms2.metric("Total Production", f"{_prod['Value'].sum()/1000:.1f} PJ")
    ms3.metric("Total Shortage", f"{sum(s['Value'] for s in res['shortage']):.1f} TJ" if res['shortage'] else "0 TJ")
    ms4.metric("Active Pipelines", len(_flow[_flow['Value'] > 10]))
    with st.expander("🗺️ Legend & Options"):
        show_labels, show_cap = st.checkbox("Show Labels", value=True), st.checkbox("Show Capacity", value=True)
    price_map = _prices.groupby('Node')['Price'].mean()
    prod_map = _prod.groupby('Node')['Value'].sum() / 1000
    flow_map = _flow.groupby(['From', 'To', 'Arc'])['Value'].sum().reset_index(); flow_map['Value'] /= 1000
    fig = go.Figure()
    # Pipeline visual constants
    PIPE_CASING_COLOR = '#0d0d0d'
    PIPE_FILL_COLOR = '#B8860B'   # Dark goldenrod – conventional gas pipeline colour
    PIPE_BASE_WIDTH = 3

    arc_base_caps, expansion_info = static_data['arcs'].set_index('Name')['Capacity'].to_dict(), static_data['expansion']
    built_by_now = builds_df[builds_df['Year'] <= map_year]['Project'].tolist()

    # Draw Infrastructure Layer (All Arcs) – casing + fill for a 3-D pipe look
    for _, arc_row in static_data['arcs'].iterrows():
        arc = arc_row['Name']
        cap = (arc_base_caps.get(arc, 0) + expansion_info[(expansion_info['Target'] == arc) & (expansion_info['Name'].isin(built_by_now))]['NewCapacity'].sum()) * 365 / 1000
        path = ARC_WAYPOINTS.get(arc, [COORDS[arc_row['From']], COORDS[arc_row['To']]])
        lats, lons = [p[0] for p in path], [p[1] for p in path]

        if show_cap and cap > 0:
            hover = f"<b>{arc}</b><br>{arc_row['From']} → {arc_row['To']}<br>Capacity: {cap:.0f} PJ/yr"
            # Outer casing
            fig.add_trace(go.Scattermapbox(lat=lats, lon=lons, mode='lines',
                                           line=dict(width=PIPE_BASE_WIDTH + 3, color=PIPE_CASING_COLOR),
                                           opacity=0.85, hoverinfo='skip', showlegend=False))
            # Pipe body
            fig.add_trace(go.Scattermapbox(lat=lats, lon=lons, mode='lines',
                                           line=dict(width=PIPE_BASE_WIDTH, color=PIPE_FILL_COLOR),
                                           opacity=0.55, text=hover, hoverinfo='text', showlegend=False))

    # Draw Flow Layer (Active Arcs Only) – casing + utilisation-coloured fill
    for _, row in flow_map.iterrows():
        arc = row['Arc']
        cap = (arc_base_caps.get(arc, 0) + expansion_info[(expansion_info['Target'] == arc) & (expansion_info['Name'].isin(built_by_now))]['NewCapacity'].sum()) * 365 / 1000
        util = (row['Value'] / cap) if cap > 0 else 0
        path = ARC_WAYPOINTS.get(arc, [COORDS[row['From']], COORDS[row['To']]])
        lats, lons = [p[0] for p in path], [p[1] for p in path]

        # Green (spare capacity) → orange (moderate) → red (constrained)
        color        = '#ef4444' if util > 0.9 else ('#f97316' if util > 0.7 else '#22c55e')
        casing_color = '#7f1d1d' if util > 0.9 else ('#7c2d12' if util > 0.7 else '#14532d')
        flow_w = max(3, min(9, 2 + np.log1p(row['Value']) * 1.5))
        hover = f"<b>{arc}</b><br>Flow: {row['Value']:.1f} PJ<br>Utilisation: {util:.1%}<br>Capacity: {cap:.0f} PJ/yr"

        # Outer casing
        fig.add_trace(go.Scattermapbox(lat=lats, lon=lons, mode='lines',
                                       line=dict(width=flow_w + 3, color=casing_color),
                                       opacity=1.0, hoverinfo='skip', showlegend=False))
        # Flow fill
        fig.add_trace(go.Scattermapbox(lat=lats, lon=lons, mode='lines',
                                       line=dict(width=flow_w, color=color),
                                       opacity=0.9, text=hover, hoverinfo='text', showlegend=False))
        add_arrowhead(fig, path[-2][0], path[-2][1], path[-1][0], path[-1][1], color, flow_w)
    
    # Nodes
    node_types, storage_info = static_data['nodes'].set_index('Name')['Type'].to_dict(), static_data['nodes'].set_index('Name')['StorageCapacity'].to_dict()
    fac_dem_map = pd.DataFrame(res['facility_demand']).groupby('Facility')['Value'].sum() / 1000 if res['facility_demand'] else {}
    map_nodes = []
    for node, c in COORDS.items():
        s_v, p_v, n_t = prod_map.get(node, 0), price_map.get(node, 0), node_types.get(node, 'Hub')
        tt = f"<b>{node} ({n_t})</b><br>Price: ${p_v:.2f}/GJ<br>"
        for _, frow in static_data['ind_facs'][static_data['ind_facs']['Node'] == node].iterrows(): tt += f"- {frow['FacilityName']}: {fac_dem_map.get(frow['FacilityName'], 0):.1f} PJ<br>"
        map_nodes.append({'Node': node, 'Lat': c[0], 'Lon': c[1], 'Type': n_t, 'Price': p_v, 'Supply': s_v, 'Tooltip': tt})
    n_df = pd.DataFrame(map_nodes)
    # Scale price colorbar consistently: 0 to max price, at least $15
    max_p = max(15.0, n_df['Price'].max())
    min_p = 0.0
    
    styling = {'Supply': ('circle', None, 4), 'Demand': ('square', '#ff4b4b', 0), 'Storage': ('diamond', '#a78bfa', 0), 'LNG': ('triangle', '#fbbf24', 0), 'Import': ('star', '#34d399', 0), 'Hub': ('circle-open', '#8b8fa3', 0)}
    for nt, (sym, col, sm) in styling.items():
        df_t = n_df[n_df['Type'] == nt]
        if df_t.empty: continue
        size = df_t['Supply'].apply(lambda x: 12 + np.sqrt(x) * sm) if sm > 0 else 14
        fig.add_trace(go.Scattermapbox(lat=df_t['Lat'], lon=df_t['Lon'], mode='markers+text' if show_labels else 'markers', 
                                         marker=dict(size=size, 
                                                     color=df_t['Price'] if col is None else col, 
                                                     colorscale='Viridis' if col is None else None, 
                                                     cmin=min_p, cmax=max_p, 
                                                     showscale=(nt == 'Supply'), 
                                                     colorbar=dict(title="Price ($/GJ)", thickness=15, x=0, y=0.2, len=0.4, tickformat=".1f") if nt == 'Supply' else None), 
                                         text=df_t['Node'] if show_labels else None, textposition="top center", hovertemplate="%{customdata}<extra></extra>", customdata=df_t['Tooltip'], name=nt))
    fig.update_layout(mapbox=dict(style='open-street-map', center=dict(lat=-31, lon=146), zoom=4.2), margin=dict(l=0, r=0, t=0, b=0), height=800, legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01, bgcolor="rgba(255,255,255,0.85)", font=dict(color="#111111")))
    st.plotly_chart(fig, use_container_width=True)

with tab_prod:
    st.subheader("Annual Production (PJ)")
    all_prod = pd.concat([pd.DataFrame(r['production']).assign(Year=r['Year']) for r in filtered_results if r['production']])
    ann_prod = all_prod.groupby(['Year', 'Node'])['Value'].sum().reset_index(); ann_prod['Value'] /= 1000
    st.plotly_chart(px.area(ann_prod, x='Year', y='Value', color='Node').update_yaxes(rangemode="tozero"), use_container_width=True)
    
    st.subheader("Daily Dispatch Timeline (TJ/d)")
    all_daily_prod = []
    for yr_res in filtered_results:
        if yr_res['production']:
            # Create a continuous day count: (Year-2025)*365 + Day
            offset = (yr_res['Year'] - 2025) * 365
            df_yr = pd.DataFrame(yr_res['production'])
            df_yr['GlobalDay'] = df_yr['Day'] + offset
            df_yr['Year'] = yr_res['Year']
            all_daily_prod.append(df_yr)
    
    if all_daily_prod:
        df_dispatch = pd.concat(all_daily_prod)
        fig_dispatch = px.area(df_dispatch, x='GlobalDay', y='Value', color='Node', 
                               title="Continuous Dispatch Horizon (2025-2050)",
                               labels={'GlobalDay': 'Days from 2025', 'Value': 'Production (TJ/d)'})
        fig_dispatch.update_layout(xaxis_rangeslider_visible=True) # Add slider for zooming into specific years
        st.plotly_chart(fig_dispatch, use_container_width=True)
    else:
        st.info("No dispatch data available. Run a scenario to populate this chart.")
    
    st.subheader("Major Flows (PJ)")
    all_flow = pd.concat([pd.DataFrame(r['flow']).assign(Year=r['Year']) for r in filtered_results if r['flow']])
    ann_flow = all_flow[all_flow['Arc'].isin(['MSP', 'EGP', 'VNI', 'WGP_Pipe', 'APLNG_Pipe', 'GLNG_Pipe'])].groupby(['Year', 'Arc'])['Value'].sum().reset_index(); ann_flow['Value'] /= 1000
    st.plotly_chart(px.line(ann_flow, x='Year', y='Value', color='Arc').update_yaxes(rangemode="tozero"), use_container_width=True)
    st.subheader("Annual Shortages (PJ)")
    all_short = [pd.DataFrame(r['shortage']).assign(Year=r['Year']) for r in filtered_results if r['shortage']]
    if all_short:
        df_s = pd.concat(all_short).groupby(['Year', 'Node'])['Value'].sum().reset_index(); df_s['Value'] /= 1000
        st.plotly_chart(px.bar(df_s, x='Year', y='Value', color='Node').update_yaxes(rangemode="tozero"), use_container_width=True)
    else:
        st.success("✅ No shortages detected in this scenario.")

with tab_storage:
    st.subheader("Continuous Storage Inventory (TJ)")
    all_storage = []
    for yr_res in filtered_results:
        if yr_res.get('storage'):
            offset = (yr_res['Year'] - 2025) * 365
            df_s = pd.DataFrame(yr_res['storage'])
            df_s['GlobalDay'] = df_s['Day'] + offset
            all_storage.append(df_s)
    
    if all_storage:
        df_store_total = pd.concat(all_storage)
        fig_inv = px.line(df_store_total, x='GlobalDay', y='Inventory', color='Node', 
                          title="Inventory Levels Across Horizon",
                          labels={'GlobalDay': 'Days from 2025', 'Inventory': 'TJ in Storage'})
        fig_inv.update_layout(xaxis_rangeslider_visible=True)
        st.plotly_chart(fig_inv, use_container_width=True)
        
        if 'Injection' in df_store_total.columns and 'Withdrawal' in df_store_total.columns:
            st.subheader("Daily Injection (+) & Withdrawal (-) Activity (TJ/d)")
            # Show relative flows (Withdrawal as negative)
            df_store_total['RelFlow'] = df_store_total['Injection'] - df_store_total['Withdrawal']
            fig_flow_store = px.bar(df_store_total, x='GlobalDay', y='RelFlow', color='Node',
                                    title="Net Storage Activity",
                                    labels={'GlobalDay': 'Days from 2025', 'RelFlow': 'Net Injection/Withdrawal (TJ/d)'})
            st.plotly_chart(fig_flow_store, use_container_width=True)
        else:
            st.warning("⚠️ Injection/Withdrawal data not found in saved results. Please clear results and re-run scenario to see activity charts.")
    else:
        st.info("No storage data available. Ensure your nodes have 'StorageCapacity' and run a scenario.")

with tab_price:
    st.plotly_chart(px.line(summary_df, x='Year', y='Avg_Price', title="VWAP ($/GJ)").update_yaxes(rangemode="tozero"), use_container_width=True)
    
    # Filter nodal prices to only show those with supply > 0
    nodal_p = prices_df.groupby(['Year', 'Node'])['Price'].mean().reset_index()
    active_prod = all_prod.groupby(['Year', 'Node'])['Value'].sum().reset_index()
    # Merge and filter
    nodal_p_filtered = pd.merge(nodal_p, active_prod, on=['Year', 'Node'], how='left')
    nodal_p_filtered = nodal_p_filtered[nodal_p_filtered['Value'] > 0.1] # Only nodes supplying > 0.1 PJ/yr
    
    st.plotly_chart(px.line(nodal_p_filtered, x='Year', y='Price', color='Node', title="Nodal Prices (Supplying Nodes Only)").update_yaxes(rangemode="tozero"), use_container_width=True)

with tab_exp:
    st.subheader("Infrastructure Timeline")
    st.table(builds_df.sort_values('Year'))

with tab_ind:
    all_ind = pd.concat([pd.DataFrame(r['facility_demand']).assign(Year=r['Year']) for r in filtered_results if r['facility_demand']])
    st.plotly_chart(px.line(all_ind.groupby(['Year', 'Facility'])['Value'].sum().reset_index().assign(PJ=lambda x: x['Value']/1000), x='Year', y='PJ', color='Facility').update_yaxes(rangemode="tozero"), use_container_width=True)
