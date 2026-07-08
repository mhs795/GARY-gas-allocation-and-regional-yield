"""
Capacity-expansion layer (perfect foresight) for the two-stage GARY solve.

This is the INVESTMENT model: it co-optimises *what to build and when* across the
whole 2025-2050 horizon with foresight, on a reduced temporal grid (12 monthly
representative days + one annual peak day per year) so it stays small and fast.
The chosen build schedule is then handed to the full-resolution (365-day) dispatch
model in solve.py, which is what produces the reported operations and prices.

Objective is a proper NPV: each year's operating cost is discounted, and each
project's full CapEx is charged once, discounted to its build year.
"""
import calendar
import pyomo.environ as pyo

# Day-of-year (1..365, non-leap) -> calendar month.
_MONTH_OF_DAY = {}
_d = 1
for _mo in range(1, 13):
    for _ in range(calendar.monthrange(2025, _mo)[1] if _mo != 2 else 28):
        if _d <= 365:
            _MONTH_OF_DAY[_d] = _mo
            _d += 1

PEAK_DAY_WEIGHT = 5.0        # days represented by the annual peak day (adequacy)


def build_representative_days(years, demand_all, gpg_all, ind_all, nodes_df):
    """Reduce each year's 365 daily profiles to representative days.

    Returns {year: [ {weight, demand{node:v}, gpg{node:v}, ind{node:v}} ]} — 12
    monthly *mean* days (weighted by days in the month) plus one annual *peak* day
    (the highest total-demand day, so the capacity layer sizes for stress).
    """
    node_names = nodes_df['Name'].tolist()
    rep = {}
    for y in years:
        # bucket days by month
        by_month = {mo: [] for mo in range(1, 13)}
        day_total = {}
        for d in range(1, 366):
            by_month[_MONTH_OF_DAY[d]].append(d)
            day_total[d] = sum(demand_all.get((n, y, d), 0) + gpg_all.get((n, y, d), 0)
                               + ind_all.get((n, y, d), 0) for n in node_names)
        days_reps = []
        for mo, days in by_month.items():
            if not days:
                continue
            w = float(len(days))
            dem = {n: sum(demand_all.get((n, y, d), 0) for d in days) / w for n in node_names}
            gpg = {n: sum(gpg_all.get((n, y, d), 0) for d in days) / w for n in node_names}
            ind = {n: sum(ind_all.get((n, y, d), 0) for d in days) / w for n in node_names}
            days_reps.append({'weight': w, 'demand': dem, 'gpg': gpg, 'ind': ind})
        # annual peak day (actual profile) for adequacy
        dpk = max(day_total, key=day_total.get)
        days_reps.append({
            'weight': PEAK_DAY_WEIGHT,
            'demand': {n: demand_all.get((n, y, dpk), 0) for n in node_names},
            'gpg':    {n: gpg_all.get((n, y, dpk), 0) for n in node_names},
            'ind':    {n: ind_all.get((n, y, dpk), 0) for n in node_names},
        })
        rep[y] = days_reps
    return rep


