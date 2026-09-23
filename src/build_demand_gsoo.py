"""
Re-base the node distribution demand on an AEMO 2026 GSOO baseline scenario. The
chosen baseline (Step Change / Accelerated Transition / Slower Growth) becomes the
model's central case; the scenario levers (Winter, LNG, gas reservation in solve.py /
solve.py) then layer multiplicatively on top of it.

Replaces the arbitrary per-node growth rates in generate_data_2050.py with
empirically-grounded GSOO trajectories:
  * City nodes (Sydney, Melbourne, Adelaide, Brisbane) -- city-gate distribution
    demand, dominantly residential & small commercial (Tariff V) -- follow the
    GSOO ResComm trajectory (Figure 17): a steep electrification-driven decline.
  * LNG nodes (APLNG, GLNG, QCLNG) -- follow the GSOO LNG trajectory (Figure 19),
    replacing the previous flat assumption.

The 2026 base level is the empirical daily shape (demand_profiles.csv + LNG
calibration), and GSOO indices are applied relative to 2026 (clamped to the
2026-2045 GSOO horizon; 2045 held flat to 2050). Deterministic (no random noise).

Output: data/demand_<scenario>.csv  (Year, Day, Node, Demand TJ/day).
For the StepChange baseline the legacy filename data/demand_2050.csv is also
written, so existing tooling keeps working.
"""
import os
import numpy as np
import pandas as pd

import params as P

BASE = os.path.dirname(__file__)
DATA = os.path.join(BASE, "data")
GSOO = os.path.join(DATA, "gsoo")

_NODES = pd.read_csv(os.path.join(DATA, "nodes.csv"))
# The distribution-delivery nodes, from the CityGate flag on nodes.csv -- a node
# attribute belongs with the node, not in a set in a module.
CITY_NODES = set(_NODES.loc[_NODES["CityGate"] == 1, "Name"])
YEARS = np.arange(P.get_int('horizon_start'), P.get_int('horizon_end') + 1)

# NT (Darwin) city-gate commercial / light-industrial load only — small, and held
# flat (AER: "local demand is not expected to change significantly"). Sized from the
# AER AGP access arrangement review (AAR 2026-31, Table 2-1, 2024-25): the Darwin
# distribution system (0.3) plus Townend Road (0.2) plus Elliot (0.1). The bulk of NT
# gas demand is power generation, modelled separately as a curtailable GPG tier at
# the Darwin and Amadeus nodes (see build_gpg_demand_gsoo.py / gpg_facilities.csv).
NT_DARWIN_COMMERCIAL_TJD = P.get('nt_darwin_commercial_tjd')

# Baseline scenarios -> output filename slug. Mirrors build_gsoo_scenarios.SCENARIOS.
SCENARIOS = P.get_list('gsoo_baselines')


def _gsoo_index(annual, scenario, sector, lo=None, hi=None, base=None):
    """Year -> level relative to the base year, for a GSOO sector/scenario, clamped."""
    lo = P.get_int('gsoo_index_base_year') if lo is None else lo
    hi = P.get_int('gsoo_index_last_year') if hi is None else hi
    base = P.get_int('gsoo_index_base_year') if base is None else base
    s = annual[(annual.Scenario == scenario) & (annual.Sector == sector)
               ].set_index("Year")["PJ_per_year"].to_dict()
    base_val = s[base]
    def idx(year):
        y = min(max(int(year), lo), hi)
        return s[y] / base_val
    return idx


def _gsoo_level(annual, scenario, sector, year=None):
    """A GSOO sector's absolute level in ``year``, TJ/day."""
    year = P.get_int('gsoo_index_base_year') if year is None else year
    row = annual[(annual.Scenario == scenario) & (annual.Sector == sector)
                 & (annual.Year == year)]
    return float(row["PJ_per_year"].iloc[0]) * 1000.0 / 365.0


PEAK_MATCHING = P.get_bool("peak_shape_matching")
PEAK_K_MIN = P.get('peak_shape_k_min')
PEAK_K_MAX = P.get('peak_shape_k_max')


