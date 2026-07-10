"""
Re-base GPG demand on an AEMO 2026 GSOO baseline scenario, per year 2026-2045,
while preserving the existing facility/node spatial detail.

Method (see build_gsoo_scenarios.py for the source data):
  * Level   : each year's daily trace is scaled so total GPG energy across the
              modelled NEM nodes matches the GSOO NEM annual (PJ/y). AEMO publishes
              a single 2026 GSOO GPG annual trajectory (not split by scenario), so
              for non-StepChange baselines that trajectory is scaled by the ratio of
              the scenario's total modelled-region GPG peak to Step Change's (the
              regional peaks ARE published per scenario). This carries the scenario's
              GPG firming signal into the energy level without inventing data.
  * Region  : the NEM annual is split across regions in proportion to each region's
              GSOO peak weight (mean of summer & winter peak) for the chosen scenario.
              Regional peaks are non-coincident, so they are used only for the *split*
              and the *seasonality*, never summed as a system total.
  * Season  : within each region the winter-day mean : summer-day mean ratio is set
              to the scenario's GSOO winter-max : summer-max ratio (the winter-peaking
              the ISP/GSOO emphasise), starting from the historical GBB daily shape.
  * Facility: nodes keep their existing within-region share (from the GBB profile),
              and gpg_facilities.csv is retained for per-facility allocation.

Yarwun (Gladstone) is dropped here: the GSOO classes it as industrial (dedicated
plant for Rio Tinto's refinery), so it moves to the industrial tier.

Output: data/gpg_demand_profile_<scenario>.csv  (Year, Node, Day, Demand in TJ/day).
For the StepChange baseline the legacy filename gpg_demand_profile_gsoo.csv is also
written.
"""
import os
import numpy as np
import pandas as pd

BASE = os.path.dirname(__file__)
DATA = os.path.join(BASE, "data")
GSOO = os.path.join(DATA, "gsoo")

SCENARIOS = ["StepChange", "Accelerated", "SlowerGrowth"]

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

# --- Northern Territory GPG (not in the NEM, so absent from the GSOO NEM data) ---
# NT domestic gas is principally power generation (AER Amadeus Gas Pipeline demand
# review): the Darwin-Katherine Interconnected System stations at the Darwin node, plus
# Owen Springs (Alice Springs) at the Amadeus Basin node. Tropical -> roughly flat
# across the year (no southern winter peak); gently declining (~0.8%/yr) as rooftop
# solar displaces gas. Scenario-independent. Per-station split: gpg_facilities.csv.
NT_GPG_BASE_TJD = {"Darwin": 42.0, "Amadeus": 6.0}   # node totals in 2026
NT_GPG_DECLINE = 0.008

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


def _modelled_peak_total(peaks_scen):
    """Per-year sum over modelled regions of the mean(summer,winter) GPG peak."""
    out = {}
    for y, py in peaks_scen.groupby("Year"):
        smax = py.pivot_table(index="Region", columns="Season", values="GPG_TJd")
        tot = 0.0
        for r in MODEL_REGIONS:
            if r in smax.index:
                tot += float((smax.loc[r, "Summer"] + smax.loc[r, "Winter"]) / 2)
        out[int(y)] = tot
    return out


def build(scenario="StepChange"):
    shapes, node_mean = _node_shapes()
    annual = pd.read_csv(os.path.join(GSOO, "annual_sector.csv"))
    # GPG annual is published for a single scenario only (tagged StepChange).
    gpg_nem = annual[(annual.Scenario == "StepChange") & (annual.Sector == "GPG_NEM")
                     ].set_index("Year")["PJ_per_year"].to_dict()
    all_peaks = pd.read_csv(os.path.join(GSOO, "regional_peak.csv"))
    peaks = all_peaks[all_peaks.Scenario == scenario]

    # Per-scenario GPG level multiplier vs Step Change, from modelled-region peaks.
    sc_peak_tot = _modelled_peak_total(all_peaks[all_peaks.Scenario == "StepChange"])
    scen_peak_tot = _modelled_peak_total(peaks)

    # within-region node share from historical means
    region_node_share = {}
    for reg, nodes in REGION_NODES.items():
        tot = sum(node_mean.get(n, 0.0) for n in nodes)
        region_node_share[reg] = {n: (node_mean.get(n, 0.0) / tot if tot else 1.0 / len(nodes))
                                   for n in nodes}

    rows = []
    years = sorted(y for y in gpg_nem if 2026 <= y <= 2045)
    for y in years:
        level_mult = (scen_peak_tot.get(y, 0.0) / sc_peak_tot[y]
                      if sc_peak_tot.get(y) else 1.0)
        A = gpg_nem[y] * level_mult  # NEM annual GPG energy for this scenario, PJ/y
        py = peaks[peaks.Year == y]
        smax = py.pivot_table(index="Region", columns="Season", values="GPG_TJd")
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

    # Northern Territory GPG — flat daily load per node, gentle solar-driven decline.
    # Scenario-independent (NT is outside the NEM/GSOO scenario framework).
    for y in years:
        f = (1 - NT_GPG_DECLINE) ** (y - 2026)
        for node, base in NT_GPG_BASE_TJD.items():
            val = round(base * f, 4)
            for d in range(1, 366):
                rows.append((y, node, d, val))

    out = pd.DataFrame(rows, columns=["Year", "Node", "Day", "Demand"])
    out_name = f"gpg_demand_profile_{scenario}.csv"
    out.to_csv(os.path.join(DATA, out_name), index=False)
    if scenario == "StepChange":
        out.to_csv(os.path.join(DATA, "gpg_demand_profile_gsoo.csv"), index=False)  # legacy

    # ---- summary ----
    print(f"=== GPG annual energy by node (PJ/y), GSOO {scenario} ===")
    ann = (out.groupby(["Year", "Node"])["Demand"].sum() / 1000).unstack().round(1)
    print(ann.loc[[y for y in (2026, 2030, 2035, 2040, 2045) if y in ann.index]].to_string())
    chk = out.groupby("Year")["Demand"].sum() / 1000
    print(f"\n=== NEM-node total GPG (PJ/y), {scenario} (Step Change base x peak ratio) ===")
    for y in (2026, 2030, 2040):
        if y in chk.index:
            print(f"  {y}: built {chk[y]:.1f}  (StepChange base {gpg_nem[y]:.1f})")
    print(f"Wrote {len(out)} rows -> data/{out_name}")


def build_all():
    for s in SCENARIOS:
        build(s)


if __name__ == "__main__":
    build_all()
