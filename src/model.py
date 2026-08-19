import pyomo.environ as pyo
import pandas as pd
import os
import re

import results_io
import solvers

# --- SA "dunkelflaute" event -------------------------------------------------
# A wind/solar drought like the sustained low-renewables spell South Australia saw
# in June 2025: SA gas generation runs near its maximum to cover the renewable
# shortfall. We replicate it by lifting the Adelaide (SA) GPG gas demand over a
# June window in a single year. Adelaide's normal early-June GPG is ~73 TJ/d
# (winter peak ~131); x2.75 -> ~200 TJ/d sustained, between the node winter peak
# and the ~309 TJ/d SA regional GPG peak — a severe but plausible call on SA gas
# that stresses Moomba->Adelaide and the SEA Gas import from Victoria. The window
# is a full month (deliberately longer than any historical event) to stress-test
# a prolonged drought.
DUNKELFLAUTE_YEAR = 2027
DUNKELFLAUTE_NODE = "Adelaide"
DUNKELFLAUTE_DAYS = range(152, 182)   # 1-30 June (gas day-of-year)
DUNKELFLAUTE_MULT = 2.75


# --- Domestic gas reservation ------------------------------------------------
# East-coast LNG export trains. A reservation carves a share of their planned
# export volume out of the export stream and puts it into the domestic market
# AT ZERO COST, so it is the cheapest gas in the system and is taken up ahead of
# everything else. Three pieces:
#
#   1. served_LNG[t] <= (1 - share) * LNG_demand[t]
#        the carve-out itself -- the trains may only liquefy what is left.
#   2. reserved_prod[t] <= share * LNG_demand[t],  priced at $0/GJ
#        the carved-out volume, offered to the domestic market for nothing.
#   3. production[source, t] + reserved_prod[t] <= source capacity
#        the reserved gas is the SAME gas, not extra: total physical deliverability
#        is unchanged, a slice of it is simply free.
#   4. sum(flow over the LNG feed pipes)[t] <= production[source, t]
#        exports may draw only on commercial gas. Without this the free gas would
#        simply flow to the trains -- they are ordinary demand nodes and do not
#        care where a molecule came from -- and the reservation would do nothing.
#        It is exact rather than an approximation because the trains have exactly
#        three feed pipes (APLNG_Pipe, GLNG_Pipe, WGP_Pipe), all from Surat.
#
# There is still no export revenue term in the objective. That coupling is what
# produced negative Perth prices in the removed WA DomGas build; here the
# reservation acts entirely through supply cost and flow eligibility.
#
# This REPLACES the earlier pure export-cap formulation (piece 1 alone), under
# which the reserved gas was never produced at all: domestic demand was already
# met, so cost minimisation simply left it in the ground and the reservation was
# an export cap by another name. Pricing it at zero is what makes it move.
LNG_NODES = ['APLNG', 'GLNG', 'QCLNG']

# Reservation shares offered by the dashboard slider (fraction of export volume).
RESERVATION_LEVELS = [0.05, 0.10, 0.20, 0.30]


# --- Mass-market demand curve ------------------------------------------------
# Distribution-level (residential/commercial) demand is not a fixed volume that
# must be served at any price. It is represented as a STEP DEMAND CURVE: the load
# at each node is split into blocks, each with a strike price, and a block is shed
# rather than supplied once the nodal price exceeds its strike. Economically this
# is the willingness-to-pay schedule of the mass market, discretised.
#
# The blocks live in data/curtailment_params.csv (rows MassMarket_B2..Bn) and are
# DERIVED, not asserted: build_demand_curves.py fits a constant-elasticity
# curve Q(P) = Q0 (P/P0)^e to a log-spaced price grid, using
#   P0 = $13.56/GJ  ACCC Gas Inquiry, producer offers for 2026 supply
#   e  = -0.180     short-run own-price elasticity of natural gas demand,
#                   Labandeira, Labeaga & Lopez-Otero (2017), Energy Policy 102,
#                   549-568, Table 6
# which yields ~31% of mass-market load with some price response and ~69%
# inelastic. See that module for the full sources and the two caveats that matter
# when reading results (extrapolation above the estimated price range, and the
# monthly-elasticity-applied-daily frequency mismatch).
#
# The inelastic ~69% is NOT a block. It is the existing `shortage` variable at
# VOLL_PER_GJ, which is exactly "served unless nothing can reach it, valued at the
# value of lost load" -- so adding a block at VOLL would just duplicate it.
#
# Off by default (`elastic_demand=False`) so cached scenarios and the inelastic
# comparison case stay reproducible; the dashboard exposes it as a lever.

