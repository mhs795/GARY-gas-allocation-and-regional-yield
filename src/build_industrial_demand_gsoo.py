"""
Re-base the large-industrial (BBLARGE) curtailable demand tier on the AEMO 2026
GSOO Step Change industrial trajectory, per year 2026-2045, keeping facility detail.

Unlike GPG (whose modelled facilities are ~the entire NEM fleet), the modelled
industrial tier is only the large transmission-connected (BBLARGE) facilities -
a *subset* of the GSOO whole-sector industrial total. So we do NOT rescale the
subset to the GSOO level; instead we keep the empirically-correct facility levels
and apply the GSOO Step Change industrial *trajectory* as a year index relative
to 2026 (industrial falls ~24% by 2045 under Step Change). The remaining
distribution-connected industrial stays embedded in the city node demand.

Yarwun (the Yarwun GPG plant) is added to industrial at Gladstone: the GSOO
classes it as industrial (dedicated plant for Rio Tinto's alumina refinery), so
it moves here out of the GPG tier, using its historical GBB daily profile.

Output: data/industrial_demand_profile_gsoo.csv  (Year, Node, Day, Demand TJ/day)
"""
import os
import pandas as pd

BASE = os.path.dirname(__file__)
DATA = os.path.join(BASE, "data")
GSOO = os.path.join(DATA, "gsoo")


def build():
    ind = pd.read_csv(os.path.join(DATA, "industrial_demand_profile.csv"))

    # Move Yarwun (old Gladstone GPG) into industrial, at Gladstone.
    gpg = pd.read_csv(os.path.join(DATA, "gpg_demand_profile.csv"))
    yarwun = gpg[gpg.Node == "Gladstone"][["Node", "Day", "Demand"]].copy()
    if not yarwun.empty:
        base = pd.concat([ind, yarwun], ignore_index=True)
        base = base.groupby(["Node", "Day"], as_index=False)["Demand"].sum()
    else:
        base = ind.copy()

    # GSOO Step Change industrial trajectory -> index relative to 2026.
    annual = pd.read_csv(os.path.join(GSOO, "annual_sector_stepchange.csv"))
    ind_tot = annual[annual.Sector == "Industrial"].set_index("Year")["PJ_per_year"].to_dict()
    base_yr = 2026
    index = {y: ind_tot[y] / ind_tot[base_yr] for y in ind_tot if 2026 <= y <= 2045}

    rows = []
    for y, idx in sorted(index.items()):
        scaled = base.copy()
        scaled["Demand"] = (scaled["Demand"] * idx).round(4)
        scaled["Year"] = y
        rows.append(scaled[["Year", "Node", "Day", "Demand"]])
    out = pd.concat(rows, ignore_index=True)
    out.to_csv(os.path.join(DATA, "industrial_demand_profile_gsoo.csv"), index=False)

    # Update facility table to include Yarwun (for per-facility detail / dashboard).
    fac = pd.read_csv(os.path.join(DATA, "industrial_facilities_bbg.csv"))
    if not fac.FacilityName.str.contains("Yarwun", case=False).any() and not yarwun.empty:
        fac = pd.concat([fac, pd.DataFrame([{
            "FacilityName": "Yarwun (GPG)", "Node": "Gladstone", "State": "QLD",
            "MeanDemand": round(float(yarwun.groupby("Day")["Demand"].sum().mean()), 3),
            "Tier": "Industrial", "Strike": 120.0,
        }])], ignore_index=True)
        fac.to_csv(os.path.join(DATA, "industrial_facilities_bbg.csv"), index=False)

    print("=== Industrial annual energy by node (PJ/y), GSOO-indexed ===")
    ann = (out.groupby(["Year", "Node"])["Demand"].sum() / 1000).unstack().round(1)
    print(ann.to_string())
    print("\nGSOO industrial index (vs 2026):",
          {y: round(v, 3) for y, v in sorted(index.items()) if y in (2026, 2030, 2035, 2040, 2045)})
    print(f"Wrote {len(out)} rows -> data/industrial_demand_profile_gsoo.csv")


if __name__ == "__main__":
    build()
