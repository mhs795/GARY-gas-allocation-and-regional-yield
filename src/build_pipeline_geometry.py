"""Build realistic pipeline geometry for the network map from OpenStreetMap.

Fetches gas-transmission pipeline geometry (`man_made=pipeline`, `substance=gas`)
from the Overpass API, stitches the multi-segment OSM ways for each model arc into
a single ordered polyline (oriented From-node -> To-node), simplifies it, and writes
`data/pipeline_geometry.json` as {arc_name: [[lat, lon], ...]}.

The dashboard (dashboard.py) loads this file and use the real geometry where it
exists, falling back to the hand-traced ARC_WAYPOINTS for arcs OSM doesn't cover
(e.g. RBP, QGP, VNI, SWP, VGP, PK2SYD).

Run standalone (`python src/build_pipeline_geometry.py`) or via regenerate_data.
Data (c) OpenStreetMap contributors, ODbL.
"""
import os
import json
import time
import math
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
OUT_FILE = os.path.join(DATA_DIR, "pipeline_geometry.json")

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# Node coordinates (must match COORDS in the GUIs) — used to orient/snap routes.
COORDS = {
    'Surat': [-27.15, 149.07], 'Moomba': [-28.1, 140.2], 'Gippsland': [-38.5, 147.0],
    'Sydney': [-33.86, 151.2], 'Melbourne': [-37.81, 144.96], 'Adelaide': [-34.92, 138.6],
    'Brisbane': [-27.47, 153.02], 'Gladstone': [-23.84, 151.26], 'APLNG': [-23.76, 151.20],
    'GLNG': [-23.80, 151.25], 'QCLNG': [-23.84, 151.30], 'Port_Kembla': [-34.45, 150.9],
    'Iona': [-38.55, 142.9], 'Silver_Springs': [-27.4, 149.2],
    'Geelong': [-38.10, 144.42],
}

# arc -> (From, To, OSM matcher). Matcher takes an OSM way's tags dict -> bool.
# Trunk lines are matched by OSM `name`; the LNG export lines by `operator` (they
# are unnamed in OSM but tagged with the operator + usage=transmission).
ARC_MATCH = {
    'MSP':        ('Moomba', 'Sydney',    lambda t: t.get('name') == 'Moomba Sydney Gas Pipeline'),
    'MAPS':       ('Moomba', 'Adelaide',  lambda t: t.get('name') == 'Moomba to Adelaide Pipeline System'),
    'SWQP':       ('Moomba', 'Surat',     lambda t: t.get('name') == 'South West Queensland Pipeline'),
    'EGP':        ('Gippsland', 'Sydney', lambda t: t.get('name', '').startswith('Eastern Gas Pipeline')),
    'SEA_Gas':    ('Melbourne', 'Adelaide', lambda t: t.get('name') == 'SEA Gas Pipeline'),
    'Longford':   ('Gippsland', 'Melbourne', lambda t: t.get('name') in ('Longford - Dandenong', 'Morwell - Dandenong')),
    'APLNG_Pipe': ('Surat', 'APLNG',      lambda t: t.get('operator') == 'APLNG' and t.get('usage') == 'transmission'),
    'GLNG_Pipe':  ('Surat', 'GLNG',       lambda t: t.get('operator') == 'Santos' and t.get('usage') == 'transmission'),
    'WGP_Pipe':   ('Surat', 'QCLNG',      lambda t: t.get('operator') == 'APA' and t.get('usage') == 'transmission'),
}

# Max gap (deg) between a stitched route end and its hub node. Model nodes are coarse
# hubs, so a legitimate route can start a couple of degrees away (e.g. LNG lines begin
# at the gas fields, SEA Gas at Port Campbell) — that draws a short, acceptable leader.
# A much larger gap means OSM is missing a whole section (the EGP coastal trunk is
# unmapped south of Wollongong, ~5 deg), so we reject it and keep the hand-traced route.
MAX_SNAP_DEG = 4.0

REGION = (-44, 135, -22, 154)          # eastern Australia (S, W, N, E)
LNG_BBOX = (-25.5, 148.3, -23.3, 151.6)  # Surat Basin -> Gladstone corridor

OVERPASS_QL = f"""[out:json][timeout:150];
(
  way["man_made"="pipeline"]["substance"="gas"]["name"]({REGION[0]},{REGION[1]},{REGION[2]},{REGION[3]});
  way["man_made"="pipeline"]["substance"="gas"]["operator"]["usage"="transmission"]({LNG_BBOX[0]},{LNG_BBOX[1]},{LNG_BBOX[2]},{LNG_BBOX[3]});
);
out geom;"""


def _dist(a, b):
    """Rough planar distance between [lat, lon] points (deg; fine at this scale)."""
    return math.hypot(a[0] - b[0], a[1] - b[1])


