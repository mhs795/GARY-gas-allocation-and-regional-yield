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

import solvers
from model import LNG_NODES, VOLL_PER_GJ, WINTER_DAYS

# Day-of-year (1..365, non-leap) -> calendar month.
_MONTH_OF_DAY = {}
_d = 1
for _mo in range(1, 13):
    for _ in range(calendar.monthrange(2025, _mo)[1] if _mo != 2 else 28):
        if _d <= 365:
            _MONTH_OF_DAY[_d] = _mo
            _d += 1

PEAK_DAY_WEIGHT = 5.0        # days represented by the annual peak day (adequacy)


def build_representative_days(years, demand_all, gpg_all, ind_all, nodes_df,
                              mm_blocks=(), reserved_all=None, dc_all=None):
    """Reduce each year's 365 daily profiles to representative days.

    Returns {year: [ {weight, demand{node:v}, gpg{node:v}, ind{node:v}} ]} — 12
    monthly *mean* days (weighted by days in the month) plus one annual *peak* day
    (the highest total-demand day, so the capacity layer sizes for stress).

    ``mm_blocks`` is the mass-market step demand curve (see model.py); each rep
    day also carries ``mm{block: {node: TJ available}}``. A block's availability is
    averaged over the bucket's real days rather than derived from the averaged
    demand, so the winter damping — which applies to some days of a month and not
    others — survives the reduction intact.

    ``dc_all`` is the data centre slice of ``ind_all`` — already inside it, not on
    top of it. It rides along as ``dc{node: TJ}`` purely so the investment layer
    can net it out of the industrial expansion headroom, exactly as the dispatch
    layer does: the load is firm, so it must not enlarge the raise blocks.
    """
    node_names = nodes_df['Name'].tolist()
    mm_node_names = [n for n in node_names if n not in LNG_NODES]
    reserved_all = reserved_all or {}
    dc_all = dc_all or {}

    def mm_avail(n, y, d, share, winter_scale):
        """TJ of one mass-market block available at node n on day d."""
        if d in WINTER_DAYS:
            share *= winter_scale
        return demand_all.get((n, y, d), 0) * share

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
            dc = {n: sum(dc_all.get((n, y, d), 0) for d in days) / w for n in node_names}
            mm = {blk: {n: sum(mm_avail(n, y, d, share, ws) for d in days) / w
                        for n in mm_node_names}
                  for blk, _strike, share, ws in mm_blocks}
            reserved = sum(reserved_all.get((y, d), 0.0) for d in days) / w
            days_reps.append({'weight': w, 'demand': dem, 'gpg': gpg, 'ind': ind,
                              'dc': dc, 'mm': mm, 'reserved': reserved})
        # annual peak day (actual profile) for adequacy
        dpk = max(day_total, key=day_total.get)
        days_reps.append({
            'weight': PEAK_DAY_WEIGHT,
            'demand': {n: demand_all.get((n, y, dpk), 0) for n in node_names},
            'gpg':    {n: gpg_all.get((n, y, dpk), 0) for n in node_names},
            'ind':    {n: ind_all.get((n, y, dpk), 0) for n in node_names},
            'dc':     {n: dc_all.get((n, y, dpk), 0) for n in node_names},
            'mm':     {blk: {n: mm_avail(n, y, dpk, share, ws) for n in mm_node_names}
                       for blk, _strike, share, ws in mm_blocks},
            'reserved': reserved_all.get((y, dpk), 0.0),
        })
        rep[y] = days_reps
    return rep


