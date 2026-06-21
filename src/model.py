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

        # --- Curtailable large-user demand (GPG + large industrial) ---------
        # Exogenous, empirically-derived (GBB BBGPG / BBLARGE) daily demand added
        # on top of the distribution-level node demand. Each tier is served
        # unless the nodal gas price exceeds its strike price, above which it
        # sheds load instead of being supplied. See build_curtailable_demand.py.
        data_dir = os.path.join(base_path, "data")
        def _load_profile(fname):
            try:
                df = pd.read_csv(os.path.join(data_dir, fname))
                return df.set_index(['Node', 'Day'])['Demand'].to_dict()
            except FileNotFoundError:
                return {}
        self.gpg_demand = _load_profile("gpg_demand_profile.csv")
        self.ind_demand = _load_profile("industrial_demand_profile.csv")
        try:
            strikes = pd.read_csv(os.path.join(data_dir, "curtailment_params.csv")
                                  ).set_index('Tier')['StrikePrice'].to_dict()
        except FileNotFoundError:
            strikes = {}
        self.strike_gpg = float(strikes.get('GPG', 22.0))
        self.strike_ind = float(strikes.get('Industrial', 120.0))
        self.gpg_nodes = sorted({n for (n, _) in self.gpg_demand})
        self.ind_nodes = sorted({n for (n, _) in self.ind_demand})
        
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

        # Curtailable large-user demand: served unless price exceeds strike.
        m.GPGNodes = pyo.Set(initialize=[n for n in self.gpg_nodes if n in self.nodes['Name'].tolist()])
        m.INDNodes = pyo.Set(initialize=[n for n in self.ind_nodes if n in self.nodes['Name'].tolist()])
        m.gpg_curtail = pyo.Var(m.GPGNodes, m.T, domain=pyo.NonNegativeReals)
        m.ind_curtail = pyo.Var(m.INDNodes, m.T, domain=pyo.NonNegativeReals)

        node_demand = self.demand.set_index(['Node', 'Day'])['Demand'].to_dict()
        gpg_dem = self.gpg_demand
        ind_dem = self.ind_demand
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
            # Curtailment penalties = strike price ($/GJ) x 1000 (GJ/TJ). Shedding a
            # tier costs its strike price, so a tier only sheds when the marginal
            # cost of supplying it would exceed that strike (GPG $22 < industrial
            # $120 < mass-market value-of-lost-load $300/GJ).
            gpg_pen = sum(m.gpg_curtail[n, t] * self.strike_gpg * 1000 for n in m.GPGNodes for t in m.T)
            ind_pen = sum(m.ind_curtail[n, t] * self.strike_ind * 1000 for n in m.INDNodes for t in m.T)
            return prod_cost + trans_cost + shortage_penalty + storage_cost + exp_capex + gpg_pen + ind_pen
        m.obj = pyo.Objective(rule=obj_rule, sense=pyo.minimize)

        arcs_to = {n: [a for a in m.Arcs if arc_data[a]['To'] == n] for n in m.Nodes}
        arcs_from = {n: [a for a in m.Arcs if arc_data[a]['From'] == n] for n in m.Nodes}
        supply_at = {n: [s for s in m.Supply if s[0] == n] for n in m.Nodes}

        def balance_rule(m, n, t):
            # Total demand at the node = distribution (mass-market) + GPG + large
            # industrial. GPG/industrial may be shed via their curtail variables.
            return (sum(m.production[s[0], s[1], t] for s in supply_at[n]) +
                    sum(m.flow[a, t] for a in arcs_to[n]) +
                    (m.withdrawal[n, t] - m.injection[n, t] if n in m.StorageNodes else 0) +
                    m.shortage[n, t] +
                    (m.gpg_curtail[n, t] if n in m.GPGNodes else 0) +
                    (m.ind_curtail[n, t] if n in m.INDNodes else 0) ==
                    node_demand.get((n, t), 0) + gpg_dem.get((n, t), 0) + ind_dem.get((n, t), 0) +
                    sum(m.flow[a, t] for a in arcs_from[n]))
        m.balance = pyo.Constraint(m.Nodes, m.T, rule=balance_rule)

        # A tier can shed at most its own demand.
        m.gpg_curtail_cap = pyo.Constraint(m.GPGNodes, m.T,
            rule=lambda m, n, t: m.gpg_curtail[n, t] <= gpg_dem.get((n, t), 0))
        m.ind_curtail_cap = pyo.Constraint(m.INDNodes, m.T,
            rule=lambda m, n, t: m.ind_curtail[n, t] <= ind_dem.get((n, t), 0))

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

        cbc = pyo.SolverFactory('cbc')
        cbc.options['threads'] = 4

        # If all expansion vars are already fixed there are no free binaries —
        # solve as pure LP (much faster, duals available immediately).
        all_fixed = all(m.build[e].is_fixed() for e in m.Expansion)
        if all_fixed:
            m.dual = pyo.Suffix(direction=pyo.Suffix.IMPORT)
            res = cbc.solve(m, tee=False)
            if res.solver.termination_condition in [pyo.TerminationCondition.optimal,
                                                     pyo.TerminationCondition.feasible]:
                return "ok"
            return str(res.solver.termination_condition)

        # MIP solve
        cbc.options['ratioGap'] = mip_gap if mip_gap is not None else 0.005
        res = cbc.solve(m, tee=False)
        if res.solver.termination_condition not in [pyo.TerminationCondition.optimal,
                                                     pyo.TerminationCondition.feasible]:
            return str(res.solver.termination_condition)

        # Fix binary decisions then re-solve as pure LP for dual values (prices)
        for e in m.Expansion:
            m.build[e].fix(pyo.value(m.build[e]))
        m.dual = pyo.Suffix(direction=pyo.Suffix.IMPORT)
        cbc.solve(m, tee=False)
        return "ok"

    def get_results(self):
        m = self.model
        res = {k: [] for k in ['prices', 'production', 'flow', 'storage', 'shortage', 'builds', 'facility_demand', 'gpg', 'industrial']}
        demand_dict = self.demand.set_index(['Node', 'Day'])['Demand'].to_dict()
        supply_at = {n: [s for s in m.Supply if s[0] == n] for n in m.Nodes}

        pv = m.production.get_values()
        fv = m.flow.get_values()
        sv = m.shortage.get_values()
        iv = m.inventory.get_values()
        inj_v = m.injection.get_values()
        wd_v = m.withdrawal.get_values()
        gpg_cv = m.gpg_curtail.get_values()
        ind_cv = m.ind_curtail.get_values()
        
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

            # Curtailable large-user demand: served vs shed
            for n in m.GPGNodes:
                dem = self.gpg_demand.get((n, t), 0)
                cur = float(gpg_cv[n, t] or 0)
                if dem > 0.001:
                    res['gpg'].append({'Day': t, 'Node': n, 'Demand': float(dem), 'Served': float(dem - cur), 'Curtailed': cur})
            for n in m.INDNodes:
                dem = self.ind_demand.get((n, t), 0)
                cur = float(ind_cv[n, t] or 0)
                if dem > 0.001:
                    res['industrial'].append({'Day': t, 'Node': n, 'Demand': float(dem), 'Served': float(dem - cur), 'Curtailed': cur})

            # Keep facility-level demand info
            for fac_name, node in self.facility_node_map.items():
                if node in ['APLNG', 'GLNG', 'QCLNG', 'Sydney', 'Melbourne', 'Adelaide', 'Brisbane']: out_v = demand_dict.get((node, t), 0)
                else: out_v = sum(pv[s[0], s[1], t] for s in supply_at.get(node, [])) if supply_at.get(node) else demand_dict.get((node, t), 0)
                res['facility_demand'].append({'Day': t, 'Facility': fac_name, 'Value': float(out_v)})
        
        for e in m.Expansion:
            if pyo.value(m.build[e]) > 0.5: res['builds'].append(e)
        res['total_cost'] = pyo.value(m.obj)
        return res