def _rescale_peak(shape, target_ratio):
    """Rescale a mean-1 daily shape so its peak-to-mean hits ``target_ratio``.

    ``out = 1 + (shape - 1) * k`` leaves the mean at exactly 1 whatever k is,
    because the deviations sum to zero -- so ANNUAL ENERGY IS UNTOUCHED and only
    the shape moves. k is chosen to put the maximum on target and then clipped,
    so a region GARY under-covers on energy cannot be forced into an absurd
    profile; the residual shows up in the diagnostic instead of being hidden.
    """
    m = float(shape.max())
    if m <= 1.0 or target_ratio <= 1.0:
        return shape
    k = min(max((target_ratio - 1.0) / (m - 1.0), PEAK_K_MIN), PEAK_K_MAX)
    out = np.maximum(1.0 + (shape - 1.0) * k, 0.0)
    return out / out.mean()          # renormalise in case the floor bit


def _regional_winter_peaks(scenario):
    """{(region, year): RC&I winter peak TJ/d} from the GSOO daily-max extract."""
    rp = pd.read_csv(os.path.join(GSOO, "regional_peak.csv"))
    rp = rp[(rp.Scenario == scenario)
            & (rp.Season.astype(str).str.lower().str.contains("winter"))]
    return {(r.Region, int(r.Year)): float(r.RCI_TJd) for r in rp.itertuples()}


def to_365(trace):
    """Fold a 366-day empirical trace onto the 365-day year the model solves.

    THE MODEL DISPATCHES ``RangeSet(1, 365)``. The empirical shape in
    demand_profiles.csv comes off a GBB sample that spans a leap year, so it
    carries a day 366 -- and every node except Darwin, which is built with an
    explicit ``range(1, 366)``, inherited it. The extra day was written into
    demand_<scenario>.csv, never dispatched, and silently discarded: 114.4 PJ
    across the horizon, 0.31% of all demand, of which 103 PJ was LNG.

    Dropping the day outright would lose that energy. Instead each node's days
    1-365 are scaled by ``annual / sum(days 1-365)``, so the ANNUAL TOTAL is
    preserved and only the daily level moves, by about a quarter of a percent.
    That is the right way round: every sector here is calibrated to a GSOO annual
    level, and the shape is empirical, so the total is the thing to hold fixed.
    """
    if int(trace["Day"].max()) <= 365:
        return trace
    out = []
    for node, grp in trace.groupby("Node", sort=False):
        total = float(grp["Demand"].sum())
        keep = grp[grp["Day"] <= 365].copy()
        kept = float(keep["Demand"].sum())
        if kept > 0:
            keep["Demand"] = keep["Demand"] * (total / kept)
        out.append(keep)
    return pd.concat(out, ignore_index=True)


