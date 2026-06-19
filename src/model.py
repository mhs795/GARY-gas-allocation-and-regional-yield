import pyomo.environ as pyo
import pandas as pd
import numpy as np
import os

class GasMarketModel:
    def __init__(self, nodes_df, arcs_df, supply_df, demand_df, expansion_df, contracts_df=None, year=2025, already_built=None, adgsm_enabled=False):
        self.nodes = nodes_df
        self.arcs = arcs_df
        self.supply = supply_df
        self.demand = demand_df
        self.expansion = expansion_df
        self.contracts = contracts_df
        self.year = year
        self.already_built = already_built if already_built else []
        self.adgsm_enabled = adgsm_enabled
        
        # Load supplemental data for industrial facilities
        base_path = os.path.dirname(__file__)
        self.ind_facs = pd.read_csv(os.path.join(base_path, "data", "industrial_facilities.csv"))
        
        # Optimized Lookups
        self.facility_node_map = self.ind_facs.set_index('FacilityName')['Node'].to_dict()
        # Mappings for specific industrial nodes
        self.facility_node_map.update({
            'APLNG': 'APLNG', 'GLNG': 'GLNG', 'QCLNG': 'QCLNG',
            'Moomba_Processing': 'Moomba', 'Surat_Train': 'Surat', 'Gippsland': 'Gippsland'
        })

    def build_model(self):
        m = pyo.ConcreteModel()
        self.model = m

        m.T = pyo.RangeSet(1, 365)
        m.Nodes = pyo.Set(initialize=self.nodes['Name'].tolist())
        m.Arcs = pyo.Set(initialize=self.arcs['Name'].tolist())
        m.Supply = pyo.Set(initialize=[(row['Node'], row['IsPotential']) for _, row in self.supply.iterrows()], dimen=2)
        m.Expansion = pyo.Set(initialize=self.expansion['Name'].tolist())
        m.StorageNodes = pyo.Set(initialize=self.nodes[self.nodes['StorageCapacity'] > 0]['Name'].tolist())

        m.production = pyo.Var(m.Supply, m.T, domain=pyo.NonNegativeReals)
        m.flow = pyo.Var(m.Arcs, m.T, domain=pyo.NonNegativeReals)
        m.shortage = pyo.Var(m.Nodes, m.T, domain=pyo.NonNegativeReals)
        m.inventory = pyo.Var(m.StorageNodes, m.T, domain=pyo.NonNegativeReals)
        m.injection = pyo.Var(m.StorageNodes, m.T, domain=pyo.NonNegativeReals)
        m.withdrawal = pyo.Var(m.StorageNodes, m.T, domain=pyo.NonNegativeReals)
        m.build = pyo.Var(m.Expansion, domain=pyo.Binary)

        node_demand = self.demand.set_index(['Node', 'Day'])['Demand'].to_dict()
        arc_data = self.arcs.set_index('Name').to_dict('index')
        supply_dict = self.supply.set_index(['Node', 'IsPotential']).to_dict('index')
        exp_data = self.expansion.set_index('Name').to_dict('index')
        storage_caps = self.nodes.set_index('Name')['StorageCapacity'].to_dict()

        def obj_rule(m):
            prod_cost = sum(m.production[s[0], s[1], t] * supply_dict[s]['Cost'] * 1000 for s in m.Supply for t in m.T)
            trans_cost = sum(m.flow[a, t] * arc_data[a]['Cost'] * 1000 for a in m.Arcs for t in m.T)
            shortage_penalty = sum(m.shortage[n, t] * 300000 for n in m.Nodes for t in m.T)
            storage_cost = sum((m.injection[sn, t] + m.withdrawal[sn, t]) * 0.5 * 1000 for sn in m.StorageNodes for t in m.T)
            exp_capex = sum(m.build[e] * exp_data[e]['CapEx'] * 0.08 for e in m.Expansion)
            return prod_cost + trans_cost + shortage_penalty + storage_cost + exp_capex
        m.obj = pyo.Objective(rule=obj_rule, sense=pyo.minimize)

        arcs_to = {n: [a for a in m.Arcs if arc_data[a]['To'] == n] for n in m.Nodes}
        arcs_from = {n: [a for a in m.Arcs if arc_data[a]['From'] == n] for n in m.Nodes}
        supply_at = {n: [s for s in m.Supply if s[0] == n] for n in m.Nodes}

        def balance_rule(m, n, t):
            return (sum(m.production[s[0], s[1], t] for s in supply_at[n]) + 
                    sum(m.flow[a, t] for a in arcs_to[n]) + 
                    (m.withdrawal[n, t] - m.injection[n, t] if n in m.StorageNodes else 0) + 
                    m.shortage[n, t] == node_demand.get((n, t), 0) + 
                    sum(m.flow[a, t] for a in arcs_from[n]))
        m.balance = pyo.Constraint(m.Nodes, m.T, rule=balance_rule)

        def supply_cap_rule(m, node, is_pot, t):
            cap = supply_dict[node, is_pot]['Capacity']
            if is_pot:
                rel_exp = [e for e in m.Expansion if exp_data[e]['Type'] == 'Terminal' and exp_data[e]['Target'] == node]
                return m.production[node, is_pot, t] <= cap * m.build[rel_exp[0]] if rel_exp else m.production[node, is_pot, t] == 0
            return m.production[node, is_pot, t] <= cap * ((1 + supply_dict[node, is_pot].get('DeclineRate', 0)) ** (self.year - 2025))
        m.supply_cap = pyo.Constraint(m.Supply, m.T, rule=supply_cap_rule)

        def flow_cap_rule(m, a, t):
            extra = sum(m.build[e] * exp_data[e]['NewCapacity'] for e in m.Expansion if exp_data[e]['Target'] == a)
            return m.flow[a, t] <= arc_data[a]['Capacity'] + extra
        m.flow_cap = pyo.Constraint(m.Arcs, m.T, rule=flow_cap_rule)

        def storage_cont_rule(m, sn, t):
            cap = storage_caps.get(sn, 0)
            if t == 1: return m.inventory[sn, t] == (cap * 0.5) + m.injection[sn, t] - m.withdrawal[sn, t]
            return m.inventory[sn, t] == m.inventory[sn, t-1] + m.injection[sn, t] - m.withdrawal[sn, t]
        m.storage_cont = pyo.Constraint(m.StorageNodes, m.T, rule=storage_cont_rule)

        def storage_cap_rule(m, sn, t):
            return m.inventory[sn, t] <= storage_caps.get(sn, 0)
        m.storage_cap = pyo.Constraint(m.StorageNodes, m.T, rule=storage_cap_rule)

        # if self.adgsm_enabled:
        #     def reservation_rule(m, t):
        #         lng_flow = sum(m.flow[a, t] for a in m.Arcs if arc_data[a]['To'] in ['APLNG', 'GLNG', 'QCLNG'])
        #         return lng_flow <= sum(m.production[s[0], s[1], t] for s in supply_at['Surat']) * 0.85
        #     m.reservation = pyo.Constraint(m.T, rule=reservation_rule)

        for e in self.already_built: m.build[e].fix(1)
        if self.year < 2028:
            for e in m.Expansion:
                if 'Terminal' in e: m.build[e].fix(0)

    def solve(self, mip_gap=0.005):
        m = self.model
        base_path = os.path.dirname(__file__)
        log_path = os.path.join(base_path, "..", "solver_log.txt")
        os.environ['TMPDIR'] = os.path.abspath(os.path.join(base_path, "..", "tmp"))
        solver = pyo.SolverFactory('cbc')
        if mip_gap is not None: solver.options['ratioGap'] = mip_gap
        solver.options['threads'] = 4
        res = solver.solve(m, tee=False)
        if res.solver.termination_condition in [pyo.TerminationCondition.optimal, pyo.TerminationCondition.feasible]:
            for e in m.Expansion: m.build[e].fix(pyo.value(m.build[e]))
            m.dual = pyo.Suffix(direction=pyo.Suffix.IMPORT)
            solver.solve(m, tee=False)
            return "ok"
        return str(res.solver.termination_condition)

    def get_results(self):
        m = self.model
        res = {k: [] for k in ['prices', 'production', 'flow', 'storage', 'shortage', 'builds', 'facility_demand']}
        demand_dict = self.demand.set_index(['Node', 'Day'])['Demand'].to_dict()
        supply_at = {n: [s for s in m.Supply if s[0] == n] for n in m.Nodes}
        
        pv = m.production.get_values()
        fv = m.flow.get_values()
        sv = m.shortage.get_values()
        iv = m.inventory.get_values()
        inj_v = m.injection.get_values()
        wd_v = m.withdrawal.get_values()
        
        for t in m.T:
            for n in m.Nodes:
                p = (m.dual[m.balance[n, t]]/1000 ) if hasattr(m, 'dual') and m.balance[n, t] in m.dual else 0.0
                res['prices'].append({'Day': t, 'Node': n, 'Price': float(p)})
                if sv[n, t] > 0.1: res['shortage'].append({'Day': t, 'Node': n, 'Value': float(sv[n, t])})
            for s in m.Supply:
                if pv[s[0], s[1], t] > 0.01: res['production'].append({'Day': t, 'Node': s[0], 'Potential': s[1], 'Value': float(pv[s[0], s[1], t])})
            for a in m.Arcs:
                if fv[a, t] > 0.01:
                    row = self.arcs[self.arcs['Name'] == a].iloc[0]
                    res['flow'].append({'Day': t, 'Arc': a, 'From': row['From'], 'To': row['To'], 'Value': float(fv[a, t])})
            for sn in m.StorageNodes: res['storage'].append({'Day': t, 'Node': sn, 'Inventory': float(iv[sn, t]), 'Injection': float(inj_v[sn, t]), 'Withdrawal': float(wd_v[sn, t])})
            
            # Keep facility-level demand info
            for fac_name, node in self.facility_node_map.items():
                if node in ['APLNG', 'GLNG', 'QCLNG', 'Sydney', 'Melbourne', 'Adelaide', 'Brisbane']: out_v = demand_dict.get((node, t), 0)
                else: out_v = sum(pv[s[0], s[1], t] for s in supply_at.get(node, [])) if supply_at.get(node) else demand_dict.get((node, t), 0)
                res['facility_demand'].append({'Day': t, 'Facility': fac_name, 'Value': float(out_v)})
        
        for e in m.Expansion:
            if pyo.value(m.build[e]) > 0.5: res['builds'].append(e)
        res['total_cost'] = pyo.value(m.obj)
        return res