class CapacityExpansionModel:
    """Perfect-foresight investment MIP: build[e, year] over representative days."""

    def __init__(self, nodes_df, arcs_df, supply_df, expansion_df, years, rep,
                 discount_rate=0.07, strike_gpg=22.0, strike_ind=120.0,
                 terminal_earliest=2028, base_year=2025):
        self.nodes, self.arcs, self.supply, self.expansion = nodes_df, arcs_df, supply_df, expansion_df
        self.years = list(years)
        self.rep = rep                      # {year: [rep-day dicts]}
        self.r = discount_rate
        self.strike_gpg, self.strike_ind = strike_gpg, strike_ind
        self.terminal_earliest = terminal_earliest
        self.base_year = base_year
        self.solved = False

    def build_model(self):
        m = pyo.ConcreteModel(); self.model = m
        Y = self.years
        # representative-day index set per year: (year, r)
        YR = [(y, i) for y in Y for i in range(len(self.rep[y]))]

        m.Nodes = pyo.Set(initialize=self.nodes['Name'].tolist())
        m.Arcs = pyo.Set(initialize=self.arcs['Name'].tolist())
        m.Supply = pyo.Set(initialize=[(r['Node'], r['IsPotential']) for _, r in self.supply.iterrows()], dimen=2)
        m.Expansion = pyo.Set(initialize=self.expansion['Name'].tolist())
        m.StorageNodes = pyo.Set(initialize=self.nodes[self.nodes['StorageCapacity'] > 0]['Name'].tolist())
        m.YR = pyo.Set(initialize=YR, dimen=2)

        gpg_nodes = sorted({n for y in Y for rd in self.rep[y] for n, v in rd['gpg'].items() if v > 0})
        ind_nodes = sorted({n for y in Y for rd in self.rep[y] for n, v in rd['ind'].items() if v > 0})
        m.GPGNodes = pyo.Set(initialize=[n for n in gpg_nodes if n in self.nodes['Name'].tolist()])
        m.INDNodes = pyo.Set(initialize=[n for n in ind_nodes if n in self.nodes['Name'].tolist()])

        m.production = pyo.Var(m.Supply, m.YR, domain=pyo.NonNegativeReals)
        m.flow = pyo.Var(m.Arcs, m.YR, domain=pyo.NonNegativeReals)
        m.shortage = pyo.Var(m.Nodes, m.YR, domain=pyo.NonNegativeReals)
        m.withdrawal = pyo.Var(m.StorageNodes, m.YR, domain=pyo.NonNegativeReals)
        m.injection = pyo.Var(m.StorageNodes, m.YR, domain=pyo.NonNegativeReals)
        m.gpg_curtail = pyo.Var(m.GPGNodes, m.YR, domain=pyo.NonNegativeReals)
        m.ind_curtail = pyo.Var(m.INDNodes, m.YR, domain=pyo.NonNegativeReals)
        m.build = pyo.Var(m.Expansion, Y, domain=pyo.Binary)   # build project e in year y

        arc_data = self.arcs.set_index('Name').to_dict('index')
        supply_dict = self.supply.set_index(['Node', 'IsPotential']).to_dict('index')
        exp_data = self.expansion.set_index('Name').to_dict('index')
        storage_caps = self.nodes.set_index('Name')['StorageCapacity'].to_dict()
        df = {y: 1.0 / ((1 + self.r) ** (y - self.base_year)) for y in Y}
        wt = {(y, i): self.rep[y][i]['weight'] for (y, i) in YR}

        def rd(y, i):
            return self.rep[y][i]

        def active(e, y):     # built in or before y
            return sum(m.build[e, yy] for yy in Y if yy <= y)

        m.build_once = pyo.Constraint(m.Expansion, rule=lambda m, e: sum(m.build[e, y] for y in Y) <= 1)

        # can't build terminals before terminal_earliest
        for e in m.Expansion:
            if exp_data[e]['Type'] == 'Terminal':
                for y in Y:
                    if y < self.terminal_earliest:
                        m.build[e, y].fix(0)

        # --- NPV objective ---------------------------------------------------
        def obj_rule(m):
            ops = pyo.quicksum(
                df[y] * wt[y, i] * (
                    pyo.quicksum(m.production[s[0], s[1], y, i] * supply_dict[s]['Cost'] * 1000 for s in m.Supply)
                    + pyo.quicksum(m.flow[a, y, i] * arc_data[a]['Cost'] * 1000 for a in m.Arcs)
                    + pyo.quicksum(m.shortage[n, y, i] * 300000 for n in m.Nodes)
                    + pyo.quicksum((m.injection[sn, y, i] + m.withdrawal[sn, y, i]) * 0.5 * 1000 for sn in m.StorageNodes)
                    + pyo.quicksum(m.gpg_curtail[n, y, i] * self.strike_gpg * 1000 for n in m.GPGNodes)
                    + pyo.quicksum(m.ind_curtail[n, y, i] * self.strike_ind * 1000 for n in m.INDNodes))
                for (y, i) in YR)
            capex = pyo.quicksum(m.build[e, y] * exp_data[e]['CapEx'] * df[y] for e in m.Expansion for y in Y)
            return ops + capex
        m.obj = pyo.Objective(rule=obj_rule, sense=pyo.minimize)

        arcs_to = {n: [a for a in m.Arcs if arc_data[a]['To'] == n] for n in m.Nodes}
        arcs_from = {n: [a for a in m.Arcs if arc_data[a]['From'] == n] for n in m.Nodes}
        supply_at = {n: [s for s in m.Supply if s[0] == n] for n in m.Nodes}

        def balance_rule(m, n, y, i):
            r = rd(y, i)
            return (pyo.quicksum(m.production[s[0], s[1], y, i] for s in supply_at[n])
                    + pyo.quicksum(m.flow[a, y, i] for a in arcs_to[n])
                    + (m.withdrawal[n, y, i] - m.injection[n, y, i] if n in m.StorageNodes else 0)
                    + m.shortage[n, y, i]
                    + (m.gpg_curtail[n, y, i] if n in m.GPGNodes else 0)
                    + (m.ind_curtail[n, y, i] if n in m.INDNodes else 0)
                    == r['demand'].get(n, 0) + r['gpg'].get(n, 0) + r['ind'].get(n, 0)
                    + pyo.quicksum(m.flow[a, y, i] for a in arcs_from[n]))
        m.balance = pyo.Constraint(m.Nodes, m.YR, rule=balance_rule)

        m.gpg_cap = pyo.Constraint(m.GPGNodes, m.YR, rule=lambda m, n, y, i: m.gpg_curtail[n, y, i] <= rd(y, i)['gpg'].get(n, 0))
        m.ind_cap = pyo.Constraint(m.INDNodes, m.YR, rule=lambda m, n, y, i: m.ind_curtail[n, y, i] <= rd(y, i)['ind'].get(n, 0))

        def supply_cap_rule(m, node, is_pot, y, i):
            cap = supply_dict[node, is_pot]['Capacity']
            if is_pot:
                rel = [e for e in m.Expansion if exp_data[e]['Type'] == 'Terminal' and exp_data[e]['Target'] == node]
                return m.production[node, is_pot, y, i] <= cap * active(rel[0], y) if rel else m.production[node, is_pot, y, i] == 0
            return m.production[node, is_pot, y, i] <= cap * ((1 + supply_dict[node, is_pot].get('DeclineRate', 0)) ** (y - 2025))
        m.supply_cap = pyo.Constraint(m.Supply, m.YR, rule=supply_cap_rule)

        def flow_cap_rule(m, a, y, i):
            extra = pyo.quicksum(active(e, y) * exp_data[e]['NewCapacity'] for e in m.Expansion if exp_data[e]['Target'] == a)
            return m.flow[a, y, i] <= arc_data[a]['Capacity'] + extra
        m.flow_cap = pyo.Constraint(m.Arcs, m.YR, rule=flow_cap_rule)

        # storage on a representative day: draw down / fill from a half-full store
        m.stor_wd = pyo.Constraint(m.StorageNodes, m.YR, rule=lambda m, sn, y, i: m.withdrawal[sn, y, i] <= 0.5 * storage_caps.get(sn, 0))
        m.stor_inj = pyo.Constraint(m.StorageNodes, m.YR, rule=lambda m, sn, y, i: m.injection[sn, y, i] <= 0.5 * storage_caps.get(sn, 0))

    def solve(self, mip_gap=0.005):
        opt = pyo.SolverFactory('appsi_highs')
        opt.options['threads'] = 4
        opt.options['mip_rel_gap'] = mip_gap if mip_gap is not None else 0.005
        res = opt.solve(self.model, tee=False)
        ok = res.solver.termination_condition in (pyo.TerminationCondition.optimal,
                                                   pyo.TerminationCondition.feasible)
        self.solved = ok
        return "ok" if ok else str(res.solver.termination_condition)

    def get_build_schedule(self):
        """Return {project: build_year or None}."""
        m = self.model
        out = {}
        for e in m.Expansion:
            yr = None
            for y in self.years:
                if pyo.value(m.build[e, y]) > 0.5:
                    yr = y
                    break
            out[e] = yr
        return out
