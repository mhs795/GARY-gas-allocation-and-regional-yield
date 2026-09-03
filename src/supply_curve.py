"""Delivered supply curves, per demand node, per year.

WHAT THIS RECONSTRUCTS. GARY never builds a supply curve -- it solves an LP and
reports the dual. This module rebuilds, from a solved year's saved results plus
the static inputs the solve used, the merit order of gas that could have reached
one demand node: every supply tranche that was available that year, stacked
cheapest-first at its DELIVERED cost (field cost + scarcity rent + the tariff on
the cheapest route with capacity left on it). The nodal price the LP reported should
land where the year's demand cuts that stack, and mostly it does: across the
thirty node-years of a 2025-50 grid the residual construction sits a median
$0.27/GJ from GARY's own nodal price. That agreement is the check that the
reconstruction is reading the same model, and where it fails it fails visibly --
2025 is the worst year, with nothing built yet and the stripping of other buyers'
claims (below) taking gas they could not physically have reached.

The three things that make it a delivered curve rather than a wellhead one, and
the order they bind in:

  1. AVAILABILITY. A developed (2P) row declines on its own curve and stops when
     its reserves are gone -- exactly _declined_capacity(), called here with the
     cumulative production summed back out of the scenario's earlier years. An
     undeveloped (2C) row or an import terminal delivers nothing until a project
     fronting it is built, and then delivers that project's capacity, which is
     what supply_cap_rule does in the dispatch model.
  2. THE OTHER BUYERS. Queensland's cheap gas is largely spoken for. In the
     default RESIDUAL construction the gas the LNG trains took, plus what every
     other demand centre consumed, is stripped off the stack cheapest-first, so
     each panel shows what its own node's demand faced. Without that step every
     eastern curve opens with 4,000 TJ/d of $3.65 Surat gas and no domestic price
     above $6 could ever be explained. Turning it off gives the gross stack: what
     gas existed that year and what it would have cost to bring here.
  3. TRANSPORT. Gas is walked to the node by repeated cheapest-path allocation
     against residual pipeline capacity: the cheapest source fills its route
     until an arc on it is full, then the next-cheapest offer -- possibly the
     same field by a longer way round -- is priced and allocated behind it. This
     is what puts the corridor limits into the picture, and it is why Sydney's
     curve steps up where the MSP fills rather than running flat to Surat's
     nameplate.

WHAT WAS TRIED AND REJECTED, so it is not tried again. Two other structures were
built and measured against GARY's own nodal prices over a 2025-50 grid, both
looking for a closer fit than the capacity stack above (median $0.27/GJ):

  * A DISPATCH STACK, built from what each tranche actually produced on average
    over the year rather than from what it could produce. Worse -- median
    $0.57/GJ. Realised production is sized exactly to demand plus exports, so
    once the other buyers' claims are stripped cheapest-first there is no slack
    left and the node is handed the dearest gas in the system: Brisbane 2030
    came out at $13.93 against a $7.67 dual.
  * NETWORK-AWARE STRIPPING, serving the other demand centres and the trains
    through the same routing engine so their claims consume pipeline capacity
    too. Better in the tail (max $2.97 against $4.20) but no better in the middle
    (median $0.35), and at day resolution it diverges badly -- a greedy
    allocation across buyers is not the LP's optimum, and once it has spent the
    corridors greedily what is left is arbitrary.

The lesson from both: the remaining gap is not in how the supply side is stacked.
Matching a dual exactly would mean re-solving the system, because a nodal price
is a property of the whole LP -- including the congestion rent on a full corridor
and the competition between nodes -- and no arrangement of one node's blocks
carries it. What DOES close most of the gap is comparing like with like on the
time dimension, which is why the price line is the median day's (see node_price).

WHAT IT IS NOT. Each node's curve treats that node as the only buyer of the gas
left after exports: Sydney and Melbourne are not competing for the same MSP space
here, though in the LP they are. It is an annual average -- capacities and demand
are both flat-rate TJ/d over the year -- so it says nothing about a winter peak,
and storage does not appear at all. Read it as "what the year's gas cost to
deliver here, in merit order", not as a re-solve.
"""
import heapq

