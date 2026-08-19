"""
Regenerate ALL derived model data from the source inputs shipped in the repo.

This is what the "Regenerate Demand Data" button in dashboard.py calls,
so a fresh clone can rebuild every generated data file locally without any of
them being committed to GitHub. Only source inputs are tracked:
  - data/GasBBActualFlowStorage.CSV            (AEMO Gas Bulletin Board actuals)
  - data/other data/2026 GSOO/.../*.xlsx       (AEMO 2026 GSOO workbooks)
  - data/demand_profiles.csv, lng_parameters.csv, nodes/arcs/supply/... (config)

Pipeline (order matters -- each step feeds the next). The GSOO extract pulls all
three baseline scenarios (Step Change / Accelerated Transition / Slower Growth),
and the GPG/industrial/distribution builders emit one set of demand files per
baseline (e.g. gpg_demand_profile_StepChange.csv, ..._Accelerated.csv, ...):
  1. build_curtailable_demand   GBB -> GPG/industrial base profiles + facilities
  2. build_gsoo_scenarios       GSOO xlsx -> per-scenario annual/regional extracts
  3. build_gpg_demand_gsoo      -> gpg_demand_profile_<scenario>.csv (all baselines)
  4. build_industrial_demand_gsoo -> industrial_demand_profile_<scenario>.csv (+Yarwun)
  5. build_demand_gsoo          -> demand_<scenario>.csv (node distribution + LNG)
  6. build_demand_curves        -> demand-curve blocks for every tier appended to
                                   curtailment_params.csv, plus gpg_capacity.csv.
                                   Must follow step 1, which rewrites that file
                                   with only the GPG and Industrial strikes.

Run the model scenarios afterwards via the "Run All Scenarios (Batch)" button.
"""
import build_curtailable_demand
import build_gsoo_scenarios
import build_gpg_demand_gsoo
import build_industrial_demand_gsoo
import build_demand_gsoo
import build_demand_curves

# (label, callable) in dependency order.
STEPS = [
    ("GBB base profiles (GPG + industrial)", build_curtailable_demand.main),
    ("GSOO scenario extract (3 baselines)", build_gsoo_scenarios.main),
    ("GPG demand (all baselines)", build_gpg_demand_gsoo.build_all),
    ("Industrial demand (all baselines)", build_industrial_demand_gsoo.build_all),
    ("Node distribution + LNG demand (all baselines)", build_demand_gsoo.build_all),
    ("Demand curves (all tiers, both directions) + GPG capacity", build_demand_curves.main),
]


def regenerate_all(progress=None):
    """Rebuild every generated data file from source. `progress`, if given, is
    called as progress(label, fraction) before each step."""
    n = len(STEPS)
    for i, (label, fn) in enumerate(STEPS):
        if progress:
            progress(label, i / n)
        fn()
    if progress:
        progress("Done", 1.0)


if __name__ == "__main__":
    regenerate_all(progress=lambda label, frac: print(f"[{frac*100:3.0f}%] {label}"))
    print("All derived data regenerated.")
