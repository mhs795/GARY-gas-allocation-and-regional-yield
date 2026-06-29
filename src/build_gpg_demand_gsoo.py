"""
Re-base GPG demand on the AEMO 2026 GSOO Step Change scenario, per year 2026-2045,
while preserving the existing facility/node spatial detail.

Method (see build_gsoo_stepchange.py for the source data):
  * Level   : each year's daily trace is scaled so total GPG energy across the
              modelled NEM nodes matches the GSOO Step Change NEM annual (PJ/y).
  * Region  : the NEM annual is split across regions in proportion to each
              region's GSOO peak weight (mean of summer & winter peak). Regional
              peaks are non-coincident, so they are used only for the *split* and
              the *seasonality*, never summed as a system total.
  * Season  : within each region the winter-day mean : summer-day mean ratio is
              set to the GSOO winter-max : summer-max ratio (the winter-peaking
              the ISP/GSOO emphasise), starting from the historical GBB daily shape.
  * Facility: nodes keep their existing within-region share (from the GBB profile),
              and gpg_facilities.csv is retained for per-facility allocation.

Yarwun (Gladstone) is dropped here: the GSOO classes it as industrial (dedicated
plant for Rio Tinto's refinery), so it moves to the industrial tier.

Output: data/gpg_demand_profile_gsoo.csv  (Year, Node, Day, Demand in TJ/day)
"""
import os
import numpy as np
import pandas as pd

BASE = os.path.dirname(__file__)
DATA = os.path.join(BASE, "data")
GSOO = os.path.join(DATA, "gsoo")

# GSOO region -> model nodes. NT/TAS have no modelled node; Gladstone(=Yarwun)
# is reclassified industrial.
REGION_NODES = {
    "NSW": ["Sydney"],
    "SA":  ["Adelaide"],
    "VIC": ["Melbourne", "Gippsland"],
    "QLD": ["Surat", "Brisbane"],
}
MODEL_REGIONS = list(REGION_NODES)
DROP_NODES = {"Gladstone"}  # Yarwun -> industrial

# Gas winter (Jun-Aug) and summer (Dec-Feb) day-of-year windows.
WINTER_DAYS = set(range(152, 244))
SUMMER_DAYS = set(range(335, 366)) | set(range(1, 60))


def _node_shapes():
    """Normalised daily shape (mean=1) per kept node, from the historical GBB trace."""
    g = pd.read_csv(os.path.join(DATA, "gpg_demand_profile.csv"))
    g = g[~g.Node.isin(DROP_NODES)]
    shapes, node_mean = {}, {}
    for node, sub in g.groupby("Node"):
        s = sub.sort_values("Day").set_index("Day")["Demand"].reindex(range(1, 366))
        s = s.fillna(s.mean())
        node_mean[node] = float(s.mean())
        shapes[node] = (s / s.mean()).values  # length-365, mean 1
    return shapes, node_mean


def _seasonalise(shape, ratio):
    """Reweight a mean-1 daily shape so winter-day mean : summer-day mean == ratio.
    Returns a new mean-1 array."""
    days = np.arange(1, 366)
    is_w = np.array([d in WINTER_DAYS for d in days])
    is_s = np.array([d in SUMMER_DAYS for d in days])
    w_mean = shape[is_w].mean()
    s_mean = shape[is_s].mean()
    cur = w_mean / s_mean if s_mean > 0 else 1.0
    # Multiplier applied to winter days to move current ratio toward target.
    boost = ratio / cur if cur > 0 else 1.0
    boost = float(np.clip(boost, 0.2, 8.0))
    out = shape.copy()
    out[is_w] *= boost
    return out / out.mean()


def build():
    shapes, node_mean = _node_shapes()
    annual = pd.read_csv(os.path.join(GSOO, "annual_sector_stepchange.csv"))
    gpg_nem = annual[annual.Sector == "GPG_NEM"].set_index("Year")["PJ_per_year"].to_dict()
    peaks = pd.read_csv(os.path.join(GSOO, "regional_peak_stepchange.csv"))

    # within-region node share from historical means
    region_node_share = {}
    for reg, nodes in REGION_NODES.items():
        tot = sum(node_mean.get(n, 0.0) for n in nodes)
        region_node_share[reg] = {n: (node_mean.get(n, 0.0) / tot if tot else 1.0 / len(nodes))
                                   for n in nodes}

    rows = []
    years = sorted(y for y in gpg_nem if 2026 <= y <= 2045)
    for y in years:
        A = gpg_nem[y]  # NEM annual GPG energy, PJ/y
        py = peaks[peaks.Year == y]
        smax = py.pivot(index="Region", columns="Season", values="GPG_TJd")
        # regional weight = mean of summer & winter peak (only modelled regions)
        weight = {r: float((smax.loc[r, "Summer"] + smax.loc[r, "Winter"]) / 2)
                  for r in MODEL_REGIONS if r in smax.index}
        wsum = sum(weight.values())
        for reg, nodes in REGION_NODES.items():
            if reg not in weight:
                continue
            reg_energy_pj = A * weight[reg] / wsum            # PJ/y to this region
            reg_mean_tjd = reg_energy_pj * 1000.0 / 365.0     # TJ/day average
            sm = smax.loc[reg, "Summer"]
            wm = smax.loc[reg, "Winter"]
            ratio = (wm / sm) if sm and sm > 0 else 3.0       # winter:summer target
            for n in nodes:
                share = region_node_share[reg][n]
                node_target_mean = reg_mean_tjd * share
                daily = _seasonalise(shapes[n], ratio) * node_target_mean
                for d in range(1, 366):
                    rows.append((y, n, d, round(float(daily[d - 1]), 4)))

    out = pd.DataFrame(rows, columns=["Year", "Node", "Day", "Demand"])
    out.to_csv(os.path.join(DATA, "gpg_demand_profile_gsoo.csv"), index=False)

    # ---- summary ----
    print("=== GPG annual energy by node (PJ/y), GSOO Step Change ===")
    ann = (out.groupby(["Year", "Node"])["Demand"].sum() / 1000).unstack().round(1)
    print(ann.to_string())
    chk = out.groupby("Year")["Demand"].sum() / 1000
    print("\n=== NEM-node total vs GSOO target (PJ/y) ===")
    for y in years:
        print(f"  {y}: built {chk[y]:.1f}  target {gpg_nem[y]:.1f}")
    print(f"\nWrote {len(out)} rows -> data/gpg_demand_profile_gsoo.csv")


if __name__ == "__main__":
    build()
