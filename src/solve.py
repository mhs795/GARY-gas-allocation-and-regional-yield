import argparse
import pandas as pd
import os
import time

import datacentre_series
import params as P
import results_io
import solvers
from model import (IMPORT_NODES, LNG_NODES, WINTER_DAYS, GasMarketModel,
                   _import_injection_cost, apply_lng_reservation)


def _lever(lever, level):
    """One row of the Scenario_Levers sheet, as a Series. Raises if absent."""
    df = P.sheet('Scenario_Levers')
    hit = df[(df['Lever'].astype(str) == lever) & (df['Level'].astype(str) == level)]
    if hit.empty:
        raise P.ParamError(f"Scenario_Levers has no row for {lever} / {level}")
    return hit.iloc[0]


HORIZON_START = P.get_int('horizon_start')
HORIZON_END = P.get_int('horizon_end')
# Last year anyone SEES. The model solves one year past it so the terminal-year
# artefact -- a finite horizon exhausts its tranches exactly at the last year it can
# see -- lands outside the reported range instead of inside it. See TODO item 14.
HORIZON_REPORT_END = P.get_int('horizon_report_end')

# Terminal-value fixed point: how many extra capacity solves to spend chasing it,
# and the tau movement (years) below which it is called settled.
SALVAGE_MAX_PASSES = P.get_int('salvage_max_passes')
SALVAGE_TAU_TOL = P.get('salvage_tau_tol')


def _nameplate_tau(cap, key):
    """The row's own reserves-to-production ratio, the first-pass guess."""
    from capacity_model import _reserve_to_production_years
    try:
        return _reserve_to_production_years(cap._supply_row(key))
    except Exception:
        return None

# Which Source value in expansion_options.csv counts as "in the GSOO".
GSOO_SOURCE = P.get_str('expansion_source_gsoo')
GSOO_ONLY_DEFAULT = P.get_bool('gsoo_expansions_only')
IMPORTS_DEFAULT = P.get_bool('allow_import_terminals')


def filter_expansions(expansion, gsoo_only, allow_imports=True):
    """The candidate set the capacity layer is allowed to choose from.

    ``expansion_options.csv`` carries every pipeline, reversal and terminal the
    market has on the table, each tagged in a ``Source`` column: ``GSOO`` for the
    committed set AEMO counts in the 2026 GSOO/VGPR supply adequacy assessment,
    ``Market`` for everything found by GARY's own market scan that sits outside
    that boundary -- pre-FID, proposed, or committed after the GSOO's cut-off.

    Filtering here rather than inside either model layer is deliberate: the two
    stages must be offered exactly the same menu, or the dispatch layer would be
    handed a build schedule containing a project it does not know about.

    ``allow_imports=False`` additionally drops every LNG IMPORT terminal, so the
    east coast has to meet demand from domestic supply and pipe. An import terminal
    is identified as a ``Type == 'Terminal'`` row whose ``Target`` is one of
    ``import_nodes`` -- derived rather than flagged, because that is already how
    model.py decides which supply rows to reprice at the injection cost, and a
    second hand-maintained flag could disagree with it. The thirteen FIELD
    DEVELOPMENTS are Type=Terminal too -- Golden Beach, Judith, the five Otway
    projects, Bowen Gas Project, Mahalo, Mt St Martin, Cooper, Amadeus and the
    Beetaloo rows -- but they target BASIN nodes, so the ban leaves them alone.

    ``Source`` says WHERE A CANDIDATE CAME FROM, not what status AEMO gives it:
    ``GSOO`` means the project is named in AEMO's 2026 GSOO material -- the G26
    Field Developments sheet for supply, the GSOO/VGPR project set for pipes --
    whatever its status there, Committed or Undeveloped alike. ``Market`` means
    GARY researched it from public announcements, or invented it outright, and AEMO
    does not name it. So ``gsoo_only`` selects the AEMO-sourced menu; the default
    offers that plus everything GARY has researched on top.

    On the supply side the GSOO menu carries 13 field developments and 4,287 TJ/d of
    backfill, so a GSOO-only run under the stock limit still has something to build.
    The two candidates it drops -- Cooper_2C and Amadeus_2C -- are GARY's own, for
    basins AEMO names no discrete development in.

    A file with no ``Source`` column (a clone predating the market scan) is
    returned untouched, so the filter can never silently empty the candidate set.
    """
    if gsoo_only and 'Source' in expansion.columns:
        expansion = expansion[
            expansion['Source'].astype(str).str.strip() == GSOO_SOURCE]
    if not allow_imports:
        is_import = ((expansion['Type'].astype(str).str.strip() == 'Terminal')
                     & (expansion['Target'].astype(str).str.strip().isin(IMPORT_NODES)))
        expansion = expansion[~is_import]
    return expansion.reset_index(drop=True)


