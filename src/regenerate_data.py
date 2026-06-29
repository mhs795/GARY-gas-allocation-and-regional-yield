"""
Regenerate ALL derived model data from the source inputs shipped in the repo.

This is what the "Regenerate Demand Data" button in gui.py / dashboard.py calls,
so a fresh clone can rebuild every generated data file locally without any of
them being committed to GitHub. Only source inputs are tracked:
  - data/GasBBActualFlowStorage.CSV            (AEMO Gas Bulletin Board actuals)
  - data/other data/2026 GSOO/.../*.xlsx       (AEMO 2026 GSOO workbooks)
  - data/demand_profiles.csv, lng_parameters.csv, nodes/arcs/supply/... (config)

Pipeline (order matters -- each step feeds the next):
  1. build_curtailable_demand   GBB -> GPG/industrial base profiles + facilities
  2. build_gsoo_stepchange      GSOO xlsx -> Step Change annual/regional extracts
  3. build_gpg_demand_gsoo      -> gpg_demand_profile_gsoo.csv
  4. build_industrial_demand_gsoo -> industrial_demand_profile_gsoo.csv (+Yarwun)
  5. build_demand_gsoo          -> demand_2050.csv (node distribution + LNG)

Run the model scenarios afterwards via the "Run All Scenarios (Batch)" button.
"""
import build_curtailable_demand
import build_gsoo_stepchange
import build_gpg_demand_gsoo
import build_industrial_demand_gsoo
import build_demand_gsoo

# (label, callable) in dependency order.
STEPS = [
    ("GBB base profiles (GPG + industrial)", build_curtailable_demand.main),
    ("GSOO Step Change extract", build_gsoo_stepchange.main),
    ("GPG demand (GSOO Step Change)", build_gpg_demand_gsoo.build),
    ("Industrial demand (GSOO Step Change)", build_industrial_demand_gsoo.build),
    ("Node distribution + LNG demand (GSOO)", build_demand_gsoo.build),
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