# Value of lost load: the price at which unserved mass-market gas is penalised.
# NOTE: the National Gas Rules set VoLL at $800/GJ in the Victorian DWGM and the
# STTM market price cap at $400/GJ (AEMO, Gas Market Parameters Review 2022, Final
# Recommendations, Feb 2023). GARY's $300/GJ predates that check and is therefore
# conservative; it is left as-is because changing it moves every historical result,
# but it is the number to revisit if VOLL ever matters to a conclusion.
VOLL_PER_GJ = 300.0

# Southern winter window (gas day-of-year) used by the Winter lever, and by the
# per-block WinterScale that damps the price response through the heating season.
WINTER_DAYS = range(150, 251)


def load_demand_blocks(data_dir):
    """Read every demand-curve block from curtailment_params.csv.

    Returns ``{(tier, direction): [(name, price $/GJ, share, winter scale), ...]}``
    where tier is MassMarket/Industrial/GPG and direction is 'shed' or 'raise'.
    Shed blocks come back cheapest-to-shed first, raise blocks highest-value
    first, which is the order each is consumed in.

    Shed blocks priced at or above VOLL_PER_GJ are dropped: shedding one would
    cost the same as the shortage variable already in the model, so it would add
    a degenerate duplicate rather than any behaviour.
    """
    try:
        df = pd.read_csv(os.path.join(data_dir, "curtailment_params.csv"))
    except FileNotFoundError:
        return {}
    if 'Share' not in df.columns or 'Direction' not in df.columns:
        return {}
    out = {}
    for _, r in df.iterrows():
        name = str(r['Tier'])
        m = re.match(r'^(MassMarket|Industrial|GPG)_([BU])\d+$', name)
        if not m or pd.isna(r['Share']):
            continue
        tier, direction = m.group(1), str(r['Direction'])
        price, share = float(r['StrikePrice']), float(r['Share'])
        if share <= 0 or (direction == 'shed' and price >= VOLL_PER_GJ):
            continue
        ws = float(r['WinterScale']) if pd.notna(r.get('WinterScale')) else 1.0
        out.setdefault((tier, direction), []).append((name, price, share, ws))
    for (tier, direction), blocks in out.items():
        # Cheapest first when shedding, most valuable first when raising.
        blocks.sort(key=lambda b: b[1], reverse=(direction == 'raise'))
    return out


def load_gpg_raise_blocks(data_dir):
    """Per-node GPG expansion ladder, as ``{(node, block): (value $/GJ, share)}``.

    Value is what a generator at that node can pay per GJ to displace the marginal
    generation IN ITS OWN JURISDICTION, at its own heat rate. Nodes whose
    jurisdiction has nothing cheaper to displace -- the NT's isolated gas-only
    system, and effectively SA and VIC, where the alternative is mine-mouth brown
    coal at a running cost gas cannot beat -- get no blocks and so cannot expand.
    Written by build_demand_curves.py.
    """
    try:
        df = pd.read_csv(os.path.join(data_dir, "gpg_raise_blocks.csv"))
    except FileNotFoundError:
        return {}
    return {(str(r['Node']), str(r['Block'])): (float(r['ValuePerGJ']), float(r['Share']))
            for _, r in df.iterrows() if float(r['ValuePerGJ']) > 0}


def load_gpg_capacity(data_dir):
    """Per-node GPG expansion ceiling, as ``{node: (nameplate TJ/d, cap multiple)}``.

    Written by build_demand_curves.py. Nameplate is the physical ceiling from the
    Gas Bulletin Board register; the cap multiple is a modelling guardrail --
    GARY models gas, not the NEM, and the fleet runs at ~9% of nameplate, so
    unbounded expansion would let an unmodelled electricity market set gas demand.
    """
    try:
        df = pd.read_csv(os.path.join(data_dir, "gpg_capacity.csv"))
    except FileNotFoundError:
        return {}
    cap = df['ExpansionCap'].astype(float) if 'ExpansionCap' in df.columns else 1.0
    return {n: (float(np_), float(c)) for n, np_, c
            in zip(df['Node'], df['Nameplate'].astype(float),
                   cap if hasattr(cap, '__iter__') else [cap] * len(df))}


