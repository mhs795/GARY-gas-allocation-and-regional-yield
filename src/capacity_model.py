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

import params as P
import solvers
from model import (IMPORT_NODES, LNG_NODES, VOLL_PER_GJ, WINTER_DAYS,
                   _declined_capacity, _reserves_pj)

# Day-of-year (1..365, non-leap) -> calendar month.
_MONTH_OF_DAY = {}
_d = 1
for _mo in range(1, 13):
    for _ in range(calendar.monthrange(2025, _mo)[1] if _mo != 2 else 28):
        if _d <= 365:
            _MONTH_OF_DAY[_d] = _mo
            _d += 1

# Days represented by the annual peak day (adequacy).
PEAK_DAY_WEIGHT = P.get('peak_day_weight', 5.0)


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
                 discount_rate=None, strike_gpg=None, strike_ind=None,
                 terminal_earliest=None, base_year=None, mm_blocks=(),
                 ind_raise=(), gpg_raise=(), gpg_capacity=None,
                 lng_arcs=(), lng_source=(), netback_by_year=None,
                 import_cost_by_year=None, lng_nameplate=None, foundation_share=0.0,
                 reservation_applied=0.0, respect_contracts=True):
        self.nodes, self.arcs, self.supply, self.expansion = nodes_df, arcs_df, supply_df, expansion_df
        self.years = list(years)
        self.rep = rep                      # {year: [rep-day dicts]}
        # Defaults come from the parameters workbook, not from the signature, so the
        # workbook stays the single place a parameter is set.
        self.r = P.get('discount_rate_default', 0.07) if discount_rate is None else discount_rate
        self.strike_gpg = P.get('strike_gpg_default', 22.0) if strike_gpg is None else strike_gpg
        self.strike_ind = P.get('strike_ind_default', 120.0) if strike_ind is None else strike_ind
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
        # {year: netback $/GJ} when LNG netback price formation is on, else empty.
        # The investment layer has to see it for the same reason it has to see the
        # demand curves: sizing the network against an export volume that dispatch
        # will decline to liquefy would build pipe for gas nobody buys.
        self.netback_by_year = dict(netback_by_year or {})
        # {year: $/GJ} injection cost for regasification terminals under netback
        # pricing. Year-varying because it tracks the international LNG price,
        # unlike a field cost, so it cannot live in the single supply frame.
        self.import_cost_by_year = dict(import_cost_by_year or {})
        # Physical liquefaction nameplate per train, and the take-or-pay share of
        # planned volume. Same split the dispatch layer makes: foundation volume
        # stays must-serve demand, the spot tail bids at the netback up to spare
        # liquefaction capacity.
        self.lng_nameplate = dict(lng_nameplate or {})
        self.foundation_share = float(foundation_share)
        # Same reservation treatment as dispatch: reserved gas comes off the export
        # ceiling, tail first.
        self.reserve_applied = float(reservation_applied or 0.0)
        _uncontracted = max(0.0, 1.0 - self.foundation_share)
        self.reserve_from_foundation = (0.0 if respect_contracts
                                        else max(0.0, self.reserve_applied - _uncontracted))
        self.terminal_earliest = (P.get_int('terminal_earliest', 2028)
                                  if terminal_earliest is None else terminal_earliest)
        self.base_year = (P.get_int('capacity_base_year', 2025)
                          if base_year is None else base_year)
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

        # LNG exports as a bounded willingness-to-pay block at the netback, the
        # same swap the dispatch layer makes. The planned volume sits in
        # rd['demand'] at the train nodes; with the lever on it becomes a ceiling
        # rather than a must-serve quantity, and is netted out of rd['demand'] in
        # balance_rule below.
        self.netback_on = bool(self.netback_by_year)
        m.LNGNodes = pyo.Set(initialize=[n for n in LNG_NODES
                                         if n in self.nodes['Name'].tolist()]
                             if self.netback_on else [])
        m.lng_export = pyo.Var(m.LNGNodes, m.YR, domain=pyo.NonNegativeReals)
        m.lng_export_cap = pyo.Constraint(m.LNGNodes, m.YR, rule=lambda m, n, y, i:
            m.lng_export[n, y, i] <= max(
                0.0, self.lng_nameplate.get(n, 0.0)
                - self.rep[y][i]['demand'].get(n, 0)
                * (self.foundation_share - self.reserve_from_foundation
                   + self.reserve_applied)))

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

        # Rival projects that deliver the SAME capacity cannot both be built.
        # APA's SWP compression and looping options are two ways to reach the one
        # 615 TJ/d Iona injection limit, and the Viva and Vopak terminals are two
        # FSRUs for one Geelong landing point -- without this the solver would take
        # both and book the capacity twice. Groups come from the Group column of
        # expansion_options.csv; a blank Group means the project stands alone.
        groups = {}
        for e in m.Expansion:
            g = exp_data[e].get('Group')
            if isinstance(g, str) and g.strip():
                groups.setdefault(g.strip(), []).append(e)
        if groups:
            m.ExpGroup = pyo.Set(initialize=sorted(groups))
            m.build_group_once = pyo.Constraint(m.ExpGroup, rule=lambda m, g:
                sum(m.build[e, y] for e in groups[g] for y in Y) <= 1)

        # can't build terminals before terminal_earliest
        for e in m.Expansion:
            if exp_data[e]['Type'] == 'Terminal':
                for y in Y:
                    if y < self.terminal_earliest:
                        m.build[e, y].fix(0)

        # --- NPV objective ---------------------------------------------------
        def obj_rule(m):
            def _supply_cost(s_, y):
                """Field cost, or the year's LNG injection cost at an import terminal."""
                if s_[0] in IMPORT_NODES and s_[1] and y in self.import_cost_by_year:
                    return self.import_cost_by_year[y]
                return supply_dict[s_]['Cost']

            ops = pyo.quicksum(
                df[y] * wt[y, i] * (
                    pyo.quicksum(m.production[s[0], s[1], y, i] * _supply_cost(s, y) * 1000 for s in m.Supply)
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
                                   if (n, b) in gpg_raise_val)
                    # Export revenue at that year's netback; bounded above by
                    # lng_export_cap and forced by nothing.
                    - pyo.quicksum(m.lng_export[n, y, i]
                                   * self.netback_by_year.get(y, 0.0) * 1000
                                   for n in m.LNGNodes))
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
                    == (r['demand'].get(n, 0)
                        * max(0.0, self.foundation_share - self.reserve_from_foundation)
                        if n in m.LNGNodes else r['demand'].get(n, 0))
                    + (m.lng_export[n, y, i] if n in m.LNGNodes else 0)
                    + r['gpg'].get(n, 0) + r['ind'].get(n, 0)
                    + (pyo.quicksum(m.ind_expand[n, b, y, i] for b in m.INDRaise)
                       if n in m.INDNodes else 0)
                    + (pyo.quicksum(m.gpg_expand[n, b, y, i] for b in m.GPGRaise)
                       if n in m.GPGRaiseNodes else 0)
                    + pyo.quicksum(m.flow[a, y, i] for a in arcs_from[n]))
        m.balance = pyo.Constraint(m.Nodes, m.YR, rule=balance_rule)

        m.gpg_cap = pyo.Constraint(m.GPGNodes, m.YR, rule=lambda m, n, y, i: m.gpg_curtail[n, y, i] <= rd(y, i)['gpg'].get(n, 0))
        # Firm data centre load is netted out of what the industrial tier may shed,
        # exactly as in the dispatch model. This has to match: the capacity stage
        # chooses the builds that the dispatch stage is then held to, so if it were
        # allowed to assume a data centre stands down at strike_ind while dispatch
        # requires it served or short at VOLL, it would under-build against the very
        # scarcity it is meant to be sizing for.
        m.ind_cap = pyo.Constraint(m.INDNodes, m.YR,
            rule=lambda m, n, y, i: m.ind_curtail[n, y, i] <= max(
                0.0, rd(y, i)['ind'].get(n, 0) - rd(y, i).get('dc', {}).get(n, 0)))
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
                # SUM over the relevant terminals, not just the first. Two rival
                # projects can front the same node -- Viva and Vopak both land at
                # Geelong -- and taking rel[0] would gate the node on whichever
                # happened to be listed first. Each terminal brings its OWN
                # NewCapacity, so a node's ceiling is set by what actually got
                # built; the mutual-exclusion Group stops rivals stacking.
                if not rel:
                    return m.production[node, is_pot, y, i] == 0
                return m.production[node, is_pot, y, i] <= pyo.quicksum(
                    active(e, y) * exp_data[e]['NewCapacity'] for e in rel)
            declined = _declined_capacity(supply_dict[node, is_pot], y, 0.0)
            # Reserved gas is a zero-cost slice of the same field, not extra gas.
            if node in self.lng_source:
                return m.production[node, is_pot, y, i] + m.reserved_prod[y, i] <= declined
            return m.production[node, is_pot, y, i] <= declined
        m.supply_cap = pyo.Constraint(m.Supply, m.YR, rule=supply_cap_rule)

        # RESERVES. Deliverability says how fast a basin can flow; reserves say how
        # much is there at all. Without this the capacity layer sizes the network
        # against an infinite supply of $3.65/GJ Surat gas and never sees a reason
        # to build anything else -- which is exactly what it did. Weighted by the
        # representative days so the sum is annual TJ, then PJ.
        #
        # The dispatch layer steps a basin from its 2P cost to its 2C cost as it
        # depletes (model._supply_cost). This screening layer cannot: the step
        # depends on cumulative production, which is endogenous here, and a step
        # function of a variable is not linear. It therefore prices every tranche at
        # the cheap 2P cost and relies on the stock limit alone. That makes the
        # capacity screen OPTIMISTIC about late-horizon gas relative to the dispatch
        # it hands over to.
        _res = {s_: _reserves_pj(supply_dict[s_]) for s_ in
                [(r['Node'], r['IsPotential']) for _, r in self.supply.iterrows()]}
        m.ReserveSupply = pyo.Set(initialize=[s_ for s_, v in _res.items() if v is not None
                                              and v > 0], dimen=2)
        m.reserve_cap = pyo.Constraint(
            m.ReserveSupply,
            rule=lambda m, n, ip: pyo.quicksum(
                wt[y, i] * m.production[n, ip, y, i] for (y, i) in YR) <= _res[(n, ip)] * 1000.0)

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