def lng_price_scenario(lng_level, baseline):
    """Which ACIL Allen price path the netback is struck off, for an LNG level.

    Under netback pricing the Global LNG lever stops scaling export VOLUME and
    starts selecting a PRICE path. That is the coherent instrument once a price
    exists in the model: strong global LNG demand raises what a train can pay,
    and the volume it liquefies follows -- rather than the old lever asserting a
    60% volume increase with no willingness to pay a cent more for it.

    Levels map to ACIL Allen's own published scenario paths (Scenario_Levers sheet,
    lever ``LNG_Netback``) rather than to an invented percentage shift, so every
    number in the chain stays sourced:

        Low     Accelerated Transition  -- weak global demand, netback collapses
        Medium  the run's own baseline
        High    Slower Growth           -- strong global demand, netback at the cap

    Note this deliberately decouples the price path from the demand baseline, so
    "Step Change demand with Slower Growth LNG prices" is expressible. That is the
    sensitivity, not a mistake.
    """
    target = str(_lever('LNG_Netback', str(lng_level))['Value']).strip()
    return baseline if target == 'baseline' else target

def load_data(baseline="StepChange"):
    base_path = os.path.dirname(__file__)
    data_dir = os.path.join(base_path, "data")
    # Node distribution + LNG demand for the chosen GSOO baseline; fall back to the
    # legacy StepChange alias (demand_2050.csv) if the per-baseline file is absent.
    demand_file = os.path.join(data_dir, f"demand_{baseline}.csv")
    if not os.path.exists(demand_file):
        demand_file = os.path.join(data_dir, "demand_2050.csv")
    return {
        'nodes': pd.read_csv(os.path.join(data_dir, "nodes.csv")),
        'arcs': pd.read_csv(os.path.join(data_dir, "arcs.csv")),
        'supply': pd.read_csv(os.path.join(data_dir, "supply.csv")).dropna(subset=['Node', 'Capacity', 'Cost']),
        'demand': pd.read_csv(demand_file),
        'expansion': pd.read_csv(os.path.join(data_dir, "expansion_options.csv")),
    }

def get_lng_mult(scenario, year):
    """LNG export VOLUME multiplier for one year (netback pricing off only).

    Every number is on the workbook: the Low and High rows of Scenario_Levers carry
    the value and the FromYear/ToYear window it applies over, and the Parameters
    sheet carries the Low path's post-window decline and floor and the High case's
    level outside its window.

      Low     1.0 before FromYear; falls by Value a year to ToYear; then by
              lng_low_decline_after a year, floored at lng_low_floor. One
              continuous line -- the post-window leg starts from wherever the
              window leg ended, so changing the step cannot open a jump.
      High    Value over FromYear..ToYear, lng_high_outside_window elsewhere.
      Medium  Value (1.0) throughout.
    """
    row = _lever('LNG', scenario)
    if scenario == "Low":
        lo, hi, step = int(row['FromYear']), int(row['ToYear']), float(row['Value'])
        if year < lo:
            return 1.0
        if year <= hi:
            return 1.0 - (year - lo + 1) * step
        at_hi = 1.0 - (hi - lo + 1) * step
        return max(P.get('lng_low_floor'),
                   at_hi - (year - hi) * P.get('lng_low_decline_after'))
    if scenario == "High":
        if int(row['FromYear']) <= year <= int(row['ToYear']):
            return float(row['Value'])
        return P.get('lng_high_outside_window')
    return float(row['Value'])


def _year_demand(data, year, winter, lng, reservation=0.0, netback_pricing=False,
                 respect_contracts=True):
    """Demand for one year with the Winter, LNG and reservation levers applied.

    Returns ``(demand frame, TJ diverted, {day: TJ reserved}, share applied)``.
    The reservation is applied last, so it bites on the export volume planned
    under this scenario rather than on the raw baseline.

    **The LNG lever changes instrument under netback pricing.** With exports
    must-serve there is no price in the model, so the only way to represent global
    LNG market strength is to scale export volume. Once the netback exists that is
    incoherent -- and it was also the source of an artefact, because the 1.6x High
    multiplier pushed planned exports well above physical liquefaction nameplate
    and the model could only report the excess as domestic lost load at VOLL. So
    with ``netback_pricing=True`` the volume multiplier is NOT applied; the level
    selects a netback price path instead (see lng_price_scenario).
    """
    dm = data['demand'].copy()
    # HOLD THE LAST PUBLISHED YEAR past the end of the demand file, the way
    # load_lng_prices holds the nearest year and _load_year_profile clamps to
    # gsoo_index_last_year. The file is built to horizon_end, so today this only
    # bites if horizon_end is raised without regenerating the demand files; then
    # a bare ``Year == year`` filter would hand the extra years an EMPTY frame --
    # zero demand, which is not a neutral assumption but the most extreme one
    # available. Holding the last year flat is padding, not a forecast.
    dyear = min(max(int(year), int(dm['Year'].min())), int(dm['Year'].max()))
    winter_mult = float(_lever('Winter', winter)['Value'])
    dm.loc[(dm['Year'] == dyear) & (dm['Node'].isin(P.get_list('winter_nodes'))) &
           (dm['Day'].isin(WINTER_DAYS)), 'Demand'] *= winter_mult
    if not netback_pricing:
        lng_mult = get_lng_mult(lng, year)
        dm.loc[(dm['Year'] == dyear) & (dm['Node'].isin(LNG_NODES)), 'Demand'] *= lng_mult
    # The frame comes from dyear; the CONTRACT rules still key off the real year,
    # because the foundation SPAs expire on their own calendar, not the file's.
    return apply_lng_reservation(dm[dm['Year'] == dyear].copy(), reservation,
                                 respect_contracts=respect_contracts,
                                 scale_demand=not netback_pricing,
                                 year=year)