import pandas as pd

from model import (IMPORT_NODES, LNG_NODES, _declined_capacity,
                   _import_injection_cost, load_lng_prices)

TJD_TO_PJ = 365.0 / 1000.0      # a flat TJ/day held for a year, in PJ
_TOL = 1e-6
_MAX_BLOCKS = 400               # allocation is greedy; refuse to spin forever


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------
# Colour identity is the BASIN, not the tranche: a legend of fifteen supply rows
# would need fifteen hues, which no palette separates safely, and the thing a
# reader is actually tracking across a grid of thirty panels is where the gas
# comes from. The 2P/2C split rides on hatching instead (see the dashboard), so
# identity never rests on colour alone. Order is fixed -- slot 1 is always the
# Surat/Bowen blue, whether or not Surat appears in a given panel -- because a
# hue that moved between panels would make the grid unreadable.
FAMILY_OF_NODE = {
    'Surat':       'Surat / Bowen',
    'Moomba':      'Cooper / Eromanga',
    'Gippsland':   'Gippsland',
    'Iona':        'Otway',
    'Amadeus':     'Amadeus',
    'Beetaloo':    'Beetaloo',
    'Blacktip':    'Bonaparte',
    'Port_Kembla': 'LNG import',
    'Geelong':     'LNG import',
    'Adelaide':    'LNG import',
}
FAMILY_ORDER = ['Surat / Bowen', 'Cooper / Eromanga', 'Gippsland', 'Otway',
                'Amadeus', 'Beetaloo', 'Bonaparte', 'LNG import']


def source_family(node):
    """Which basin (or the import terminals) a supply node belongs to."""
    return FAMILY_OF_NODE.get(str(node), str(node).replace('_', ' '))


def source_label(row):
    """Legend name for a supply row: what the gas is, not where it is priced.

    Node plus tranche, because the two rows a basin carries are different gas at
    different money -- Surat 2P at $3.65 and the 2C behind it at $6.65 are the
    whole point of the curve having steps. Import terminals carry no tranche and
    are named for what they are.
    """
    node = str(row['Node']).replace('_', ' ')
    if str(row['Node']) in IMPORT_NODES and bool(row['IsPotential']):
        return f'{node} LNG import'
    tranche = str(row.get('Tranche') or '').strip()
    if tranche and tranche.lower() not in ('nan', 'none'):
        return f'{node} {tranche}'
    return f'{node} 2P' if not bool(row['IsPotential']) else f'{node} 2C'


# ---------------------------------------------------------------------------
# Reading a scenario's own history back out of its results
# ---------------------------------------------------------------------------
def accumulate(cum, res):
    """Add one solved year's production, PJ, onto the running per-tranche totals.

    The same quantity solve.py carries forward in `cumulative` and hands the
    dispatch model as `cumulative_pj`, recomputed here from the saved production
    frames because it is not itself saved. Reserved gas ('Potential' == the
    string 'Reserved') depletes the commercial row it was carved out of, as in
    solve._accumulate. Returns a new dict; the caller walks its years in order.
    """
    out = dict(cum)
    prod = res.get('production')
    if prod is None or prod.empty:
        return out
    df = prod.astype({'Node': str, 'Potential': str})
    for (node, pot), vol in df.groupby(['Node', 'Potential'])['Value'].sum().items():
        k = (node, pot == 'True')
        out[k] = out.get(k, 0.0) + float(vol) / 1000.0
    return out


def cumulative_pj(scenario_years, year):
    """{(Node, IsPotential): PJ} produced in this run before `year`."""
    cum = {}
    for res in sorted(scenario_years, key=lambda r: r['Year']):
        if res['Year'] >= year:
            break
        cum = accumulate(cum, res)
    return cum


