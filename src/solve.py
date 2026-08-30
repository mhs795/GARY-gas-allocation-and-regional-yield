import argparse
import pandas as pd
import os
import time

import datacentre_series
import params as P
import solvers
from model import (IMPORT_NODES, GasMarketModel, _import_injection_cost,
                   apply_lng_reservation)


def _lever(lever, level, default):
    """One row of the Scenario_Levers sheet, or the in-code default."""
    df = P.sheet('Scenario_Levers')
    if df.empty:
        return default
    hit = df[(df['Lever'].astype(str) == lever) & (df['Level'].astype(str) == level)]
    return default if hit.empty else float(hit.iloc[0]['Value'])


HORIZON_START = P.get_int('horizon_start', 2025)
HORIZON_END = P.get_int('horizon_end', 2050)

# Which Source value in expansion_options.csv counts as "in the GSOO".
GSOO_SOURCE = P.get_str('expansion_source_gsoo', 'GSOO')
GSOO_ONLY_DEFAULT = str(P.get_str('gsoo_expansions_only', 'FALSE')).strip().upper() in ('TRUE', '1', 'YES')
IMPORTS_DEFAULT = str(P.get_str('allow_import_terminals', 'TRUE')).strip().upper() in ('TRUE', '1', 'YES')


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
    df = P.sheet('Scenario_Levers')
    target = None
    if not df.empty and 'Lever' in df.columns:
        hit = df[(df['Lever'].astype(str) == 'LNG_Netback')
                 & (df['Level'].astype(str) == str(lng_level))]
        if not hit.empty:
            target = str(hit.iloc[0]['Value']).strip()
    if target is None:
        target = {'Low': 'Accelerated', 'Medium': 'baseline',
                  'High': 'SlowerGrowth'}.get(lng_level, 'baseline')
    return baseline if target in ('baseline', '', 'nan') else target

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
    """LNG export demand multiplier for one year. Levels come from the
    Scenario_Levers sheet of the parameters workbook (see params.py); the piecewise
    SHAPE stays here because it is model structure, not a parameter."""
    HIGH_LNG_START = 2026
    HIGH_LNG_END = 2030
    if scenario == "Low":
        step = _lever('LNG', 'Low', 0.04)
        if year <= 2025: return 1.0
        elif year <= 2030: return 1.0 - (year - 2025) * step
        elif year <= 2040: return 0.8 - (year - 2030) * 0.03
        else: return max(0.2, 0.5 - (year - 2040) * 0.03)
    elif scenario == "High":
        if HIGH_LNG_START <= year <= HIGH_LNG_END: return _lever('LNG', 'High', 1.6)
        else: return 1.1
    return _lever('LNG', 'Medium', 1.0)

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
    winter_mult = _lever('Winter', winter,
                         {"Low": 1.0, "Medium": 1.5, "High": 2.2}[winter])
    dm.loc[(dm['Year'] == year) & (dm['Node'].isin(['Melbourne', 'Adelaide', 'Sydney'])) &
           (dm['Day'] >= 150) & (dm['Day'] <= 250), 'Demand'] *= winter_mult
    if not netback_pricing:
        lng_mult = get_lng_mult(lng, year)
        dm.loc[(dm['Year'] == year) & (dm['Node'].isin(['APLNG', 'GLNG', 'QCLNG'])), 'Demand'] *= lng_mult
    return apply_lng_reservation(dm[dm['Year'] == year].copy(), reservation,
                                 respect_contracts=respect_contracts,
                                 scale_demand=not netback_pricing)


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


