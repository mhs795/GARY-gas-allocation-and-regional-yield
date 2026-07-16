"""Export the pre-solved scenarios to one self-contained HTML file.

    python src/export_static.py            -> output/gary_static.html

Why this exists
---------------
``dashboard.py`` is a live Dash app: it re-solves the model on demand, which takes
~165s per scenario (a Pyomo MIP). That is the right tool for exploring, but it
can't be emailed, archived, or opened without starting a server.

This script takes the 28 scenarios already solved in
``src/data/precalculated_results.pkl`` and bakes their charts into a single HTML
file with the same lever buttons. No server, no network, nothing to install.

What carries over and what doesn't
----------------------------------
Kept: the baseline x winter x LNG grid (27 combinations) plus the Dunkelflaute
variant, and every chart the Dash app draws -- they are built by calling the app's
own callbacks, so the figures are identical.

Lost, unavoidably:
  * the discount-rate and MIP-gap sliders. Only the default values were
    pre-solved, and each new value needs a fresh ~165s MIP solve.
  * "Run scenario", "Run batch", "Regenerate data" -- all re-solve.
  * the dark theme. The figure builders bake the template in server-side, so
    shipping both themes would double an already large file.

Size
----
GARY's dispatch charts are daily over 2025-2050, so this is ~20-25 MB against
~5 MB for the DARIA and CGE dashboards. Float64 is downcast to float32 (chart
data does not need 15 significant figures), which roughly halves it. Use
``--no-dispatch`` to drop the daily dispatch traces and get well under 10 MB.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import warnings
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import plotly.io as pio  # noqa: E402
from plotly.io.json import to_json_plotly  # noqa: E402
from plotly.offline import get_plotlyjs  # noqa: E402

# Importing the dashboard module loads the results pickle and builds the Dash app.
# We never call app.run -- we only borrow its callbacks, which are plain functions.
import dashboard as D  # noqa: E402

# (label, callback, tab id, graph ids in output order)
CHART_GROUPS = [
    ("prod", D.update_prod, "tab-prod",
     ["prod-annual-graph", "prod-dispatch-graph", "flow-graph", "prod-extra-graph"]),
    ("storage", D.update_storage, "tab-storage",
     ["storage-inventory-graph", "storage-extra-graph"]),
    ("price", D.update_prices, "tab-price",
     ["price-high-graph", "price-low-graph"]),
    ("exp", D.update_expansions, "tab-exp", ["exp-graph"]),
    ("ind", D.update_industrial, "tab-ind", ["ind-graph"]),
]

TITLES = {
    "prod-annual-graph": ("Production, annual", "By source, 2025-2050"),
    "prod-dispatch-graph": ("Production, daily dispatch", "Selected year"),
    "flow-graph": ("Pipeline flows", "By arc"),
    "prod-extra-graph": ("Production, additional", ""),
    "storage-inventory-graph": ("Storage inventory", "Daily"),
    "storage-extra-graph": ("Storage, additional", ""),
    "price-high-graph": ("Prices, high-demand days", "Nodal, $/GJ"),
    "price-low-graph": ("Prices, low-demand days", "Nodal, $/GJ"),
    "exp-graph": ("Capacity expansions", "Builds chosen by the capacity model"),
    "ind-graph": ("Industrial demand", ""),
    "map-graph": ("Network map", "Flows and nodal prices"),
}

LEVERS = {
    "baseline": {
        "name": "Demand baseline",
        "default": "StepChange",
        "options": [("StepChange", "Step Change (central)"),
                    ("Accelerated", "Accelerated Transition"),
                    ("SlowerGrowth", "Slower Growth")],
    },
    "winter": {
        "name": "Winter demand",
        "default": "Medium",
        "options": [("Low", "Low"), ("Medium", "Medium"), ("High", "High")],
    },
    "lng": {
        "name": "LNG netback",
        "default": "Medium",
        "options": [("Low", "Low"), ("Medium", "Medium"), ("High", "High")],
    },
    "dunkel": {
        "name": "Dunkelflaute",
        "default": "off",
        "options": [("off", "off"), ("on", "on")],
    },
}

KEY_RE = re.compile(
    r"Base_(?P<baseline>\w+?)_ADGSM_(?P<adgsm>\w+)_Winter_(?P<winter>\w+?)_LNG_(?P<lng>\w+?)(?P<dunkel>_Dunkelflaute)?$"
)


def parse_key(key: str) -> dict | None:
    m = KEY_RE.match(key)
    if not m:
        return None
    g = m.groupdict()
    return {
        "baseline": g["baseline"],
        "winter": g["winter"],
        "lng": g["lng"],
        "dunkel": "on" if g["dunkel"] else "off",
    }


def lever_key(d: dict) -> str:
    return "|".join(f"{k}={d[k]}" for k in LEVERS)


_PACKED_DTYPES = {"f8": "<f8", "f4": "<f4", "i8": "<i8", "i4": "<i4", "i2": "<i2",
                  "u8": "<u8", "u4": "<u4", "u2": "<u2", "u1": "|u1", "i1": "|i1"}


def _unpack(v):
    """Decode a plotly-packed array to numpy, or None if it isn't one.

    ``to_plotly_json`` hands back arrays already binary-packed as
    ``{"dtype": "f8", "bdata": "<base64>"}``. Calling ``np.asarray`` on that dict
    yields a useless 0-d object array, which is why an earlier version silently
    did nothing.
    """
    if isinstance(v, dict) and "bdata" in v and v.get("dtype") in _PACKED_DTYPES:
        return np.frombuffer(base64.b64decode(v["bdata"]),
                             dtype=_PACKED_DTYPES[v["dtype"]])
    if isinstance(v, np.ndarray):
        return v
    if isinstance(v, (list, tuple)) and v and isinstance(v[0], (int, float)):
        return np.asarray(v)
    return None


def _pack(arr):
    arr = np.ascontiguousarray(arr)
    dt = {"<f4": "f4", "<f8": "f8"}.get(arr.dtype.str, arr.dtype.str.lstrip("<|"))
    return {"dtype": dt, "bdata": base64.b64encode(arr.tobytes()).decode("ascii")}


# Charts whose x-axis is daily over 2025-2050 (~9,490 points). At that density a
# static chart is a solid block of ink, so we thin them to roughly weekly. The
# live Dash app keeps full daily resolution.
_DAILY_CHARTS = {"prod-dispatch-graph", "flow-graph", "storage-inventory-graph"}


def _shrink(fig_json: dict, gid: str, stride: int = 7) -> dict:
    """Downcast floats to float32, and thin daily charts to ~weekly.

    f8 -> f4 is safe: these are chart coordinates, not accounting. float32 keeps
    ~7 significant figures and the charts render to three at best. Halves the
    payload on its own; the daily thinning does the rest.
    """
    thin = gid in _DAILY_CHARTS and stride > 1
    for tr in fig_json.get("data", []):
        # scattergl renders via WebGL, which is fragile in headless renderers and
        # some locked-down browsers. After weekly thinning the point counts are
        # low enough for plain SVG scatter, which renders anywhere -- the whole
        # point of a self-contained file to share.
        if tr.get("type") == "scattergl":
            tr["type"] = "scatter"
        # A trace's axes must be sliced together, so decode them all first.
        cols = {ax: _unpack(tr.get(ax)) for ax in
                ("x", "y", "z", "customdata", "hovertext", "text")}
        n = max((len(a) for a in cols.values() if a is not None), default=0)
        for ax, arr in cols.items():
            if arr is None:
                continue
            if thin and len(arr) == n and n > 2 * stride:
                arr = arr[::stride]
            if isinstance(arr, np.ndarray) and arr.dtype == np.float64:
                arr = arr.astype(np.float32)
            tr[ax] = _pack(arr) if arr.dtype.kind in "fiu" else arr.tolist()
    return fig_json


def build_payload(results: dict, end_year: int, map_year: int,
                  drop_dispatch: bool, stride: int = 7, verbose: bool = True) -> tuple[dict, list]:
    scenarios = results["all_scenarios"]
    payload: dict[str, dict] = {}
    graph_ids: list[str] = []

    for n, key in enumerate(sorted(scenarios), 1):
        parsed = parse_key(key)
        if parsed is None:
            if verbose:
                print(f"  skipping unparsable key: {key}")
            continue
        figs: dict[str, dict] = {}
        for label, fn, tab, ids in CHART_GROUPS:
            try:
                out = fn(key, end_year, tab, "light")
            except Exception as e:
                if verbose:
                    print(f"  {key}: {label} failed: {str(e)[:60]}")
                continue
            items = out if isinstance(out, (tuple, list)) else [out]
            for gid, o in zip(ids, items):
                if not hasattr(o, "to_plotly_json"):
                    continue
                j = o.to_plotly_json()
                if not j.get("data"):
                    continue
                if drop_dispatch and gid == "prod-dispatch-graph":
                    continue
                figs[gid] = _shrink(j, gid, stride)
        # The map is built by its own inner function.
        try:
            m = D._update_map_inner(key, end_year, map_year, [], False)
            items = m if isinstance(m, (tuple, list)) else [m]
            for o in items:
                if hasattr(o, "to_plotly_json"):
                    j = o.to_plotly_json()
                    if j.get("data"):
                        figs["map-graph"] = _shrink(j, "map-graph")
                    break
        except Exception as e:
            if verbose:
                print(f"  {key}: map failed: {str(e)[:60]}")

        payload[lever_key(parsed)] = figs
        for gid in figs:
            if gid not in graph_ids:
                graph_ids.append(gid)
        if verbose:
            print(f"  [{n:2d}/{len(scenarios)}] {key}  ({len(figs)} charts)")

    return payload, graph_ids


_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&display=swap');
:root{--md-bg:#F0F4F8;--md-surface:#FFFFFF;--md-primary:#1976D2;
 --md-text:rgba(0,0,0,0.87);--md-text-med:rgba(0,0,0,0.60);--md-divider:rgba(0,0,0,0.10);
 --md-e1:0 1px 3px rgba(0,0,0,0.10),0 1px 2px rgba(0,0,0,0.07);
 --md-e2:0 3px 6px rgba(0,0,0,0.10),0 2px 4px rgba(0,0,0,0.06);}
*{box-sizing:border-box;}
body{margin:0;background:var(--md-bg);font-family:'Roboto',sans-serif;color:var(--md-text);}
.appbar{background:var(--md-primary);color:#fff;padding:16px 28px;box-shadow:var(--md-e2);position:sticky;top:0;z-index:10;}
.appbar h1{margin:0;font-size:20px;font-weight:500;letter-spacing:0.2px;}
.appbar .sub{font-size:13px;opacity:0.85;margin-top:2px;font-weight:300;}
.wrap{max-width:1360px;margin:0 auto;padding:24px 28px 48px;}
.levers{background:var(--md-surface);border-radius:10px;padding:16px 18px 12px;box-shadow:var(--md-e1);margin-bottom:22px;}
.lever-row{display:flex;align-items:center;gap:12px;margin-bottom:10px;flex-wrap:wrap;}
.lever-row:last-child{margin-bottom:0;}
.lever-name{font-size:12px;text-transform:uppercase;letter-spacing:0.6px;color:var(--md-text-med);font-weight:500;min-width:170px;}
.lever-btn{background:#fff;border:1px solid var(--md-divider);border-radius:6px;padding:6px 14px;font-size:13px;
 font-family:'Roboto',sans-serif;cursor:pointer;color:var(--md-text);transition:all .12s;}
.lever-btn:hover{background:rgba(25,118,210,0.06);}
.lever-btn.on{background:var(--md-primary);color:#fff;border-color:var(--md-primary);font-weight:500;box-shadow:var(--md-e1);}
.lever-btn:disabled{opacity:0.35;cursor:not-allowed;}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:20px;}
.card{background:var(--md-surface);border-radius:10px;padding:14px 16px 8px;box-shadow:var(--md-e1);min-width:0;}
.card.wide{grid-column:1/-1;}
.card-title{font-size:15px;font-weight:500;line-height:1.25;}
.card-sub{font-size:12px;font-weight:300;color:var(--md-text-med);margin-top:1px;margin-bottom:4px;}
.banner{background:#FFF8E1;border-left:4px solid #F9A825;border-radius:8px;padding:12px 16px;margin-bottom:22px;
 font-size:13px;line-height:1.6;font-weight:300;}
.banner b{font-weight:500;}
.warn{display:none;background:#FFEBEE;border-left:4px solid #E53935;border-radius:8px;padding:10px 14px;
 margin-bottom:16px;font-size:13px;}
.note{margin-top:24px;font-size:12px;color:var(--md-text-med);line-height:1.6;font-weight:300;}
@media(max-width:900px){.grid{grid-template-columns:1fr;}}
"""

