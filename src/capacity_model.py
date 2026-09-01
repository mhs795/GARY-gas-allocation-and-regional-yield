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
from model import (IMPORT_NODES, LNG_NODES, STORAGE_CYCLE_COST,
                   VOLL_PER_GJ,
                   _declined_capacity, _reserves_pj)

# Day-of-year (1..365, non-leap) -> calendar month.
_MONTH_OF_DAY = {}
_d = 1
for _mo in range(1, 13):
    for _ in range(calendar.monthrange(2025, _mo)[1] if _mo != 2 else 28):
        if _d <= 365:
            _MONTH_OF_DAY[_d] = _mo
            _d += 1

def _salvage_rate(row, backstop):
    """What one GJ still in the ground is worth at the horizon, $/GJ.

    The backstop price less this row's own extraction cost, floored at zero: a
    tranche that costs more to lift than the substitute costs to buy is worth
    nothing in the ground. Netting is what makes the credit differ by basin.
    """
    if not backstop:
        return 0.0
    return max(0.0, float(backstop) - float(row['Cost']))


# Days represented by the annual peak day (adequacy).
PEAK_DAY_WEIGHT = P.get('peak_day_weight', 5.0)


def build_representative_days(years, demand_all, gpg_all, ind_all, nodes_df,
                              reserved_all=None, dc_all=None, bins_per_month=None,
                              medoid_days=None):
    """Reduce each year's 365 daily profiles to representative days.

    Returns {year: [ {weight, demand{node:v}, gpg{node:v}, ind{node:v}} ]} --
    ``bins_per_month`` load bins within each month plus one annual *peak* day (the
    highest total-demand day, so the capacity layer sizes for stress).

    WITHIN-MONTH RESOLUTION. This used to be one MEAN day per month, which flattens
    the load-duration curve inside the month completely: a mild day and a cold snap
    in the same July became one average July day. That matters because the marginal
    supply differs between them -- cheap southern gas clears a mild day, imports come
    in on a cold one -- so averaging first and dispatching second is not the same as
    dispatching each day and averaging after. Measured 30 Aug 2026 at one bin, the
    MIP planned ~44 PJ/yr LESS southern production than the 365-day dispatch layer
    actually drew, three years running. That exhausted the southern tranches a year
    before the plan expected and put 16,008 TJ of shortage into 2032 which the MIP
    never saw, so it scheduled no build for it.

    Days are sorted by total demand within each month and split into contiguous bins
    of near-equal count, so each bin is a segment of that month's load-duration curve
    and carries its own day weight.

    ``dc_all`` is the data centre slice of ``ind_all`` -- already inside it, not on
    top of it. It rides along as ``dc{node: TJ}`` purely so the investment layer
    can net it out of the industrial expansion headroom, exactly as the dispatch
    layer does: the load is firm, so it must not enlarge the raise blocks.
    """
    node_names = nodes_df['Name'].tolist()
    reserved_all = reserved_all or {}
    dc_all = dc_all or {}
    k = max(1, int(bins_per_month if bins_per_month is not None
                   else P.get_int('rep_bins_per_month', 3)))
    # 'medoid' picks a REAL day per bin; 'mean' averages the bin. See the note in the
    # loop below -- a mean cannot represent a binding corridor.
    medoid = (medoid_days if medoid_days is not None
              else P.get_str('rep_day_mode', 'medoid').strip().lower() == 'medoid')

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
            # segments of this month's load-duration curve, mild days first
            ordered = sorted(days, key=lambda d: day_total[d])
            n_d = len(ordered)
            edges = [round(i * n_d / k) for i in range(k + 1)]
            for lo, hi in zip(edges[:-1], edges[1:]):
                chunk = ordered[lo:hi]
                if not chunk:
                    continue
                w = float(len(chunk))
                if medoid:
                    # A REAL day, the one whose total sits closest to the bin's mean.
                    # Averaging a bin destroys COINCIDENCE, not just level: a real cold
                    # day is cold in Melbourne and Adelaide and Sydney at once, which is
                    # exactly when the corridors bind. A mean spreads the same energy
                    # over node-day combinations that never co-occurred, so the network
                    # never looks tight and the investment layer sees no congestion to
                    # build around. More bins do not help, because each bin is still an
                    # average -- which is why 13, 37 and 61 representative days produced
                    # byte-identical build lists.
                    target = sum(day_total[d] for d in chunk) / w
                    d0 = min(chunk, key=lambda d: abs(day_total[d] - target))
                    dem = {n: demand_all.get((n, y, d0), 0) for n in node_names}
                    gpg = {n: gpg_all.get((n, y, d0), 0) for n in node_names}
                    ind = {n: ind_all.get((n, y, d0), 0) for n in node_names}
                    dc = {n: dc_all.get((n, y, d0), 0) for n in node_names}
                    reserved = reserved_all.get((y, d0), 0.0)
                else:
                    dem = {n: sum(demand_all.get((n, y, d), 0) for d in chunk) / w for n in node_names}
                    gpg = {n: sum(gpg_all.get((n, y, d), 0) for d in chunk) / w for n in node_names}
                    ind = {n: sum(ind_all.get((n, y, d), 0) for d in chunk) / w for n in node_names}
                    dc = {n: sum(dc_all.get((n, y, d), 0) for d in chunk) / w for n in node_names}
                    reserved = sum(reserved_all.get((y, d), 0.0) for d in chunk) / w
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
                 salvage_price=None,
                 ):
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
        # Physical liquefaction nameplate per train, and the take-or-pay share of
        # planned volume. Same split the dispatch layer makes: foundation volume
        # stays must-serve demand, the spot tail bids at the netback up to spare
        # liquefaction capacity.
        self.lng_nameplate = dict(lng_nameplate or {})
        # {year: share} or a scalar. The take-or-pay share is TIME-VARYING because
        # the contracts are: ACCC has the Queensland foundation SPAs expiring from
        # 2031 with a sharp drop after 2035, so must-serve export demand disappears
        # mid-horizon and the rest becomes contestable. Holding one scalar across
        # 26 years is what let inelastic export demand bid a depleting resource up
        # to VOLL. A bare float still works and applies to every year.
        if isinstance(foundation_share, dict):
            self.foundation_share_by_year = {int(k): float(v)
                                             for k, v in foundation_share.items()}
            self.foundation_share = float(max(self.foundation_share_by_year.values(),
                                              default=0.0))
        else:
            self.foundation_share_by_year = {}
            self.foundation_share = float(foundation_share)
        # Same reservation treatment as dispatch: reserved gas comes off the export
        # ceiling, tail first.
        #
        # PER-YEAR, for the same reason foundation_share is. With contracts
        # respected the applied share is capped at the uncontracted tail, and that
        # tail grows from ~7% to 100% as the SPAs expire mid-horizon -- so a single
        # scalar cannot describe it. Passing one meant the investment layer sized
        # the network against the FINAL year's share (the full 20%, contracts long
        # gone) in every year, while dispatch correctly reserved 7% while the SPAs
        # ran. Contract-BREAKING runs were unaffected: there the applied share is
        # the requested one in every year, so any single year's value is right.
        self.respect_contracts = bool(respect_contracts)
        if isinstance(reservation_applied, dict):
            self.reserve_applied_by_year = {int(k): float(v)
                                            for k, v in reservation_applied.items()}
            self.reserve_applied = float(max(self.reserve_applied_by_year.values(),
                                             default=0.0))
        else:
            self.reserve_applied_by_year = {}
            self.reserve_applied = float(reservation_applied or 0.0)
        self.terminal_earliest = (P.get_int('terminal_earliest', 2028)
                                  if terminal_earliest is None else terminal_earliest)
        self.base_year = (P.get_int('capacity_base_year', 2025)
                          if base_year is None else base_year)
        # $/GJ that gas is worth at the horizon -- the BACKSTOP price, i.e. what the
        # substitute costs. Landed imported LNG here, which is genuinely GARY's
        # backstop. Without a terminal value the objective prices leftover gas at
        # zero, so the optimal plan exhausts every tranche exactly at the last year
        # and that year shorts at VOLL. None disables it.
        self.salvage_price = salvage_price
        self.solved = False

    def _foundation(self, y):
        """Take-or-pay share of planned export volume in year ``y``."""
        return self.foundation_share_by_year.get(y, self.foundation_share)

    def _reserve(self, y):
        """Reservation share actually applied in year ``y``."""
        return self.reserve_applied_by_year.get(y, self.reserve_applied)

    def _reserve_from_foundation(self, y):
        """How much of year ``y``'s reservation has to come out of contracted gas.

        Zero when the run respects contracts -- that is what respecting them means.
        Otherwise it is whatever the reservation asks for beyond that year's
        uncontracted tail, which shrinks to nothing once the SPAs expire.
        """
        if self.respect_contracts:
            return 0.0
        return max(0.0, self._reserve(y) - max(0.0, 1.0 - self._foundation(y)))

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
                * (self._foundation(y) - self._reserve_from_foundation(y)
                   + self._reserve(y))))

        m.build = pyo.Var(m.Expansion, Y, domain=pyo.Binary)   # build project e in year y
        m.reserved_prod = pyo.Var(m.YR, domain=pyo.NonNegativeReals)
        m.reserved_cap = pyo.Constraint(m.YR, rule=lambda m, y, i:
            m.reserved_prod[y, i] <= self.rep[y][i].get('reserved', 0.0))

        arc_data = self.arcs.set_index('Name').to_dict('index')
        supply_dict = self.supply.set_index(['Node', 'IsPotential']).to_dict('index')
        exp_data = self.expansion.set_index('Name').to_dict('index')
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
        # Annual production against a reserve, PJ. The representative-day weights
        # sum to ~370 rather than 365 -- the annual peak day carries PEAK_DAY_WEIGHT
        # on top of twelve full months -- so this books ~1.4% MORE production against
        # every reserve than the 365-day dispatch layer will actually draw.
        #
        # That is deliberately LEFT ALONE. Over-booking makes the MIP conservative:
        # it plans as though reserves deplete slightly faster than they will, so
        # dispatch finds gas spare rather than short. Normalising it to 365 was tried
        # on 30 Aug 2026 and made the terminal year WORSE -- 2050 shortage went from
        # 139,736 TJ to 480,425 -- because a looser constraint let the MIP plan more
        # production and dispatch then drained the tranche sooner. The 1.4% errs in
        # the safe direction; do not "fix" it.
        def _annual_pj(m, node, is_pot):
            return pyo.quicksum(m.production[node, is_pot, y, i] * wt[y, i]
                                for (y, i) in YR) / 1000.0

        def obj_rule(m):
            def _supply_cost(s_, y):
                """Field cost, $/GJ. One tranche, one cost -- see model.py.

                An import terminal is the only row whose cost moves with the year,
                because it tracks the international LNG price rather than a field
                development cost.
                """
                if s_[0] in IMPORT_NODES and s_[1] and y in self.import_cost_by_year:
                    return self.import_cost_by_year[y]
                return supply_dict[s_]['Cost']

            def _reserved_cost(y):
                """What the reserved tranche costs to LIFT, $/GJ. Not what it sells for.

                The reservation prices this gas at $0 to the domestic buyer -- that is
                the policy, and dispatch keeps it, because there the price is only
                setting merit order and the level of the objective changes nothing
                about which gas flows.

                HERE THE LEVEL IS THE DECISION. This objective is weighed directly
                against CapEx, so leaving the tranche at $0 tells the planner that
                ~96 PJ/yr arrives from nowhere at no resource cost. It does not: by
                supply_cap the reserved gas displaces commercial production one for
                one out of the same capped field, so the system lifts the same
                molecules and simply charges nobody for some of them. Uncosted, that
                came to $6.4bn NPV against the $1.0bn terminal the reservation runs
                then declined to build -- the plan was dropping real capacity to chase
                a bookkeeping saving. docs/scenarios.md already warns that System Cost
                is not comparable across reservation levels for exactly this reason;
                this objective IS that number, so the warning had to be honoured here
                rather than only on the KPI card.

                Costing it changes no mechanism -- export eligibility, the reserved
                cap and the shared capacity limit are all constraints, not prices. It
                makes the reservation cost-NEUTRAL to the investment layer, which is
                what it physically is: same gas, same lifting cost, different pocket.
                """
                rows = [s_ for s_ in m.Supply if s_[0] in self.lng_source and not s_[1]]
                return min((_supply_cost(s_, y) for s_ in rows), default=0.0)

            ops = pyo.quicksum(
                df[y] * wt[y, i] * (
                    pyo.quicksum(m.production[s[0], s[1], y, i] * _supply_cost(s, y) * 1000 for s in m.Supply)
                    + m.reserved_prod[y, i] * _reserved_cost(y) * 1000
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
            # TERMINAL SALVAGE VALUE, net of extraction cost.
            #
            # Gas left in a tranche at the horizon is worth what the substitute costs
            # LESS what you would still pay to lift it -- it is un-extracted, so the
            # gross backstop price overstates it. That netting is also what keeps the
            # credit BASIN-SPECIFIC: Surat's $12.29-3.65 = $8.64 against Otway's
            # $12.29-7.27 = $5.02.
            #
            # A first attempt (30 Aug 2026) credited the gross price identically for
            # every basin. It was uniform by construction, so every scarcity rent
            # collapsed to the same $2.26 floor and stopped distinguishing a
            # nearly-exhausted basin from an abundant one. Do not drop the netting.
            #
            # This is the Hotelling terminal condition: the rent rises until the
            # price reaches the backstop exactly as the resource runs out.
            if self.salvage_price:
                salvage = pyo.quicksum(
                    df[Y[-1]] * _salvage_rate(supply_dict[s], self.salvage_price) * 1e6
                    * (_reserves_pj(supply_dict[s]) - _annual_pj(m, s[0], s[1]))
                    for s in m.Supply if _reserves_pj(supply_dict[s]) is not None)
                return ops + capex - salvage
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
                        * max(0.0, self._foundation(y) - self._reserve_from_foundation(y))
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

        # THE STOCK LIMIT. A row cannot produce more gas over the horizon than its
        # tranche holds. The dispatch layer applies the same limit year by year
        # through model._declined_capacity; the MIP sees all 26 years at once, so
        # it can state it exactly rather than approximate it -- which is what makes
        # the investment layer build the backfill BEFORE the tranche it replaces
        # runs out, instead of discovering the hole a year late.
        #
        # Weighted by the representative days each rep day stands for, and divided
        # by 1000 to put TJ into PJ. Total weight is 370 rather than 365 -- the peak
        # day carries PEAK_DAY_WEIGHT on top of twelve full months -- so the MIP
        # counts production ~1.4% high against the reserve. That errs toward
        # building backfill slightly EARLY, which is the safe direction here, so the
        # adequacy weighting is left alone rather than netted out.
        stock_rows = [s for s in m.Supply if _reserves_pj(supply_dict[s]) is not None]

        def reserve_rule(m, node, is_pot):
            return _annual_pj(m, node, is_pot) <= _reserves_pj(supply_dict[node, is_pot])
        # The index set must be ATTACHED to the model. Passing an inline
        # pyo.Set(...) as a Constraint index leaves it unconstructed, and only part
        # of it came back: 6 of 11 rows got reserve constraints, and the five
        # missing ones were every DEVELOPED (2P) row -- including Surat's, the
        # tranche that actually runs out. They faced no opportunity cost at all.
        m.StockRows = pyo.Set(initialize=stock_rows, dimen=2)
        m.reserve_limit = pyo.Constraint(m.StockRows, rule=reserve_rule)

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
        # THE YEAR IS CLOSED: a store must be refilled with exactly what it gave up.
        #
        # This used to bound NET withdrawal at STORAGE_OPENING x capacity, which was
        # meant as "the investment layer's equivalent of the dispatch layer's closed
        # year" and is not that at all. It permits a store to be drained without ever
        # being filled, and because the bound applies per year it permits that EVERY
        # year -- so the MIP believed it could take half of Moomba and half of Iona
        # out of the ground, annually, forever.
        #
        # Measured 30 Aug 2026, that is exactly what it did: injection 0.0 PJ into
        # every store in every year, against withdrawals of 35.0 PJ from Moomba
        # (70,000 TJ x 0.5), 12.2 from Iona (24,400 x 0.5) and 3.7 from Silver Springs
        # (rate-limited) -- 50.9 PJ/yr of gas that never had to be produced. Dispatch
        # cannot do that: its inventory returns to its opening level, so its injection
        # equals its withdrawal exactly.
        #
        # That free gas is the whole divergence. The MIP planned ~44 PJ/yr LESS
        # production than dispatch drew, concentrated on Iona and Moomba -- the only
        # two supply rows with a store attached -- which exhausted the southern
        # tranches a year early and left a hole the MIP never saw. 50.9 against 44.
        #
        # Netting to zero is what dispatch does, so the two layers now agree about
        # what a store is: a way to move gas WITHIN a year, not a source of it.
        m.stor_annual = pyo.Constraint(m.StorageNodes, Y, rule=lambda m, sn, y:
            pyo.quicksum((m.withdrawal[sn, y, i] - m.injection[sn, y, i]) * wt[y, i]
                         for (yy, i) in YR if yy == y) == 0.0)

    def solve(self, mip_gap=0.005):
        opt = solvers.make_solver(rel_gap=mip_gap if mip_gap is not None else 0.005,
                                  time_limit=solvers.env_time_limit())
        res = opt.solve(self.model, tee=False)
        ok = res.solver.termination_condition in (pyo.TerminationCondition.optimal,
                                                   pyo.TerminationCondition.feasible)
        self.solved = ok
        if not ok:
            return str(res.solver.termination_condition)
        self._solve_duals()
        return "ok"

    def _solve_duals(self):
        """Re-solve with the build decisions fixed, as a pure LP, to get duals.

        Needed for the SCARCITY RENT on m.reserve_limit. Neither backend returns
        duals while an integer variable is present, even a fixed one, so the
        binaries are fixed at their MIP values and relaxed to Reals first --
        the same trick model.py uses to get nodal prices out of its own MILP.

        A failure here is not fatal: rents come back empty and the dispatch layer
        falls back to bare field costs, which is the pre-rent behaviour.
        """
        m = self.model
        try:
            for e in m.Expansion:
                for y in self.years:
                    m.build[e, y].fix(pyo.value(m.build[e, y]))
                    m.build[e, y].domain = pyo.Reals
            m.dual = pyo.Suffix(direction=pyo.Suffix.IMPORT)
            solvers.make_solver(time_limit=solvers.env_time_limit()).solve(m, tee=False)
        except Exception:
            if hasattr(m, 'dual'):
                del m.dual

    def get_scarcity_rents(self):
        """Hotelling rent on each stock-limited supply row, {(node, is_pot, year): $/GJ}.

        THE OPPORTUNITY COST OF DEPLETION, and the thing that makes a myopic
        dispatch ration a finite resource instead of burning it cheapest-first.

        Without it the two layers disagree about depletion and the disagreement is
        fatal. This MIP has perfect foresight and a horizon-wide reserve limit, so
        it can SPREAD a basin's reserve thinly across 26 years and never hit a wall.
        The dispatch layer is myopic: it takes the cheapest gas first at full rate,
        exhausts Surat's 2P by 2046 and then has nothing, because the backfill the
        MIP saw no need to build was never built. Measured 30 Aug 2026: 758 PJ/yr
        of shortage over 2047-50 and a $168/GJ mean price, plus a southern spike in
        2029-30 as Otway, Gippsland and Cooper ran dry ahead of their backfill.

        The dual on m.reserve_limit is exactly the missing signal: what one more PJ
        in the ground is worth to the system. Adding it to a field's marginal cost
        makes the cheap tranche price like the scarce thing it is, so dispatch
        saves it for the years that value it most and the two layers agree.

        The objective discounts each year, so the dual is on an NPV basis. Dividing
        by that year's discount factor puts it back on a cash basis -- which makes
        the rent grow at the discount rate, the Hotelling result, rather than
        being imposed as one.
        """
        m = self.model
        if not self.solved or not hasattr(m, 'dual') or not hasattr(m, 'reserve_limit'):
            return {}
        # The rent has TWO components once a terminal value exists, and dispatch
        # needs both: the reserve dual, plus the salvage the last PJ forgoes by being
        # produced. Passing the dual alone under-prices gas relative to the plan
        # dispatch is executing, so it over-produces.
        df_last = 1.0 / ((1.0 + self.r) ** (self.years[-1] - self.base_year))
        supply_dict = self.supply.set_index(['Node', 'IsPotential']).to_dict('index')
        out = {}
        for idx in m.reserve_limit:
            con = m.reserve_limit[idx]
            # A non-binding reserve constraint can be absent from the dual suffix
            # entirely; absent means a zero dual, not a missing row.
            lam_raw = float(m.dual[con]) if con in m.dual else 0.0
            # The constraint's RHS is in PJ and the objective is in dollars, so the
            # dual is $/PJ. One PJ is 1e6 GJ, so that is the divisor -- NOT the 1000
            # that turns TJ into GJ everywhere else in this model. Getting it wrong
            # by that factor makes the rent 1000x too large, which prices every
            # developed field out of the market entirely and shorts from year one.
            # abs() because the sign convention on a <= constraint differs by
            # backend.
            lam = abs(lam_raw) / 1e6
            salvage_npv = df_last * _salvage_rate(supply_dict[(idx[0], idx[1])],
                                                  self.salvage_price)
            if lam + salvage_npv <= 0:
                continue
            for y in self.years:
                df = 1.0 / ((1.0 + self.r) ** (y - self.base_year))
                out[(idx[0], idx[1], y)] = (lam + salvage_npv) / df
        return out

    def get_annual_production_pj(self):
        """Implied annual production per field, ``{(node, is_potential): {year: PJ}}``.

        Representative-day production weighted by the days each day stands for.
        Total weight is 370 rather than 365 (the peak day carries PEAK_DAY_WEIGHT
        on top of twelve full months), so this runs ~1.4% high.

        A DIAGNOSTIC now, not part of the solve. It used to feed the second
        capacity pass that carried the old path-dependent cost step; reserves are a
        hard constraint inside the MIP since, so nothing calls this but reporting.
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
