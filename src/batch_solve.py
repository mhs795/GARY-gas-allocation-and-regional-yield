import pandas as pd
import numpy as np
import os
import pickle
from model import GasMarketModel

def load_data():
    base_path = os.path.dirname(__file__)
    data_dir = os.path.join(base_path, "data")
    nodes = pd.read_csv(os.path.join(data_dir, "nodes.csv"))
    arcs = pd.read_csv(os.path.join(data_dir, "arcs.csv"))
    supply = pd.read_csv(os.path.join(data_dir, "supply.csv"))
    supply = supply.dropna(subset=['Node', 'Capacity', 'Cost'])
    demand = pd.read_csv(os.path.join(data_dir, "demand_2050.csv"))
    expansion = pd.read_csv(os.path.join(data_dir, "expansion_options.csv"))
    contracts = pd.read_csv(os.path.join(data_dir, "contracts.csv"))
    return {
        'nodes': nodes, 'arcs': arcs, 'supply': supply,
        'demand': demand, 'expansion': expansion, 'contracts': contracts
    }

def run_batch():
    data = load_data()
    start_year, end_year = 2025, 2050
    
    adgsm_options = [False, True]
    winter_options = ["Low", "Medium", "High"]
    lng_options = ["Low", "Medium", "High"]
    
    all_scenarios_results = {}
    
    total_scenarios = len(adgsm_options) * len(winter_options) * len(lng_options)
    count = 0
    
    print(f"Starting batch pre-calculation of {total_scenarios} scenarios...")
    
    for adgsm in adgsm_options:
        for winter in winter_options:
            for lng in lng_options:
                count += 1
                scenario_key = (adgsm, winter, lng)
                print(f"[{count}/{total_scenarios}] Solving: ADGSM={adgsm}, Winter={winter}, LNG={lng}")
                
                built_projects = []
                scenario_results = []
                
                for year in range(start_year, end_year + 1):
                    demand_mod = data['demand'].copy()
                    
                    # Scenario Logic (Match gui.py)
                    winter_mult = {"Low": 1.0, "Medium": 1.5, "High": 2.2}[winter]
                    demand_mod.loc[(demand_mod['Year'] == year) & (demand_mod['Node'].isin(['Melbourne', 'Adelaide', 'Sydney'])) & 
                                   (demand_mod['Day'] >= 150) & (demand_mod['Day'] <= 250), 'Demand'] *= winter_mult
                    
                    if year <= 2030:
                        lng_mult = {"Low": 0.7, "Medium": 1.0, "High": 1.3}[lng]
                        demand_mod.loc[(demand_mod['Year'] == year) & (demand_mod['Node'].isin(['APLNG', 'GLNG', 'QCLNG'])), 'Demand'] *= lng_mult
                    
                    active_contracts = data['contracts'] if year <= 2040 else None
                    
                    model = GasMarketModel(
                        data['nodes'], data['arcs'], data['supply'], demand_mod, data['expansion'], 
                        contracts_df=active_contracts, year=year, already_built=built_projects, adgsm_enabled=adgsm
                    )
                    model.build_model()
                    solve_status = model.solve()
                    
                    year_results = model.get_results()
                    if not year_results['solved']:
                        print(f"  FAILED at Year {year}")
                        break
                    
                    year_results['Year'] = year
                    scenario_results.append(year_results)
                    # Update built projects for next year
                    built_projects.extend([b for b in year_results['builds'] if b not in built_projects])
                
                all_scenarios_results[scenario_key] = scenario_results

    # Save to compressed pickle
    with open("src/data/precalculated_results.pkl", "wb") as f:
        pickle.dump(all_scenarios_results, f)
    print("Pre-calculation complete. Results saved to src/data/precalculated_results.pkl")

if __name__ == "__main__":
    run_batch()