# Long-form baseline names for the one-line run header; the dashboard's dropdown
# labels are its own (BASELINE_LABEL), these only ever go to the terminal.
BASELINE_LABELS = {'StepChange': 'Step Change', 'Accelerated': 'Accelerated Transition',
                   'SlowerGrowth': 'Slower Growth'}


def datacentre_label(datacentre):
    """One-line description of the data centre lever, for the run header.

    Names the linked file where one is in play, because a series run is only as
    reproducible as the spreadsheet behind it and the header is where an analyst
    reading a log finds out which one it was.
    """
    series = (datacentre.get('series') or {})
    cells = [f"{datacentre.get(st, 0):g} PJ {st}"
             for st in ('NSW', 'VIC')
             if st not in series and float(datacentre.get(st, 0) or 0) > 0]
    bits = []
    if series:
        source = datacentre.get('source') or 'linked file'
        bits.append(f"data centres {datacentre_series.describe(series)} "
                    f"({os.path.basename(source)}, held flat after the last row)")
    if cells:
        bits.append(("data centres " if not bits else "")
                    + " / ".join(cells) + f" from {datacentre['start_year']}")
    return "  ·  ".join(bits) if bits else "data centres (no volume)"


def run_title(winter, lng, baseline="StepChange", dunkelflaute=False, reservation=0.0, foresight=True, datacentre=None,
              netback_pricing=False, gsoo_expansions_only=False,
              allow_import_terminals=True):
    """One-line description of a scenario, for the terminal header."""
    bits = [BASELINE_LABELS.get(baseline, baseline), f"Winter {winter}", f"LNG {lng}"]
    if dunkelflaute:
        bits.append("SA Dunkelflaute 2027")
    if reservation:
        bits.append(f"{round(reservation * 100)}% reservation")
    if datacentre:
        bits.append(datacentre_label(datacentre))
    if netback_pricing:
        bits.append("LNG netback pricing")
    if not allow_import_terminals:
        bits.append("no import terminals")
    if gsoo_expansions_only:
        bits.append("GSOO expansions only")
    if not foresight:
        bits.append("myopic")
    return "  ·  ".join(bits)


