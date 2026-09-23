"""
Build empirical GPG and large-industrial demand traces from the AEMO Gas
Bulletin Board (GBB) Actual Flow & Storage data.

These are large, transmission-connected gas users that are reported on the
Bulletin Board as their own facilities (FacilityType BBGPG / BBLARGE) and are
therefore NOT captured in the city distribution demand that demand_2050.csv was
calibrated from. They are added to the model as additional, *curtailable*
demand (see model.py): each tier is served unless the nodal gas price exceeds a
strike price, above which the load sheds instead of paying.

Outputs (written to src/data/):
  - gpg_facilities.csv            per-facility GPG metadata + mean demand + node
  - industrial_facilities_bbg.csv per-facility industrial metadata + node
  - gpg_demand_profile.csv        365-day per-node GPG demand trace (TJ/day)
  - industrial_demand_profile.csv 365-day per-node industrial demand (TJ/day)
  - curtailment_params.csv        strike prices ($/GJ) per demand tier

Method mirrors how demand_profiles.csv was built: average recent years
(2022-2026) of GBB actual flow into a representative day-of-year shape.
"""
import os
import pandas as pd

import params as P

BASE = os.path.dirname(__file__)
DATA = os.path.join(BASE, "data")
GBB = os.path.join(DATA, "GasBBActualFlowStorage.CSV")

RECENT_FROM = P.get_int('gbb_recent_from')

# Model demand/transport nodes that can carry gas. GPG/industrial facilities are
# mapped to the nearest of these. Facilities not on the modelled eastern network
# (NT, TAS, Mount Isa / North Queensland) are excluded.
#
# Mapping is by facility name (LocationName "Regional - <STATE>" is too coarse
# to separate e.g. Surat-basin plants from Mount Isa plants).
FACILITY_NODE = {
    # --- NSW -> Sydney ---
    "Uranquinty Power Station": "Sydney",
    "Tallawarra": "Sydney",
    "Hunter Power Station": "Sydney",
    "Colongra": "Sydney",
    "Smithfield Energy Facility": "Sydney",
    "Orica (Kooragang Island)": "Sydney",
    "Shoalhaven Starches Cogeneration Plant": "Sydney",
    "BlueScope": "Sydney",
    "Qenos (Botany)": "Sydney",
    # --- SA -> Adelaide ---
    "Torrens Island PS": "Adelaide",
    "Pelican Point Power Station": "Adelaide",
    "Osborne": "Adelaide",
    "Quarantine Power Station": "Adelaide",
    "Mintaro Power Station": "Adelaide",
    "Hallett": "Adelaide",
    "Ladbroke Grove PS": "Adelaide",
    "Barker Inlet Power Station": "Adelaide",
    "Snapper Point": "Adelaide",
    "Bolivar": "Adelaide",
    "Dry Creek Power Station": "Adelaide",
    "Whyalla Steelworks": "Adelaide",
    "Adelaide Brighton": "Adelaide",
    # --- VIC: Latrobe Valley / Gippsland -> Gippsland, rest -> Melbourne ---
    "Jeerelang": "Gippsland",
    "Valley Power GPG": "Gippsland",
    "Loy Yang B": "Gippsland",
    "Bairnsdale Power Station": "Gippsland",
    "Australian Paper (Opal)": "Gippsland",
    "Mortlake PS": "Melbourne",
    "Newport": "Melbourne",
    "Laverton": "Melbourne",
    "Somerton": "Melbourne",
    "Viva Energy Refinery": "Melbourne",
    "Qenos": "Melbourne",
    # --- QLD: Surat basin -> Surat, Gladstone cogen -> Gladstone, SEQ -> Brisbane
    "DDPS": "Surat",                 # Darling Downs Power Station
    "Braemar Power Station": "Surat",
    "Braemar 2 Power Station": "Surat",
    "Condamine Power Station": "Surat",
    "Oakey PS": "Surat",
    "Roma Power Station": "Surat",
    "Swanbank Power Station": "Brisbane",
    "Yarwun": "Gladstone",
    "Queensland Aluminia": "Gladstone",
    "Orica": "Gladstone",            # Orica Yarwun (QLD)
}

# Explicitly excluded (off the modelled eastern network).
EXCLUDE = {
    "Diamantina Power Station",  # Mount Isa (Carpentaria system)
    "Mica Creek",                # Mount Isa
    "Townsville",                # North Queensland
    "Phosphate Hill",            # Incitec, near Mount Isa
}

# Strike prices ($/GJ): the nodal gas price above which each tier sheds load
# rather than being supplied. Mass-market (residual node demand) is firm and
# keeps the model's $300/GJ value-of-lost-load.
#   GPG ~ $22/GJ : gas generators become uneconomic vs coal/batteries/imports.
#   Industrial ~ $120/GJ : high cost of lost production, few alternatives, so
#                          they curtail only in extreme scarcity (still < VoLL).
# Same two strikes model.py reads; one definition, on the Parameters sheet.
STRIKES = {"GPG": P.get('strike_gpg_default'),
           "Industrial": P.get('strike_ind_default')}

