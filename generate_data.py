import pandas as pd
import numpy as np

def generate_demand(filename="gas_market_model/data/demand_profiles.csv"):
    days = np.arange(1, 366)
    nodes = ['Sydney', 'Melbourne', 'Adelaide', 'Brisbane', 'Gladstone']
    
    # Base demands (Average TJ/day)
    base_demand = {
        'Sydney': 250,
        'Melbourne': 400,
        'Adelaide': 100,
        'Brisbane': 150,
        'Gladstone': 1200 # LNG export is large and relatively flat
    }
    
    # Seasonality: Sine wave peaking in winter (July, around day 180-210)
    # Peak-to-base ratio for domestic nodes
    seasonality = {
        'Sydney': 0.4,
        'Melbourne': 0.6,
        'Adelaide': 0.3,
        'Brisbane': 0.1,
        'Gladstone': 0.05
    }
    
    data = []
    for day in days:
        # Season factor: max at day 200 (July)
        season_factor = np.cos(2 * np.pi * (day - 200) / 365)
        
        for node in nodes:
            demand = base_demand[node] * (1 + seasonality[node] * season_factor)
            # Add some daily noise
            demand += np.random.normal(0, 0.02 * base_demand[node])
            data.append({'Day': day, 'Node': node, 'Demand': max(0, demand)})
            
    df = pd.DataFrame(data)
    df.to_csv(filename, index=False)
    print(f"Generated demand profiles in {filename}")

if __name__ == "__main__":
    generate_demand()