def solve_scenario(winter, lng, mip_gap=None, callback=None,
                   baseline="StepChange", dunkelflaute=False, discount_rate=None,
                   foresight=True, reservation=0.0,
                   datacentre=None, netback_pricing=False,
                   respect_contracts=True, gsoo_expansions_only=None,
                   allow_import_terminals=None, rep_bins=None,
                   title=None, log=True):
    """Solve a scenario over the horizon, reported to ``horizon_report_end``.

    ``foresight=True`` (default) uses the two-stage full-horizon method: a
    perfect-foresight capacity model chooses builds across the whole horizon, then
    each year is dispatched at full 365-day resolution with those builds fixed.

    ``foresight=False`` uses the myopic year-by-year method: each year decides its
    own builds reactively (no knowledge of future years), carrying built projects
    forward. Preferred for *unanticipated* shocks (e.g. a price-shock or dunkelflaute
    scenario) where perfect foresight would unrealistically pre-build ahead of it.

    ``datacentre`` is the data centre load lever:
    ``{'NSW': PJ/yr, 'VIC': PJ/yr, 'start_year': yyyy}``, or None for off. From
    the start year on, the volume is added to LARGE INDUSTRIAL demand at Sydney
    and Melbourne, spread across the year on each node's own GPG daily shape —
    see the header block in model.py for what that choice does and does not
    assume. It may also carry ``'series': {state: {year: PJ/yr}}`` read from a
    linked spreadsheet (``--dc-file``, or the sidebar box), which replaces the
    flat cell for the states the file has a column for.

    ``netback_pricing=True`` applies ACIL Allen's price formation: the LNG trains
    stop being must-serve demand and become willingness-to-pay blocks valued at the
    export netback, and LNG imports are priced at ACIL Allen's injection cost. This
    is what makes the international price discipline domestic prices instead of
    exports being taken at any price. See the header block in model.py, and
    build_lng_prices.py for where the series comes from.

    ``allow_import_terminals=False`` drops every LNG import terminal from the
    candidate set, so the east coast must be supplied domestically. ``None`` takes
    the workbook default (``allow_import_terminals`` on the Parameters sheet).

    ``gsoo_expansions_only=True`` restricts the capacity layer to the committed
    expansions AEMO counts in the 2026 GSOO/VGPR, dropping every candidate GARY's
    own market scan added (Bulloo Interlink, the Geelong FSRUs, Golden Beach,
    Outer Harbor, the VTS expansion, SWP looping). Use it to see how much of a
    result rests on projects that are not yet anybody's commitment. ``None``
    takes the workbook default (``gsoo_expansions_only`` on the Parameters sheet).

    Prints a one-line scenario header followed by one line per solved year;
    ``title`` overrides the header text and ``log=False`` silences both, which is
    what parallel sweeps use — year lines from several workers at once interleave
    into noise, so the parent process reports one line per finished scenario.
    """
    # One header per scenario, then one line per year: with the batch solving 28
    # scenarios x 26 years, the year lines are meaningless without it. Callers that
    # already have a nicer label (the dashboard passes its scenario key) override it.
    if mip_gap is None:
        mip_gap = P.get('mip_gap_default')
    if discount_rate is None:
        discount_rate = P.get('discount_rate_default')
    if gsoo_expansions_only is None:
        gsoo_expansions_only = GSOO_ONLY_DEFAULT
    if allow_import_terminals is None:
        allow_import_terminals = IMPORTS_DEFAULT
    if log:
        print(f"\n{title or run_title(winter, lng, baseline, dunkelflaute, reservation, foresight, datacentre, netback_pricing, gsoo_expansions_only, allow_import_terminals)}",
              flush=True)
    data = load_data(baseline)
    # Filter ONCE, here, so the capacity layer and the dispatch layer are offered
    # exactly the same menu -- see filter_expansions.
    data['expansion'] = filter_expansions(data['expansion'], gsoo_expansions_only,
                                          allow_import_terminals)
    years = list(range(HORIZON_START, HORIZON_END + 1))
    if foresight:
        results = _solve_foresight(data, years, winter, lng, baseline,
                                   dunkelflaute, mip_gap, discount_rate, callback,
                                   reservation, log, datacentre,
                                   netback_pricing, respect_contracts, rep_bins)
    else:
        results = _solve_myopic(data, years, winter, lng, baseline,
                                dunkelflaute, mip_gap, callback, reservation, log,
                                datacentre, netback_pricing, respect_contracts,
                                discount_rate)
    # PROVENANCE: what this answer was solved with, so a cached copy can be told
    # apart from a fresh one after an input or code change (results_io.stale_reason)
    # and a build schedule can be read against the gap it was settled to.
    meta = dict(results_io.provenance(), solver=solvers.get_solver_name(),
                mip_gap=float(mip_gap), mip_abs_gap_aud=P.get('mip_abs_gap_aud'),
                discount_rate=float(discount_rate))
    for r in results:
        r['run_meta'] = meta
    return _trim_to_report_horizon(results)


def _trim_to_report_horizon(results):
    """Drop the years solved past ``horizon_report_end`` before anyone sees them.

    The model solves to HORIZON_END and reports to HORIZON_REPORT_END. A
    finite-horizon model exhausts its reserve tranches exactly at the last year it
    can see -- gas left in the ground past the horizon is worth nothing to the
    objective -- so that year absorbs every accounting discrepancy between the
    capacity layer's representative days and the dispatch layer's 365 real days, and
    shows shortage at value-of-lost-load. Measured 30 Aug 2026 at 207,569 TJ.

    The salvage value on remaining reserves now exists, so the last year no longer
    empties the basins. The pad is ONE year (horizon_end 2051, reported to 2050).
    A fifteen-year pad to 2065 was tried on 2 Sep 2026 and reverted: it put
    fourteen extra years of held-flat demand against the same finite reserves and
    moved every late-horizon result (see the horizon_end note in the workbook and
    docs/depletion.md). The remaining terminal effects are TODO items 14 and 16.

    The padded year does real work: it is in the capacity MIP's foresight, so
    builds and scarcity rents are struck against it.
    """
    return [r for r in results if r.get('Year', 0) <= HORIZON_REPORT_END]


