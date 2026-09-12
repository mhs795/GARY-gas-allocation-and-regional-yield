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

THIS IS A MARGINAL COST, NOT A WHOLESALE PRICE, AND THE DIFFERENCE IS BIGGEST
EARLY. Before ~2031 no basin has depleted, so every field sits on AEMO's 2P cost --
which AEMO defines as "largely marginal operating costs, royalties and tax" -- and
both parity anchors (export netback, import injection) sit ABOVE the domestic
price, so neither binds. A 2026 Melbourne dual is therefore Gippsland's opex plus a
tariff: a system marginal cost, roughly half a contract price, and not a number to
quote as a wholesale gas price. From ~2032 the basins step to their 2C costs (which
AEMO defines to include drilling, plant capital and a return) and the import
terminal builds, so the marginal unit starts carrying full costs and the model's
prices become comparable with ACIL Allen's. See "What a GARY price is, and when it
is not a wholesale price" in the README, and acil_segment_prices.py.

Three levers change what "marginal" can mean, and each has its own header block
below: the LNG NETBACK (exports become a bounded willingness-to-pay block instead
of must-serve demand, so an export price can discipline a domestic one), DATA
CENTRE load (firm demand that neither expands when gas is cheap nor sheds when it
is dear), and DOMESTIC RESERVATION (export volume withheld and offered at $0).
"""
import pyomo.environ as pyo
import pandas as pd
import os

import datacentre_series
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
# nothing forces uptake. Re-check nodal prices for negatives if this
# formulation changes.
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


# GBB facility name -> GARY LNG node. The Bulletin Board is the source of record for
# what each train can physically liquefy, and GARY already carries the file.
GBB_LNG_FACILITY = {
    'australia pacific lng': 'APLNG',
    'glng (curtis island)': 'GLNG',
    'qclng lng plant': 'QCLNG',
}


def lng_train_nameplate():
    """Each train's liquefaction nameplate, as ``{node: TJ/day}``.

    This is the PHYSICAL ceiling on exports under netback pricing: a train cannot
    liquefy more than it can liquefy, however attractive the netback.

    READ PER FACILITY FROM THE GAS BULLETIN BOARD, not split out of one total. The
    total it used to be split out of was 3,680 TJ/day, and the trouble with that
    number is not that it is unsourced but that it is **too small to serve GARY's
    own input**: the 2026 GSOO's LNG consumption is 3,757 TJ/day in 2025 and peaks
    at 3,875 in 2029, and the trains actually consumed 3,942 TJ/day in 2024. A
    physical ceiling below observed physical throughput is not a ceiling, it is an
    error, and it was silently clipping ~27 PJ/yr off every early year and holding
    every scenario at exactly 1,343 PJ/yr.

    ``GasBBNameplateRatingCurrent.CSV`` carries an MDQ per export facility, and for
    two of the three it carries TWO, which the Bulletin Board itself distinguishes:

        GLNG   1,384  "Capacity that can be processed by LNG plant"
               1,497  "Capacity that can be received by LNG plant"
        QCLNG  1,420  "...can process to a liquefied state on a gas day"
               1,573  "...can receive from a pipeline on a gas day"
        APLNG  1,591  "2023 Name Plate Capacity"   (one row)

    GARY's constraint is on LIQUEFACTION, so the PROCESS figure is the right one --
    the smaller of the pair, which is also the conservative read. Total 4,395 TJ/day
    against the old 3,680.

    THEN SCALED BY AN AVAILABILITY FACTOR, because an MDQ is a DAY's maximum and
    GARY has no maintenance model. Left at the raw MDQ the model runs the fleet at
    100% for 365 days whenever the netback is good -- 1,604 PJ in 2030, which no
    year on record comes close to. AEMO's own Figure 19 actuals against that same
    1,604 PJ: 82.6% (2019), 83.4%, 87.7%, 84.6%, 85.5%, **89.7% (2024)**, 88.7%
    (2025). ``lng_availability`` is the best year on record, so it is a ceiling
    rather than an expectation, and it lands the fleet limit at 3,942 TJ/day --
    exactly what the trains actually consumed in 2024.

    Falls back to the workbook total x shares if the Bulletin Board file is missing,
    so a clone without it still runs.
    """
    avail = P.get('lng_availability', 0.897)
    try:
        _data = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
        gbb = pd.read_csv(os.path.join(_data, 'GasBBNameplateRatingCurrent.csv'))
        rows = gbb[(gbb['facilitytype'] == 'LNGEXPORT')
                   & (gbb['capacitytype'] == 'MDQ')]
        out = {}
        for name, grp in rows.groupby('facilityname'):
            node = GBB_LNG_FACILITY.get(str(name).strip().lower())
            if node:
                # the smaller of process/receive is what the plant can liquefy
                out[node] = float(grp['capacityquantity'].min())
        if len(out) == len(GBB_LNG_FACILITY):
            return {k: v * avail for k, v in out.items()}
    except (OSError, KeyError, ValueError):
        pass
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


def _contracted_export_pj():
    """{year: PJ} still under Queensland LNG foundation SPAs, from the workbook.

    The LNG_Contracts sheet, sourced from ACCC's Gas Inquiry June 2025 interim
    update: 16,301 PJ of remaining contracted exports to 2036 (Chart 1), expiring
    "from 2031" with "a sharp drop off in exports ... after 2035". Empty if the
    sheet is missing, in which case callers fall back to the flat scalar.
    """
    df = P.sheet('LNG_Contracts')
    if df.empty:
        return {}
    cols = {str(c).strip(): c for c in df.columns}
    # The sheet carries a sourcing preamble above the series, so the Year/
    # Contracted_PJ header lands mid-sheet and pandas does not see it as columns.
    if 'Year' not in cols:
        hdr = df.index[df.iloc[:, 0].astype(str).str.strip() == 'Year']
        if len(hdr) == 0:
            return {}
        body = df.iloc[hdr[0] + 1:, :2]
    else:
        body = df[[cols['Year'], cols['Contracted_PJ']]]
    out = {}
    for _, r in body.iterrows():
        try:
            out[int(float(r.iloc[0]))] = max(0.0, float(r.iloc[1]))
        except (TypeError, ValueError):
            continue
    return out


def lng_foundation_share(year=None, planned_pj=None):
    """Share of planned export volume under take-or-pay foundation SPAs.

    TIME-VARYING, because the contracts are. GARY used to hold this at a flat 0.93
    for all 26 years -- one quarter's ACCC snapshot applied to 2051 -- which meant
    93% of export volume stayed physically must-serve for 15 years after the SPAs
    underpinning it had expired. With depletion switched on that was not harmless:
    inelastic export demand bidding against a shrinking resource drove Queensland
    and Darwin prices to $27/GJ against a $7.12 netback, and to VOLL beyond.

    A take-or-pay obligation is FINANCIAL, not physical. Once the contracts end
    there is nothing to force a cargo, so the share falls to zero and every
    remaining molecule of export volume becomes contestable -- the trains bid at
    the netback like any other buyer, and Queensland gas is priced by supply and
    demand rather than by an expired commitment.

    ``planned_pj`` is that year's planned export volume; the share is the
    contracted volume over it, capped at 1. Without a year (or without the sheet)
    this returns the flat scalar, which is the pre-2026 behaviour.
    """
    flat = P.get('lng_foundation_share', 0.93)
    if year is None:
        return flat
    contracted = _contracted_export_pj()
    if not contracted or year not in contracted:
        return flat
    if not planned_pj or planned_pj <= 0:
        return flat if contracted[year] > 0 else 0.0
    # CAPPED AT THE SCALAR, not at 1.0. ACCC's contracted total slightly exceeds
    # GARY's planned export volume -- producers are over-committed against their own
    # production and buy from third parties to cover it -- so an uncapped ratio
    # returns 1.0 and makes export demand perfectly rigid while contracts run. That
    # is more rigid than reality and more rigid than the model can absorb: it
    # removed the ~7% uncontracted tail that used to soak up a tight day, and put a
    # 54 TJ shortfall into 2029 at ~25 days of VOLL. The scalar is ACCC's own
    # uncontracted share (22 PJ of ~325 PJ, Q1 2026), so it stands as the ceiling
    # on how much of a year's volume can be take-or-pay; the contract profile then
    # scales it DOWN as the SPAs expire.
    return max(0.0, min(flat, contracted[year] / float(planned_pj)))


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
# A "what if" lever for hyperscale data centre load in NSW and VIC. The volume is
# stated in either of two ways, and everything below this paragraph is the same
# whichever is used -- they differ only in how much shape the analyst has to say:
#
#   SIMPLE CELL      one annual volume in PJ per state, switched on in a chosen
#                    year and held flat for the rest of the horizon. Answers
#                    "what would N PJ/yr of this do to the east coast market",
#                    nothing finer. Still the default.
#   LINKED SERIES    a year-by-year volume per state, read at solve time out of a
#                    spreadsheet the analyst keeps the pipeline in. Answers the
#                    same question about a build-out with a shape: a first site,
#                    a second, a plateau. See datacentre_series.py for the file
#                    layouts, and for what it does between and beyond the rows.
#
# A state with a column in the linked file takes its volumes from the file; a
# state without one falls back to its cell. So a file with only an NSW column
# leaves the VIC cell doing exactly what it did before, and nothing is ever
# counted twice.
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
# From a CELL it steps on IN FULL at the start year, with no ramp. The slider
# asks "what if this much load is there from year X"; a built-in ramp would
# quietly answer a different question, and a ramp that matters to a conclusion
# belongs in the analyst's own series -- which is what the linked spreadsheet is
# for, and there the ramp is stated rather than assumed.
#
# The volume is also kept as its own series (``GasMarketModel.dc_demand``) so it
# can be netted out of the industrial tier in both directions. Its consumption is
# set by its compute, not by the gas price, so it neither takes up more gas when
# gas is cheap (netted out of the *expansion* headroom the raise blocks are a
# share of) nor stands down when gas is dear (netted out of the *curtailment*
# cap). Everything else about it is ordinary industrial load.
DATACENTRE_STATE_NODE = P.get_pairs('datacentre_state_node',
                                    [('NSW', 'Sydney'), ('VIC', 'Melbourne')])


def datacentre_volume(spec, state, year):
    """This state's data centre volume in PJ for ``year``, from cell or file.

    The linked series wins wherever it has a column for the state: a file that
    names NSW answers for NSW in every year (zero before its first row), and the
    NSW cell is then not consulted at all. A state the file does not mention
    falls back to the cell, which is a flat volume from ``start_year`` on.
    """
    series = (spec.get('series') or {}).get(state)
    if series:
        return float(datacentre_series.value_for(series, year))
    if int(year) < int(spec.get('start_year', 2025)):   # cell not switched on yet
        return 0.0
    return float(spec.get(state, 0) or 0)


def datacentre_profile(spec, year, gpg_demand):
    """Data centre load for one year, as ``{(node, day): TJ}``.

    ``spec`` is ``{'NSW': PJ/yr, 'VIC': PJ/yr, 'start_year': yyyy}``, optionally
    with ``'series': {state: {year: PJ/yr}}`` from a linked spreadsheet, which
    takes precedence for the states it covers (see ``datacentre_volume``).
    Returns an empty dict when the lever is off or nothing is switched on yet.

    Each state's annual volume is distributed over the 365 days in proportion to
    that node's GPG demand, so the shape follows gas-fired generation rather than
    heating load. The daily figures sum to the annual volume by construction.
    """
    if not spec:                                        # lever off entirely
        return {}
    out = {}
    for state, node in DATACENTRE_STATE_NODE:           # NSW->Sydney, VIC->Melbourne
        pj = datacentre_volume(spec, state, year)       # this year's volume
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


# --- Curtailable demand ------------------------------------------------------
# Demand is a fixed volume. Two large-user tiers may stand down rather than be
# supplied, each at a flat strike price, and anything still unserved falls to the
# value of lost load:
#
#   $22/GJ   GPG         -- switches to another fuel or another generator
#   $120/GJ  industrial  -- stops the process line
#   $300/GJ  VOLL        -- nothing left to shed, load simply goes unserved
#
# Firm load has no rung on this ladder: data centre load and foundation LNG
# cargoes cannot shed at a strike and go straight to VOLL, which is what makes
# them outbid everything else for scarce gas.
#
# Distribution (mass-market) load is NOT price-responsive here. A step demand
# curve for it, with industrial and GPG blocks that raised demand when gas was
# cheap, was built and then removed on 29 Aug 2026: every block was calibrated
# against a reference price that had to be re-struck each time the model's price
# level moved, and the GPG ladder -- the largest response of the three -- had
# become unable to fire at any price the model produced. Recoverable from git
# history (build_demand_curves.py) if it is ever wanted back.

# Value of lost load: the price at which unserved mass-market gas is penalised.
# NOTE: the National Gas Rules set VoLL at $800/GJ in the Victorian DWGM and the
# STTM market price cap at $400/GJ (AEMO, Gas Market Parameters Review 2022, Final
# Recommendations, Feb 2023). GARY's $300/GJ predates that check and is therefore
# conservative; it is left as-is because changing it moves every historical result,
# but it is the number to revisit if VOLL ever matters to a conclusion.
# First modelled year, the origin of every decline curve.
BASE_YEAR = P.get_int('horizon_start', 2025)

VOLL_PER_GJ = P.get('voll_per_gj', 300.0)

# ACIL Allen's regasification allowance inside the import injection price. GARY
# also charges an import terminal its CapEx in expansion_options.csv, and a regas
# tolling fee is how a terminal recovers exactly that capital -- so charging both
# bills the terminal's capital twice. The adder comes back off the injection price
# wherever the CapEx is charged; see _import_injection_cost.
REGAS_ADDER = P.get('regasification', 1.50)

# Annual carrying cost charged on anything the capacity layer built, as a share of
# CapEx. A dispatch solve covers one year and has no NPV to charge a lump sum
# against, so a build has to show up as an annual cost or it would look free. It
# sits above the capacity MIP's discount_rate_default because it stands in for
# return OF capital as well as return ON it. GARY's own number, not a source.
CAPEX_ANNUALISATION = P.get('capex_annualisation_rate', 0.08)

# Round-trip charge on storage, applied to injection AND withdrawal, so the solver
# cycles inventory only when the seasonal price spread justifies it. Shared with
# capacity_model.py so both layers value a store the same way.
STORAGE_CYCLE_COST = P.get('storage_cycle_cost', 0.50)

# Share of capacity a store holds on day 1, and the level it must be back at on
# day 365. Each year is solved independently, so this is an assumption either way;
# what matters is that the two are the SAME number, or the year creates gas.
STORAGE_OPENING = P.get('storage_opening_fraction', 0.5)

# Southern winter window (gas day-of-year) used by the Winter lever, and by the
# per-block WinterScale that damps the price response through the heating season.
WINTER_DAYS = range(P.get_int('winter_day_start', 150),
                    P.get_int('winter_day_end', 250) + 1)


def planned_export_pj(demand_df):
    """Planned LNG export volume in a year's demand frame, PJ.

    The denominator for the foundation share: contracted volume over planned
    volume is what fraction of a year's exports is actually take-or-pay.
    """
    if demand_df is None or 'Node' not in getattr(demand_df, 'columns', []):
        return 0.0
    mask = demand_df['Node'].isin(LNG_NODES)
    return float(demand_df.loc[mask, 'Demand'].sum()) / 1000.0


def apply_lng_reservation(demand_df, share, respect_contracts=True, scale_demand=True,
                          year=None):
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
        uncontracted = max(0.0, 1.0 - lng_foundation_share(
            year, planned_export_pj(demand_df)))
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


def _num(row, key):
    """A numeric column that may be blank/NaN/absent -> float or None."""
    v = row.get(key)
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f          # NaN


def _import_injection_cost(lng_prices):
    """What running an import terminal costs, $/GJ, with its capital charged once.

    ACIL Allen's injection price is Asian LNG + shipping + a REGASIFICATION
    allowance (Table 2.1 / B.11). That last term is a tolling fee, and a tolling
    fee is how an FSRU recovers its capital. GARY charges the terminal's CapEx
    separately in expansion_options.csv, so leaving the adder in would bill the
    same capital twice -- about $1.50/GJ against the ~$0.29/GJ the CapEx itself
    annualises to on a 750 TJ/d terminal.

    The adder is removed rather than the CapEx because the CapEx is the term GARY
    can vary per project: two Geelong FSRUs compete on their build cost, and a
    tolling fee averaged across all terminals cannot express that.
    """
    injection = float(lng_prices.get('Import_Injection_AUD_GJ', 0.0))
    return max(0.0, injection - REGAS_ADDER) if injection > 0 else 0.0


def _reserves_pj(row):
    """The stock of gas behind a supply row, PJ, or None if it is not stock-limited.

    Each row is ONE tranche and carries its own reserve: a developed row holds the
    basin's 2P reserves, the undeveloped row that sits behind an AEMO field
    development holds its 2C contingent resource. A row with no figure -- an import
    terminal, Blacktip -- is limited by deliverability and any EndYear alone.

    Source: AEMO 2026 GSOO, G26 Reserves Costs assumptions, Reserves and Resources.
    """
    return _num(row, 'Reserves_PJ')


def _declined_capacity(row, year, cumulative_pj=0.0):
    """A field's deliverability in ``year``, TJ/day, given what it has produced.

    Three things can bind, and the tightest wins:

    * the DECLINE CURVE on its base capacity -- how fast the wells fall away;
    * ``EndYear`` (optional), the first year it no longer produces at all. For a
      source whose life is set by a contract or a licence rather than by a decline
      curve -- Blacktip, whose PWC gas sale agreement runs out in the mid-2030s,
      and no decline rate expresses "and then it stops";
    * the HARD STOCK LIMIT: a row cannot produce more gas than its tranche holds.
      Once ``cumulative_pj`` reaches the row's reserve it delivers nothing, and in
      the year it runs out it delivers only the remainder, spread over the year.

    The stock limit was tried once before, on 28 Aug 2026, and had to be reverted:
    it drove Iona to zero by 2036 and Moomba by 2048 and produced 6,419 TJ of
    shortage at $104-115/GJ. The reason was not the limit but what GARY was missing
    behind it. AEMO's Figure 27 has southern EXISTING production collapsing exactly
    that way -- 304 PJ/yr in 2025 to 5 PJ/yr by 2044 -- while developments backfill
    it to a ~230-280 PJ/yr plateau. GARY had no backfill, so the limit modelled the
    collapse without the replacement.

    It holds now because the backfill exists. Every basin carries a second,
    undeveloped row holding its 2C contingent resource, gated on the AEMO field
    developments in expansion_options.csv (Judith, the five Otway projects, Bowen
    Gas Project, Mahalo, Mt St Martin, the Beetaloo pilots). Depletion is a real
    supply curve now: the cheap 2P tranche runs out and the dear 2C tranche behind
    it has to be built to replace it.
    """
    cap = float(row['Capacity'])
    end = row.get('EndYear')
    if end is not None and str(end).strip() not in ('', 'nan', 'None'):
        if year >= int(float(end)):
            return 0.0
    cap = max(0.0, cap * ((1 + (row.get('DeclineRate') or 0)) ** (year - BASE_YEAR)))
    reserves = _reserves_pj(row)
    if reserves is not None:
        remaining_pj = reserves - float(cumulative_pj or 0.0)
        if remaining_pj <= 0.0:
            return 0.0
        # PJ left -> the flat TJ/day that would exhaust them over one year, so the
        # final year tapers to the remainder instead of stopping dead on a day.
        cap = min(cap, remaining_pj * 1000.0 / 365.0)
    return cap


class GasMarketModel:
    def __init__(self, nodes_df, arcs_df, supply_df, demand_df, expansion_df, year=2025, already_built=None, baseline="StepChange", dunkelflaute=False, builds_fixed=None, reserved_by_day=None, datacentre=None, netback_pricing=False, code_price_cap=True, netback_scenario=None,
                 reservation_applied=0.0, respect_contracts=True):
        self.nodes = nodes_df
        self.arcs = arcs_df
        self.supply = supply_df
        self.demand = demand_df
        self.expansion = expansion_df
        self.year = year
        self.already_built = already_built if already_built else []
        # {(Node, IsPotential): PJ produced in every earlier year of this run}.
        # Set by the solve loop before build_model(); empty means a fresh basin.
        self.cumulative_pj = {}
        # {(Node, IsPotential, year): $/GJ} SCARCITY RENT -- the opportunity cost of
        # producing a PJ now rather than later, from the dual on the capacity MIP's
        # reserve limit. Set by the solve loop alongside cumulative_pj; empty means
        # no rent, which is the behaviour before the rent existed. A myopic
        # year-by-year dispatch WILL burn a finite tranche cheapest-first without
        # it -- see capacity_model.get_scarcity_rents for what that cost.
        self.scarcity_rent = {}
        # AEMO 2026 GSOO baseline scenario: StepChange / Accelerated / SlowerGrowth.
        # Selects which per-baseline GPG & industrial demand profiles to load.
        self.baseline = baseline
        # SA dunkelflaute event lever (applied to Adelaide GPG in DUNKELFLAUTE_YEAR).
        self.dunkelflaute = dunkelflaute
        # {day: TJ} of export volume withheld and offered domestically at $0.
        self.reserved_by_day = dict(reserved_by_day or {})
        # Data centre lever: {'NSW': PJ/yr, 'VIC': PJ/yr, 'start_year': yyyy},
        # optionally carrying 'series': {state: {year: PJ/yr}} read from a linked
        # spreadsheet, which supersedes the cell for the states it covers.
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
            # null result.
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
        self.foundation_share = (lng_foundation_share(self.year,
                                                      planned_export_pj(self.demand))
                                 if self.netback_pricing else 0.0)
        if self.netback_pricing:
            # LNG imports are priced on ACIL Allen's injection cost (Asian LNG
            # + shipping + regasification, Table 2.1) rather than the single flat
            # figure in supply.csv, so the import terminal competes on the same
            # international price the exports are valued at. Copied first: the
            # foresight solve hands one supply frame to all 26 years.
            injection = _import_injection_cost(self.lng_prices)
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
        #   $300/GJ  VOLL         -- nothing left to shed, load simply goes unserved
        #
        # Firm load has no rung on this ladder: data centres and foundation LNG
        # cargoes cannot shed at a strike and go straight to VOLL, which is what
        # makes them outbid everything else for scarce gas.
        self.strike_gpg = float(strikes.get('GPG', P.get('strike_gpg_default', 22.0)))
        self.strike_ind = float(strikes.get('Industrial', P.get('strike_ind_default', 120.0)))
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

        gpg_dem = self.gpg_demand
        ind_dem = self.ind_demand
        dc_dem = self.dc_demand

        arc_data = self.arcs.set_index('Name').to_dict('index')
        supply_dict = self.supply.set_index(['Node', 'IsPotential']).to_dict('index')
        exp_data = self.expansion.set_index('Name').to_dict('index')
        storage_caps = self.nodes.set_index('Name')['StorageCapacity'].to_dict()
        # Published daily injection / withdrawal rates (2026 GSOO Storage sheet).
        # These columns existed in nodes.csv from the start and were never read, so
        # every facility ran far past its own rating -- Moomba withdrew at 506 TJ/d
        # against a published 120, and injected at 399 into a store AEMO lists as
        # withdrawal-only. A store is a rate as much as a volume.
        _sn = self.nodes.set_index('Name')
        stor_inj_max = _sn['MaxInjection'].to_dict()
        stor_wd_max = _sn['MaxWithdrawal'].to_dict()

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
            prod_cost = sum(m.production[s[0], s[1], t]
                            * (supply_dict[s]['Cost']
                               + self.scarcity_rent.get((s[0], s[1], self.year), 0.0))
                            * 1000 for s in m.Supply for t in m.T)
            # Cost of moving it: each arc's tariff x what flows down it. This is the
            # term that makes a Melbourne price differ from a Surat price.
            trans_cost = sum(m.flow[a, t] * arc_data[a]['Cost'] * 1000 for a in m.Arcs for t in m.T)
            # Unserved demand, valued at the value of lost load ($300/GJ). This is
            # the backstop that keeps the LP feasible: there is always the option of
            # simply not serving a node, at a price nobody wants to pay. It is also
            # what firm load (data centres, foundation LNG cargoes) falls through to
            # when it cannot be shed -- see ind_curtail_cap.
            shortage_penalty = sum(m.shortage[n, t] * VOLL_PER_GJ * 1000 for n in m.Nodes for t in m.T)
            # A small round-trip charge on storage, so the solver cycles inventory
            # only when the seasonal price spread justifies it.
            storage_cost = sum((m.injection[sn, t] + m.withdrawal[sn, t])
                               * STORAGE_CYCLE_COST * 1000
                               for sn in m.StorageNodes for t in m.T)
            # Annualised capex for anything built, at CAPEX_ANNUALISATION. build[e]
            # is binary, which is what makes the capacity layer a MILP not an LP.
            exp_capex = sum(m.build[e] * exp_data[e]['CapEx'] * CAPEX_ANNUALISATION
                            for e in m.Expansion)
            # Curtailment penalties = strike price ($/GJ) x 1000 (GJ/TJ). Shedding a
            # tier costs its strike price, so a tier only sheds when the marginal
            # cost of supplying it would exceed that strike (GPG $22 < industrial
            # $120 < mass-market value-of-lost-load $300/GJ).
            gpg_pen = sum(m.gpg_curtail[n, t] * self.strike_gpg * 1000 for n in m.GPGNodes for t in m.T)
            ind_pen = sum(m.ind_curtail[n, t] * self.strike_ind * 1000 for n in m.INDNodes for t in m.T)
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
            # Kept as the SAME expression objects the objective is summed from, so a
            # breakdown reported later cannot drift from the total. Reconstructing
            # these from the saved result frames was tried and abandoned: it has to
            # re-derive the year-varying import cost, the per-row scarcity rent, the
            # foundation/spot export split and the $0 reserved tranche sitting inside
            # `production`, and each of those is a place to silently disagree with the
            # objective. Read them here or not at all.
            self._cost_terms = {
                'production': prod_cost, 'transport': trans_cost,
                'shortage': shortage_penalty, 'storage': storage_cost,
                'capex': exp_capex, 'gpg_curtailment': gpg_pen,
                'ind_curtailment': ind_pen, 'lng_revenue': -lng_benefit,
            }
            return (prod_cost + trans_cost + shortage_penalty + storage_cost + exp_capex
                    + gpg_pen + ind_pen - lng_benefit)
        m.obj = pyo.Objective(rule=obj_rule, sense=pyo.minimize)

        arcs_to = {n: [a for a in m.Arcs if arc_data[a]['To'] == n] for n in m.Nodes}
        arcs_from = {n: [a for a in m.Arcs if arc_data[a]['From'] == n] for n in m.Nodes}

        # Fields sitting at each node, for the balance constraint below.
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
                    0 ==
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
                    # Piped onward to somewhere else.
                    sum(m.flow[a, t] for a in arcs_from[n]))
        m.balance = pyo.Constraint(m.Nodes, m.T, rule=balance_rule)

        # A tier can shed at most its own demand.
        m.gpg_curtail_cap = pyo.Constraint(m.GPGNodes, m.T,
            rule=lambda m, n, t: m.gpg_curtail[n, t] <= gpg_dem.get((n, t), 0))
        # Data centre load is FIRM and is netted out of what the industrial tier is
        # allowed to shed. It rides inside ind_demand so that it is transported and
        # priced like any other large industrial load, but it is not price-responsive
        # in either direction: it does not stand down when gas is dear, because
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

        def supply_cap_rule(m, node, is_pot, t):
            if is_pot:
                rel_exp = [e for e in m.Expansion if exp_data[e]['Type'] == 'Terminal' and exp_data[e]['Target'] == node]
                # Sum over every terminal fronting this node rather than taking the
                # first -- see the matching note in capacity_model.py. Rival FSRUs
                # at one landing point are kept from stacking by build_group_once.
                if not rel_exp:
                    return m.production[node, is_pot, t] == 0
                return m.production[node, is_pot, t] <= sum(
                    m.build[e] * exp_data[e]['NewCapacity'] for e in rel_exp)
            declined = _declined_capacity(supply_dict[node, is_pot], self.year,
                                          self.cumulative_pj.get((node, is_pot), 0.0))
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
        #
        # EVERY COMMERCIAL TRANCHE AT THE SOURCE COUNTS, developed and undeveloped alike.
        # This used to filter on ``not s_[1]`` -- IsPotential -- which was meant to keep
        # the free reserved tranche out and did not do that: the reserved gas is
        # ``reserved_prod``, a separate variable, and it is excluded simply by not
        # appearing in this sum. What ``not s_[1]`` actually excluded was Surat's 2C
        # row: 23,270 PJ, the Bowen Gas Project backfill the capacity layer builds in
        # 2030, structurally barred from the three feed pipes. Exports could then be
        # supplied only by the DEPLETING 2P tranche plus whatever transited in up the
        # SWQP, so the backfill could never backfill an export, and once 2P was drawn
        # down the trains had nowhere to turn. Measured in the LNG High case, exports
        # fell to 134 PJ in 2047-48 and 55 PJ in 2049-50 -- the transit inflows and
        # nothing else.
        commercial_at_source = [s_ for s_ in m.Supply if s_[0] in lng_source]
        inflows_to_source = [a for n in lng_source for a in arcs_to[n]]
        m.export_eligibility = pyo.Constraint(m.T, rule=lambda m, t:
            sum(m.flow[a, t] for a in lng_arcs) <=
            sum(m.production[s_[0], s_[1], t] for s_ in commercial_at_source)
            + sum(m.flow[a, t] for a in inflows_to_source))

        def storage_cont_rule(m, sn, t):
            cap = storage_caps.get(sn, 0)
            if t == 1: return m.inventory[sn, t] == (cap * STORAGE_OPENING) + m.injection[sn, t] - m.withdrawal[sn, t]
            return m.inventory[sn, t] == m.inventory[sn, t-1] + m.injection[sn, t] - m.withdrawal[sn, t]
        m.storage_cont = pyo.Constraint(m.StorageNodes, m.T, rule=storage_cont_rule)

        def storage_cap_rule(m, sn, t):
            return m.inventory[sn, t] <= storage_caps.get(sn, 0)
        m.storage_cap = pyo.Constraint(m.StorageNodes, m.T, rule=storage_cap_rule)

        # A store cannot be filled or emptied faster than its plant allows.
        m.inj_rate = pyo.Constraint(m.StorageNodes, m.T, rule=lambda m, sn, t:
            m.injection[sn, t] <= float(stor_inj_max.get(sn, 0) or 0))
        m.wd_rate = pyo.Constraint(m.StorageNodes, m.T, rule=lambda m, sn, t:
            m.withdrawal[sn, t] <= float(stor_wd_max.get(sn, 0) or 0))

        # CLOSE THE YEAR. Every year starts at half-full and, before this, nothing
        # required the store to be at any particular level on day 365 -- so the
        # solver emptied all three stores every year and got them back each
        # January. That created 46,200 TJ/yr from nothing, about 11% of domestic
        # supply, at the $0.50/GJ cycling charge: cheaper than any field in the
        # model. Each year is solved independently, so the opening level is an
        # assumption either way; requiring the year to close is what stops the
        # assumption being a subsidy. `>=` rather than `==` so ending fuller stays
        # legal -- it costs money, so the solver will not do it without a reason.
        m.storage_close = pyo.Constraint(m.StorageNodes, rule=lambda m, sn:
            m.inventory[sn, 365] >= STORAGE_OPENING * storage_caps.get(sn, 0))

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
            # Earliest build year, per project. A row's own EarliestYear wins;
            # TERMINAL_EARLIEST is the fallback floor for terminals without one.
            # (This used to test `'Terminal' in e`, a NAME match that only ever
            # caught Port_Kembla_Terminal, then the Type, which still let a
            # pipeline with a stated 2030s date be built in 2026.)
            for e in m.Expansion:
                lo = exp_data[e].get('EarliestYear')
                try:
                    lo = int(lo)
                except (TypeError, ValueError):
                    lo = TERMINAL_EARLIEST if exp_data[e]['Type'] == 'Terminal' else None
                if lo is not None and self.year < lo:
                    m.build[e].fix(0)
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
        res = {k: [] for k in ['prices', 'production', 'flow', 'storage', 'shortage', 'builds', 'gpg', 'industrial', 'distribution', 'lng']}
        demand_dict = self.demand.set_index(['Node', 'Day'])['Demand'].to_dict()
        # Distribution nodes: those carrying city-gate load. The LNG trains are
        # demand nodes too, but their volume is an export commitment.
        dist_nodes = sorted({n for (n, _), v in demand_dict.items()
                             if v > 0 and n not in LNG_NODES and n in set(m.Nodes)})

        pv = m.production.get_values()
        fv = m.flow.get_values()
        sv = m.shortage.get_values()
        iv = m.inventory.get_values()
        inj_v = m.injection.get_values()
        wd_v = m.withdrawal.get_values()
        gpg_cv = m.gpg_curtail.get_values()
        ind_cv = m.ind_curtail.get_values()
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
                #
                # It is NOT a wholesale price, and least of all before ~2031, when
                # every field is still on AEMO's 2P (largely operating) cost and
                # neither parity anchor binds. See the module header block.
                p = (m.dual[m.balance[n, t]]/1000 ) if hasattr(m, 'dual') and m.balance[n, t] in m.dual else 0.0
                # SECOND degeneracy guard. The set test above asks whether a node
                # EVER carries gas; it passes on a trickle of 0.01 TJ on a single
                # day, which is not enough to pin the dual. A node with no demand
                # cannot be losing load, so a dual sitting at VOLL there is
                # definitionally degenerate -- the balance row is effectively 0 == 0
                # and the solver reported the penalty. Measured 30 Aug 2026: Amadeus
                # sat at exactly $300.00 for all of 2049 and 2050, on zero production
                # and zero arc throughput, once its 2P tranche was exhausted and its
                # 2C development was never built.
                if n not in has_demand and p >= VOLL_PER_GJ - 1e-6:
                    continue
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
            # DISTRIBUTION (mass-market) volume, per node-day. Emitted because
            # nothing else in the results carries it: gpg and industrial each have
            # a served series and distribution did not, so any volume-weighted
            # price average could only weight by the two tiers that are 8-23% of a
            # city node's load. It is the residential/commercial segment's own
            # weight, and that segment is 100% contract in ACIL Allen's split.
            #
            # Served is demand less node shortage. Attributing all of a node's
            # shortage here is deliberate: GPG and industrial have their own
            # curtailment variables and shed at their strikes long before anything
            # reaches VOLL, so what is left unserved at a distribution node is
            # distribution load (or firm load that cannot shed, priced the same way).
            for n in dist_nodes:
                dem = demand_dict.get((n, t), 0.0)
                if dem <= 0.001:
                    continue
                short = float(sv[n, t] or 0)
                res['distribution'].append({
                    'Day': t, 'Node': n, 'Demand': float(dem),
                    'Served': float(max(0.0, dem - short)), 'Curtailed': 0.0})
            # LNG exports under netback pricing: planned volume vs what was
            # actually worth liquefying at the netback. Empty when the lever is
            # off, so a must-serve-export run stores nothing extra.
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
        # Where the volumes came from, when they came from a linked spreadsheet.
        # The scenario key carries only a hash of the numbers -- a path is not
        # something a key can hold -- so this is the only record in the result of
        # which file a series run was solved against.
        res['datacentre_source'] = (self.datacentre or {}).get('source') or ''
        res['reserved_served_tj'] = float(sum(rp[t] or 0 for t in m.T))
        res['reserved_offered_tj'] = float(sum(self.reserved_by_day.values()))

        for e in m.Expansion:
            if pyo.value(m.build[e]) > 0.5: res['builds'].append(e)
        res['total_cost'] = pyo.value(m.obj)
        # The objective's own terms, in dollars, signed as they enter it -- so they
        # sum to total_cost exactly. lng_revenue is negative: the system is PAID for
        # an export cargo.
        res['cost_components'] = {k: float(pyo.value(v))
                                  for k, v in getattr(self, '_cost_terms', {}).items()}
        res['solved'] = getattr(self, 'solved', True)
        # Collapse the per-day record lists into packed columns straight away: a
        # full batch holds every scenario in memory at once, and the dicts cost
        # orders of magnitude more RAM than the frames they become.
        return results_io.frames_from_year(res)
