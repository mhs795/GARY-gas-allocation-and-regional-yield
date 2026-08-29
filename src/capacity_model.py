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
from model import (IMPORT_NODES, LNG_NODES, STORAGE_CYCLE_COST, STORAGE_OPENING,
                   VOLL_PER_GJ, WINTER_DAYS,
                   _declined_capacity)

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
                              reserved_all=None, dc_all=None):
    """Reduce each year's 365 daily profiles to representative days.

    Returns {year: [ {weight, demand{node:v}, gpg{node:v}, ind{node:v}} ]} — 12
    monthly *mean* days (weighted by days in the month) plus one annual *peak* day
    (the highest total-demand day, so the capacity layer sizes for stress).

    ``dc_all`` is the data centre slice of ``ind_all`` — already inside it, not on
    top of it. It rides along as ``dc{node: TJ}`` purely so the investment layer
    can net it out of the industrial expansion headroom, exactly as the dispatch
    layer does: the load is firm, so it must not enlarge the raise blocks.
    """
    node_names = nodes_df['Name'].tolist()
    reserved_all = reserved_all or {}
    dc_all = dc_all or {}

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
            reserved = sum(reserved_all.get((y, d), 0.0) for d in days) / w
            days_reps.append({'weight': w, 'demand': dem, 'gpg': gpg, 'ind': ind,
                              'dc': dc, 'reserved': reserved})
        # annual peak day (actual profile) for adequacy
        dpk = max(day_total, key=day_total.get)
        days_reps.append({
            'weight': PEAK_DAY_WEIGHT,
            'demand': {n: demand_all.get((n, y, dpk), 0) for n in node_names},
            'gpg':    {n: gpg_all.get((n, y, dpk), 0) for n in node_names},
            'ind':    {n: ind_all.get((n, y, dpk), 0) for n in node_names},
            'dc':     {n: dc_all.get((n, y, dpk), 0) for n in node_names},
            'reserved': reserved_all.get((y, dpk), 0.0),
        })
        rep[y] = days_reps
    return rep


