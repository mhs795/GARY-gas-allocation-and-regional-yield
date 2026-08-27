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
    lng_idx = _gsoo_index(annual, scenario, "LNG")

    rows = []
    for year in YEARS:
        ci = rescomm_idx(year)
        li = lng_idx(year)

        # LNG nodes: shared Curtis Island shape, split, scaled to GSOO LNG trajectory.
        for _, r in aplng_trace.iterrows():
            for node, split in lng_nodes.items():
                val = r["Demand"] * scaling_factor * split * li
                rows.append({"Year": int(year), "Day": int(r["Day"]), "Node": node,
                             "Demand": round(max(0.0, val), 4)})

        # Other (city) nodes: distribution demand on the GSOO ResComm trajectory.
        for _, r in daily_trace[daily_trace["Node"] != "APLNG"].iterrows():
            node = r["Node"]
            factor = ci if node in CITY_NODES else 1.0
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
