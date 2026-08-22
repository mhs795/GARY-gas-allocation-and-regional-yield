"""GARY's optimisation core: one linear program per year of the horizon.

HOW A PRICE IS FORMED HERE, since nothing in this file sets one directly:

  1. Every way of getting or not getting gas is given a dollar value in
     ``obj_rule`` -- production, transport, storage cycling, capex, and the
     penalty for shedding each tier of demand.
  2. ``balance_rule`` requires supply to equal demand at every node on every day.
  3. The solver minimises (1) subject to (2).
  4. The nodal price is the DUAL of (2): what it would cost the system to push one
     more TJ into that node that day. It is read off in ``get_results``.

So a price is never assumed -- it falls out of whichever option is marginal. In an
easy year that is a field cost plus a pipeline tariff, and Melbourne sits a
transport differential above Surat. In a tight year it is whichever tier is next
to be shed, and the node settles at that tier's strike price.

Three levers change what "marginal" can mean, and each has its own header block
below: the LNG NETBACK (exports become a bounded willingness-to-pay block instead
of must-serve demand, so an export price can discipline a domestic one), DATA
CENTRE load (firm demand that neither expands when gas is cheap nor sheds when it
is dear), and DOMESTIC RESERVATION (export volume withheld and offered at $0).
"""
import pyomo.environ as pyo
import pandas as pd
import os
import re

import params as P
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
DUNKELFLAUTE_YEAR = P.get_int('dunkelflaute_year', 2027)
DUNKELFLAUTE_NODE = P.get_str('dunkelflaute_node', "Adelaide")
DUNKELFLAUTE_DAYS = range(P.get_int('dunkelflaute_day_start', 152),
                          P.get_int('dunkelflaute_day_end', 181) + 1)
DUNKELFLAUTE_MULT = P.get('dunkelflaute_mult', 2.75)


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
LNG_NODES = P.get_list('lng_nodes', ['APLNG', 'GLNG', 'QCLNG'])


# --- LNG netback price formation (ACIL Allen / GasMark methodology) ----------
# ACIL Allen produce the wholesale gas price projections behind AEMO's GSOO with
# GasMark, a partial spatial equilibrium LP over supply nodes, demand nodes,
# liquefaction and receiving facilities, solved to maximise producer plus consumer
# surplus. GARY is the same class of model and, with the demand curves on, already
# carries the same objective. The piece it did not carry is the one ACIL Allen
# identify as setting east coast prices:
#
#     "Price formation from 2026 is then based off the LNG netback pricing
#      mechanism, which was the price setting mechanism until the price cap was
#      introduced."   -- ACIL Allen (14 July 2023), s4.1
#
# WITHOUT THIS LEVER the three Queensland trains are ordinary must-serve demand
# nodes: their volume is taken at any price, unserved export is penalised at VOLL
# like lost household load, and no export price enters the model anywhere. Exports
# can therefore never lose to a domestic buyer, and the netback never disciplines a
# domestic price.
#
# WITH IT the trains become a bounded willingness-to-pay block instead. Each train
# may liquefy up to its planned volume and pays the export netback per GJ, entering
# the objective as a negative cost exactly as the GPG and industrial raise blocks
# do. Gas flows to the trains only while it can be got for less than the netback,
# and a domestic buyer who will pay more than the netback outbids the export
# stream. That is the netback acting as a price, which is the whole mechanism.
#
# WHY THIS IS NOT THE WA NEGATIVE-PRICE TRAP. The removed WA build put export
# revenue in the objective and coupled it through a reservation constraint that
# FORCED domestic service, which is unbounded benefit and drove Perth nodal prices
# negative. Here every block is bounded above by the train's own planned volume and
# nothing forces uptake -- the same shape as gpg_expand/ind_expand, which are
# already proven safe. Re-check nodal prices for negatives if this formulation
# changes.
#
# The netback series itself is built by build_lng_prices.py from ACIL Allen's
# published oil price path, oil-linked contract formula, spot share and blended
# Asian LNG price, less the avoidable liquefaction and shipping deduction. See that
# module for what is sourced and for the one number that is not published.
LNG_PRICES_FILE = "lng_prices.csv"

# Regasification terminals: potential supply whose cost is an international price
# (Asian LNG + shipping + regas), not a field development cost.
IMPORT_NODES = P.get_list('import_nodes', ['Port_Kembla', 'Geelong', 'Adelaide'])

# Earliest year an import/field terminal may be commissioned. The capacity layer
# reads the same parameter (capacity_model.py), so the two stages cannot disagree
# about when a terminal is allowed to exist.
TERMINAL_EARLIEST = P.get_int('terminal_earliest', 2028)