def export_offtake_tjd(res):
    """Annual-average TJ/day that left each node for an LNG train in this year.

    Taken from the flow frame rather than the lng frame so it is the gas that
    physically left the SUPPLY node -- the quantity that has to come off the
    domestic stack -- rather than what cleared at the terminal.
    """
    flow = res.get('flow')
    if flow is None or flow.empty:
        return {}
    f = flow.astype({'From': str, 'To': str})
    exp = f[f['To'].isin(LNG_NODES)]
    if exp.empty:
        return {}
    return (exp.groupby('From')['Value'].sum() / 365.0).to_dict()


# ---------------------------------------------------------------------------
# The stack of gas that existed in one year
# ---------------------------------------------------------------------------
def available_sources(res, supply_df, expansion_df, cum_pj, arcs_df=None,
                      netback_scenario=None, data_dir=None):
    """Every supply tranche that could produce in this year: cost and TJ/day.

    Mirrors model.supply_cap_rule -- a potential row is capped by the terminals
    built in front of it and nothing else, a developed row by its decline curve
    and what is left of its reserves -- and prices each row the way the objective
    does, at its cost plus the year's scarcity rent on that row. With ``arcs_df``
    it also carries the reservation carve-out, which is not a supply row at all
    (see add_reserved_tranche).
    """
    year = int(res['Year'])
    builds = set(res.get('builds') or [])
    rents = res.get('scarcity_rent') or {}
    exp = expansion_df.set_index('Name')

    # Import terminals are priced off the ACIL Allen injection cost under netback
    # pricing, not the flat figure in supply.csv -- the same substitution
    # GasMarketModel makes on its own supply frame.
    import_cost = None
    if res.get('netback_pricing') and data_dir:
        prices = load_lng_prices(data_dir, netback_scenario or res.get('netback_scenario'),
                                 year, res.get('code_price_cap', True))
        if prices:
            c = _import_injection_cost(prices)
            import_cost = c if c > 0 else None

    out = []
    for _, row in supply_df.iterrows():
        node, is_pot = str(row['Node']), bool(row['IsPotential'])
        if is_pot:
            fronting = [e for e in builds
                        if e in exp.index
                        and exp.loc[e, 'Type'] == 'Terminal'
                        and str(exp.loc[e, 'Target']) == node]
            cap = float(sum(exp.loc[e, 'NewCapacity'] for e in fronting))
        else:
            cap = _declined_capacity(row, year, cum_pj.get((node, is_pot), 0.0))
        if cap <= _TOL:
            continue
        cost = float(row['Cost'])
        if import_cost is not None and node in IMPORT_NODES and is_pot:
            cost = import_cost
        cost += float(rents.get((node, is_pot), 0.0))
        out.append({'node': node, 'is_pot': is_pot,
                    'kind': 'potential' if is_pot else 'developed',
                    'label': source_label(row), 'family': source_family(node),
                    'cost': cost, 'cap': cap})
    if arcs_df is not None:
        out = add_reserved_tranche(out, res, arcs_df)
    return out


def lng_source_nodes(arcs_df):
    """The nodes that feed the LNG trains -- Surat, on the published network."""
    a = arcs_df.astype({'From': str, 'To': str})
    return sorted(set(a.loc[a['To'].isin(LNG_NODES), 'From']))