class CapacityExpansionModel:
    """Perfect-foresight investment MIP: build[e, year] over representative days."""

    def __init__(self, nodes_df, arcs_df, supply_df, expansion_df, years, rep,
                 discount_rate=None, strike_gpg=None, strike_ind=None,
                 terminal_earliest=None, base_year=None,
                 lng_arcs=(), lng_source=(), netback_by_year=None,
                 import_cost_by_year=None, lng_nameplate=None, foundation_share=0.0,
                 reservation_applied=0.0, respect_contracts=True,
                 supply_cost_by_year=None):
        self.nodes, self.arcs, self.supply, self.expansion = nodes_df, arcs_df, supply_df, expansion_df
        self.years = list(years)
        self.rep = rep                      # {year: [rep-day dicts]}
        # Defaults come from the parameters workbook, not from the signature, so the
        # workbook stays the single place a parameter is set.
        self.r = P.get('discount_rate_default', 0.07) if discount_rate is None else discount_rate
        self.strike_gpg = P.get('strike_gpg_default', 22.0) if strike_gpg is None else strike_gpg
        self.strike_ind = P.get('strike_ind_default', 120.0) if strike_ind is None else strike_ind
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
        # {(node, is_potential, year): $/GJ} once depletion is known -- see
        # solve.py's two-pass capacity solve. WITHOUT THIS the investment layer
        # prices every basin at its 2P cost for all 26 years while dispatch steps
        # it to 2C, so the MIP chooses builds against a cost path the dispatch it
        # governs will never see, and under-builds the southern relief the cost
        # step is meant to make economic. Empty on the first pass.
        self.supply_cost_by_year = dict(supply_cost_by_year or {})
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

        # EARLIEST BUILD YEAR, per project. terminal_earliest used to be the only
        # timing rule and it gated Type=Terminal only, so a pipeline candidate with
        # a stated 2030s date -- NEAP -- could be built in 2026. A row's own
        # EarliestYear wins where it has one; terminal_earliest stays as the
        # fallback floor for terminals that do not.
        def _earliest(e):
            v = exp_data[e].get('EarliestYear')
            try:
                v = int(v)
            except (TypeError, ValueError):
                v = None
            if v is None and exp_data[e]['Type'] == 'Terminal':
                v = self.terminal_earliest
            return v
        for e in m.Expansion:
            lo = _earliest(e)
            if lo is not None:
                for y in Y:
                    if y < lo:
                        m.build[e, y].fix(0)

        # COMMITTED PROJECTS ARE BUILT. The Status column carried
        # Committed/Pre-FID/Proposed/Built from the start and no code read it, so a
        # project with FID taken and steel in the ground was optimised on exactly
        # the same terms as a speculative one -- and ECGG_3A_MSP and EGP_Reversal
        # were both dropped when the arcs moved to posted tariffs. A commitment is
        # not a choice the model gets to make, so these are forced in by their
        # stated year. It also neutralises the brownfield tariff bias (TODO item 1)
        # for exactly the projects where that bias is worst, since a forced build
        # does not care that its economics are understated.
        _committed = [e for e in m.Expansion
                      if str(exp_data[e].get('Status', '')).strip() == 'Committed'
                      and _earliest(e) is not None]
        if _committed:
            m.build_committed = pyo.Constraint(
                pyo.Set(initialize=_committed), rule=lambda m, e: sum(
                    m.build[e, y] for y in Y if y <= max(_earliest(e), Y[0])) == 1)

        # --- NPV objective ---------------------------------------------------
        def obj_rule(m):
            def _supply_cost(s_, y):
                """Field cost in year y, or the LNG injection cost at an import terminal.

                Field cost comes from supply_cost_by_year where a depletion path is
                known, so the MIP sees the same 2P -> 2C step the dispatch layer
                will apply. Falls back to the flat base cost on the first pass.
                """
                if s_[0] in IMPORT_NODES and s_[1] and y in self.import_cost_by_year:
                    return self.import_cost_by_year[y]
                stepped = self.supply_cost_by_year.get((s_[0], s_[1], y))
                if stepped is not None:
                    return stepped
                return supply_dict[s_]['Cost']

            ops = pyo.quicksum(
                df[y] * wt[y, i] * (
                    pyo.quicksum(m.production[s[0], s[1], y, i] * _supply_cost(s, y) * 1000 for s in m.Supply)
                    + pyo.quicksum(m.flow[a, y, i] * arc_data[a]['Cost'] * 1000 for a in m.Arcs)
                    + pyo.quicksum(m.shortage[n, y, i] * VOLL_PER_GJ * 1000 for n in m.Nodes)
                    + pyo.quicksum((m.injection[sn, y, i] + m.withdrawal[sn, y, i])
                                   * STORAGE_CYCLE_COST * 1000 for sn in m.StorageNodes)
                    + pyo.quicksum(m.gpg_curtail[n, y, i] * self.strike_gpg * 1000 for n in m.GPGNodes)
                    + pyo.quicksum(m.ind_curtail[n, y, i] * self.strike_ind * 1000 for n in m.INDNodes)
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
                    == (r['demand'].get(n, 0)
                        * max(0.0, self.foundation_share - self.reserve_from_foundation)
                        if n in m.LNGNodes else r['demand'].get(n, 0))
                    + (m.lng_export[n, y, i] if n in m.LNGNodes else 0)
                    + r['gpg'].get(n, 0) + r['ind'].get(n, 0)
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

        # NO cumulative reserve constraint here, deliberately -- the cost STEP is
        # carried instead, via supply_cost_by_year (see __init__). It was tried on
        # 28 Aug 2026 and removed the same day: with no backfill supply in GARY
        # (Surat_Potential cannot produce, Gippsland_Potential only unlocks via a
        # Golden_Beach nobody builds) a hard stock limit starves the south and the
        # dispatch layer then prices at value-of-lost-load. Depletion is carried as
        # a COST step instead -- see model._supply_cost and _declined_capacity.

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

        # Storage on a representative day. This layer carries no inventory state,
        # so the bounds have to do two jobs: cap the DAILY rate at the facility's
        # published plant rating, and stop the year as a whole drawing the store
        # down. The old bound did neither -- it allowed half the store's entire
        # volume to move on every representative day of every year, which let the
        # investment layer size the network against gas that does not exist and so
        # under-build southern relief.
        _sn = self.nodes.set_index('Name')
        inj_max = _sn['MaxInjection'].to_dict()
        wd_max = _sn['MaxWithdrawal'].to_dict()
        m.stor_wd = pyo.Constraint(m.StorageNodes, m.YR, rule=lambda m, sn, y, i:
            m.withdrawal[sn, y, i] <= float(wd_max.get(sn, 0) or 0))
        m.stor_inj = pyo.Constraint(m.StorageNodes, m.YR, rule=lambda m, sn, y, i:
            m.injection[sn, y, i] <= float(inj_max.get(sn, 0) or 0))
        # Net annual withdrawal cannot exceed what the store held to begin with:
        # the investment layer's equivalent of the dispatch layer's closed year.
        m.stor_annual = pyo.Constraint(m.StorageNodes, Y, rule=lambda m, sn, y:
            pyo.quicksum((m.withdrawal[sn, y, i] - m.injection[sn, y, i]) * wt[y, i]
                         for (yy, i) in YR if yy == y) <= STORAGE_OPENING * storage_caps.get(sn, 0))

    def solve(self, mip_gap=0.005):
        opt = solvers.make_solver(rel_gap=mip_gap if mip_gap is not None else 0.005,
                                  time_limit=solvers.env_time_limit())
        res = opt.solve(self.model, tee=False)
        ok = res.solver.termination_condition in (pyo.TerminationCondition.optimal,
                                                   pyo.TerminationCondition.feasible)
        self.solved = ok
        return "ok" if ok else str(res.solver.termination_condition)

    def get_annual_production_pj(self):
        """Implied annual production per field, ``{(node, is_potential): {year: PJ}}``.

        Representative-day production weighted by the days each day stands for.
        Total weight is 370 rather than 365 (the peak day carries PEAK_DAY_WEIGHT
        on top of twelve full months), so this runs ~1.4% high -- fine for
        deciding WHEN a basin crosses its 2P reserves, which is all it is used for.
        """
        m = self.model
        out = {}
        for s_ in m.Supply:
            per_year = {}
            for y in self.years:
                per_year[y] = sum(
                    pyo.value(m.production[s_[0], s_[1], y, i])
                    * self.rep[y][i]['weight'] / 1000.0
                    for i in range(len(self.rep[y])))
            out[(s_[0], s_[1])] = per_year
        return out

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