def lng_train_nameplate():
    """Each train's liquefaction nameplate, as ``{node: TJ/day}``.

    This is the PHYSICAL ceiling on exports under netback pricing: a train cannot
    liquefy more than it can liquefy, however attractive the netback. It replaces
    the earlier ``export_headroom`` multiple, which was an arbitrary number
    standing in for a capacity GARY already knew.
    """
    total = P.get('lng_nameplate_tj_day', 3680.0)
    shares = P.get_pairs('lng_train_shares',
                         [('APLNG', 0.357), ('GLNG', 0.31), ('QCLNG', 0.333)])
    out = {}
    for node, share in shares:
        try:
            out[node] = total * float(share)
        except (TypeError, ValueError):
            continue
    return out


def lng_foundation_share():
    """Share of planned export volume under take-or-pay foundation SPAs."""
    return P.get('lng_foundation_share', 0.93)


def load_params(data_dir=None):
    """Scalar ACIL Allen assumptions, from the parameters workbook.

    ``data_dir`` is accepted and ignored: the workbook is the source of truth and
    lives at a fixed path (see params.py). The signature is kept so callers that
    already thread a data directory around do not need to care.
    """
    defaults = {'oil_link_fixed': 0.40, 'oil_link_slope': 0.12,
                'fx_usd_per_aud': 0.66, 'gj_per_mmbtu': 1.055,
                'shipping': 0.80, 'regasification': 1.50,
                'export_netback_deduction': 2.87, 'code_price_cap': 12.00}
    return {k: P.get(k, v) for k, v in defaults.items()}


def load_lng_prices(data_dir, baseline, year, code_price_cap=True):
    """ACIL Allen price row for one baseline and year, as a dict of floats.

    Returns ``{}`` when the file is absent, which is what keeps the lever
    optional; model code treats an empty result as "netback pricing unavailable".
    ``code_price_cap`` selects the capped or uncapped netback column -- see
    build_lng_prices.py for why the cap is applied to the netback rather than to
    domestic prices, and for ACIL Allen's own scepticism that it binds.
    """
    try:
        df = pd.read_csv(os.path.join(data_dir, LNG_PRICES_FILE))
    except FileNotFoundError:
        return {}
    sub = df[(df['Scenario'] == baseline) & (df['Year'] == int(year))]
    if sub.empty:
        # Outside the published horizon: hold the nearest year rather than
        # extrapolate an oil-linked price decades past its anchors.
        sub = df[df['Scenario'] == baseline]
        if sub.empty:
            return {}
        sub = sub.iloc[[(sub['Year'] - int(year)).abs().idxmin() - sub.index[0]]]
    row = sub.iloc[0].to_dict()
    row['Netback_AUD_GJ'] = float(row['Netback_Capped_AUD_GJ' if code_price_cap
                                      else 'Netback_Uncapped_AUD_GJ'])
    return row

# Reservation shares offered by the dashboard slider (fraction of export volume).
RESERVATION_LEVELS = P.get_list('reservation_levels', [0.05, 0.10, 0.20, 0.30], cast=float)


# --- Data centre gas demand --------------------------------------------------
# A "what if" lever for hyperscale data centre load: an annual volume in PJ for
# NSW and for VIC, switched on in a chosen year and held for the rest of the
# horizon. It answers "what would N PJ/yr of new data centre gas demand in these
# two states do to the east coast market", nothing finer.
#
# It enters as LARGE INDUSTRIAL demand. That is what a data centre's gas call is
# -- a firm, round-the-clock load at a handful of large sites, not
# distribution-level household gas -- and it has two consequences worth knowing
# when reading a result: it does NOT curtail with the rest of that tier (see the
# ind_curtail_cap constraint -- the load is firm, and reaches the shortage
# variable at VOLL rather than standing down at strike_ind), and it reaches the
# capacity layer through the same industrial series, so the investment model
# sizes pipe and storage for it.
#
# The annual volume is spread across the year on the NODE'S OWN GPG DAILY SHAPE,
# not evenly and not on the industrial shape it sits inside. A data centre's gas
# call tracks electricity system conditions -- it is there to firm a load whose
# cost and availability follow the power market -- so the gas-powered generation
# profile at the same node is the closest shape GARY carries. Concretely, day d
# gets ``PJ * 1000 * gpg[node, d] / sum(gpg[node, :])``, which preserves the
# annual total exactly whatever the shape.
#
# A node with no GPG demand in that year falls back to a flat spread; there is no
# such node today (Sydney and Melbourne both carry GPG in every GSOO baseline),
# but the fallback keeps the lever from silently dropping volume if that changes.
#
# CAVEAT: the GPG shape is VERY peaky, because gas generation runs intermittently.
# 50 PJ/yr at Sydney arrives as anything from ~0.05 to ~580 TJ on a given day,
# against ~137 TJ/d spread evenly. That is the right shape if the load is read as
# gas generation firming a data centre; it materially overstates day-to-day
# variation if it is meant to be the site's own boilers or fuel cells. The peak
# days are what the capacity layer sizes the network against, so this choice
# shows up in builds, not just in dispatch.
#
# It steps on IN FULL at the start year, with no ramp. The slider asks "what if
# this much load is there from year X"; a built-in ramp would quietly answer a
# different question, and a ramp that matters to a conclusion belongs in the
# demand trajectory, not in a sensitivity lever.
#
# The volume is also kept as its own series (``GasMarketModel.dc_demand``) so it
# can be netted out of the industrial tier in both directions. Its consumption is
# set by its compute, not by the gas price, so it neither takes up more gas when
# gas is cheap (netted out of the *expansion* headroom the raise blocks are a
# share of) nor stands down when gas is dear (netted out of the *curtailment*
# cap). Everything else about it is ordinary industrial load.
DATACENTRE_STATE_NODE = P.get_pairs('datacentre_state_node',
                                    [('NSW', 'Sydney'), ('VIC', 'Melbourne')])


