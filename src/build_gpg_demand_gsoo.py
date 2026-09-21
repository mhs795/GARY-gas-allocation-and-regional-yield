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
import functools
import os
import numpy as np
import pandas as pd

import params as P

BASE = os.path.dirname(__file__)
DATA = os.path.join(BASE, "data")
GSOO = os.path.join(DATA, "gsoo")

SCENARIOS = P.get_list("gsoo_baselines", ["StepChange", "Accelerated", "SlowerGrowth"])

# GSOO region -> model nodes. NT/TAS have no modelled node; Gladstone(=Yarwun)
# is reclassified industrial.
# GSOO region -> the GARY nodes that carry its gas-fired generation, derived from
# the Region column on nodes.csv rather than restated here. Gladstone is dropped
# because Yarwun is reclassified to industrial (see the module docstring), and the
# NT is outside the NEM so it is handled separately below.
DROP_NODES = {"Gladstone"}
_GPG_REGIONS = P.get_list("gpg_nem_regions", ["NSW", "SA", "VIC", "QLD"])


@functools.lru_cache(maxsize=1)
def region_nodes():
    """GSOO region -> the GARY nodes that carry its gas-fired generation.

    Read when first needed rather than at import. gpg_facilities.csv is written by
    an earlier regeneration step (build_curtailable_demand), so on a fresh clone it
    does not exist yet; reading it at import made this module unimportable, and the
    dashboard imports it in order to offer the button that creates the file.
    """
    fac = pd.read_csv(os.path.join(DATA, "gpg_facilities.csv"))
    nodes = {
        r: sorted(set(fac.loc[(fac["State"] == r) & (~fac["Node"].isin(DROP_NODES)),
                              "Node"]))
        for r in _GPG_REGIONS
    }
    return {r: n for r, n in nodes.items() if n}


def model_regions():
    """The GSOO regions that have at least one modelled node."""
    return list(region_nodes())


# --- Northern Territory GPG (not in the NEM, so absent from the GSOO NEM data) ---
# NT domestic gas is principally power generation. Sized from the AER's Amadeus Gas
# Pipeline access arrangement review (AAR 2026-31, June 2025), Table 2-1 "Average
# annual demand by delivery point", 2024-25 estimate, in TJ/day:
#
#   Darwin 26.3 | Pine Creek 5.5 | Katherine 2.1 | Daly Waters 8.2 | Elliot 0.1
#   Tennant Creek 1.3 | Tanami Road 8.1 | Palm Valley (Alice Springs) 1.6
#   -> 53.2 TJ/d of NT domestic load (the Warrego 8.1 is gas leaving on the NGP,
#      not NT demand)
#
# Daly Waters (8.2) is EXCLUDED: the AER's stated forecast assumption is that
# "the delivery of gas to the Daly Waters delivery point is expected to end prior to
# the access arrangement period", i.e. before GARY's 2026 base year. NT domestic
# demand is therefore 45.0 TJ/d, not 53.2.
#
# GARY has three NT nodes, so delivery points are assigned by which side of Tennant
# Creek they sit on. NORTH of it reaches load over AGP_N and is carried at the Darwin
# node: Channel Island 24.4 + Weddell-via-AGP 1.4 + Pine Creek 5.5 + Katherine 2.1
# = 33.4 GPG, plus 0.6 of distribution/Townend Road that build_demand_gsoo adds as
# commercial. SOUTH of it is Amadeus: Tanami Road 8.1 + Palm Valley (Alice Springs)
# 1.6 + Tennant Creek 1.3 = 11.0.
#
# OUT OF SCOPE: Weddell now takes most of its gas direct from the LNG producers at
# Wickham Point rather than through the AGP, which is why its AGP delivery is only
# 1.4. That supply route bypasses every pipe GARY models, so it sits outside the
# network the same way Darwin LNG, Ichthys and WA do. GARY carries what the AGP
# delivers, not total NT gas burn.
#
# Note Darwin's AGP-delivered demand fell 40.4 -> 26.3 TJ/d over 2021-22 to 2024-25,
# but much of that is Weddell Power Station buying direct from the LNG producers at
# Wickham Point rather than through the AGP -- a change of ROUTE, not of consumption.
# GARY models neither Wickham Point nor that switch, so the node carries the load.
# The AER's own forecast assumption is that "local demand is not expected to change
# significantly", which is what the gentle decline below represents (rooftop solar).
# Tropical -> roughly flat across the year. Per-station split: gpg_facilities.csv.
NT_GPG_BASE_TJD = {n: float(v) for n, v in
                   P.get_pairs("nt_gpg_base_tjd",
                               [("Darwin", 33.4), ("Amadeus", 11.0)])}
NT_GPG_DECLINE = P.get("nt_gpg_decline", 0.008)

# Gas winter (Jun-Aug) and summer (Dec-Feb) day-of-year windows.
# Same southern winter window model.py uses, plus the summer window the
# winter:summer peak ratio is struck against. Both on the Parameters sheet.
WINTER_DAYS = set(range(P.get_int("winter_day_start", 150),
                        P.get_int("winter_day_end", 250) + 1))
_S0, _S1 = P.get_int("summer_day_start", 335), P.get_int("summer_day_end", 59)
SUMMER_DAYS = set(range(_S0, 366)) | set(range(1, _S1 + 1))


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
        for r in model_regions():
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
    for reg, nodes in region_nodes().items():
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
                  for r in model_regions() if r in smax.index}
        wsum = sum(weight.values())
        for reg, nodes in region_nodes().items():
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