def add_reserved_tranche(sources, res, arcs_df):
    """Fold a domestic reservation into the stack as free gas at the field.

    A reservation is NOT a supply row: model.py carves it out of the export
    stream as its own variable, `reserved_prod`, priced at $0/GJ and capped at
    the share of planned exports withheld that day. Reading supply.csv alone
    therefore misses it entirely, and a reserved scenario's curve would open at
    the commercial tranche as though the lever did nothing.

    Two things about how the model states it carry straight over here:

    * IT IS THE SAME GAS, NOT EXTRA. `production + reserved_prod <= capacity`
      means a slice of Surat's deliverability is simply free rather than added
      on top of it, so the carve-out is taken OFF the commercial rows there. The
      model applies that cap ROW BY ROW -- the 2P row and the 2C row behind it
      each lose the reserved volume from their own headroom -- so this deducts it
      from each of them too rather than from the cheapest one only. Mirroring it
      matters: the two differ by one carve-out's worth of gas on the stack, and
      the point of this module is to reproduce what the LP saw, quirks included.
    * $0 IS A WELLHEAD PRICE, NOT A DELIVERED ONE. The block still has to be
      shipped, so it lands on a demand node's curve at the tariff of the route
      it took: ~$0.70/GJ at Brisbane, ~$1.40 at Sydney via the MSP. Free gas
      still costs what the pipe charges.
    """
    offered = float(res.get('reserved_offered_tj') or 0.0) / 365.0
    if offered <= _TOL:
        return sources
    node = next(iter(lng_source_nodes(arcs_df)), None)
    if node is None:
        return sources

    for s in sources:
        if s['node'] == node:
            s['cap'] = max(0.0, s['cap'] - offered)
    sources = [s for s in sources if s['cap'] > _TOL]
    sources.append({'node': node, 'is_pot': False, 'kind': 'reserved',
                    'label': 'Reserved (LNG carve-out)',
                    'family': source_family(node), 'cost': 0.0, 'cap': offered})
    return sources


def prior_claims_tjd(res, node, demand_nodes):
    """(export, domestic) TJ/day already spoken for by everyone except `node`.

    Split in two because the two claims are not eligible for the same gas: the
    trains may draw only on COMMERCIAL gas (model.py constrains the LNG feed
    pipes to `production`, not to `reserved_prod`, or the free gas would simply
    flow to the trains and the reservation would do nothing), while a domestic
    buyer takes whatever is cheapest, the carve-out first.

    A supply curve drawn for one node has to answer "what could have reached ME",
    and the honest answer subtracts the other claims on the same molecules: the
    LNG trains, and every other demand centre. That makes each panel a RESIDUAL
    supply curve -- the standard construction for asking what one buyer faced --
    and it is what brings the reconstruction onto the LP's own price. Without it
    Brisbane's curve intersects in Surat's cheap 2P tranche at ~$5 while GARY
    priced the node at ~$8, because in the LP that cheap gas had already gone to
    the trains and to the southern states and the marginal molecule was the
    dearer 2C tranche behind it.
    """
    exports = sum(export_offtake_tjd(res).values())
    domestic = sum(node_demand_tjd(res, n) for n in demand_nodes if n != node)
    return exports, domestic


def strip_claims(sources, claims, kinds=None):
    """Take `claims` TJ/day off the stack cheapest-first, and return what is left.

    ``kinds`` restricts which rows a claim may eat into -- the trains are held to
    commercial gas, so the reservation carve-out is not on offer to them.

    Cheapest-first because the claims being removed are buyers, and a buyer takes
    the cheapest gas on offer. Removing volume pro rata across tranches instead
    would leave a phantom slice of the cheap tranche on the residual curve that
    the trains and the southern states had in fact already taken -- which is
    exactly the error that put the price line off the curve.

    It is a stack-wide sweep with no transport in it, so it can in principle
    remove gas the other buyers could not physically have reached (NT gas, in a
    year with no southbound corridor). The distortion is small wherever the
    cheapest gas is also the well-connected gas, which in this system it is.
    """
    remaining = float(claims)
    for s in sorted(sources, key=lambda s: s['cost']):
        if remaining <= _TOL:
            break
        if kinds is not None and s['kind'] not in kinds:
            continue
        used = min(s['cap'], remaining)
        s['cap'] -= used
        remaining -= used
    return [s for s in sources if s['cap'] > _TOL]