def solve_scenario(winter, lng, mip_gap=0.005, callback=None,
                   baseline="StepChange", dunkelflaute=False, discount_rate=0.07,
                   foresight=True, reservation=0.0,
                   datacentre=None, netback_pricing=False,
                   respect_contracts=True, gsoo_expansions_only=None,
                   allow_import_terminals=None,
                   title=None, log=True):
    """Solve a scenario over 2025-2050.

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
        return _solve_foresight(data, years, winter, lng, baseline,
                                dunkelflaute, mip_gap, discount_rate, callback,
                                reservation, log, datacentre,
                                netback_pricing, respect_contracts)
    return _solve_myopic(data, years, winter, lng, baseline,
                         dunkelflaute, mip_gap, callback, reservation, log, datacentre, netback_pricing,
                         respect_contracts)


def _solve_myopic(data, years, winter, lng, baseline, dunkelflaute,
                  mip_gap, callback, reservation=0.0, log=True,
                  datacentre=None, netback_pricing=False, respect_contracts=True):
    """Reactive year-by-year solve: each year decides builds with no foresight."""
    built_projects, results = [], []
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
            reservation_applied=applied, respect_contracts=respect_contracts)
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
        built_projects.extend([b for b in yr_res['builds'] if b not in built_projects])
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


def _num_or_none(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _solve_foresight(data, years, winter, lng, baseline, dunkelflaute,
                     mip_gap, discount_rate, callback, reservation=0.0, log=True, datacentre=None,
                     netback_pricing=False, respect_contracts=True):
    """Two-stage full-horizon solve: perfect-foresight capacity + 365-day dispatch."""
    from capacity_model import CapacityExpansionModel, build_representative_days
    start_year, end_year = years[0], years[-1]

    # --- Pass 1: assemble every year's demand + GPG/industrial (events applied) ---
    dispatch_models, demand_all, gpg_all, ind_all = {}, {}, {}, {}
    diverted_by_year, reserved_all, dc_all = {}, {}, {}
    applied_share = 0.0
    for year in years:
        # Applied here, before the representative days are built, so the capacity
        # layer sizes the network against the same post-reservation demand the
        # dispatch layer will face.
        demand_yr, diverted_by_year[year], reserved_day, applied_share = _year_demand(
            data, year, winter, lng, reservation, netback_pricing, respect_contracts)
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
    rep = build_representative_days(years, demand_all, gpg_all, ind_all,
                                    data['nodes'],
                                    reserved_all=reserved_all, dc_all=dc_all)
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
        lng_nameplate=dispatch_models[start_year].lng_nameplate,
        foundation_share=dispatch_models[start_year].foundation_share,
        reservation_applied=applied_share, respect_contracts=respect_contracts)
    def _build_cap():
        c = CapacityExpansionModel(**cap_kwargs)
        c.build_model()
        st = c.solve(mip_gap=mip_gap)
        if st != "ok":
            raise RuntimeError(f"Capacity model failed: {st}")
        return c

    # --- Pass 2: the investment MIP -----------------------------------------
    # One pass. It used to be two: depletion was carried as a path-dependent COST
    # step, so the MIP had to be solved once to learn the production path and again
    # against the stepped costs. Reserves are a hard stock limit now and the MIP
    # sees every year at once, so it states the limit directly as a constraint and
    # there is nothing left to iterate on.
    cap = _build_cap()
    # THE SCARCITY RENT. Without it the myopic dispatch layer burns each cheap
    # tranche at full rate and hits a wall the perfect-foresight MIP never saw --
    # measured at 758 PJ/yr of shortage over 2047-50 before this was added. See
    # capacity_model.get_scarcity_rents.
    rents = cap.get_scarcity_rents()
    globals()['_LAST_RENTS'] = rents
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
        gm.cumulative_pj = cumulative
        gm.scarcity_rent = rents
        gm.build_model()
        status = gm.solve(mip_gap=mip_gap)
        if status != "ok":
            raise RuntimeError(f"Dispatch failed in {year}: {status}")
        yr_res = gm.get_results()
        yr_res['Year'] = year
        yr_res['reservation_share'] = reservation
        yr_res['reservation_share_applied'] = applied_share
        yr_res['reservation_respects_contracts'] = respect_contracts
        yr_res['lng_reserved_tj'] = diverted_by_year[year]
        # The scarcity rent this year faced, per supply row -- recorded like the
        # other run metadata so a result can be read back without re-solving.
        yr_res['scarcity_rent'] = {(n, pot): v for (n, pot, yy), v in rents.items()
                                   if yy == year}
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
    parser.add_argument("--mip-gap", type=float, default=0.005)
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
