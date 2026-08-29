import pandas as pd
import numpy as np
import os
import results_io
from model import GasMarketModel

def load_data(baseline="StepChange"):
    base_path = os.path.dirname(__file__)
    data_dir = os.path.join(base_path, "data")
    nodes = pd.read_csv(os.path.join(data_dir, "nodes.csv"))
    arcs = pd.read_csv(os.path.join(data_dir, "arcs.csv"))
    supply = pd.read_csv(os.path.join(data_dir, "supply.csv"))
    supply = supply.dropna(subset=['Node', 'Capacity', 'Cost'])
    demand_file = os.path.join(data_dir, f"demand_{baseline}.csv")
    if not os.path.exists(demand_file):
        demand_file = os.path.join(data_dir, "demand_2050.csv")
    demand = pd.read_csv(demand_file)
    expansion = pd.read_csv(os.path.join(data_dir, "expansion_options.csv"))
    return {
        'nodes': nodes, 'arcs': arcs, 'supply': supply,
        'demand': demand, 'expansion': expansion
    }

def run_batch(baselines=("StepChange", "Accelerated", "SlowerGrowth"),
              winter_options=("Low", "Medium", "High"),
              lng_options=("Low", "Medium", "High")):
    start_year, end_year = 2025, 2050

    all_scenarios_results = {}

    total_scenarios = len(baselines) * len(winter_options) * len(lng_options)
    count = 0

    print(f"Starting batch pre-calculation of {total_scenarios} scenarios...")

    for baseline in baselines:
        data = load_data(baseline)
        for winter in winter_options:
            for lng in lng_options:
                count += 1
                scenario_key = (baseline, winter, lng)
                print(f"[{count}/{total_scenarios}] Solving: Baseline={baseline}, Winter={winter}, LNG={lng}")

                built_projects = []
                scenario_results = []

                for year in range(start_year, end_year + 1):
                    demand_mod = data['demand'].copy()

                    # Scenario Logic (match solve.py)
                    winter_mult = {"Low": 1.0, "Medium": 1.5, "High": 2.2}[winter]
                    demand_mod.loc[(demand_mod['Year'] == year) & (demand_mod['Node'].isin(['Melbourne', 'Adelaide', 'Sydney'])) &
                                   (demand_mod['Day'] >= 150) & (demand_mod['Day'] <= 250), 'Demand'] *= winter_mult

                    if year <= 2030:
                        lng_mult = {"Low": 0.7, "Medium": 1.0, "High": 1.3}[lng]
                        demand_mod.loc[(demand_mod['Year'] == year) & (demand_mod['Node'].isin(['APLNG', 'GLNG', 'QCLNG'])), 'Demand'] *= lng_mult


                    model = GasMarketModel(
                        data['nodes'], data['arcs'], data['supply'], demand_mod, data['expansion'],
                        year=year, already_built=built_projects,
                    baseline=baseline
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

    results_io.save({'all_scenarios': all_scenarios_results, 'current_key': None},
                    "src/data/precalculated_results.pkl")
    print("Pre-calculation complete. Results saved to src/data/precalculated_results.pkl")

if __name__ == "__main__":
    import argparse
    import solvers
    parser = argparse.ArgumentParser(description="Pre-calculate every GARY scenario.")
    solvers.add_solver_argument(parser)
    args = parser.parse_args()
    if args.solver:
        solvers.set_solver_name(args.solver)
    print(f"Solver: {solvers.describe()}")
    solvers.require_available()
    run_batch()
