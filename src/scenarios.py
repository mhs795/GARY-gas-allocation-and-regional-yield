import pandas as pd
from model import GasMarketModel

def run_base_case(data_dir="gas_market_model/src/data/"):
    nodes = pd.read_csv(f"{data_dir}nodes.csv")
    arcs = pd.read_csv(f"{data_dir}arcs.csv")
    supply = pd.read_csv(f"{data_dir}supply.csv").dropna(subset=['Node', 'Capacity', 'Cost'])
    demand = pd.read_csv(f"{data_dir}demand_2050.csv")
    expansion = pd.read_csv(f"{data_dir}expansion_options.csv")
    
    model = GasMarketModel(nodes, arcs, supply, demand, expansion)
    model.build_model()
    model.solve()
    return model.get_results()

def run_high_winter_demand(data_dir="gas_market_model/src/data/", multiplier=1.5):
    nodes = pd.read_csv(f"{data_dir}nodes.csv")
    arcs = pd.read_csv(f"{data_dir}arcs.csv")
    supply = pd.read_csv(f"{data_dir}supply.csv").dropna(subset=['Node', 'Capacity', 'Cost'])
    demand = pd.read_csv(f"{data_dir}demand_2050.csv")
    expansion = pd.read_csv(f"{data_dir}expansion_options.csv")
    
    # Increase demand during winter (Days 150 to 240 - May to August)
    demand.loc[(demand['Day'] >= 150) & (demand['Day'] <= 240), 'Demand'] *= multiplier
    
    model = GasMarketModel(nodes, arcs, supply, demand, expansion)
    model.build_model()
    model.solve()
    return model.get_results()

def run_high_lng_demand(data_dir="gas_market_model/src/data/", multiplier=1.2):
    nodes = pd.read_csv(f"{data_dir}nodes.csv")
    arcs = pd.read_csv(f"{data_dir}arcs.csv")
    supply = pd.read_csv(f"{data_dir}supply.csv").dropna(subset=['Node', 'Capacity', 'Cost'])
    demand = pd.read_csv(f"{data_dir}demand_2050.csv")
    expansion = pd.read_csv(f"{data_dir}expansion_options.csv")
    
    # Increase LNG demand at Gladstone cluster
    demand.loc[demand['Node'].isin(['APLNG', 'GLNG', 'QCLNG']), 'Demand'] *= multiplier
    
    model = GasMarketModel(nodes, arcs, supply, demand, expansion)
    model.build_model()
    model.solve()
    return model.get_results()