def fetch_overpass():
    body = urllib.parse.urlencode({"data": OVERPASS_QL}).encode()
    last = None
    for endpoint in OVERPASS_ENDPOINTS:
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    endpoint, data=body,
                    headers={"User-Agent": "GARY-gas-model/1.0 (pipeline geometry import)",
                             "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=180) as r:
                    raw = r.read().decode("utf-8", "replace")
                if raw.lstrip().startswith("{"):
                    print(f"  fetched from {endpoint} (attempt {attempt + 1})")
                    return json.loads(raw)
                last = raw[:200]
                print(f"  {endpoint} busy, retrying...")
                time.sleep(8)
            except Exception as e:  # noqa: BLE001 - network best-effort
                last = str(e)
                print(f"  {endpoint} error: {e}")
                time.sleep(5)
    raise RuntimeError(f"Overpass unavailable. Last response: {last}")


def stitch(ways, start, end):
    """Chain OSM ways (each a list of [lat, lon]) into one polyline start->end.

    Greedy nearest-endpoint stitching: begin with the way nearest `start`, then keep
    appending the unused way whose nearest endpoint touches the current tail (within
    a tolerance), flipping ways as needed. Leftover ways (laterals) are dropped.
    """
    ways = [w for w in ways if len(w) >= 2]
    if not ways:
        return None
    TOL = 0.35  # deg (~35 km) max gap to join consecutive segments

    # pick the starting way + orientation closest to the From node
    def end_dist(w):
        return min(_dist(w[0], start), _dist(w[-1], start))
    first = min(ways, key=end_dist)
    ways.remove(first)
    chain = first if _dist(first[0], start) <= _dist(first[-1], start) else first[::-1]

    changed = True
    while changed and ways:
        changed = False
        tail = chain[-1]
        best, best_d, flip = None, TOL, False
        for w in ways:
            d0, d1 = _dist(w[0], tail), _dist(w[-1], tail)
            if d0 < best_d:
                best, best_d, flip = w, d0, False
            if d1 < best_d:
                best, best_d, flip = w, d1, True
        if best is not None:
            seg = best[::-1] if flip else best
            chain += seg[1:] if _dist(seg[0], tail) < 1e-6 else seg
            ways.remove(best)
            changed = True

    # orient the finished chain so it runs From -> To
    if _dist(chain[0], end) < _dist(chain[-1], end):
        chain = chain[::-1]
    return chain


def rdp(points, eps=0.008):
    """Ramer-Douglas-Peucker simplification (eps in degrees, ~0.8 km)."""
    if len(points) < 3:
        return points
    a, b = points[0], points[-1]
    dmax, idx = 0.0, 0
    for i in range(1, len(points) - 1):
        d = _perp(points[i], a, b)
        if d > dmax:
            dmax, idx = d, i
    if dmax > eps:
        left = rdp(points[:idx + 1], eps)
        right = rdp(points[idx:], eps)
        return left[:-1] + right
    return [a, b]


def _perp(p, a, b):
    if a == b:
        return _dist(p, a)
    num = abs((b[0] - a[0]) * (a[1] - p[1]) - (a[0] - p[0]) * (b[1] - a[1]))
    return num / math.hypot(b[0] - a[0], b[1] - a[1])


def build():
    print("Fetching pipeline geometry from OpenStreetMap (Overpass)...")
    data = fetch_overpass()
    ways = [e for e in data.get("elements", []) if e.get("type") == "way" and e.get("geometry")]
    print(f"  {len(ways)} gas pipeline ways returned")

    geom = {}
    for arc, (frm, to, match) in ARC_MATCH.items():
        matched = [[[g["lat"], g["lon"]] for g in w["geometry"]]
                   for w in ways if match(w.get("tags", {}))]
        if not matched:
            print(f"  {arc:12s} -> no OSM match (keeping hand-traced fallback)")
            continue
        route = stitch(matched, COORDS[frm], COORDS[to])
        if not route or len(route) < 2:
            print(f"  {arc:12s} -> stitch failed (keeping fallback)")
            continue
        # Guard against incompletely-mapped routes: if a stitched end is far from its
        # node, OSM is missing that stretch and snapping would draw a long straight
        # leader (e.g. the EGP coastal trunk is unmapped south of Wollongong). Reject
        # and fall back to the hand-traced route.
        gap = max(_dist(route[0], COORDS[frm]), _dist(route[-1], COORDS[to]))
        if gap > MAX_SNAP_DEG:
            print(f"  {arc:12s} -> OSM route incomplete (gap {gap:.1f} deg > {MAX_SNAP_DEG}); keeping fallback")
            continue
        # snap the ends to the model node markers so lines meet the dots
        route[0], route[-1] = COORDS[frm], COORDS[to]
        route = rdp(route)
        route = [[round(la, 4), round(lo, 4)] for la, lo in route]
        geom[arc] = route
        print(f"  {arc:12s} <- {len(matched)} way(s) -> {len(route)} points")

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT_FILE, "w") as f:
        json.dump(geom, f, separators=(",", ":"))
    print(f"Wrote {len(geom)} routes to {OUT_FILE}")
    return geom


if __name__ == "__main__":
    build()