def _solve_myopic(data, years, winter, lng, baseline, dunkelflaute,
                  mip_gap, callback, reservation=0.0, log=True,
                  datacentre=None, netback_pricing=False, respect_contracts=True,
                  discount_rate=None):
    """Reactive year-by-year solve: each year decides builds with no foresight."""
    built_projects, results = [], []
    build_years = {}         # project -> year first built, for the capital charge
    cumulative = {}          # (Node, IsPotential) -> PJ produced so far
    for i, year in enumerate(years):
        if callback:
            callback(year, i / len(years))
        demand_yr, diverted, reserved_day, applied = _year_demand(
            data, year, winter, lng, reservation, netback_pricing, respect_contracts)
        gm = GasMarketModel(
            data['nodes'], data['arcs'], data['supply'], demand_yr, data['expansion'],
            year=year,
            already_built=built_projects,
            baseline=baseline, dunkelflaute=dunkelflaute, reserved_by_day=reserved_day,
            datacentre=datacentre, netback_pricing=netback_pricing,
            netback_scenario=lng_price_scenario(lng, baseline) if netback_pricing else None,
            reservation_applied=applied, respect_contracts=respect_contracts,
            discount_rate=discount_rate, build_years=build_years)
        gm.cumulative_pj = cumulative
        gm.build_model()
        status = gm.solve(mip_gap=mip_gap)
        if status != "ok":
            raise RuntimeError(f"Solver failed in {year}: {status}")
        yr_res = gm.get_results()
        yr_res['Year'] = year
        yr_res['reservation_share'] = reservation
        yr_res['reservation_share_applied'] = applied
        yr_res['reservation_respects_contracts'] = respect_contracts
        yr_res['lng_reserved_tj'] = diverted
        cumulative = _accumulate(cumulative, yr_res)
        results.append(yr_res)
        for b in yr_res['builds']:
            if b not in built_projects:
                built_projects.append(b)
                build_years[b] = year
        if log:
            print(f"    Year {year} complete", flush=True)
    if callback:
        callback(years[-1], 1.0)
    return results


def _accumulate(cum, results):
    """Add one solved year's production (PJ) onto the running per-field totals.

    Depletion is what makes a basin climb its cost curve, so the dispatch loop has
    to carry produced volume forward from year to year. Keyed the way the model's
    Supply set is, ``(Node, IsPotential)``.

    get_results tags each row with ``Potential``: True/False for the two supply
    rows a node can have, or the string ``'Reserved'`` for a domestic reservation's
    carve-out. Reserved gas is the SAME field's gas priced at zero, not extra gas,
    so it depletes the commercial row it came out of.
    """
    import pandas as _pd
    prod = results.get('production')
    # get_results hands back a list of dicts; a cache round-trip hands back a
    # DataFrame. Accept either -- `or []` on a DataFrame raises.
    df = prod if isinstance(prod, _pd.DataFrame) else _pd.DataFrame(prod if prod is not None else [])
    if df.empty or 'Node' not in df.columns:
        return cum
    for _, r in df.iterrows():
        pot = r.get('Potential', False)
        is_pot = False if isinstance(pot, str) else bool(pot)
        k = (r['Node'], is_pot)
        cum[k] = cum.get(k, 0.0) + float(r['Value']) / 1000.0   # TJ -> PJ
    return cum


def _supply_import_cost(supply):
    """$/GJ an import terminal costs in supply.csv: the backstop without netback."""
    rows = supply[supply['Node'].isin(IMPORT_NODES) & supply['IsPotential'].astype(bool)]
    if rows.empty:
        raise ValueError("supply.csv has no import-terminal rows to take a backstop from")
    return float(rows['Cost'].max())