# Northern Territory gas-power stations. NT is not on the (NEM) Gas Bulletin Board,
# so its facilities are added explicitly. NT domestic gas demand is principally power
# generation (AER Amadeus Gas Pipeline demand review): the Darwin-Katherine
# Interconnected System (Channel Island, Weddell, Katherine, Pine Creek) at the Darwin
# node, plus Owen Springs (Alice Springs) off the Amadeus Basin. MeanDemand is a
# representative TJ/day; the map scales these shares to each scenario-year's served gas.
# MeanDemand is the AER's 2024-25 delivery-point estimate (AAR 2026-31, Table 2-1).
# Weddell is only 1.4 because it now buys most of its gas direct from the LNG
# producers at Wickham Point, off the AGP and so outside GARY's network.
NT_GPG_FACILITIES = [
    ("Channel Island Power Station", "Darwin",  "NT", 24.4),
    ("Pine Creek Power Station",     "Darwin",  "NT",  5.5),
    ("Katherine Power Station",      "Darwin",  "NT",  2.1),
    ("Weddell Power Station",        "Darwin",  "NT",  1.4),
    ("Tanami power stations",        "Amadeus", "NT",  8.1),
    ("Owen Springs Power Station",   "Amadeus", "NT",  1.6),
    ("Tennant Creek Power Station",  "Amadeus", "NT",  1.3),
]


def _load_type(df, ftype):
    g = df[df.FacilityType == ftype].copy()
    g["GasDate"] = pd.to_datetime(g.GasDate, format="%Y/%m/%d")
    g = g[g.GasDate.dt.year >= RECENT_FROM]
    g = g[~g.FacilityName.isin(EXCLUDE)]
    # day-of-year 1..365 (drop leap day 366 so it aligns with the model calendar)
    g["Day"] = g.GasDate.dt.dayofyear
    g = g[g.Day <= 365]
    g["Node"] = g.FacilityName.map(FACILITY_NODE)
    unmapped = sorted(set(g[g.Node.isna()].FacilityName.unique()))
    if unmapped:
        print(f"  [{ftype}] WARNING unmapped (excluded): {unmapped}")
    g = g.dropna(subset=["Node"])
    g["Demand"] = g["Demand"].clip(lower=0)
    return g


def _facility_table(g, tier):
    fac = (g.groupby(["FacilityName", "Node", "State"])["Demand"].mean()
             .reset_index().rename(columns={"Demand": "MeanDemand"}))
    fac["Tier"] = tier
    fac["Strike"] = STRIKES[tier]
    fac["MeanDemand"] = fac["MeanDemand"].round(3)
    return fac.sort_values(["Node", "MeanDemand"], ascending=[True, False])


def _node_profile(g):
    # Node daily total = SUM across all facilities at that node on each actual
    # date, then average across recent years for a representative 365-day shape.
    # (Summing first is essential: a node has many facilities; averaging across
    # them would understate node demand by ~the facility count.)
    g = g.copy()
    g["Year"] = g["GasDate"].dt.year
    daily = g.groupby(["Year", "Day", "Node"])["Demand"].sum().reset_index()
    prof = daily.groupby(["Day", "Node"])["Demand"].mean().reset_index()
    prof["Demand"] = prof["Demand"].round(4)
    # ensure every node has all 365 days (fill gaps with node mean)
    nodes = prof.Node.unique()
    full = pd.MultiIndex.from_product([range(1, 366), nodes], names=["Day", "Node"])
    prof = prof.set_index(["Day", "Node"]).reindex(full)
    prof["Demand"] = prof.groupby("Node")["Demand"].transform(
        lambda s: s.fillna(s.mean()))
    return prof.reset_index()


def main():
    print(f"Reading {GBB} ...")
    df = pd.read_csv(GBB, low_memory=False)

    gpg = _load_type(df, "BBGPG")
    ind = _load_type(df, "BBLARGE")

    gpg_fac = _facility_table(gpg, "GPG")
    ind_fac = _facility_table(ind, "Industrial")
    # Add the NT gas-power stations (not on the NEM GBB) to the GPG facility table.
    nt = pd.DataFrame(NT_GPG_FACILITIES, columns=["FacilityName", "Node", "State", "MeanDemand"])
    nt["Tier"] = "GPG"
    nt["Strike"] = STRIKES["GPG"]
    gpg_fac = pd.concat([gpg_fac, nt], ignore_index=True).sort_values(
        ["Node", "MeanDemand"], ascending=[True, False])
    gpg_fac.to_csv(os.path.join(DATA, "gpg_facilities.csv"), index=False)
    ind_fac.to_csv(os.path.join(DATA, "industrial_facilities_bbg.csv"), index=False)

    gpg_prof = _node_profile(gpg)
    ind_prof = _node_profile(ind)
    gpg_prof.to_csv(os.path.join(DATA, "gpg_demand_profile.csv"), index=False)
    ind_prof.to_csv(os.path.join(DATA, "industrial_demand_profile.csv"), index=False)

    pd.DataFrame([{"Tier": k, "StrikePrice": v} for k, v in STRIKES.items()]
                 ).to_csv(os.path.join(DATA, "curtailment_params.csv"), index=False)

    # ---- summary ----
    print("\n=== GPG demand by node (TJ/day, annual mean) ===")
    print(gpg_prof.groupby("Node")["Demand"].mean().round(1).to_string())
    print(f"  total GPG: {gpg_prof.groupby('Day')['Demand'].sum().mean():.1f} TJ/day")
    print("\n=== Industrial demand by node (TJ/day, annual mean) ===")
    print(ind_prof.groupby("Node")["Demand"].mean().round(1).to_string())
    print(f"  total industrial: {ind_prof.groupby('Day')['Demand'].sum().mean():.1f} TJ/day")
    print(f"\nStrike prices ($/GJ): {STRIKES}")
    print("Wrote gpg_facilities.csv, industrial_facilities_bbg.csv, "
          "gpg_demand_profile.csv, industrial_demand_profile.csv, curtailment_params.csv")


if __name__ == "__main__":
    main()
