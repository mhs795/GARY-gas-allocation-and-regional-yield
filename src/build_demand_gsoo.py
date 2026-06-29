"""
Re-base the node distribution demand (demand_2050.csv) on the AEMO 2026 GSOO
Step Change scenario. This makes Step Change the model's central case; the
existing scenario levers (Winter, LNG, ADGSM in batch_solve.py / gui.py) then
layer multiplicatively on top.

Replaces the arbitrary per-node growth rates in generate_data_2050.py with
empirically-grounded GSOO Step Change trajectories:
  * City nodes (Sydney, Melbourne, Adelaide, Brisbane) -- city-gate distribution
    demand, dominantly residential & small commercial (Tariff V) -- follow the
    GSOO ResComm trajectory (Figure 17): a steep electrification-driven decline.
  * LNG nodes (APLNG, GLNG, QCLNG) -- follow the GSOO LNG trajectory (Figure 19),
    replacing the previous flat assumption.

The 2026 base level is the empirical daily shape (demand_profiles.csv + LNG
calibration), and GSOO indices are applied relative to 2026 (clamped to the
2026-2045 GSOO horizon; 2045 held flat to 2050). Deterministic (no random noise).

Output: data/demand_2050.csv  (Year, Day, Node, Demand TJ/day)
"""
import os
import numpy as np
import pandas as pd

BASE = os.path.dirname(__file__)
DATA = os.path.join(BASE, "data")
GSOO = os.path.join(DATA, "gsoo")

CITY_NODES = {"Sydney", "Melbourne", "Adelaide", "Brisbane"}
YEARS = np.arange(2025, 2051)


def _gsoo_index(sector, lo=2026, hi=2045, base=2026):
    """Year -> level relative to the base year, for a GSOO sector, clamped."""
    annual = pd.read_csv(os.path.join(GSOO, "annual_sector_stepchange.csv"))
    s = annual[annual.Sector == sector].set_index("Year")["PJ_per_year"].to_dict()
    base_val = s[base]
    def idx(year):
        y = min(max(int(year), lo), hi)
        return s[y] / base_val
    return idx


def build():
    base_trace = pd.read_csv(os.path.join(DATA, "demand_profiles.csv"))
    lng_params = pd.read_csv(os.path.join(DATA, "lng_parameters.csv")).set_index("Parameter")["Value"].to_dict()

    daily_trace = base_trace.groupby(["Day", "Node"])["Demand"].mean().reset_index()

    lng_nodes = {"APLNG": lng_params["aplng_factor"],
                 "GLNG": lng_params["glng_factor"],
                 "QCLNG": lng_params["qclng_factor"]}
    aplng_trace = daily_trace[daily_trace["Node"] == "APLNG"]
    scaling_factor = lng_params["lng_daily_target"] / aplng_trace["Demand"].mean()

    rescomm_idx = _gsoo_index("ResComm")
    lng_idx = _gsoo_index("LNG")

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

    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(DATA, "demand_2050.csv"), index=False)

    ann = (df.groupby(["Year", "Node"])["Demand"].sum() / 1000).unstack().round(1)
    cols = [c for c in ["Adelaide", "Brisbane", "Melbourne", "Sydney", "APLNG", "GLNG", "QCLNG"] if c in ann.columns]
    print("=== Annual PJ by node/year (GSOO Step Change central case) ===")
    print(ann.loc[[2026, 2030, 2035, 2040, 2045], cols].to_string())
    city = ann[[c for c in ["Adelaide", "Brisbane", "Melbourne", "Sydney"] if c in ann.columns]].sum(axis=1)
    lng = ann[[c for c in ["APLNG", "GLNG", "QCLNG"] if c in ann.columns]].sum(axis=1)
    print("\nCity-node total PJ:", {y: round(city[y], 0) for y in (2026, 2035, 2045)})
    print("LNG-node total PJ :", {y: round(lng[y], 0) for y in (2026, 2035, 2045)})
    print(f"Wrote {len(df)} rows -> data/demand_2050.csv")


if __name__ == "__main__":
    build()