def _num_or_none(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _solve_foresight(data, years, winter, lng, baseline, dunkelflaute,
                     mip_gap, discount_rate, callback, reservation=0.0, log=True, datacentre=None,
                     netback_pricing=False, respect_contracts=True, rep_bins=None):
    """Two-stage full-horizon solve: perfect-foresight capacity + 365-day dispatch."""
    from capacity_model import CapacityExpansionModel, build_representative_days
    start_year, end_year = years[0], years[-1]

    # --- Pass 1: assemble every year's demand + GPG/industrial (events applied) ---
    dispatch_models, demand_all, gpg_all, ind_all = {}, {}, {}, {}
    diverted_by_year, reserved_all, dc_all = {}, {}, {}
    # Per year, not one scalar. Under respect_contracts the applied share is capped
    # at that year's uncontracted tail, which grows as the SPAs expire, so the last
    # year's value describes none of the others.
    applied_by_year = {}
    for year in years:
        # Applied here, before the representative days are built, so the capacity
        # layer sizes the network against the same post-reservation demand the
        # dispatch layer will face.
        demand_yr, diverted_by_year[year], reserved_day, applied_share = _year_demand(
            data, year, winter, lng, reservation, netback_pricing, respect_contracts)
        applied_by_year[year] = applied_share
        for d, v in reserved_day.items():
            reserved_all[(year, d)] = v
        gm = GasMarketModel(
            data['nodes'], data['arcs'], data['supply'], demand_yr, data['expansion'],
            year=year,
            baseline=baseline, dunkelflaute=dunkelflaute, reserved_by_day=reserved_day,
            datacentre=datacentre, netback_pricing=netback_pricing,
            netback_scenario=lng_price_scenario(lng, baseline) if netback_pricing else None,
            reservation_applied=applied_share, respect_contracts=respect_contracts)
        dispatch_models[year] = gm
        for (n, d), v in demand_yr.set_index(['Node', 'Day'])['Demand'].to_dict().items():
            demand_all[(n, year, d)] = v
        for (n, d), v in gm.gpg_demand.items():
            gpg_all[(n, year, d)] = v
        for (n, d), v in gm.ind_demand.items():
            ind_all[(n, year, d)] = v
        # Already inside ind_all; carried separately only so the capacity layer
        # can keep firm data centre load out of the industrial raise headroom.
        for (n, d), v in gm.dc_demand.items():
            dc_all[(n, year, d)] = v

    # --- Pass 2: capacity expansion, perfect foresight over the horizon ----------
    if callback:
        callback(start_year, 0.0)
    # Load bins per month in the capacity layer. More bins resolve the
    # load-duration curve inside each month, which is what keeps the MIP's planned
    # drawdown in step with what the 365-day dispatch layer actually draws.
    rep = build_representative_days(years, demand_all, gpg_all, ind_all,
                                    data['nodes'],
                                    reserved_all=reserved_all, dc_all=dc_all,
                                    bins_per_month=rep_bins)
    cap_kwargs = dict(
        nodes_df=data['nodes'], arcs_df=data['arcs'], supply_df=data['supply'],
        expansion_df=data['expansion'], years=years, rep=rep,
        discount_rate=discount_rate,
        strike_gpg=dispatch_models[start_year].strike_gpg,
        strike_ind=dispatch_models[start_year].strike_ind,
        lng_arcs=dispatch_models[start_year].lng_arcs,
        lng_source=dispatch_models[start_year].lng_source,
        # One netback and one import cost per year: both track the international
        # LNG price, so unlike a field cost they cannot be a single number.
        netback_by_year={y: dispatch_models[y].netback for y in years}
                        if netback_pricing else None,
        import_cost_by_year={
            y: _import_injection_cost(dispatch_models[y].lng_prices)
            for y in years} if netback_pricing else None,
        # Backstop price for the terminal salvage value: what replacement gas costs
        # landed in the final year. Published (ACIL Allen injection cost), not chosen.
        # Without netback pricing there is no ACIL series, and the backstop is the
        # import terminals' own cost in supply.csv -- the same number dispatch
        # charges them -- rather than a literal here.
        salvage_price=(float(dispatch_models[years[-1]].lng_prices['Import_Injection_AUD_GJ'])
                       if netback_pricing else _supply_import_cost(data['supply'])),
        lng_nameplate=dispatch_models[start_year].lng_nameplate,
        # Per-year, not a scalar: the contracts expire mid-horizon, so the MIP has
        # to see the same must-serve profile the dispatch layer will face.
        foundation_share={y: dispatch_models[y].foundation_share for y in years},
        # Per-year for the same reason foundation_share is, and they are read
        # together: the cap is on the contracted share PLUS the reservation.
        reservation_applied=applied_by_year, respect_contracts=respect_contracts)
    def _build_cap():
        c = CapacityExpansionModel(**cap_kwargs)
        c.build_model()
        st = c.solve(mip_gap=mip_gap)
        if st != "ok":
            raise RuntimeError(f"Capacity model failed: {st}")
        return c

    # --- Pass 2: the investment MIP, iterated to a terminal-value fixed point ----
    # OFF BY DEFAULT: salvage_max_passes is 0 on the workbook, so the loop below
    # does not run and every row keeps its nameplate tau. It was measured on
    # 3 Sep 2026 and found not to matter (TODO item 16 addendum); it is kept so it
    # can be switched back on. What follows describes it when it is on.
    # The salvage credit scales the margin on leftover gas by 1/(1+r.tau), where tau
    # is how many years of production the stock represents. tau therefore has to be
    # measured on the stock LEFT AT THE HORIZON -- but that is an output of the very
    # solve it feeds, so it is a fixed point and has to be iterated.
    #
    # The first pass uses each row's NAMEPLATE ratio, which asks how long the tranche
    # would take to sell if none of it had been produced. At the horizon that is the
    # wrong question, and wrong in one direction: it treats a nearly-exhausted Surat
    # as though it still held twenty years of gas, and so under-prices its last
    # molecules. Measured 3 Sep 2026 at the 2051 horizon, Surat 2P finished with 772
    # PJ of 28,911 left -- 0.7 years of production, not 19.8.
    #
    # THE ITERATION IS SELF-CORRECTING. A higher salvage makes the MIP produce less,
    # which leaves more stock, which lengthens tau, which lowers the salvage again.
    # Negative feedback, so it settles. (Setting the salvage to the model's own
    # marginal value at the horizon instead is POSITIVE feedback -- every pass adds
    # the reserve dual back on and the credit walks up to the backstop, which is
    # exactly the flat-supply-curve bug this whole line of work started from. Do not
    # do that.)
    #
    # Damped at 50% because the first correction is large and undamped passes
    # overshoot; capped at SALVAGE_MAX_PASSES because a fixed point that has not
    # settled by then is telling us something the tolerance will not fix.
    cap = _build_cap()
    tau_now = {}
    for _pass in range(SALVAGE_MAX_PASSES):
        measured = cap.measured_tau()
        if not measured:
            break
        moved = 0.0
        nxt = {}
        for k, tau_m in measured.items():
            prev = tau_now.get(k, _nameplate_tau(cap, k))
            if tau_m < 0.0 or prev is None:
                nxt[k] = tau_m
                continue
            blend = 0.5 * prev + 0.5 * tau_m
            moved = max(moved, abs(blend - prev))
            nxt[k] = blend
        tau_now = nxt
        if log:
            print(f"    salvage pass {_pass + 1}: max tau move {moved:.2f} yr", flush=True)
        if moved < SALVAGE_TAU_TOL:
            break
        cap_kwargs['tau_override'] = tau_now
        cap = _build_cap()
    else:
        if SALVAGE_MAX_PASSES > 0:
            print(f"    WARNING: salvage tau did not settle within {SALVAGE_MAX_PASSES} "
                  f"passes (last move {moved:.2f} yr > {SALVAGE_TAU_TOL} yr)", flush=True)
    globals()['_LAST_TAU'] = tau_now
    # THE SCARCITY RENT. Without it the myopic dispatch layer burns each cheap
    # tranche at full rate and hits a wall the perfect-foresight MIP never saw --
    # measured at 758 PJ/yr of shortage over 2047-50 before this was added. See
    # capacity_model.get_scarcity_rents.
    rents = cap.get_scarcity_rents()
    globals()['_LAST_RENTS'] = rents
    if not rents:
        print("    WARNING: the capacity model produced no scarcity rents; dispatch "
              "will run on bare field costs", flush=True)
    cap_gap = {'abs_aud': getattr(cap, 'gap_abs', None), 'rel': getattr(cap, 'gap_rel', None)}
    if log and cap_gap['abs_aud'] is not None:
        print(f"    capacity MIP closed to ${cap_gap['abs_aud']/1e6:,.1f}m "
              f"({cap_gap['rel']:.4%}) of its bound", flush=True)
    build_year = cap.get_build_schedule()
    active_by_year = {y: {e for e, by in build_year.items() if by is not None and by <= y} for y in years}

    # --- Pass 3: full 365-day dispatch each year with builds fixed ----------------
    scenario_results = []
    cumulative = {}          # (Node, IsPotential) -> PJ produced so far
    for i, year in enumerate(years):
        if callback:
            callback(year, (i + 1) / len(years))
        gm = dispatch_models[year]
        gm.builds_fixed = active_by_year[year]
        gm.build_years = {e: by for e, by in build_year.items() if by is not None}
        gm.discount_rate = discount_rate
        gm.cumulative_pj = cumulative
        gm.scarcity_rent = rents
        gm.build_model()
        status = gm.solve(mip_gap=mip_gap)
        if status != "ok":
            raise RuntimeError(f"Dispatch failed in {year}: {status}")
        yr_res = gm.get_results()
        yr_res['Year'] = year
        yr_res['reservation_share'] = reservation
        yr_res['reservation_share_applied'] = applied_by_year[year]
        yr_res['reservation_respects_contracts'] = respect_contracts
        yr_res['lng_reserved_tj'] = diverted_by_year[year]
        # The scarcity rent this year faced, per supply row -- recorded like the
        # other run metadata so a result can be read back without re-solving.
        yr_res['scarcity_rent'] = {(n, pot): v for (n, pot, yy), v in rents.items()
                                   if yy == year}
        yr_res['capacity_gap'] = cap_gap
        cumulative = _accumulate(cumulative, yr_res)
        scenario_results.append(yr_res)
        if log:
            print(f"    Year {year} complete", flush=True)

    if callback:
        callback(end_year, 1.0)
    return scenario_results


def main():
    parser = argparse.ArgumentParser(
        description="Solve a GARY scenario over 2025-2050 and print a summary.")
    parser.add_argument("--winter", choices=['Low', 'Medium', 'High'], default='Medium')
    parser.add_argument("--lng", choices=['Low', 'Medium', 'High'], default='Medium')
    parser.add_argument("--baseline", default="StepChange")
    parser.add_argument("--dunkelflaute", action="store_true")
    parser.add_argument("--reservation", type=float, default=0.0, metavar="PCT",
                        help="Domestic gas reservation: %% of LNG export volume "
                             "diverted to the domestic market (e.g. 20 for 20%%)")
    parser.add_argument("--dc-nsw", type=float, default=0.0, metavar="PJ",
                        help="Extra data centre gas demand in NSW, PJ/yr, added to "
                             "Sydney industrial demand on the GPG daily shape")
    parser.add_argument("--dc-vic", type=float, default=0.0, metavar="PJ",
                        help="Extra data centre gas demand in VIC, PJ/yr, added to "
                             "Melbourne industrial demand on the GPG daily shape")
    parser.add_argument("--dc-start", type=int, default=2030, metavar="YEAR",
                        help="First year the CELL data centre demand appears "
                             "(default 2030). Ignored for a state supplied by "
                             "--dc-file, whose own rows say when it starts")
    parser.add_argument("--dc-file", default=None, metavar="PATH",
                        help="Link a spreadsheet holding a YEAR-BY-YEAR data "
                             "centre demand series instead of one flat volume: a "
                             ".csv/.xlsx with a Year column and NSW and/or VIC "
                             "columns in PJ/yr (or Year/State/PJ rows). Add "
                             "'#SheetName' to name a sheet. A state the file "
                             "does not cover falls back to --dc-nsw/--dc-vic. "
                             "See src/data/datacentre_demand_example.csv")
    parser.add_argument("--netback-pricing", action="store_true",
                        help="ACIL Allen LNG netback price formation: LNG exports "
                             "become willingness-to-pay blocks at the netback and "
                             "imports are priced at the injection cost, instead of "
                             "exports being must-serve demand")
    parser.add_argument("--break-lng-contracts", action="store_true",
                        help="Let a reservation take its share of ALL export "
                             "volume, foundation SPAs included. By default a "
                             "reservation is capped at the uncontracted share, "
                             "which is how the Heads of Agreement works")
    parser.add_argument("--no-import-terminals", action="store_true",
                        help="Drop every LNG import terminal from the candidate "
                             "set (Port Kembla, the two Geelong FSRUs, Outer "
                             "Harbor), so the east coast must be supplied from "
                             "domestic fields and pipe. Field developments such as "
                             "Golden Beach are unaffected")
    parser.add_argument("--gsoo-expansions-only", action="store_true",
                        help="Restrict the capacity layer to the expansions AEMO "
                             "counts as committed in the 2026 GSOO/VGPR, dropping "
                             "every pre-FID and proposed candidate GARY's own "
                             "market scan added (Source=Market in "
                             "expansion_options.csv)")
    parser.add_argument("--myopic", action="store_true",
                        help="Year-by-year solve instead of the two-stage foresight method")
    parser.add_argument("--mip-gap", type=float, default=None,
                        help="Relative MIP gap (default: mip_gap_default on the "
                             "workbook; mip_abs_gap_aud caps it in dollars)")
    solvers.add_solver_argument(parser)
    args = parser.parse_args()

    if args.solver:
        solvers.set_solver_name(args.solver)
    print(f"Solver: {solvers.describe()}")
    solvers.require_available()

    series = {}
    if args.dc_file:
        # Fail loudly: a mistyped path that quietly solved with no data centre
        # load would look exactly like a run that had some and it did nothing.
        try:
            series = datacentre_series.load(args.dc_file)
        except datacentre_series.DataCentreSeriesError as exc:
            parser.error(f"--dc-file: {exc}")
        print(f"Data centre series: {datacentre_series.describe(series)} "
              f"from {args.dc_file}")
    datacentre = None
    if args.dc_nsw > 0 or args.dc_vic > 0 or series:
        datacentre = {'NSW': args.dc_nsw, 'VIC': args.dc_vic,
                      'start_year': args.dc_start}
        if series:
            datacentre['series'] = series
            datacentre['source'] = args.dc_file
            datacentre['fingerprint'] = datacentre_series.fingerprint(series)
            datacentre['label'] = datacentre_series.label(args.dc_file)

    t0 = time.time()
    results = solve_scenario(
        args.winter, args.lng, mip_gap=args.mip_gap,
        baseline=args.baseline, dunkelflaute=args.dunkelflaute,
        foresight=not args.myopic, reservation=args.reservation / 100.0,
        datacentre=datacentre,
        netback_pricing=args.netback_pricing,
        gsoo_expansions_only=args.gsoo_expansions_only,
        allow_import_terminals=not args.no_import_terminals,
        respect_contracts=not args.break_lng_contracts)
    print(f"\nSolved {len(results)} years in {time.time() - t0:.1f}s "
          f"using {solvers.describe()}")
    builds = sorted({b for yr in results for b in yr['builds']})
    print(f"Projects built: {', '.join(builds) if builds else 'none'}")


if __name__ == "__main__":
    main()