_JS = """
const S = __PAYLOAD__, LEVERS = __LEVERS__, GRAPHS = __GRAPHS__;
const CFG = {displayModeBar:false, responsive:true};
const state = {};
Object.keys(LEVERS).forEach(k => state[k] = LEVERS[k].default);
const key = () => Object.keys(LEVERS).map(k => k + '=' + state[k]).join('|');

function render(){
  const s = S[key()];
  const warn = document.getElementById('miss');
  if(!s){ warn.style.display='block'; return; }
  warn.style.display='none';
  GRAPHS.forEach(g => {
    const el = document.getElementById(g);
    if(!el) return;
    const f = s[g];
    const card = el.closest('.card');
    if(!f){ if(card) card.style.display='none'; return; }
    if(card) card.style.display='';
    Plotly.react(g, f.data, f.layout, CFG);
  });
}
function refreshButtons(){
  Object.keys(LEVERS).forEach(lv => {
    document.querySelectorAll('.lever-btn[data-lever="'+lv+'"]').forEach(b => {
      b.classList.toggle('on', b.getAttribute('data-val') === state[lv]);
      // grey out combinations that were never solved
      const probe = Object.assign({}, state); probe[lv] = b.getAttribute('data-val');
      const k = Object.keys(LEVERS).map(x => x + '=' + probe[x]).join('|');
      b.disabled = !S[k];
    });
  });
}
document.querySelectorAll('.lever-btn').forEach(b => {
  b.addEventListener('click', () => {
    if(b.disabled) return;
    state[b.getAttribute('data-lever')] = b.getAttribute('data-val');
    refreshButtons(); render();
  });
});
refreshButtons(); render();
"""