class CapacityExpansionModel:
    """Perfect-foresight investment MIP: build[e, year] over representative days."""

    def __init__(self, nodes_df, arcs_df, supply_df, expansion_df, years, rep,
                 discount_rate=0.07, strike_gpg=22.0, strike_ind=120.0,
                 terminal_earliest=2028, base_year=2025, mm_blocks=(),
                 ind_raise=(), gpg_raise=(), gpg_capacity=None,
                 lng_arcs=(), lng_source=()):
        self.nodes, self.arcs, self.supply, self.expansion = nodes_df, arcs_df, supply_df, expansion_df
        self.years = list(years)
        self.rep = rep                      # {year: [rep-day dicts]}
        self.r = discount_rate
        self.strike_gpg, self.strike_ind = strike_gpg, strike_ind
        # Mass-market step demand curve, same blocks the dispatch layer will face.
        # The investment layer has to see it too: if it sized the network against
        # demand that dispatch then sheds, it would build pipe for gas nobody is
        # willing to pay for.
        self.mm_blocks = list(mm_blocks)
        # Raise-side blocks need no rep-day series of their own: industrial
        # headroom is a share of rd['ind'] and GPG headroom is nameplate less
        # rd['gpg'], both already carried on the representative day.
        self.ind_raise = list(ind_raise)
        # Per-node: {(node, block): (value $/GJ, share of headroom)}. GPG's ladder
        # is regional because what gas displaces differs by jurisdiction.
        self.gpg_raise = dict(gpg_raise)
        self.gpg_capacity = dict(gpg_capacity or {})
        # Reservation: which arcs feed the LNG trains and which node supplies them,
        # so the investment layer sees the same zero-cost reserved tranche and the
        # same export-eligibility rule the dispatch layer will face.
        self.lng_arcs, self.lng_source = list(lng_arcs), list(lng_source)
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

        mm_strike = {b[0]: b[1] for b in self.mm_blocks}
        mm_nodes = sorted({n for y in Y for rd_ in self.rep[y]
                           for blk in rd_.get('mm', {})
                           for n, v in rd_['mm'][blk].items() if v > 0})
        m.MMNodes = pyo.Set(initialize=[n for n in mm_nodes if n in self.nodes['Name'].tolist()])
        m.MMBlocks = pyo.Set(initialize=list(mm_strike))
        m.mm_curtail = pyo.Var(m.MMNodes, m.MMBlocks, m.YR, domain=pyo.NonNegativeReals)

        ind_raise_val = {b[0]: b[1] for b in self.ind_raise}
        ind_raise_share = {b[0]: b[2] for b in self.ind_raise}
        gpg_raise_val = {k: v[0] for k, v in self.gpg_raise.items()}
        gpg_raise_share = {k: v[1] for k, v in self.gpg_raise.items()}
        m.INDRaise = pyo.Set(initialize=list(ind_raise_val))
        m.GPGRaise = pyo.Set(initialize=sorted({b for _, b in gpg_raise_val}))
        m.GPGRaiseNodes = pyo.Set(initialize=sorted(
            {n for (n, _) in gpg_raise_val if n in m.GPGNodes and n in self.gpg_capacity}))
        m.ind_expand = pyo.Var(m.INDNodes, m.INDRaise, m.YR, domain=pyo.NonNegativeReals)
        m.gpg_expand = pyo.Var(m.GPGRaiseNodes, m.GPGRaise, m.YR, domain=pyo.NonNegativeReals)

        def ind_raise_avail(n, b, y, i):
            # Firm data centre load is inside rd['ind'] but does not respond to
            # price, so it is netted out here just as it is in the dispatch model.
            base = (self.rep[y][i]['ind'].get(n, 0)
                    - self.rep[y][i].get('dc', {}).get(n, 0))
            return max(0.0, base) * ind_raise_share[b]

        def gpg_raise_avail(n, b, y, i):
            share = gpg_raise_share.get((n, b))
            if share is None:
                return 0.0
            base = self.rep[y][i]['gpg'].get(n, 0)
            nameplate, cap_mult = self.gpg_capacity.get(n, (0.0, 0.0))
            return max(0.0, min(nameplate - base, cap_mult * base)) * share
        m.build = pyo.Var(m.Expansion, Y, domain=pyo.Binary)   # build project e in year y
        m.reserved_prod = pyo.Var(m.YR, domain=pyo.NonNegativeReals)
        m.reserved_cap = pyo.Constraint(m.YR, rule=lambda m, y, i:
            m.reserved_prod[y, i] <= self.rep[y][i].get('reserved', 0.0))

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
                    + pyo.quicksum(m.shortage[n, y, i] * VOLL_PER_GJ * 1000 for n in m.Nodes)
                    + pyo.quicksum((m.injection[sn, y, i] + m.withdrawal[sn, y, i]) * 0.5 * 1000 for sn in m.StorageNodes)
                    + pyo.quicksum(m.gpg_curtail[n, y, i] * self.strike_gpg * 1000 for n in m.GPGNodes)
                    + pyo.quicksum(m.ind_curtail[n, y, i] * self.strike_ind * 1000 for n in m.INDNodes)
                    + pyo.quicksum(m.mm_curtail[n, b, y, i] * mm_strike[b] * 1000
                                   for n in m.MMNodes for b in m.MMBlocks)
                    # Negative: benefit of demand taken up while gas is cheap.
                    - pyo.quicksum(m.ind_expand[n, b, y, i] * ind_raise_val[b] * 1000
                                   for n in m.INDNodes for b in m.INDRaise)
                    - pyo.quicksum(m.gpg_expand[n, b, y, i] * gpg_raise_val[n, b] * 1000
                                   for n in m.GPGRaiseNodes for b in m.GPGRaise
                                   if (n, b) in gpg_raise_val))
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
                    + (m.reserved_prod[y, i] if n in self.lng_source else 0)
                    + pyo.quicksum(m.flow[a, y, i] for a in arcs_to[n])
                    + (m.withdrawal[n, y, i] - m.injection[n, y, i] if n in m.StorageNodes else 0)
                    + m.shortage[n, y, i]
                    + (m.gpg_curtail[n, y, i] if n in m.GPGNodes else 0)
                    + (m.ind_curtail[n, y, i] if n in m.INDNodes else 0)
                    + (pyo.quicksum(m.mm_curtail[n, b, y, i] for b in m.MMBlocks)
                       if n in m.MMNodes else 0)
                    == r['demand'].get(n, 0) + r['gpg'].get(n, 0) + r['ind'].get(n, 0)
                    + (pyo.quicksum(m.ind_expand[n, b, y, i] for b in m.INDRaise)
                       if n in m.INDNodes else 0)
                    + (pyo.quicksum(m.gpg_expand[n, b, y, i] for b in m.GPGRaise)
                       if n in m.GPGRaiseNodes else 0)
                    + pyo.quicksum(m.flow[a, y, i] for a in arcs_from[n]))
        m.balance = pyo.Constraint(m.Nodes, m.YR, rule=balance_rule)

        m.gpg_cap = pyo.Constraint(m.GPGNodes, m.YR, rule=lambda m, n, y, i: m.gpg_curtail[n, y, i] <= rd(y, i)['gpg'].get(n, 0))
        m.ind_cap = pyo.Constraint(m.INDNodes, m.YR, rule=lambda m, n, y, i: m.ind_curtail[n, y, i] <= rd(y, i)['ind'].get(n, 0))
        m.mm_cap = pyo.Constraint(m.MMNodes, m.MMBlocks, m.YR,
            rule=lambda m, n, b, y, i: m.mm_curtail[n, b, y, i] <= rd(y, i)['mm'].get(b, {}).get(n, 0))
        m.ind_expand_cap = pyo.Constraint(m.INDNodes, m.INDRaise, m.YR,
            rule=lambda m, n, b, y, i: m.ind_expand[n, b, y, i] <= ind_raise_avail(n, b, y, i))
        m.gpg_expand_cap = pyo.Constraint(m.GPGRaiseNodes, m.GPGRaise, m.YR,
            rule=lambda m, n, b, y, i: m.gpg_expand[n, b, y, i] <= gpg_raise_avail(n, b, y, i))

        def supply_cap_rule(m, node, is_pot, y, i):
            cap = supply_dict[node, is_pot]['Capacity']
            if is_pot:
                rel = [e for e in m.Expansion if exp_data[e]['Type'] == 'Terminal' and exp_data[e]['Target'] == node]
                return m.production[node, is_pot, y, i] <= cap * active(rel[0], y) if rel else m.production[node, is_pot, y, i] == 0
            declined = cap * ((1 + supply_dict[node, is_pot].get('DeclineRate', 0)) ** (y - 2025))
            # Reserved gas is a zero-cost slice of the same field, not extra gas.
            if node in self.lng_source:
                return m.production[node, is_pot, y, i] + m.reserved_prod[y, i] <= declined
            return m.production[node, is_pot, y, i] <= declined
        m.supply_cap = pyo.Constraint(m.Supply, m.YR, rule=supply_cap_rule)

        # Exports draw only on commercial gas, so the free reserved gas cannot
        # simply flow to the trains. See the header block in model.py.


        def flow_cap_rule(m, a, y, i):
            extra = pyo.quicksum(active(e, y) * exp_data[e]['NewCapacity'] for e in m.Expansion if exp_data[e]['Target'] == a)
            return m.flow[a, y, i] <= arc_data[a]['Capacity'] + extra
        m.flow_cap = pyo.Constraint(m.Arcs, m.YR, rule=flow_cap_rule)

        # Exports draw only on commercial gas -- everything reaching the source
        # node except the reserved tranche, transit inflows included. See the
        # header block in model.py.
        if self.lng_arcs:
            _commercial = [s_ for s_ in m.Supply if s_[0] in self.lng_source and not s_[1]]
            _inflows = [a for n in self.lng_source for a in arcs_to[n]]
            m.export_eligibility = pyo.Constraint(m.YR, rule=lambda m, y, i:
                pyo.quicksum(m.flow[a, y, i] for a in self.lng_arcs) <=
                pyo.quicksum(m.production[s_[0], s_[1], y, i] for s_ in _commercial)
                + pyo.quicksum(m.flow[a, y, i] for a in _inflows))

        # storage on a representative day: draw down / fill from a half-full store
        m.stor_wd = pyo.Constraint(m.StorageNodes, m.YR, rule=lambda m, sn, y, i: m.withdrawal[sn, y, i] <= 0.5 * storage_caps.get(sn, 0))
        m.stor_inj = pyo.Constraint(m.StorageNodes, m.YR, rule=lambda m, sn, y, i: m.injection[sn, y, i] <= 0.5 * storage_caps.get(sn, 0))

    def solve(self, mip_gap=0.005):
        opt = solvers.make_solver(rel_gap=mip_gap if mip_gap is not None else 0.005,
                                  time_limit=solvers.env_time_limit())
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
