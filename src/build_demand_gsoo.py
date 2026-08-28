"""
Re-base the node distribution demand on an AEMO 2026 GSOO baseline scenario. The
chosen baseline (Step Change / Accelerated Transition / Slower Growth) becomes the
model's central case; the scenario levers (Winter, LNG, gas reservation in solve.py /
batch_solve.py / solve.py) then layer multiplicatively on top of it.

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

BASE = os.path.dirname(__file__)
DATA = os.path.join(BASE, "data")
GSOO = os.path.join(DATA, "gsoo")

CITY_NODES = {"Sydney", "Melbourne", "Adelaide", "Brisbane"}
YEARS = np.arange(2025, 2051)

# NT (Darwin) city-gate commercial / light-industrial load only — small, and held
# flat (AER: "local demand is not expected to change significantly"). Sized from the
# AER AGP access arrangement review (AAR 2026-31, Table 2-1, 2024-25): the Darwin
# distribution system (0.3) plus Townend Road (0.2) plus Elliot (0.1). The bulk of NT
# gas demand is power generation, modelled separately as a curtailable GPG tier at
# the Darwin and Amadeus nodes (see build_gpg_demand_gsoo.py / gpg_facilities.csv).
NT_DARWIN_COMMERCIAL_TJD = 0.6

# Baseline scenarios -> output filename slug. Mirrors build_gsoo_scenarios.SCENARIOS.
SCENARIOS = ["StepChange", "Accelerated", "SlowerGrowth"]


def _gsoo_index(annual, scenario, sector, lo=2026, hi=2045, base=2026):
    """Year -> level relative to the base year, for a GSOO sector/scenario, clamped."""
    s = annual[(annual.Scenario == scenario) & (annual.Sector == sector)
               ].set_index("Year")["PJ_per_year"].to_dict()
    base_val = s[base]
    def idx(year):
        y = min(max(int(year), lo), hi)
        return s[y] / base_val
    return idx


def _gsoo_level(annual, scenario, sector, year=2026):
    """A GSOO sector's absolute level in ``year``, TJ/day."""
    row = annual[(annual.Scenario == scenario) & (annual.Sector == sector)
                 & (annual.Year == year)]
    return float(row["PJ_per_year"].iloc[0]) * 1000.0 / 365.0


def build(scenario="StepChange"):
    annual = pd.read_csv(os.path.join(GSOO, "annual_sector.csv"))
    base_trace = pd.read_csv(os.path.join(DATA, "demand_profiles.csv"))
    lng_params = pd.read_csv(os.path.join(DATA, "lng_parameters.csv")).set_index("Parameter")["Value"].to_dict()

    daily_trace = base_trace.groupby(["Day", "Node"])["Demand"].mean().reset_index()

    lng_nodes = {"APLNG": lng_params["aplng_factor"],
                 "GLNG": lng_params["glng_factor"],
                 "QCLNG": lng_params["qclng_factor"]}
    aplng_trace = daily_trace[daily_trace["Node"] == "APLNG"]
    scaling_factor = lng_params["lng_daily_target"] / aplng_trace["Demand"].mean()

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
        blended = rc_share * ci + (1.0 - rc_share) * ii
        for _, r in daily_trace[daily_trace["Node"] != "APLNG"].iterrows():
            node = r["Node"]
            factor = calib * blended if node in CITY_NODES else 1.0
            rows.append({"Year": int(year), "Day": int(r["Day"]), "Node": node,
                         "Demand": round(max(0.0, r["Demand"] * factor), 4)})

        # NT (Darwin) — small city-gate commercial/light-industrial load, held flat.
        # Power generation is modelled separately as a GPG tier (build_gpg_demand_gsoo).
        for day in range(1, 366):
            rows.append({"Year": int(year), "Day": day, "Node": "Darwin",
                         "Demand": round(NT_DARWIN_COMMERCIAL_TJD, 4)})

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
