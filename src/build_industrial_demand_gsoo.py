"""
Re-base the large-industrial (BBLARGE) curtailable demand tier on an AEMO 2026
GSOO baseline scenario's industrial trajectory, per year 2026-2045, keeping
facility detail.

Unlike GPG (whose modelled facilities are ~the entire NEM fleet), the modelled
industrial tier is only the large transmission-connected (BBLARGE) facilities -
a *subset* of the GSOO whole-sector industrial total. So we do NOT rescale the
subset to the GSOO level; instead we keep the empirically-correct facility levels
and apply the GSOO industrial *trajectory* (for the chosen scenario) as a year
index relative to 2026. The remaining distribution-connected industrial stays
embedded in the city node demand.

Yarwun (the Yarwun GPG plant) is added to industrial at Gladstone: the GSOO
classes it as industrial (dedicated plant for Rio Tinto's alumina refinery), so
it moves here out of the GPG tier, using its historical GBB daily profile.

Output: data/industrial_demand_profile_<scenario>.csv  (Year, Node, Day, Demand TJ/day).
For the StepChange baseline the legacy filename industrial_demand_profile_gsoo.csv
is also written.
"""
import os
import pandas as pd

import params as P

BASE = os.path.dirname(__file__)
DATA = os.path.join(BASE, "data")
GSOO = os.path.join(DATA, "gsoo")

SCENARIOS = P.get_list("gsoo_baselines", ["StepChange", "Accelerated", "SlowerGrowth"])


def build(scenario="StepChange"):
    ind = pd.read_csv(os.path.join(DATA, "industrial_demand_profile.csv"))

    # Move Yarwun (old Gladstone GPG) into industrial, at Gladstone.
    gpg = pd.read_csv(os.path.join(DATA, "gpg_demand_profile.csv"))
    yarwun = gpg[gpg.Node == "Gladstone"][["Node", "Day", "Demand"]].copy()
    if not yarwun.empty:
        base = pd.concat([ind, yarwun], ignore_index=True)
        base = base.groupby(["Node", "Day"], as_index=False)["Demand"].sum()
    else:
        base = ind.copy()

    # GSOO industrial trajectory for the chosen scenario -> index relative to 2026.
    annual = pd.read_csv(os.path.join(GSOO, "annual_sector.csv"))
    ind_tot = annual[(annual.Scenario == scenario) & (annual.Sector == "Industrial")
                     ].set_index("Year")["PJ_per_year"].to_dict()
    base_yr = P.get_int("gsoo_index_base_year", 2026)
    last_yr = P.get_int("gsoo_index_last_year", 2045)
    index = {y: ind_tot[y] / ind_tot[base_yr] for y in ind_tot if base_yr <= y <= last_yr}

    rows = []
    for y, idx in sorted(index.items()):
        scaled = base.copy()
        scaled["Demand"] = (scaled["Demand"] * idx).round(4)
        scaled["Year"] = y
        rows.append(scaled[["Year", "Node", "Day", "Demand"]])
    out = pd.concat(rows, ignore_index=True)
    out_name = f"industrial_demand_profile_{scenario}.csv"
    out.to_csv(os.path.join(DATA, out_name), index=False)
    if scenario == "StepChange":
        out.to_csv(os.path.join(DATA, "industrial_demand_profile_gsoo.csv"), index=False)  # legacy

    # Update facility table to include Yarwun (for per-facility detail / dashboard).
    fac = pd.read_csv(os.path.join(DATA, "industrial_facilities_bbg.csv"))
    if not fac.FacilityName.str.contains("Yarwun", case=False).any() and not yarwun.empty:
        fac = pd.concat([fac, pd.DataFrame([{
            "FacilityName": "Yarwun (GPG)", "Node": "Gladstone", "State": "QLD",
            "MeanDemand": round(float(yarwun.groupby("Day")["Demand"].sum().mean()), 3),
            "Tier": "Industrial", "Strike": 120.0,
        }])], ignore_index=True)
        fac.to_csv(os.path.join(DATA, "industrial_facilities_bbg.csv"), index=False)

    print(f"=== Industrial annual energy by node (PJ/y), GSOO {scenario}-indexed ===")
    ann = (out.groupby(["Year", "Node"])["Demand"].sum() / 1000).unstack().round(1)
    print(ann.loc[[y for y in (2026, 2030, 2035, 2040, 2045) if y in ann.index]].to_string())
    print(f"\nGSOO {scenario} industrial index (vs 2026):",
          {y: round(v, 3) for y, v in sorted(index.items()) if y in (2026, 2030, 2035, 2040, 2045)})
    print(f"Wrote {len(out)} rows -> data/{out_name}")


def build_all():
    for s in SCENARIOS:
        build(s)


if __name__ == "__main__":
    build_all()