def datacentre_profile(spec, year, gpg_demand):
    """Data centre load for one year, as ``{(node, day): TJ}``.

    ``spec`` is ``{'NSW': PJ/yr, 'VIC': PJ/yr, 'start_year': yyyy}``. Returns an
    empty dict when the lever is off, both volumes are zero, or the year is
    before the start year.

    Each state's annual volume is distributed over the 365 days in proportion to
    that node's GPG demand, so the shape follows gas-fired generation rather than
    heating load. The daily figures sum to the annual volume by construction.
    """
    if not spec:                                        # lever off entirely
        return {}
    if int(year) < int(spec.get('start_year', 2025)):   # not switched on yet
        return {}
    out = {}
    for state, node in DATACENTRE_STATE_NODE:           # NSW->Sydney, VIC->Melbourne
        pj = float(spec.get(state, 0) or 0)             # this state's annual volume
        if pj <= 0:
            continue
        total_tj = pj * 1000.0                          # PJ/yr -> TJ/yr
        # Take the node's own gas-fired generation profile as the daily shape.
        shape = {d: gpg_demand.get((node, d), 0.0) for d in range(1, 366)}
        gpg_total = sum(shape.values())
        if gpg_total > 0:
            # Normalise the shape to 1.0 and scale by the annual volume, so the
            # daily figures sum back to exactly total_tj. See the CAVEAT above:
            # this shape is very peaky, and the peaks are what the capacity layer
            # sizes the network against.
            for d, v in shape.items():
                out[(node, d)] = total_tj * v / gpg_total
        else:
            # No GPG at this node this year: nothing to take a shape from, so
            # spread it evenly rather than dropping the volume.
            flat = total_tj / 365.0
            for d in range(1, 366):
                out[(node, d)] = flat
    return out


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
VOLL_PER_GJ = P.get('voll_per_gj', 300.0)

# Southern winter window (gas day-of-year) used by the Winter lever, and by the
# per-block WinterScale that damps the price response through the heating season.
WINTER_DAYS = range(P.get_int('winter_day_start', 150),
                    P.get_int('winter_day_end', 250) + 1)


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


def apply_lng_reservation(demand_df, share, respect_contracts=True, scale_demand=True):
    """Divert ``share`` (0-1) of LNG export volume to the domestic market.

    Returns ``(demand frame, TJ diverted, {day: TJ reserved}, share applied)``.
    The per-day series is what the zero-cost supply tranche is sized against, so
    the volume released each day matches the export volume withheld that day
    rather than an annual average. Applied after the Winter/LNG scenario levers,
    so the share bites on the export volume actually planned under the scenario
    rather than on the raw baseline.

    ``respect_contracts=True`` (the default) caps the reservation at the
    UNCONTRACTED share of export volume, ``1 - lng_foundation_share``. That is how
    the Heads of Agreement with the east coast LNG exporters actually works -- it
    covers gas the producers have not already sold, not their foundation SPAs --
    and the ACCC's quarterly outlook is written in exactly those terms: what
    matters to the domestic balance is what the producers do with their
    *uncontracted* gas.

    The practical consequence is blunt and worth seeing: with the foundation share
    at 0.93 only ~7% of export volume is contestable, so **every reservation level
    above 7% is capped**, and 5%/10%/20%/30% all collapse towards the same
    outcome. The applied share is returned rather than the requested one precisely
    so that gap is reported rather than hidden.

    ``respect_contracts=False`` restores the earlier behaviour: the share is taken
    off ALL export volume, foundation contracts included. That is a policy that
    breaks take-or-pay contracts, which is a real (if drastic) option, so it is a
    lever rather than something the model quietly refuses to represent.

    ``scale_demand=False`` computes the reserved volume but leaves the demand frame
    alone. That is what netback pricing needs, and the reason is a trap worth
    naming: under netback pricing the train's demand row is the PLANNED volume that
    the foundation/spot split is derived from, so scaling it down here would shrink
    the foundation leg and *enlarge* the spot headroom (nameplate less foundation).
    A reservation would then have converted contracted export into spot export and
    left total exports untouched -- the opposite of withholding gas. The model
    instead subtracts the reserved volume from the export ceiling directly; see
    build_model.
    """
    if not share:
        return demand_df, 0.0, {}, 0.0
    applied = share
    if respect_contracts:
        uncontracted = max(0.0, 1.0 - lng_foundation_share())
        applied = min(share, uncontracted)
    if not applied:
        return demand_df, 0.0, {}, 0.0
    mask = demand_df['Node'].isin(LNG_NODES)
    diverted = float(demand_df.loc[mask, 'Demand'].sum()) * applied
    by_day = (demand_df.loc[mask].groupby('Day')['Demand'].sum() * applied).to_dict()
    if scale_demand:
        demand_df = demand_df.copy()
        demand_df.loc[mask, 'Demand'] *= (1.0 - applied)
    return (demand_df, diverted, {int(d): float(v) for d, v in by_day.items()},
            float(applied))