def apply_lng_reservation(demand_df, share):
    """Divert ``share`` (0-1) of LNG export volume to the domestic market.

    Returns ``(demand frame, TJ diverted, {day: TJ reserved})``. The per-day
    series is what the zero-cost supply tranche is sized against, so the volume
    released each day matches the export volume withheld that day rather than an
    annual average. Applied after the Winter/LNG scenario levers, so the share
    bites on the export volume actually planned under the scenario rather than on
    the raw baseline.
    """
    if not share:
        return demand_df, 0.0, {}
    mask = demand_df['Node'].isin(LNG_NODES)
    diverted = float(demand_df.loc[mask, 'Demand'].sum()) * share
    by_day = (demand_df.loc[mask].groupby('Day')['Demand'].sum() * share).to_dict()
    demand_df = demand_df.copy()
    demand_df.loc[mask, 'Demand'] *= (1.0 - share)
    return demand_df, diverted, {int(d): float(v) for d, v in by_day.items()}


class GasMarketModel:
    def __init__(self, nodes_df, arcs_df, supply_df, demand_df, expansion_df, contracts_df=None, year=2025, already_built=None, baseline="StepChange", dunkelflaute=False, builds_fixed=None, elastic_demand=False, reserved_by_day=None):
        self.nodes = nodes_df
        self.arcs = arcs_df
        self.supply = supply_df
        self.demand = demand_df
        self.expansion = expansion_df
        self.contracts = contracts_df
        self.year = year
        self.already_built = already_built if already_built else []
        # AEMO 2026 GSOO baseline scenario: StepChange / Accelerated / SlowerGrowth.
        # Selects which per-baseline GPG & industrial demand profiles to load.
        self.baseline = baseline
        # SA dunkelflaute event lever (applied to Adelaide GPG in DUNKELFLAUTE_YEAR).
        self.dunkelflaute = dunkelflaute
        # Mass-market step demand curve on/off (see the header block above).
        self.elastic_demand = elastic_demand
        # {day: TJ} of export volume withheld and offered domestically at $0.
        self.reserved_by_day = dict(reserved_by_day or {})
        # The LNG trains' only feed pipes, and the node behind them. Resolved here
        # rather than in build_model so the capacity layer can read them before any
        # dispatch model has been built.
        _arc_ix = arcs_df.set_index('Name')
        self.lng_arcs = [a for a in arcs_df['Name'] if _arc_ix.loc[a, 'To'] in LNG_NODES]
        self.lng_source = sorted({_arc_ix.loc[a, 'From'] for a in self.lng_arcs})
        # When set (a set of active project names), ALL build decisions are fixed:
        # projects in the set -> 1, all others -> 0. Used by the two-stage solve so
        # dispatch honours the capacity model's schedule and runs as a pure LP.
        self.builds_fixed = builds_fixed
        self.solved = False

        base_path = os.path.dirname(__file__)

        # --- Curtailable large-user demand (GPG + large industrial) ---------
        # Daily demand added on top of the distribution-level node demand. Each
        # tier is served unless the nodal gas price exceeds its strike price,
        # above which it sheds load instead of being supplied. Levels/trajectory
        # are GSOO 2026 Step Change (year-varying); daily shapes are empirical
        # (GBB). See build_gpg_demand_gsoo.py / build_industrial_demand_gsoo.py.
        data_dir = os.path.join(base_path, "data")
        def _load_profile(fname):
            try:
                df = pd.read_csv(os.path.join(data_dir, fname))
                return df.set_index(['Node', 'Day'])['Demand'].to_dict()
            except FileNotFoundError:
                return {}

        def _load_year_profile(year_fname, flat_fname, lo=2026, hi=2045):
            """Year-specific GSOO Step Change profile (Year,Node,Day,Demand),
            clamped to the available range; falls back to the flat GBB profile."""
            try:
                df = pd.read_csv(os.path.join(data_dir, year_fname))
            except FileNotFoundError:
                return _load_profile(flat_fname)
            yr = min(max(int(self.year), lo), hi)
            sub = df[df['Year'] == yr]
            if sub.empty:
                return _load_profile(flat_fname)
            return sub.set_index(['Node', 'Day'])['Demand'].to_dict()

        # GPG and industrial both re-based on the chosen GSOO 2026 baseline
        # (year-varying). Each baseline has its own profile file; fall back to the
        # legacy StepChange (_gsoo) file, then to the flat GBB profile, if absent.
        def _baseline_profile(prefix, legacy, flat):
            scen_file = f"{prefix}_{self.baseline}.csv"
            if os.path.exists(os.path.join(data_dir, scen_file)):
                return _load_year_profile(scen_file, flat)
            return _load_year_profile(legacy, flat)

        self.gpg_demand = _baseline_profile("gpg_demand_profile",
                                            "gpg_demand_profile_gsoo.csv",
                                            "gpg_demand_profile.csv")
        self.ind_demand = _baseline_profile("industrial_demand_profile",
                                            "industrial_demand_profile_gsoo.csv",
                                            "industrial_demand_profile.csv")
        # SA dunkelflaute: surge Adelaide GPG gas call over the mid-June window in
        # the event year, on top of whichever GSOO baseline is loaded.
        if self.dunkelflaute and int(self.year) == DUNKELFLAUTE_YEAR:
            for d in DUNKELFLAUTE_DAYS:
                key = (DUNKELFLAUTE_NODE, d)
                if key in self.gpg_demand:
                    self.gpg_demand[key] *= DUNKELFLAUTE_MULT
        try:
            strikes = pd.read_csv(os.path.join(data_dir, "curtailment_params.csv")
                                  ).set_index('Tier')['StrikePrice'].to_dict()
        except FileNotFoundError:
            strikes = {}
        self.strike_gpg = float(strikes.get('GPG', 22.0))
        self.strike_ind = float(strikes.get('Industrial', 120.0))
        blocks = load_demand_blocks(data_dir) if self.elastic_demand else {}
        self.mm_blocks = blocks.get(('MassMarket', 'shed'), [])
        self.mm_raise = blocks.get(('MassMarket', 'raise'), [])
        self.ind_raise = blocks.get(('Industrial', 'raise'), [])
        # GPG's ladder is per-node (regional displacement), so it lives in its own
        # file rather than the national block table.
        self.gpg_raise = load_gpg_raise_blocks(data_dir) if self.elastic_demand else {}
        self.gpg_capacity = load_gpg_capacity(data_dir) if self.elastic_demand else {}
        if self.elastic_demand and not blocks:
            # Silently falling back to fixed demand would look like the lever
            # simply had no effect, which is indistinguishable from a real result.
            raise RuntimeError(
                "elastic_demand=True but no demand-curve blocks were found in "
                "data/curtailment_params.csv. Run: python src/build_demand_curves.py")
        self.gpg_nodes = sorted({n for (n, _) in self.gpg_demand})
        self.ind_nodes = sorted({n for (n, _) in self.ind_demand})

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

        # Reserved gas: the carved-out export volume, offered domestically at $0.
        # Sourced from whichever node feeds the LNG trains (Surat), so it enters
        # that node's balance and must then find its own way to a domestic buyer.
        lng_arcs, lng_source = self.lng_arcs, self.lng_source
        m.reserved_prod = pyo.Var(m.T, domain=pyo.NonNegativeReals)
        reserved_day = self.reserved_by_day
        m.reserved_cap = pyo.Constraint(m.T,
            rule=lambda m, t: m.reserved_prod[t] <= reserved_day.get(t, 0.0))

        # Curtailable large-user demand: served unless price exceeds strike.
        m.GPGNodes = pyo.Set(initialize=[n for n in self.gpg_nodes if n in self.nodes['Name'].tolist()])
        m.INDNodes = pyo.Set(initialize=[n for n in self.ind_nodes if n in self.nodes['Name'].tolist()])
        m.gpg_curtail = pyo.Var(m.GPGNodes, m.T, domain=pyo.NonNegativeReals)
        m.ind_curtail = pyo.Var(m.INDNodes, m.T, domain=pyo.NonNegativeReals)

        node_demand = self.demand.set_index(['Node', 'Day'])['Demand'].to_dict()

        # Mass-market step demand curve. Only distribution nodes carry it: the LNG
        # trains enter the network as demand nodes too, but their volume is an
        # export commitment, not price-responsive household load.
        mm_nodes = sorted({n for (n, _), v in node_demand.items()
                           if v > 0 and n not in LNG_NODES and n in set(m.Nodes)})
        m.MMNodes = pyo.Set(initialize=mm_nodes)
        m.MMBlocks = pyo.Set(initialize=[b[0] for b in self.mm_blocks])
        m.mm_curtail = pyo.Var(m.MMNodes, m.MMBlocks, m.T, domain=pyo.NonNegativeReals)
        mm_strike = {b[0]: b[1] for b in self.mm_blocks}
        mm_share = {b[0]: b[2] for b in self.mm_blocks}
        mm_winter = {b[0]: b[3] for b in self.mm_blocks}

        def mm_available(n, blk, t):
            """TJ of block `blk` present at node `n` on day `t`."""
            share = mm_share[blk]
            if t in WINTER_DAYS:
                share *= mm_winter[blk]
            return node_demand.get((n, t), 0) * share
        self._mm_available = mm_available
        gpg_dem = self.gpg_demand
        ind_dem = self.ind_demand

        # --- Demand that RISES when gas is cheap -----------------------------
        # Shedding is penalised, so serving is implicitly worth the strike price.
        # Expansion is the mirror image: a block adds demand and pays its value
        # into the objective as a negative cost, so the solver takes it up only
        # while the marginal cost of supplying it stays below what it is worth.
        # At an interior optimum the nodal price equals the block's value, which
        # is exactly a demand curve. Every block is bounded above, which is what
        # keeps this from reproducing the unbounded export-revenue formulation
        # that drove Perth prices negative in the removed WA build.
        ind_raise_val = {b[0]: b[1] for b in self.ind_raise}
        ind_raise_share = {b[0]: b[2] for b in self.ind_raise}
        gpg_raise_val = {k: v[0] for k, v in self.gpg_raise.items()}
        gpg_raise_share = {k: v[1] for k, v in self.gpg_raise.items()}

        def ind_raise_available(n, blk, t):
            return ind_dem.get((n, t), 0) * ind_raise_share[blk]

        def gpg_raise_available(n, blk, t):
            """Headroom for extra GPG: physical nameplate less what already runs,
            and never more than the cap multiple of baseline demand."""
            share = gpg_raise_share.get((n, blk))
            if share is None:
                return 0.0
            base = gpg_dem.get((n, t), 0)
            nameplate, cap_mult = self.gpg_capacity.get(n, (0.0, 0.0))
            headroom = max(0.0, min(nameplate - base, cap_mult * base))
            return headroom * share
        self._ind_raise_available = ind_raise_available
        self._gpg_raise_available = gpg_raise_available

        m.INDRaise = pyo.Set(initialize=[b[0] for b in self.ind_raise])
        m.GPGRaise = pyo.Set(initialize=sorted({b for _, b in self.gpg_raise}))
        gpg_raise_nodes = sorted({n for (n, _) in self.gpg_raise
                                  if n in m.GPGNodes and n in self.gpg_capacity})
        m.GPGRaiseNodes = pyo.Set(initialize=gpg_raise_nodes)
        m.ind_expand = pyo.Var(m.INDNodes, m.INDRaise, m.T, domain=pyo.NonNegativeReals)
        m.gpg_expand = pyo.Var(m.GPGRaiseNodes, m.GPGRaise, m.T, domain=pyo.NonNegativeReals)
        arc_data = self.arcs.set_index('Name').to_dict('index')
        supply_dict = self.supply.set_index(['Node', 'IsPotential']).to_dict('index')
        exp_data = self.expansion.set_index('Name').to_dict('index')
        storage_caps = self.nodes.set_index('Name')['StorageCapacity'].to_dict()

        def obj_rule(m):
            prod_cost = sum(m.production[s[0], s[1], t] * supply_dict[s]['Cost'] * 1000 for s in m.Supply for t in m.T)
            trans_cost = sum(m.flow[a, t] * arc_data[a]['Cost'] * 1000 for a in m.Arcs for t in m.T)
            shortage_penalty = sum(m.shortage[n, t] * VOLL_PER_GJ * 1000 for n in m.Nodes for t in m.T)
            storage_cost = sum((m.injection[sn, t] + m.withdrawal[sn, t]) * 0.5 * 1000 for sn in m.StorageNodes for t in m.T)
            exp_capex = sum(m.build[e] * exp_data[e]['CapEx'] * 0.08 for e in m.Expansion)
            # Curtailment penalties = strike price ($/GJ) x 1000 (GJ/TJ). Shedding a
            # tier costs its strike price, so a tier only sheds when the marginal
            # cost of supplying it would exceed that strike (GPG $22 < industrial
            # $120 < mass-market value-of-lost-load $300/GJ).
            gpg_pen = sum(m.gpg_curtail[n, t] * self.strike_gpg * 1000 for n in m.GPGNodes for t in m.T)
            ind_pen = sum(m.ind_curtail[n, t] * self.strike_ind * 1000 for n in m.INDNodes for t in m.T)
            # Mass-market blocks: shedding one costs its own willingness to pay, so
            # a block is shed exactly when the marginal cost of serving it exceeds
            # that. The inelastic remainder is priced by shortage_penalty at VOLL.
            mm_pen = sum(m.mm_curtail[n, b, t] * mm_strike[b] * 1000
                         for n in m.MMNodes for b in m.MMBlocks for t in m.T)
            # Benefit of demand taken up when gas is cheap; negative, so the
            # solver serves a block only while supplying it costs less than its
            # value. This makes total_cost NOT comparable with an inelastic run.
            ind_benefit = sum(m.ind_expand[n, b, t] * ind_raise_val[b] * 1000
                              for n in m.INDNodes for b in m.INDRaise for t in m.T)
            gpg_benefit = sum(m.gpg_expand[n, b, t] * gpg_raise_val[n, b] * 1000
                              for n in m.GPGRaiseNodes for b in m.GPGRaise for t in m.T
                              if (n, b) in gpg_raise_val)
            return (prod_cost + trans_cost + shortage_penalty + storage_cost + exp_capex
                    + gpg_pen + ind_pen + mm_pen - ind_benefit - gpg_benefit)
        m.obj = pyo.Objective(rule=obj_rule, sense=pyo.minimize)

        arcs_to = {n: [a for a in m.Arcs if arc_data[a]['To'] == n] for n in m.Nodes}
        arcs_from = {n: [a for a in m.Arcs if arc_data[a]['From'] == n] for n in m.Nodes}
        supply_at = {n: [s for s in m.Supply if s[0] == n] for n in m.Nodes}

        def balance_rule(m, n, t):
            # Total demand at the node = distribution (mass-market) + GPG + large
            # industrial. GPG/industrial may be shed via their curtail variables.
            return (sum(m.production[s[0], s[1], t] for s in supply_at[n]) +
                    (m.reserved_prod[t] if n in lng_source else 0) +
                    sum(m.flow[a, t] for a in arcs_to[n]) +
                    (m.withdrawal[n, t] - m.injection[n, t] if n in m.StorageNodes else 0) +
                    m.shortage[n, t] +
                    (m.gpg_curtail[n, t] if n in m.GPGNodes else 0) +
                    (m.ind_curtail[n, t] if n in m.INDNodes else 0) +
                    (sum(m.mm_curtail[n, b, t] for b in m.MMBlocks) if n in m.MMNodes else 0) ==
                    node_demand.get((n, t), 0) + gpg_dem.get((n, t), 0) + ind_dem.get((n, t), 0) +
                    (sum(m.ind_expand[n, b, t] for b in m.INDRaise) if n in m.INDNodes else 0) +
                    (sum(m.gpg_expand[n, b, t] for b in m.GPGRaise) if n in m.GPGRaiseNodes else 0) +
                    sum(m.flow[a, t] for a in arcs_from[n]))
        m.balance = pyo.Constraint(m.Nodes, m.T, rule=balance_rule)

        # A tier can shed at most its own demand.
        m.gpg_curtail_cap = pyo.Constraint(m.GPGNodes, m.T,
            rule=lambda m, n, t: m.gpg_curtail[n, t] <= gpg_dem.get((n, t), 0))
        m.ind_curtail_cap = pyo.Constraint(m.INDNodes, m.T,
            rule=lambda m, n, t: m.ind_curtail[n, t] <= ind_dem.get((n, t), 0))
        # A mass-market block can shed at most its own slice of that node's load.
        m.mm_curtail_cap = pyo.Constraint(m.MMNodes, m.MMBlocks, m.T,
            rule=lambda m, n, b, t: m.mm_curtail[n, b, t] <= mm_available(n, b, t))
        # A block can add at most its own slice of the available headroom.
        m.ind_expand_cap = pyo.Constraint(m.INDNodes, m.INDRaise, m.T,
            rule=lambda m, n, b, t: m.ind_expand[n, b, t] <= ind_raise_available(n, b, t))
        m.gpg_expand_cap = pyo.Constraint(m.GPGRaiseNodes, m.GPGRaise, m.T,
            rule=lambda m, n, b, t: m.gpg_expand[n, b, t] <= gpg_raise_available(n, b, t))

        def supply_cap_rule(m, node, is_pot, t):
            cap = supply_dict[node, is_pot]['Capacity']
            if is_pot:
                rel_exp = [e for e in m.Expansion if exp_data[e]['Type'] == 'Terminal' and exp_data[e]['Target'] == node]
                return m.production[node, is_pot, t] <= cap * m.build[rel_exp[0]] if rel_exp else m.production[node, is_pot, t] == 0
            declined = cap * ((1 + supply_dict[node, is_pot].get('DeclineRate', 0)) ** (self.year - 2025))
            # The reserved tranche is a slice of the SAME field, priced at zero --
            # not extra gas. Both draw on one physical deliverability limit.
            if node in lng_source:
                return m.production[node, is_pot, t] + m.reserved_prod[t] <= declined
            return m.production[node, is_pot, t] <= declined
        m.supply_cap = pyo.Constraint(m.Supply, m.T, rule=supply_cap_rule)


        def flow_cap_rule(m, a, t):
            extra = sum(m.build[e] * exp_data[e]['NewCapacity'] for e in m.Expansion if exp_data[e]['Target'] == a)
            return m.flow[a, t] <= arc_data[a]['Capacity'] + extra
        m.flow_cap = pyo.Constraint(m.Arcs, m.T, rule=flow_cap_rule)

        # Exports may draw only on commercial gas. Without this the free reserved
        # gas would flow straight to the trains -- they are ordinary demand nodes
        # and cannot tell one molecule from another -- and the reservation would
        # achieve nothing. Exact, not an approximation: the trains have no feed
        # other than these pipes.
        #
        # "Commercial gas" is everything reaching the source node EXCEPT the
        # reserved tranche, which includes gas that transited in from elsewhere.
        # Surat has inflows from Moomba (SWQP) and Silver Springs, and LNG demand
        # exceeds Surat's own deliverability on ~30 days a year, so restricting
        # exports to Surat's own production would wrongly strand the trains on
        # those days and change the no-reservation base case.
        # With the node balance, this is equivalent to requiring the reserved gas
        # to be absorbed by demand at the source or to leave on a non-LNG route.
        commercial_at_source = [s_ for s_ in m.Supply if s_[0] in lng_source and not s_[1]]
        inflows_to_source = [a for n in lng_source for a in arcs_to[n]]
        m.export_eligibility = pyo.Constraint(m.T, rule=lambda m, t:
            sum(m.flow[a, t] for a in lng_arcs) <=
            sum(m.production[s_[0], s_[1], t] for s_ in commercial_at_source)
            + sum(m.flow[a, t] for a in inflows_to_source))

        def storage_cont_rule(m, sn, t):
            cap = storage_caps.get(sn, 0)
            if t == 1: return m.inventory[sn, t] == (cap * 0.5) + m.injection[sn, t] - m.withdrawal[sn, t]
            return m.inventory[sn, t] == m.inventory[sn, t-1] + m.injection[sn, t] - m.withdrawal[sn, t]
        m.storage_cont = pyo.Constraint(m.StorageNodes, m.T, rule=storage_cont_rule)

        def storage_cap_rule(m, sn, t):
            return m.inventory[sn, t] <= storage_caps.get(sn, 0)
        m.storage_cap = pyo.Constraint(m.StorageNodes, m.T, rule=storage_cap_rule)

        # The gas reservation is applied to LNG demand before the model is built
        # (see apply_lng_reservation above and _year_demand in solve.py), so there
        # is no reservation constraint here. It replaced an unfinished flow-side
        # rule -- LNG flow <= 85% of Surat production -- that was never enabled.

        if self.builds_fixed is not None:
            # Two-stage mode: honour the capacity model's schedule exactly.
            for e in m.Expansion:
                m.build[e].fix(1 if e in self.builds_fixed else 0)
        else:
            for e in self.already_built: m.build[e].fix(1)
            if self.year < 2028:
                for e in m.Expansion:
                    if 'Terminal' in e: m.build[e].fix(0)

    def solve(self, mip_gap=0.005):
        m = self.model

        def make_solver(rel_gap=None):
            # Fresh solver instance per solve: the persistent appsi interface
            # caches the model between calls, so a new instance avoids stale
            # state when we fix vars / change domains between solves.
            return solvers.make_solver(rel_gap=rel_gap,
                                       time_limit=solvers.env_time_limit())

        # Neither backend returns duals (nodal prices) for anything but a pure
        # LP — they refuse duals while any integer/binary var is present, even
        # if fixed. So before any dual solve we relax the (already-decided)
        # build binaries to Reals; they stay fixed at 0/1, but the problem is
        # then a true LP.

        # If all expansion vars are already fixed there are no free binaries —
        # solve as pure LP (much faster, duals available immediately).
        all_fixed = all(m.build[e].is_fixed() for e in m.Expansion)
        if all_fixed:
            for e in m.Expansion:
                m.build[e].domain = pyo.Reals
            m.dual = pyo.Suffix(direction=pyo.Suffix.IMPORT)
            res = make_solver().solve(m, tee=False)
            if res.solver.termination_condition in [pyo.TerminationCondition.optimal,
                                                     pyo.TerminationCondition.feasible]:
                self.solved = True
                return "ok"
            self.solved = False
            return str(res.solver.termination_condition)

        # MIP solve (free binaries)
        res = make_solver(rel_gap=mip_gap if mip_gap is not None else 0.005).solve(m, tee=False)
        if res.solver.termination_condition not in [pyo.TerminationCondition.optimal,
                                                     pyo.TerminationCondition.feasible]:
            self.solved = False
            return str(res.solver.termination_condition)

        # Fix binary decisions, relax their domain, then re-solve as a pure LP
        # for dual values (prices)
        for e in m.Expansion:
            m.build[e].fix(pyo.value(m.build[e]))
            m.build[e].domain = pyo.Reals
        m.dual = pyo.Suffix(direction=pyo.Suffix.IMPORT)
        make_solver().solve(m, tee=False)
        self.solved = True
        return "ok"

    def get_results(self):
        m = self.model
        res = {k: [] for k in ['prices', 'production', 'flow', 'storage', 'shortage', 'builds', 'gpg', 'industrial', 'massmarket', 'demand_raise']}
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
        mm_cv = m.mm_curtail.get_values()
        ind_ev = m.ind_expand.get_values()
        gpg_ev = m.gpg_expand.get_values()
        rp = m.reserved_prod.get_values()

        # Nodes whose balance dual is a meaningful price. A node that never has
        # demand and never carries gas has a degenerate dual -- the constraint is
        # 0 == 0, so the solver is free to report anything, and it reports the
        # shortage penalty. Beetaloo (undeveloped supply, no demand) sat at a flat
        # $300/GJ for the whole horizon that way, which is not a price.
        has_demand = {n for n in m.Nodes
                      if any(demand_dict.get((n, t), 0) + self.gpg_demand.get((n, t), 0)
                             + self.ind_demand.get((n, t), 0) > 0 for t in m.T)}
        carries_gas = {s_[0] for s_ in m.Supply for t in m.T if pv[s_[0], s_[1], t] > 0.01}
        for a in m.Arcs:
            if any(fv[a, t] > 0.01 for t in m.T):
                row = self.arcs[self.arcs['Name'] == a].iloc[0]
                carries_gas.update([row['From'], row['To']])
        priced_nodes = has_demand | carries_gas
        
        for t in m.T:
            for n in m.Nodes:
                if n not in priced_nodes:
                    continue
                p = (m.dual[m.balance[n, t]]/1000 ) if hasattr(m, 'dual') and m.balance[n, t] in m.dual else 0.0
                res['prices'].append({'Day': t, 'Node': n, 'Price': float(p)})
                if sv[n, t] > 0.1: res['shortage'].append({'Day': t, 'Node': n, 'Value': float(sv[n, t])})
            for s in m.Supply:
                if pv[s[0], s[1], t] > 0.01: res['production'].append({'Day': t, 'Node': s[0], 'Potential': s[1], 'Value': float(pv[s[0], s[1], t])})
            # The reserved tranche is real gas out of the same field, so it belongs
            # in production totals -- tagged 'Reserved' rather than merged into the
            # commercial rows, so the two can still be told apart.
            if (rp[t] or 0) > 0.01:
                for n in self.lng_source:
                    res['production'].append({'Day': t, 'Node': n, 'Potential': 'Reserved',
                                              'Value': float(rp[t])})
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
            # Mass-market blocks: only rows that actually shed, so an unstressed
            # year costs nothing to store (18 nodes x 3 blocks x 365 days would).
            for n in m.MMNodes:
                for b in m.MMBlocks:
                    cur = float(mm_cv[n, b, t] or 0)
                    if cur > 0.001:
                        dem = self._mm_available(n, b, t)
                        res['massmarket'].append({'Day': t, 'Node': n, 'Block': b, 'Demand': float(dem),
                                                  'Served': float(dem - cur), 'Curtailed': cur})
            # Demand taken up because gas was cheap; only rows that fired.
            for n in m.INDNodes:
                for b in m.INDRaise:
                    v = float(ind_ev[n, b, t] or 0)
                    if v > 0.001:
                        res['demand_raise'].append({'Day': t, 'Node': n, 'Tier': 'Industrial',
                                                    'Block': b, 'Value': v})
            for n in m.GPGRaiseNodes:
                for b in m.GPGRaise:
                    v = float(gpg_ev[n, b, t] or 0)
                    if v > 0.001:
                        res['demand_raise'].append({'Day': t, 'Node': n, 'Tier': 'GPG',
                                                    'Block': b, 'Value': v})

        res['reserved_served_tj'] = float(sum(rp[t] or 0 for t in m.T))
        res['reserved_offered_tj'] = float(sum(self.reserved_by_day.values()))

        for e in m.Expansion:
            if pyo.value(m.build[e]) > 0.5: res['builds'].append(e)
        res['total_cost'] = pyo.value(m.obj)
        res['solved'] = getattr(self, 'solved', True)
        # Collapse the per-day record lists into packed columns straight away: a
        # full batch holds every scenario in memory at once, and the dicts cost
        # orders of magnitude more RAM than the frames they become.
        return results_io.frames_from_year(res)