def build_html(payload: dict, graph_ids: list, end_year: int, map_year: int,
               out_path: Path) -> Path:
    lever_html = []
    for lv, spec in LEVERS.items():
        btns = "".join(
            f'<button class="lever-btn" data-lever="{lv}" data-val="{v}">{lab}</button>'
            for v, lab in spec["options"]
        )
        lever_html.append(
            f'<div class="lever-row"><div class="lever-name">{spec["name"]}</div>{btns}</div>'
        )

    cards = []
    for gid in graph_ids:
        title, sub = TITLES.get(gid, (gid, ""))
        wide = gid in ("prod-dispatch-graph", "map-graph", "storage-inventory-graph")
        cards.append(
            f'<div class="card{" wide" if wide else ""}">'
            f'<div class="card-title">{title}</div>'
            f'<div class="card-sub">{sub}</div><div id="{gid}"></div></div>'
        )

    banner = (
        "<b>Static export — charts only, no solver.</b> These are the "
        f"{len(payload)} scenarios already solved and stored in "
        "<code>precalculated_results.pkl</code>, baked into one file. The live Dash "
        "app (<code>run_dashboard.sh</code>) re-solves on demand — each scenario is a "
        "~165s MIP — so the <b>discount rate</b> and <b>MIP gap</b> sliders, the "
        "<b>Run scenario</b> / <b>Run batch</b> buttons and <b>Regenerate data</b> are "
        "not available here: they all need a fresh solve. Greyed-out lever "
        "combinations were never solved. Use the Dash app to explore; use this to share."
    )
    stamp = time.strftime("%d %b %Y %H:%M")
    js = (_JS.replace("__PAYLOAD__", to_json_plotly(payload))
             .replace("__LEVERS__", json.dumps({k: {"default": v["default"]} for k, v in LEVERS.items()}))
             .replace("__GRAPHS__", json.dumps(graph_ids)))

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GARY — Gas market model (static export)</title>
<style>{_CSS}</style>
<script>{get_plotlyjs()}</script>
</head><body>
<div class="appbar">
  <h1>GARY — Gas Allocation and Regional Yield</h1>
  <div class="sub">Eastern Australian gas market model · static export of {len(payload)} pre-solved scenarios
    · horizon to {end_year}, map year {map_year} · built {stamp}</div>