# ---------------------------------------------------------------------------
# Getting it to the node
# ---------------------------------------------------------------------------
def arc_capacity(arcs_df, expansion_df, builds):
    """{arc: TJ/day} with every built pipeline expansion added to its target arc."""
    caps = arcs_df.set_index('Name')['Capacity'].astype(float).to_dict()
    exp = expansion_df.set_index('Name')
    for e in builds:
        if e in exp.index and exp.loc[e, 'Type'] == 'Pipeline':
            target = str(exp.loc[e, 'Target'])
            if target in caps:
                caps[target] += float(exp.loc[e, 'NewCapacity'])
    return caps


def _cheapest_path(source, target, adjacency, resid):
    """Dijkstra on tariff over arcs that still have capacity: (cost, [arc names])."""
    if source == target:
        return 0.0, []
    seen, queue = set(), [(0.0, source, [])]
    while queue:
        cost, node, path = heapq.heappop(queue)
        if node == target:
            return cost, path
        if node in seen:
            continue
        seen.add(node)
        for arc, to, tariff in adjacency.get(node, ()):
            if to in seen or resid.get(arc, 0.0) <= _TOL:
                continue
            heapq.heappush(queue, (cost + tariff, to, path + [arc]))
    return None, None


def delivered_curve(target, sources, arcs_df, caps):
    """Merit order of gas delivered to `target`: a list of blocks, cheapest first.

    Greedy min-cost allocation. Each round prices every source that still has gas
    at its cheapest route with capacity left, takes the cheapest of those offers,
    and pushes as much down it as the tightest arc on the route allows. A field
    whose cheap route fills therefore reappears further up the curve at the cost
    of its second route, which is the behaviour that makes a corridor limit
    visible as a step rather than as a missing source.
    """
    adjacency = {}
    for _, a in arcs_df.iterrows():
        adjacency.setdefault(str(a['From']), []).append(
            (str(a['Name']), str(a['To']), float(a['Cost'])))
    resid = {k: float(v) for k, v in caps.items()}
    left = [dict(s) for s in sources]

    blocks = []
    for _ in range(_MAX_BLOCKS):
        best = None
        for s in left:
            if s['cap'] <= _TOL:
                continue
            tariff, path = _cheapest_path(s['node'], target, adjacency, resid)
            if tariff is None:
                continue
            delivered = s['cost'] + tariff
            if best is None or delivered < best[0]:
                best = (delivered, s, path)
        if best is None:
            break
        delivered, s, path = best
        headroom = min([resid[a] for a in path], default=float('inf'))
        qty = min(s['cap'], headroom)
        if qty <= _TOL:
            break
        s['cap'] -= qty
        for a in path:
            resid[a] -= qty
        blocks.append({'label': s['label'], 'family': s['family'],
                       'kind': s['kind'], 'is_pot': s['is_pot'], 'node': s['node'],
                       'cost': delivered, 'field_cost': s['cost'],
                       'tariff': delivered - s['cost'], 'qty': qty,
                       'route': ' → '.join(path) if path else 'at node'})

    blocks.sort(key=lambda b: b['cost'])
    return _merge(blocks)


def _merge(blocks):
    """Fold adjacent blocks of the same source at the same cost into one step."""
    out = []
    for b in blocks:
        if out and out[-1]['label'] == b['label'] and abs(out[-1]['cost'] - b['cost']) < 1e-9:
            out[-1]['qty'] += b['qty']
        else:
            out.append(dict(b))
    return out


# ---------------------------------------------------------------------------
# Where demand and price sit on it
# ---------------------------------------------------------------------------
def node_demand_tjd(res, node):
    """Annual-average TJ/day of demand at a node: mass market + GPG + industrial.

    Demand as posted, not as served -- a curtailed tier still wanted the gas, and
    the point of the line is to show where the year's want cut the stack.
    """
    total = 0.0
    for series in ('distribution', 'gpg', 'industrial'):
        df = res.get(series)
        if df is None or df.empty or 'Demand' not in df.columns:
            continue
        sub = df[df['Node'].astype(str) == node]
        if not sub.empty:
            total += float(sub['Demand'].sum()) / 365.0
    return total


