import pandas as pd
import os
import time
from model import GasMarketModel

def load_data():
    base_path = os.path.dirname(__file__)
    data_dir = os.path.join(base_path, "data")
    return {
        'nodes': pd.read_csv(os.path.join(data_dir, "nodes.csv")),
        'arcs': pd.read_csv(os.path.join(data_dir, "arcs.csv")),
        'supply': pd.read_csv(os.path.join(data_dir, "supply.csv")).dropna(subset=['Node', 'Capacity', 'Cost']),
        'demand': pd.read_csv(os.path.join(data_dir, "demand_2050.csv")),
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

def solve_scenario(winter, lng, adgsm_enabled=False, mip_gap=0.005, callback=None):
    data = load_data()
    built_projects = []
    scenario_results = []
    
    # Load contracts once
    contracts_all = data['contracts']
    
    start_year, end_year = 2025, 2050
    total_years = end_year - start_year + 1

    for year in range(start_year, end_year + 1):
        if callback:
            progress = (year - start_year) / total_years
            callback(year, progress)

        demand_mod = data['demand'].copy()
        
        # Winter Logic
        winter_mult = {"Low": 1.0, "Medium": 1.5, "High": 2.2}[winter]
        demand_mod.loc[(demand_mod['Year'] == year) & 
                       (demand_mod['Node'].isin(['Melbourne', 'Adelaide', 'Sydney'])) & 
                       (demand_mod['Day'] >= 150) & (demand_mod['Day'] <= 250), 'Demand'] *= winter_mult
        
        # LNG Logic
        lng_mult = get_lng_mult(lng, year)
        demand_mod.loc[(demand_mod['Year'] == year) & (demand_mod['Node'].isin(['APLNG', 'GLNG', 'QCLNG'])), 'Demand'] *= lng_mult
        
        # Filter demand to current year
        demand_yr = demand_mod[demand_mod['Year'] == year].copy()
        
        model = GasMarketModel(
            data['nodes'], data['arcs'], data['supply'], demand_yr, data['expansion'], 
            contracts_df=contracts_all if year <= 2040 else None,
            year=year, 
            already_built=built_projects, 
            adgsm_enabled=adgsm_enabled
        )
        model.build_model()
        
        status = model.solve(mip_gap=mip_gap)
        if status != "ok":
            raise RuntimeError(f"Solver failed in {year} with condition: {status}")
        
        yr_res = model.get_results()
        yr_res['Year'] = year
        scenario_results.append(yr_res)
        
        # Update memory for next year
        built_projects.extend([b for b in yr_res['builds'] if b not in built_projects])
        
        print(f"Year {year} complete (Gap: {mip_gap})")
    
    if callback:
        callback(2050, 1.0)
        
    return scenario_results