def build(scenario="StepChange"):
    annual = pd.read_csv(os.path.join(GSOO, "annual_sector.csv"))
    base_trace = to_365(pd.read_csv(os.path.join(DATA, "demand_profiles.csv")))
    daily_trace = base_trace.groupby(["Day", "Node"])["Demand"].mean().reset_index()

    # One source for the train split: the Parameters sheet, which the model also
    # reads for the liquefaction nameplate split. These used to be duplicated in
    # lng_parameters.csv, so the two could -- and did -- drift apart.
    # get_pairs returns the value side as text (a node name and a price share sit
    # in the same untyped column), so cast here the way model.py does.
    lng_nodes = {n: float(v) for n, v in
                 P.get_pairs('lng_train_shares')}
    aplng_trace = daily_trace[daily_trace["Node"] == "APLNG"]
    # ANCHOR THE TRAINS TO THE GSOO, NOT TO NAMEPLATE. This used to scale the
    # Curtis Island trace so the 2026 base equalled lng_daily_target (3,680 TJ/d
    # = physical liquefaction capacity). Every other sector is calibrated to a
    # GSOO level, so LNG was the one series pinned to a plant rating instead --
    # and it started 2% below the GSOO's own 2026 LNG figure and stayed there.
    # Where the GSOO's volume exceeds nameplate the model handles it correctly:
    # under netback pricing foundation + spot is bounded by nameplate and the
    # residual is reported as Forgone, rather than the shortfall being invisible.
    lng_base_tjd = _gsoo_level(annual, scenario, "LNG")
    scaling_factor = lng_base_tjd / aplng_trace["Demand"].mean()

    rescomm_idx = _gsoo_index(annual, scenario, "ResComm")
    industrial_idx = _gsoo_index(annual, scenario, "Industrial")
    lng_idx = _gsoo_index(annual, scenario, "LNG")

    # --- What the city-gate bucket actually contains -------------------------
    # A city node's demand is DISTRIBUTION DELIVERY (confirmed per node in
    # demand_decomposition_validation.csv), so it is not pure residential and
    # commercial load: a large amount of commercial and small-industrial gas
    # rides the distribution network, and the GSOO counts that in Industrial.
    #
    # This used to be indexed entirely on the ResComm trajectory, which falls to
    # 0.21 of its 2026 level by 2045 against Industrial's 0.76. Sending ~240 TJ/d
    # of slow-declining load down the steep residential curve made GARY's domestic
    # demand 66% of the GSOO's by 2045 instead of matching it -- and a market that
    # short of load never needs an import cargo, which is why GARY priced the whole
    # east coast at the export netback. See TODO.md item 9.
    #
    # So: split the bucket, and index each half on its own sector.
    base_rc = _gsoo_level(annual, scenario, "ResComm")
    base_ind = _gsoo_level(annual, scenario, "Industrial")
    # Whatever GARY already meters separately as industrial is NOT embedded here.
    try:
        _ind = pd.read_csv(os.path.join(DATA, f"industrial_demand_profile_{scenario}.csv"))
        metered_ind = float(_ind[_ind.Year == 2026]["Demand"].sum()) / 365.0
    except (FileNotFoundError, KeyError, IndexError):
        metered_ind = 0.0
    embedded_ind = max(0.0, base_ind - metered_ind)
    citygate_target = base_rc + embedded_ind
    observed = float(daily_trace[daily_trace["Node"].isin(CITY_NODES)]
                     .groupby("Node")["Demand"].mean().sum())
    # CALIBRATION. The observed GBB city-gate trace sees 696 TJ/d against a target
    # of ~848: the Bulletin Board does not register distribution-connected users,
    # regional networks outside the four city nodes, or Tasmania at all. Scaling
    # the trace closes that coverage gap by putting the unobserved load on the
    # nodes GARY does have. It is a real simplification -- regional load ends up in
    # the capitals -- and it breaks the node-level agreement with
    # demand_decomposition_validation.csv, which was a check on the RAW trace.
    calib = citygate_target / observed if observed > 0 else 1.0
    rc_share = base_rc / citygate_target if citygate_target > 0 else 1.0
    print(f"  city-gate: observed {observed:.0f} -> target {citygate_target:.0f} TJ/d "
          f"(x{calib:.3f}); {rc_share:.1%} ResComm / {1 - rc_share:.1%} embedded industrial "
          f"(metered industrial {metered_ind:.0f} TJ/d held out)")

    # --- inputs the per-year shape rescale needs -----------------------------
    _nodes = pd.read_csv(os.path.join(DATA, "nodes.csv"))
    node_region = dict(zip(_nodes["Name"], _nodes["Region"]))
    regional_peak = _regional_winter_peaks(scenario) if PEAK_MATCHING else {}
    idx_lo = P.get_int('gsoo_index_base_year')
    idx_hi = P.get_int('gsoo_index_last_year')
    # Mean-1 daily shape and base mean level for each city-gate node.
    city_shapes, node_base_mean = {}, {}
    for node in sorted(CITY_NODES):
        t = (daily_trace[daily_trace["Node"] == node]
             .sort_values("Day")["Demand"].to_numpy(dtype=float))
        node_base_mean[node] = float(t.mean())
        city_shapes[node] = t / t.mean() if t.mean() > 0 else t
    # Metered industrial per region and year, held out of the regional RC&I peak
    # before it becomes a city-gate target. Its MEAN, not its peak: the industrial
    # trace is near-flat baseload, so what it contributes on the region's peak day
    # is its ordinary level. Subtracting its own maximum instead understated the
    # city-gate target enough to invert it in NSW.
    ind_region_peak = {}
    try:
        _ip = pd.read_csv(os.path.join(DATA, f"industrial_demand_profile_{scenario}.csv"))
        _ip["Region"] = _ip["Node"].map(node_region)
        for (reg, yr), g in _ip.groupby(["Region", "Year"]):
            ind_region_peak[(reg, int(yr))] = float(g.groupby("Day")["Demand"].sum().mean())
    except FileNotFoundError:
        pass
    peak_report = {}

    rows = []
    for year in YEARS:
        ci = rescomm_idx(year)
        ii = industrial_idx(year)
        li = lng_idx(year)

        # LNG nodes: shared Curtis Island shape, split, scaled to GSOO LNG trajectory.
        for _, r in aplng_trace.iterrows():
            for node, split in lng_nodes.items():
                val = r["Demand"] * scaling_factor * split * li
                rows.append({"Year": int(year), "Day": int(r["Day"]), "Node": node,
                             "Demand": round(max(0.0, val), 4)})

        # Other (city) nodes: calibrated distribution delivery, split between the
        # GSOO's ResComm and Industrial trajectories in the shares fixed above.
        # ANNUAL ENERGY comes from that blend; the DAILY SHAPE is then rescaled to
        # the GSOO's own regional RC&I winter peak for the year. Both are needed:
        # the blend alone matched the GSOO's energy to ~1% while leaving the 2026
        # daily shape frozen for 26 years, so as the load behind it shifted from
        # heating to industrial the peak drifted badly -- VIC 22% over AEMO in 2026
        # and 43% over by 2045, QLD 20-30% under throughout.
        blended = rc_share * ci + (1.0 - rc_share) * ii
        for node, trace in city_shapes.items():
            mean_tjd = node_base_mean[node] * calib * blended
            shape = trace
            if PEAK_MATCHING:
                region = node_region.get(node)
                yr_c = min(max(int(year), idx_lo), idx_hi)
                rci = regional_peak.get((region, yr_c))
                if rci and mean_tjd > 0:
                    # The region's RC&I peak covers load GARY meters separately as
                    # industrial at other nodes, so hold that out before targeting.
                    target = max(0.0, rci - ind_region_peak.get((region, yr_c), 0.0))
                    shape = _rescale_peak(trace, target / mean_tjd)
                    peak_report.setdefault(region, {})[int(year)] = (
                        target, float(shape.max()) * mean_tjd)
            for day, v in enumerate(shape * mean_tjd, start=1):
                rows.append({"Year": int(year), "Day": day, "Node": node,
                             "Demand": round(max(0.0, float(v)), 4)})
        # Non-city nodes carry their trace unchanged.
        for _, r in daily_trace[(~daily_trace["Node"].isin(CITY_NODES))
                                & (daily_trace["Node"] != "APLNG")].iterrows():
            rows.append({"Year": int(year), "Day": int(r["Day"]), "Node": r["Node"],
                         "Demand": round(max(0.0, r["Demand"]), 4)})

        # NT (Darwin) — small city-gate commercial/light-industrial load, held flat.
        # Power generation is modelled separately as a GPG tier (build_gpg_demand_gsoo).
        for day in range(1, 366):
            rows.append({"Year": int(year), "Day": day, "Node": "Darwin",
                         "Demand": round(NT_DARWIN_COMMERCIAL_TJD, 4)})

    if peak_report:
        print("  winter peak day, GARY city-gate vs GSOO regional RC&I target (TJ/d):")
        for region in sorted(peak_report):
            yrs = [y for y in (2026, 2035, 2045) if y in peak_report[region]]
            cells = "  ".join(
                f"{y}: {peak_report[region][y][1]:.0f}/{peak_report[region][y][0]:.0f}"
                f" ({peak_report[region][y][1] / peak_report[region][y][0]:.2f})"
                for y in yrs if peak_report[region][y][0] > 0)
            print(f"    {region:<4} {cells}")

    df = pd.DataFrame(rows)
    out_name = f"demand_{scenario}.csv"
    df.to_csv(os.path.join(DATA, out_name), index=False)
    if scenario == "StepChange":
        df.to_csv(os.path.join(DATA, "demand_2050.csv"), index=False)  # legacy alias

    ann = (df.groupby(["Year", "Node"])["Demand"].sum() / 1000).unstack().round(1)
    cols = [c for c in ["Adelaide", "Brisbane", "Melbourne", "Sydney", "APLNG", "GLNG", "QCLNG"] if c in ann.columns]
    print(f"=== Annual PJ by node/year (GSOO {scenario}) ===")
    print(ann.loc[[2026, 2030, 2035, 2040, 2045], cols].to_string())
    city = ann[[c for c in ["Adelaide", "Brisbane", "Melbourne", "Sydney"] if c in ann.columns]].sum(axis=1)
    lng = ann[[c for c in ["APLNG", "GLNG", "QCLNG"] if c in ann.columns]].sum(axis=1)
    print("\nCity-node total PJ:", {y: round(city[y], 0) for y in (2026, 2035, 2045)})
    print("LNG-node total PJ :", {y: round(lng[y], 0) for y in (2026, 2035, 2045)})
    print(f"Wrote {len(df)} rows -> data/{out_name}")


def build_all():
    for s in SCENARIOS:
        build(s)


if __name__ == "__main__":
    build_all()
