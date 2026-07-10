import pandas as pd
import os
import time
from model import GasMarketModel

def load_data(baseline="StepChange"):
    base_path = os.path.dirname(__file__)
    data_dir = os.path.join(base_path, "data")
    # Node distribution + LNG demand for the chosen GSOO baseline; fall back to the
    # legacy StepChange alias (demand_2050.csv) if the per-baseline file is absent.
    demand_file = os.path.join(data_dir, f"demand_{baseline}.csv")
    if not os.path.exists(demand_file):
        demand_file = os.path.join(data_dir, "demand_2050.csv")
    return {
        'nodes': pd.read_csv(os.path.join(data_dir, "nodes.csv")),
        'arcs': pd.read_csv(os.path.join(data_dir, "arcs.csv")),
        'supply': pd.read_csv(os.path.join(data_dir, "supply.csv")).dropna(subset=['Node', 'Capacity', 'Cost']),
        'demand': pd.read_csv(demand_file),
        'expansion': pd.read_csv(os.path.join(data_dir, "expansion_options.csv")),
        'contracts': pd.read_csv(os.path.join(data_dir, "contracts.csv"))
    }

def get_lng_mult(scenario, year):
    # Constants
    HIGH_LNG_START = 2026
    HIGH_LNG_END = 2030
    if scenario == "Low":
        if year <= 2025: return 1.0
        elif year <= 2030: return 1.0 - (year - 2025) * 0.04
        elif year <= 2040: return 0.8 - (year - 2030) * 0.03
        else: return max(0.2, 0.5 - (year - 2040) * 0.03)
    elif scenario == "High":
        if HIGH_LNG_START <= year <= HIGH_LNG_END: return 1.6
        else: return 1.1
    return 1.0

def _year_demand(data, year, winter, lng):
    """Mass-market demand for one year with the Winter and LNG levers applied."""
    dm = data['demand'].copy()
    winter_mult = {"Low": 1.0, "Medium": 1.5, "High": 2.2}[winter]
    dm.loc[(dm['Year'] == year) & (dm['Node'].isin(['Melbourne', 'Adelaide', 'Sydney'])) &
           (dm['Day'] >= 150) & (dm['Day'] <= 250), 'Demand'] *= winter_mult
    lng_mult = get_lng_mult(lng, year)
    dm.loc[(dm['Year'] == year) & (dm['Node'].isin(['APLNG', 'GLNG', 'QCLNG'])), 'Demand'] *= lng_mult
    return dm[dm['Year'] == year].copy()


def solve_scenario(winter, lng, adgsm_enabled=False, mip_gap=0.005, callback=None,
                   baseline="StepChange", dunkelflaute=False, discount_rate=0.07,
                   foresight=True):
    """Solve a scenario over 2025-2050.

    ``foresight=True`` (default) uses the two-stage full-horizon method: a
    perfect-foresight capacity model chooses builds across the whole horizon, then
    each year is dispatched at full 365-day resolution with those builds fixed.

    ``foresight=False`` uses the myopic year-by-year method: each year decides its
    own builds reactively (no knowledge of future years), carrying built projects
    forward. Preferred for *unanticipated* shocks (e.g. a price-shock or dunkelflaute
    scenario) where perfect foresight would unrealistically pre-build ahead of it.
    """
    data = load_data(baseline)
    years = list(range(2025, 2051))
    if foresight:
        return _solve_foresight(data, years, winter, lng, adgsm_enabled, baseline,
                                dunkelflaute, mip_gap, discount_rate, callback)
    return _solve_myopic(data, years, winter, lng, adgsm_enabled, baseline,
                         dunkelflaute, mip_gap, callback)


def _solve_myopic(data, years, winter, lng, adgsm_enabled, baseline, dunkelflaute, mip_gap, callback):
    """Reactive year-by-year solve: each year decides builds with no foresight."""
    contracts_all = data['contracts']
    built_projects, results = [], []
    for i, year in enumerate(years):
        if callback:
            callback(year, i / len(years))
        demand_yr = _year_demand(data, year, winter, lng)
        gm = GasMarketModel(
            data['nodes'], data['arcs'], data['supply'], demand_yr, data['expansion'],
            contracts_df=contracts_all if year <= 2040 else None, year=year,
            already_built=built_projects, adgsm_enabled=adgsm_enabled,
            baseline=baseline, dunkelflaute=dunkelflaute)
        gm.build_model()
        status = gm.solve(mip_gap=mip_gap)
        if status != "ok":
            raise RuntimeError(f"Solver failed in {year}: {status}")
        yr_res = gm.get_results()
        yr_res['Year'] = year
        results.append(yr_res)
        built_projects.extend([b for b in yr_res['builds'] if b not in built_projects])
        print(f"Year {year} complete")
    if callback:
        callback(years[-1], 1.0)
    return results


def _solve_foresight(data, years, winter, lng, adgsm_enabled, baseline, dunkelflaute, mip_gap, discount_rate, callback):
    """Two-stage full-horizon solve: perfect-foresight capacity + 365-day dispatch."""
    from capacity_model import CapacityExpansionModel, build_representative_days
    contracts_all = data['contracts']
    start_year, end_year = years[0], years[-1]

    # --- Pass 1: assemble every year's demand + GPG/industrial (events applied) ---
    dispatch_models, demand_all, gpg_all, ind_all = {}, {}, {}, {}
    for year in years:
        demand_yr = _year_demand(data, year, winter, lng)
        gm = GasMarketModel(
            data['nodes'], data['arcs'], data['supply'], demand_yr, data['expansion'],
            contracts_df=contracts_all if year <= 2040 else None, year=year,
            adgsm_enabled=adgsm_enabled, baseline=baseline, dunkelflaute=dunkelflaute)
        dispatch_models[year] = gm
        for (n, d), v in demand_yr.set_index(['Node', 'Day'])['Demand'].to_dict().items():
            demand_all[(n, year, d)] = v
        for (n, d), v in gm.gpg_demand.items():
            gpg_all[(n, year, d)] = v
        for (n, d), v in gm.ind_demand.items():
            ind_all[(n, year, d)] = v

    # --- Pass 2: capacity expansion, perfect foresight over the horizon ----------
    if callback:
        callback(start_year, 0.0)
    rep = build_representative_days(years, demand_all, gpg_all, ind_all, data['nodes'])
    cap = CapacityExpansionModel(
        data['nodes'], data['arcs'], data['supply'], data['expansion'], years, rep,
        discount_rate=discount_rate,
        strike_gpg=dispatch_models[start_year].strike_gpg,
        strike_ind=dispatch_models[start_year].strike_ind)
    cap.build_model()
    status = cap.solve(mip_gap=mip_gap)
    if status != "ok":
        raise RuntimeError(f"Capacity model failed: {status}")
    build_year = cap.get_build_schedule()
    active_by_year = {y: {e for e, by in build_year.items() if by is not None and by <= y} for y in years}

    # --- Pass 3: full 365-day dispatch each year with builds fixed ----------------
    scenario_results = []
    for i, year in enumerate(years):
        if callback:
            callback(year, (i + 1) / len(years))
        gm = dispatch_models[year]
        gm.builds_fixed = active_by_year[year]
        gm.build_model()
        status = gm.solve(mip_gap=mip_gap)
        if status != "ok":
            raise RuntimeError(f"Dispatch failed in {year}: {status}")
        yr_res = gm.get_results()
        yr_res['Year'] = year
        scenario_results.append(yr_res)
        print(f"Year {year} complete")

    if callback:
        callback(end_year, 1.0)
    return scenario_results
