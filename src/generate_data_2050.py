import pandas as pd
import numpy as np
import os

def generate_long_term_data():
    years = np.arange(2025, 2051)
    # Load base trace
    base_trace = pd.read_csv(os.path.join(os.path.dirname(__file__), 'data', 'demand_profiles.csv'))
    
    yearly_growth = {
        'Sydney': -0.03, 'Melbourne': -0.04, 'Adelaide': -0.02,
        'Brisbane': -0.01, 'APLNG': 0.0, 'GLNG': 0.0, 'QCLNG': 0.0
    }
    
    # Load LNG parameters from spreadsheet
    lng_params = pd.read_csv(os.path.join(os.path.dirname(__file__), 'data', 'lng_parameters.csv')).set_index('Parameter')['Value'].to_dict()
    
    # 1. Aggregate all historical traces to a single 365-day mean shape
    daily_trace = base_trace.groupby(['Day', 'Node'])['Demand'].mean().reset_index()
    vol_mod = float(lng_params.get('volatility', 0.03))
    
    lng_daily_target = lng_params['lng_daily_target']
    lng_nodes = {
        'APLNG': lng_params['aplng_factor'], 
        'GLNG': lng_params['glng_factor'], 
        'QCLNG': lng_params['qclng_factor']
    }
    
    # Get the base trace for APLNG (the cluster shape)
    aplng_trace = daily_trace[daily_trace['Node'] == 'APLNG']
    
    lng_base_mean = aplng_trace['Demand'].mean()
    scaling_factor = lng_daily_target / lng_base_mean
    
    all_demand = []
    
    for year in years:
        years_passed = year - 2025
        
        # Process LNG Nodes
        for _, row in aplng_trace.iterrows():
            day = row['Day']
            base_total = row['Demand']
            
            for lng_node, split in lng_nodes.items():
                val = base_total * scaling_factor * split
                demand_val = val * ((1 + yearly_growth.get(lng_node, 0)) ** years_passed)
                demand_val += np.random.normal(0, vol_mod * demand_val)
                all_demand.append({'Year': year, 'Day': day, 'Node': lng_node, 'Demand': max(0, demand_val)})
        
        # Process Other Nodes
        other_nodes_trace = daily_trace[daily_trace['Node'] != 'APLNG']
        for _, row in other_nodes_trace.iterrows():
            node = row['Node']
            day = row['Day']
            val = row['Demand']
            demand_val = val * ((1 + yearly_growth.get(node, 0)) ** years_passed)
            demand_val += np.random.normal(0, vol_mod * demand_val)
            all_demand.append({'Year': year, 'Day': day, 'Node': node, 'Demand': max(0, demand_val)})
    
    df = pd.DataFrame(all_demand)
    data_dir = os.path.join(os.path.dirname(__file__), 'data')
    df.to_csv(os.path.join(data_dir, "demand_2050.csv"), index=False)
    print(f"Generated demand: Correctly aggregated (mean) and split.")

if __name__ == "__main__":
    generate_long_term_data()