def node_price(res, node):
    """The spread of the year's daily nodal prices at a node, $/GJ.

    Returns {'median', 'mean', 'p10', 'p90'}, or None if the node was never
    priced. THE MEDIAN IS THE ONE THAT BELONGS ON AN ANNUAL SUPPLY CURVE, and
    the reason is worth stating because it is the whole business of comparing a
    curve with a dual.

    A panel here is a TYPICAL-DAY construction: capacities and demand are both
    annual-average flat rates. GARY's reported price for a year is the mean of
    365 daily duals, and on a winter day the marginal source is a dearer one, so
    that mean sits above what a typical day actually cleared at. Comparing the
    two mismatches the time dimension, not the supply: across a 2025-50 grid the
    curve lands on the MEDIAN day's price to the cent in 14 of 30 panels against
    8 for the mean, and a median $0.19/GJ from it against $0.27.

    The p10-p90 spread is carried so the chart can SHOW that rather than hide
    it. Where the band runs well above the curve, the year's dear days are being
    set by something an annual average cannot hold -- a winter peak, or a
    congestion rent on a corridor that no supply block carries.
    """
    prices = res.get('prices')
    if prices is None or prices.empty:
        return None
    sub = prices[prices['Node'].astype(str) == node]['Price']
    if sub.empty:
        return None
    return {'median': float(sub.median()), 'mean': float(sub.mean()),
            'p10': float(sub.quantile(0.10)), 'p90': float(sub.quantile(0.90))}


# ---------------------------------------------------------------------------
# One node-year, assembled
# ---------------------------------------------------------------------------
def curves_for_year(res, nodes, supply_df, arcs_df, expansion_df, demand_nodes,
                    cum=None, scenario_years=None, data_dir=None, residual=True):
    """One solved year -> {node: (blocks DataFrame, demand PJ/yr, price $/GJ)}.

    The year's available stack is built once and re-used for every node, because
    it is the same gas -- what differs between panels is only which claims are
    stripped off it and what it costs to get the rest there.

    ``residual`` selects the construction: True nets off the LNG trains and the
    other demand centres, so a panel is the curve its own node's demand faced;
    False leaves the whole year's stack in, which answers the different question
    of what gas existed and what it would have cost to bring here.
    """
    if cum is None:
        cum = cumulative_pj(scenario_years or [], int(res['Year']))
    stack = available_sources(res, supply_df, expansion_df, cum, arcs_df=arcs_df,
                              data_dir=data_dir)
    caps = arc_capacity(arcs_df, expansion_df, set(res.get('builds') or []))

    out = {}
    for node in nodes:
        sources = [dict(s) for s in stack]
        if residual:
            exports, domestic = prior_claims_tjd(res, node, demand_nodes)
            sources = strip_claims(sources, exports,
                                   kinds={'developed', 'potential'})
            sources = strip_claims(sources, domestic)
        blocks = delivered_curve(node, sources, arcs_df, dict(caps))
        df = pd.DataFrame(blocks, columns=['label', 'family', 'kind', 'is_pot', 'node',
                                           'cost', 'field_cost', 'tariff', 'qty',
                                           'route'])
        if not df.empty:
            df['PJ'] = df['qty'] * TJD_TO_PJ
            df['CumPJ'] = df['PJ'].cumsum()
            df['StartPJ'] = df['CumPJ'] - df['PJ']
        out[node] = (df, node_demand_tjd(res, node) * TJD_TO_PJ, node_price(res, node))
    return out


def curve_for(res, node, supply_df, arcs_df, expansion_df, scenario_years,
              demand_nodes, data_dir=None, residual=True):
    """One node-year, for callers that want a single panel's worth of data."""
    return curves_for_year(res, [node], supply_df, arcs_df, expansion_df,
                           demand_nodes, scenario_years=scenario_years,
                           data_dir=data_dir, residual=residual)[node]