class GasMarketModel:
    def __init__(self, nodes_df, arcs_df, supply_df, demand_df, expansion_df, contracts_df=None, year=2025, already_built=None, baseline="StepChange", dunkelflaute=False, builds_fixed=None, elastic_demand=False, reserved_by_day=None, datacentre=None, netback_pricing=False, code_price_cap=True, netback_scenario=None,
                 reservation_applied=0.0, respect_contracts=True):
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
        # Data centre lever: {'NSW': PJ/yr, 'VIC': PJ/yr, 'start_year': yyyy}.
        self.datacentre = dict(datacentre) if datacentre else None
        # LNG netback price formation (ACIL Allen / GasMark) -- see the header
        # block. Off by default: it changes every scenario, not just export ones,
        # so the must-serve-export case stays the comparison baseline.
        self.netback_pricing = netback_pricing
        self.code_price_cap = code_price_cap
        # Which of ACIL Allen's published price paths the netback is struck off.
        # Under netback pricing the Global LNG lever selects this instead of
        # scaling export volume -- see lng_price_scenario() in solve.py. None
        # means the run's own GSOO baseline.
        self.netback_scenario = netback_scenario or baseline
        # Under netback pricing the reservation is not applied to the demand frame
        # (see apply_lng_reservation); the model subtracts it from the export
        # ceiling instead, so it needs the share and the contracts rule here.
        self.reservation_applied = float(reservation_applied or 0.0)
        self.respect_contracts = respect_contracts
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
        # Data centre load, added to the large-industrial tier (see the header
        # block). Held separately as well because the industrial raise blocks are
        # a share of price-responsive industrial load and this load is firm.
        self.dc_demand = datacentre_profile(self.datacentre, self.year, self.gpg_demand)
        # ADDED to the industrial tier, not substituted into it: the data centre is
        # new load on top of the refineries and smelters already there. Keeping
        # dc_demand as its own series alongside is what lets the two nettings later
        # in build_model() find it again -- out of the raise headroom (it will not
        # consume more when gas is cheap) and out of the curtailment cap (it will
        # not stand down when gas is dear).
        for _key, _tj in self.dc_demand.items():
            self.ind_demand[_key] = self.ind_demand.get(_key, 0.0) + _tj

        # --- ACIL Allen price series -------------------------------------
        self.lng_prices = (load_lng_prices(data_dir, self.netback_scenario, self.year,
                                           self.code_price_cap)
                           if self.netback_pricing else {})
        if self.netback_pricing and not self.lng_prices:
            # Falling back to must-serve exports would look exactly like the
            # lever having no effect, which is indistinguishable from a real
            # null result -- the same trap the elastic-demand lever raises on.
            raise RuntimeError(
                "netback_pricing=True but no price series was found in "
                "data/lng_prices.csv. Run: python src/build_lng_prices.py")
        # The number the whole methodology reduces to: what a Queensland train can
        # net per GJ after liquefaction and shipping are deducted from the Asian
        # LNG price. Everything upstream of this (oil path -> oil-linked contract
        # formula -> blended Asian price -> deduction) happens in
        # build_lng_prices.py; by the time the model sees it, it is one $/GJ figure
        # for the year, and it functions as the reserve price at the export door.
        self.netback = float(self.lng_prices.get('Netback_AUD_GJ', 0.0))
        # Physical liquefaction capacity and the take-or-pay share of planned
        # volume: together these replace the old export_headroom multiple with the
        # two things that actually bound an export decision.
        self.lng_nameplate = lng_train_nameplate() if self.netback_pricing else {}
        self.foundation_share = lng_foundation_share() if self.netback_pricing else 0.0
        if self.netback_pricing:
            # LNG imports are priced on ACIL Allen's injection cost (Asian LNG
            # + shipping + regasification, Table 2.1) rather than the single flat
            # figure in supply.csv, so the import terminal competes on the same
            # international price the exports are valued at. Copied first: the
            # foresight solve hands one supply frame to all 26 years.
            injection = float(self.lng_prices.get('Import_Injection_AUD_GJ', 0.0))
            if injection > 0:
                self.supply = self.supply.copy()
                _imp = (self.supply['Node'].isin(IMPORT_NODES)
                        & self.supply['IsPotential'].astype(bool))
                self.supply.loc[_imp, 'Cost'] = injection
        try:
            strikes = pd.read_csv(os.path.join(data_dir, "curtailment_params.csv")
                                  ).set_index('Tier')['StrikePrice'].to_dict()
        except FileNotFoundError:
            strikes = {}
        # THE MERIT ORDER OF SHEDDING, and with it the ladder domestic prices climb
        # in a tight year. Each strike is what that tier is assumed to be willing to
        # pay before it stops taking gas, so it is both a penalty in the objective
        # and the price a scarce node settles at once that tier is the marginal one:
        #
        #   $22/GJ   GPG          -- switches to another fuel or another generator
        #   $120/GJ  industrial   -- stops the process line
        #   mass-market            -- NOT one strike but a ladder of blocks, each
        #                            with its own willingness to pay, fitted to a
        #                            constant-elasticity curve anchored at $13.56/GJ
        #                            (see load_demand_blocks). Blocks drop out one
        #                            at a time as the price climbs, which is what
        #                            makes this a demand curve rather than a switch.
        #   $300/GJ  VOLL         -- nothing left to shed, load simply goes unserved
        #
        # Firm load has no rung on this ladder: data centres and foundation LNG
        # cargoes cannot shed at a strike and go straight to VOLL, which is what
        # makes them outbid everything else for scarce gas.
        self.strike_gpg = float(strikes.get('GPG', P.get('strike_gpg_default', 22.0)))
        self.strike_ind = float(strikes.get('Industrial', P.get('strike_ind_default', 120.0)))
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

        # --- LNG exports: foundation contracts + a contestable spot tail ---
        # See the netback header block. Planned export volume splits in two, the
        # way the east coast market actually sells gas:
        #
        #   FOUNDATION  the take-or-pay share of planned volume (long-term SPAs).
        #               Price-insensitive by construction -- the cargo goes
        #               whatever the netback -- so it stays in node_demand as
        #               ordinary must-serve demand.
        #   SPOT TAIL   everything else the train could liquefy, bid at the
        #               netback. Bounded above by PHYSICAL LIQUEFACTION NAMEPLATE
        #               less the foundation volume, not by an arbitrary multiple:
        #               a train can absorb cheap gas up to the capacity it has and
        #               not one TJ further.
        #
        # This is what lets the netback ANCHOR domestic prices rather than only cap
        # them in scarcity. In a well-supplied year the spare liquefaction absorbs
        # cheap gas until the domestic price is bid up towards export parity; in a
        # stressed year a domestic buyer outbids the spot tail and that gas stays
        # home. Neither can touch the foundation volume, which is the point.
        #
        # Volumes are read AFTER the reservation has been applied to the demand
        # frame, so a reservation still carves its share out first.
        #
        # A RESERVATION shrinks the export ceiling rather than the planned volume:
        # reserved gas is gas the trains may not liquefy. It comes out of the
        # uncontracted tail first; only with respect_contracts=False can the excess
        # beyond the uncontracted share cut into the foundation leg as well.
        # lng_planned  : what the demand frame said each train would export, kept
        #                for reporting (the "planned" denominator on the dashboard).
        # lng_spot_cap : the ceiling on the contestable block, per train per day.
        lng_planned, lng_spot_cap = {}, {}
        from_foundation = 0.0
        if self.netback_pricing:
            # Share of export volume the reservation actually withholds. May be
            # less than the slider asked for -- see reservation_applied.
            applied = self.reservation_applied
            # The portion of planned volume NOT under take-or-pay, i.e. the part a
            # reservation can take without breaking a contract. At a 93% foundation
            # share this is 7%, which is why a 20% slider can only deliver 7%.
            uncontracted = max(0.0, 1.0 - self.foundation_share)
            # How much of the PLANNED volume comes out of the foundation leg. The
            # rest comes out of the tail -- but note the TOTAL export ceiling below
            # is reduced by the full applied share either way. Breaking contracts
            # changes how much export is committed, not how much is withheld.
            from_foundation = (0.0 if self.respect_contracts
                               else max(0.0, applied - uncontracted))
            for n in LNG_NODES:
                # Physical liquefaction ceiling for this train, TJ/day.
                nameplate = self.lng_nameplate.get(n, 0.0)
                for t in range(1, 366):
                    # Whatever the demand frame has this train exporting today.
                    planned = node_demand.get((n, t))
                    if planned is None:
                        continue
                    lng_planned[(n, t)] = planned
                    # The take-or-pay leg: sold years ago, lifted regardless of
                    # today's netback. Reduced only if respect_contracts=False has
                    # let the reservation cut into contracted volume.
                    foundation = max(0.0, planned * (self.foundation_share - from_foundation))
                    # OVERWRITE the train's entry in node_demand with just the
                    # foundation leg. This is the pivot of the whole methodology:
                    # what stays in node_demand is must-serve and is taken at any
                    # price, and what has been removed becomes the price-responsive
                    # lng_export block below. Before this lever existed the full
                    # planned volume sat here, which is why exports could never
                    # lose a bidding war to a domestic buyer.
                    node_demand[(n, t)] = foundation
                    # Total export ceiling is nameplate less the reserved
                    # volume; the spot block gets whatever is left after the
                    # foundation leg. Using the full applied share here (not just
                    # the part taken from the tail) is what makes a bigger
                    # reservation actually export less.
                    lng_spot_cap[(n, t)] = max(
                        0.0, nameplate - foundation - planned * applied)
        m.LNGNodes = pyo.Set(initialize=[n for n in LNG_NODES
                                         if n in self.nodes['Name'].tolist()]
                             if self.netback_pricing else [])
        # The contestable spot tail: how much each train chooses to liquefy today.
        # Free to sit at zero -- nothing forces uptake, which is what keeps this
        # from repeating the WA negative-price trap described in the header.
        m.lng_export = pyo.Var(m.LNGNodes, m.T, domain=pyo.NonNegativeReals)
        # Bounded above by spare liquefaction capacity. Bounded, plus optional, plus
        # paid at a finite price = a well-behaved willingness-to-pay block.
        m.lng_export_cap = pyo.Constraint(m.LNGNodes, m.T,
            rule=lambda m, n, t: m.lng_export[n, t] <= lng_spot_cap.get((n, t), 0.0))
        # $/GJ the export block bids. One number for the whole year, from ACIL
        # Allen's oil-linked series; it is the price a domestic buyer must beat.
        netback = self.netback
        self._lng_planned = lng_planned
        # The foundation leg AFTER any contract-breaking reservation, so the
        # reported split matches what the balance constraint actually served.
        _served_share = (max(0.0, self.foundation_share - from_foundation)
                         if self.netback_pricing else 0.0)
        self._lng_foundation = {k: v * _served_share for k, v in lng_planned.items()}

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
        dc_dem = self.dc_demand

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
            # Data centre load is firm: it is served or it is curtailed, it does
            # not take up more gas because gas got cheap, so it is netted out of
            # the headroom the raise blocks are a share of.
            base = ind_dem.get((n, t), 0) - dc_dem.get((n, t), 0)
            return max(0.0, base) * ind_raise_share[blk]

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
            # THE OBJECTIVE IS WHERE EVERY PRICE IN GARY COMES FROM. Nothing sets a
            # price directly. The solver minimises this expression subject to the
            # balance constraint, and the nodal price reported for (node, day) is
            # that constraint's DUAL -- the cost of forcing one more TJ into that
            # node on that day. So each term below is really a statement about what
            # gas is worth to somebody, and the price is whatever the marginal one
            # turns out to be.
            #
            # Every term is in DOLLARS. Volumes are TJ and prices are $/GJ, hence
            # the * 1000 (GJ per TJ) on almost every line; the dual therefore comes
            # out in $/TJ and is divided by 1000 again in get_results().
            #
            # Cost of getting gas out of the ground, per field, per day.
            prod_cost = sum(m.production[s[0], s[1], t] * supply_dict[s]['Cost'] * 1000 for s in m.Supply for t in m.T)
            # Cost of moving it: each arc's tariff x what flows down it. This is the
            # term that makes a Melbourne price differ from a Surat price.
            trans_cost = sum(m.flow[a, t] * arc_data[a]['Cost'] * 1000 for a in m.Arcs for t in m.T)
            # Unserved demand, valued at the value of lost load ($300/GJ). This is
            # the backstop that keeps the LP feasible: there is always the option of
            # simply not serving a node, at a price nobody wants to pay. It is also
            # what firm load (data centres, foundation LNG cargoes) falls through to
            # when it cannot be shed -- see ind_curtail_cap.
            shortage_penalty = sum(m.shortage[n, t] * VOLL_PER_GJ * 1000 for n in m.Nodes for t in m.T)
            # A small ($0.50/GJ) round-trip charge on storage, so the solver cycles
            # inventory only when the seasonal price spread justifies it.
            storage_cost = sum((m.injection[sn, t] + m.withdrawal[sn, t]) * 0.5 * 1000 for sn in m.StorageNodes for t in m.T)
            # Annualised capex for anything built, at 8%/yr. build[e] is binary,
            # which is what makes the capacity layer a MILP rather than an LP.
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
            # Export revenue at the LNG netback. Negative, like the demand-raise
            # benefits: gas reaches a train only while supplying it costs less
            # than the netback. Bounded by lng_export_cap and forced by nothing,
            # which is what separates it from the WA formulation.
            lng_benefit = sum(m.lng_export[n, t] * netback * 1000
                              for n in m.LNGNodes for t in m.T)
            # Costs are added, benefits subtracted. Read the sign as "what would
            # the system pay to avoid this": it pays to produce and to ship, it
            # pays dearly to shed, and it is paid by an export cargo or by an
            # industrial user taking up cheap gas. Minimising the whole thing is
            # equivalent to maximising producer plus consumer surplus, which is the
            # same objective GasMark solves -- the point of the netback section
            # above. Because benefits are bounded above (every raise and export
            # block has a cap), the sum cannot run away negative.
            return (prod_cost + trans_cost + shortage_penalty + storage_cost + exp_capex
                    + gpg_pen + ind_pen + mm_pen - ind_benefit - gpg_benefit
                    - lng_benefit)
        m.obj = pyo.Objective(rule=obj_rule, sense=pyo.minimize)

        arcs_to = {n: [a for a in m.Arcs if arc_data[a]['To'] == n] for n in m.Nodes}
        arcs_from = {n: [a for a in m.Arcs if arc_data[a]['From'] == n] for n in m.Nodes}
        supply_at = {n: [s for s in m.Supply if s[0] == n] for n in m.Nodes}

        def balance_rule(m, n, t):
            # ONE CONSTRAINT PER NODE PER DAY, and the single most important line in
            # the model: everything arriving must equal everything leaving. Its DUAL
            # is the nodal price -- the marginal cost of one more TJ here today --
            # so every price on the dashboard is read off this equation.
            #
            # A shed variable appears on the SUPPLY side, which looks odd until you
            # read it as "demand I did not have to meet counts the same as gas I
            # found". That is exactly why shedding has to be priced in the objective:
            # otherwise the solver would shed everything for free.
            return (
                    # --- gas arriving ---------------------------------------
                    # Produced from fields at this node.
                    sum(m.production[s[0], s[1], t] for s in supply_at[n]) +
                    # The reserved tranche, if a domestic reservation is running and
                    # this is the field it is carved out of.
                    (m.reserved_prod[t] if n in lng_source else 0) +
                    # Piped in from elsewhere.
                    sum(m.flow[a, t] for a in arcs_to[n]) +
                    # Net storage withdrawal (negative when injecting).
                    (m.withdrawal[n, t] - m.injection[n, t] if n in m.StorageNodes else 0) +
                    # --- demand not actually met -----------------------------
                    # Unserved, at VOLL. The backstop of last resort.
                    m.shortage[n, t] +
                    # Gas-powered generation stood down, at strike_gpg.
                    (m.gpg_curtail[n, t] if n in m.GPGNodes else 0) +
                    # Large industrial stood down, at strike_ind. Capped BELOW the
                    # tier's demand by ind_curtail_cap so firm data centre load
                    # cannot be shed here -- it must find gas or go to shortage.
                    (m.ind_curtail[n, t] if n in m.INDNodes else 0) +
                    # Mass-market blocks that priced themselves out, each at its own
                    # willingness to pay.
                    (sum(m.mm_curtail[n, b, t] for b in m.MMBlocks) if n in m.MMNodes else 0) ==
                    # --- gas leaving -----------------------------------------
                    # Distribution/mass-market load, GPG load, industrial load
                    # (data centre volume already folded into the last of these).
                    node_demand.get((n, t), 0) + gpg_dem.get((n, t), 0) + ind_dem.get((n, t), 0) +
                    # The contestable LNG spot tail, bid at the netback. Unlike the
                    # foundation volume sitting inside node_demand above, this is a
                    # CHOICE: the solver liquefies only while gas costs less than
                    # the netback, which is the mechanism that lets an export price
                    # discipline a domestic one.
                    (m.lng_export[n, t] if n in m.LNGNodes else 0) +
                    # Extra demand taken up because gas turned out cheap.
                    (sum(m.ind_expand[n, b, t] for b in m.INDRaise) if n in m.INDNodes else 0) +
                    (sum(m.gpg_expand[n, b, t] for b in m.GPGRaise) if n in m.GPGRaiseNodes else 0) +
                    # Piped onward to somewhere else.
                    sum(m.flow[a, t] for a in arcs_from[n]))
        m.balance = pyo.Constraint(m.Nodes, m.T, rule=balance_rule)

        # A tier can shed at most its own demand.
        m.gpg_curtail_cap = pyo.Constraint(m.GPGNodes, m.T,
            rule=lambda m, n, t: m.gpg_curtail[n, t] <= gpg_dem.get((n, t), 0))
        # Data centre load is FIRM and is netted out of what the industrial tier is
        # allowed to shed. It rides inside ind_demand so that it is transported and
        # priced like any other large industrial load, but it is not price-responsive
        # in either direction: it does not take up more gas when gas is cheap (see
        # ind_raise_available) and it does not stand down when gas is dear, because
        # its consumption is set by its compute.
        #
        # That does not make it unshortable. If gas physically cannot reach it the
        # `shortage` variable still absorbs the volume, at VOLL rather than at the
        # industrial strike -- which is the right price for load that had no choice
        # but to keep running. The practical effect is that a data centre outbids
        # the refinery next to it for scarce winter gas instead of being shed
        # alongside it at the same strike.
        m.ind_curtail_cap = pyo.Constraint(m.INDNodes, m.T,
            rule=lambda m, n, t: m.ind_curtail[n, t] <= max(
                0.0, ind_dem.get((n, t), 0) - dc_dem.get((n, t), 0)))
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
                # Sum over every terminal fronting this node rather than taking the
                # first -- see the matching note in capacity_model.py. Rival FSRUs
                # at one landing point are kept from stacking by build_group_once.
                if not rel_exp:
                    return m.production[node, is_pot, t] == 0
                return m.production[node, is_pot, t] <= sum(
                    m.build[e] * exp_data[e]['NewCapacity'] for e in rel_exp)
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
            if self.year < TERMINAL_EARLIEST:
                # Test the project's TYPE, not its name. This used to read
                # `'Terminal' in e`, which only ever matched Port_Kembla_Terminal
                # and silently let every other import/field terminal be built from
                # 2025 -- harmless while Beetaloo_Dev was the only other one, but
                # not once the candidate set carries four more.
                for e in m.Expansion:
                    if exp_data[e]['Type'] == 'Terminal': m.build[e].fix(0)
            # Rival projects delivering the same capacity cannot both be built --
            # see build_group_once in capacity_model.py. Only needed on the free
            # (myopic) path; the two-stage path fixes builds from the schedule.
            groups = {}
            for e in m.Expansion:
                g = exp_data[e].get('Group')
                if isinstance(g, str) and g.strip():
                    groups.setdefault(g.strip(), []).append(e)
            if groups:
                m.ExpGroup = pyo.Set(initialize=sorted(groups))
                m.build_group_once = pyo.Constraint(m.ExpGroup, rule=lambda m, g:
                    sum(m.build[e] for e in groups[g]) <= 1)

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
        res = {k: [] for k in ['prices', 'production', 'flow', 'storage', 'shortage', 'builds', 'gpg', 'industrial', 'massmarket', 'demand_raise', 'lng']}
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
        lng_ev = m.lng_export.get_values()

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
                # THE PRICE. m.dual[...] is the shadow price of this node-day's
                # balance constraint: the change in total system cost from forcing
                # one more unit of gas into this node today. Volumes are TJ and the
                # objective is dollars, so the dual is $/TJ -- divide by 1000 for
                # the $/GJ everything downstream reports.
                #
                # It equals the marginal cost of whatever the system would do to
                # find that extra TJ: run a dearer field, pay a pipeline tariff,
                # pull from storage, outbid an export cargo at the netback, or, when
                # nothing physical is left, shed a tier at its strike or fall to
                # VOLL. That is why nodal prices differ by transport cost in an easy
                # year and converge on a strike price in a tight one.
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
            # LNG exports under netback pricing: planned volume vs what was
            # actually worth liquefying at the netback. Empty when the lever is
            # off, so an inelastic run stores nothing extra.
            for n in m.LNGNodes:
                planned = self._lng_planned.get((n, t), 0.0)
                foundation = self._lng_foundation.get((n, t), 0.0)
                spot = float(lng_ev[n, t] or 0)
                if planned > 0.001:
                    # Foundation volume is served through node_demand, so total
                    # exports are the contracted leg plus whatever spot cleared.
                    # Forgone is measured against PLANNED volume, so it stays
                    # comparable with a must-serve run.
                    res['lng'].append({'Day': t, 'Node': n, 'Planned': float(planned),
                                       'Foundation': foundation, 'Spot': spot,
                                       'Exported': foundation + spot,
                                       'Forgone': float(max(0.0, planned - foundation - spot))})
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

        # Data centre load carried this year, TJ. Zero when the lever is off or
        # the year is before its start year.
        # Netback price formation: what the export stream was worth and how much
        # of it actually cleared. Zero/absent when the lever is off.
        res['netback_aud_gj'] = float(self.netback) if self.netback_pricing else 0.0
        res['lng_price_aud_gj'] = float(self.lng_prices.get('LNG_Asia_AUD_GJ', 0.0))
        res['lng_spot_tj'] = float(sum(lng_ev[n, t] or 0
                                       for n in m.LNGNodes for t in m.T))
        res['lng_foundation_tj'] = float(sum(self._lng_foundation.values()))
        res['lng_exported_tj'] = res['lng_foundation_tj'] + res['lng_spot_tj']
        res['lng_planned_tj'] = float(sum(self._lng_planned.values()))
        res['lng_foundation_share'] = float(self.foundation_share)
        res['netback_scenario'] = self.netback_scenario if self.netback_pricing else None
        res['netback_pricing'] = self.netback_pricing
        res['datacentre_tj'] = float(sum(self.dc_demand.values()))
        # Per-node split as well as the total: the map hover card names the load
        # sitting at each node, and a single system-wide figure cannot say
        # whether it landed on Sydney or Melbourne.
        _dc_by_node = {}
        for (_n, _d), _v in self.dc_demand.items():
            _dc_by_node[_n] = _dc_by_node.get(_n, 0.0) + float(_v)
        res['datacentre_by_node_tj'] = _dc_by_node
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