</div>
<div class="wrap">
  <div class="banner">{banner}</div>
  <div class="levers">{''.join(lever_html)}</div>
  <div class="warn" id="miss"><b>This combination was not pre-solved.</b>
     Run it in the Dash app to add it.</div>
  <div class="grid">{''.join(cards)}</div>
  <div class="note">
    Generated from <code>src/data/precalculated_results.pkl</code> by
    <code>src/export_static.py</code>. Figures are built by the Dash app's own
    callbacks, so they match the live dashboard exactly. Float64 chart data is
    downcast to float32 to halve the file size.
  </div>
</div>
<script>{js}</script>
</body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="output/gary_static.html")
    ap.add_argument("--end-year", type=int, default=2050)
    ap.add_argument("--map-year", type=int, default=2050)
    ap.add_argument("--no-dispatch", action="store_true",
                    help="drop the daily dispatch traces entirely")
    ap.add_argument("--stride", type=int, default=7,
                    help="thin daily charts by this factor (7 = weekly; 1 = keep daily)")
    args = ap.parse_args()

    t0 = time.time()
    results = D.load_results()
    if not results.get("all_scenarios"):
        print("no pre-solved scenarios found in precalculated_results.pkl")
        return 1
    print(f"building charts for {len(results['all_scenarios'])} pre-solved scenarios ...")
    payload, graph_ids = build_payload(results, args.end_year, args.map_year,
                                       args.no_dispatch, stride=args.stride)
    out = build_html(payload, graph_ids, args.end_year, args.map_year, Path(args.out))
    mb = out.stat().st_size / 1e6
    print(f"\n{len(payload)} scenarios, {len(graph_ids)} charts each")
    print(f"{out.resolve()}  ({mb:.1f} MB, {time.time()-t0:.0f}s)")
    if mb > 40:
        print("  tip: --no-dispatch drops the daily traces and shrinks this a lot")
    return 0


if __name__ == "__main__":
    sys.exit(main())
