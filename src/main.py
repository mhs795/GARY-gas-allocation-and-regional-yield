import argparse
import pandas as pd
import os
from scenarios import run_base_case, run_high_winter_demand, run_high_lng_demand

def print_summary(results, name):
    print(f"\n{'='*20} {name} {'='*20}")
    
    # Builds
    if results['builds']:
        print("\nRecommended Infrastructure Builds:")
        for build in results['builds']:
            print(f" - {build}")
    else:
        print("\nNo new infrastructure builds recommended.")
        
    # Prices
    price_df = pd.DataFrame(results['prices'])
    if not price_df.empty:
        avg_prices = price_df.groupby('Node')['Price'].mean()
        print("\nAverage Nodal Gas Prices ($/GJ):")
        for node, price in avg_prices.items():
            print(f" - {node:15}: {price:6.2f}")
            
    # Production
    prod_df = pd.DataFrame(results['production'])
    if not prod_df.empty:
        total_prod = prod_df.groupby(['Node', 'Potential'])['Value'].sum() / 1000 # Convert to PJ total for year
        print("\nTotal Annual Production (PJ):")
        for (node, pot), val in total_prod.items():
            pot_str = " (Potential)" if pot else ""
            print(f" - {node + pot_str:25}: {val:6.2f}")
            
    # Shortages
    if results['shortage']:
        short_df = pd.DataFrame(results['shortage'])
        total_short = short_df['Value'].sum()
        print(f"\nTotal System Shortage (TJ): {total_short:.2f}")
    else:
        print("\nTotal System Shortage: 0 TJ")

def main():
    parser = argparse.ArgumentParser(description="Gas Market Nodal Optimization Model")
    parser.add_argument("--scenario", choices=['base', 'high_winter', 'high_lng'], default='base', help="Scenario to run")
    parser.add_argument("--multiplier", type=float, default=1.5, help="Demand multiplier for scenarios")
    
    args = parser.parse_args()
    
    if args.scenario == 'base':
        results = run_base_case()
    elif args.scenario == 'high_winter':
        results = run_high_winter_demand(multiplier=args.multiplier)
    elif args.scenario == 'high_lng':
        results = run_high_lng_demand(multiplier=args.multiplier)
        
    print_summary(results, args.scenario.upper())

if __name__ == "__main__":
    main()
