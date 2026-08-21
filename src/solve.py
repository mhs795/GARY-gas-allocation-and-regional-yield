import argparse
import pandas as pd
import os
import time

import params as P
import solvers
from model import GasMarketModel, apply_lng_reservation


def _lever(lever, level, default):
    """One row of the Scenario_Levers sheet, or the in-code default."""
    df = P.sheet('Scenario_Levers')
    if df.empty:
        return default
    hit = df[(df['Lever'].astype(str) == lever) & (df['Level'].astype(str) == level)]
    return default if hit.empty else float(hit.iloc[0]['Value'])


HORIZON_START = P.get_int('horizon_start', 2025)
HORIZON_END = P.get_int('horizon_end', 2050)

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
        'contracts': pd.read_csv(os.path.join(data_dir, "contracts.csv"))
    }

def get_lng_mult(scenario, year):
    """LNG export demand multiplier for one year. Levels come from the
    Scenario_Levers sheet of the inputs workbook (see params.py); the piecewise
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

def _year_demand(data, year, winter, lng, reservation=0.0):
    """Demand for one year with the Winter, LNG and reservation levers applied.

    Returns ``(demand frame, TJ diverted, {day: TJ reserved})``.
    The reservation is applied last, so it bites on the export volume planned
    under this scenario rather than on the raw baseline.
    """
    dm = data['demand'].copy()
    winter_mult = _lever('Winter', winter,
                         {"Low": 1.0, "Medium": 1.5, "High": 2.2}[winter])
    dm.loc[(dm['Year'] == year) & (dm['Node'].isin(['Melbourne', 'Adelaide', 'Sydney'])) &
           (dm['Day'] >= 150) & (dm['Day'] <= 250), 'Demand'] *= winter_mult
    lng_mult = get_lng_mult(lng, year)
    dm.loc[(dm['Year'] == year) & (dm['Node'].isin(['APLNG', 'GLNG', 'QCLNG'])), 'Demand'] *= lng_mult
    return apply_lng_reservation(dm[dm['Year'] == year].copy(), reservation)


# Long-form baseline names for the one-line run header; the dashboard's dropdown
# labels are its own (BASELINE_LABEL), these only ever go to the terminal.
BASELINE_LABELS = {'StepChange': 'Step Change', 'Accelerated': 'Accelerated Transition',
                   'SlowerGrowth': 'Slower Growth'}


def run_title(winter, lng, baseline="StepChange", dunkelflaute=False, reservation=0.0,
              elastic_demand=False, foresight=True, datacentre=None,
              netback_pricing=False):
    """One-line description of a scenario, for the terminal header."""
    bits = [BASELINE_LABELS.get(baseline, baseline), f"Winter {winter}", f"LNG {lng}"]
    if dunkelflaute:
        bits.append("SA Dunkelflaute 2027")
    if reservation:
        bits.append(f"{round(reservation * 100)}% reservation")
    if datacentre:
        bits.append(f"data centres {datacentre.get('NSW', 0):g} PJ NSW / "
                    f"{datacentre.get('VIC', 0):g} PJ VIC from {datacentre['start_year']}")
    if netback_pricing:
        bits.append("LNG netback pricing")
    if elastic_demand:
        bits.append("elastic demand")
    if not foresight:
        bits.append("myopic")
    return "  ·  ".join(bits)


def solve_scenario(winter, lng, mip_gap=0.005, callback=None,
                   baseline="StepChange", dunkelflaute=False, discount_rate=0.07,
                   foresight=True, reservation=0.0, elastic_demand=False,
                   datacentre=None, netback_pricing=False, title=None, log=True):
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
    assume.

    ``netback_pricing=True`` applies ACIL Allen's price formation: the LNG trains
    stop being must-serve demand and become willingness-to-pay blocks valued at the
    export netback, and LNG imports are priced at ACIL Allen's injection cost. This
    is what makes the international price discipline domestic prices instead of
    exports being taken at any price. See the header block in model.py, and
    build_lng_prices.py for where the series comes from.

    ``elastic_demand=True`` replaces must-serve mass-market demand with the step
    demand curves calibrated in build_demand_curves.py, so distribution load
    sheds when the nodal price exceeds its willingness to pay instead of being
    served at any cost. Off by default: it changes every scenario, not just
    reservation runs, so the inelastic case stays the comparison baseline.

    Prints a one-line scenario header followed by one line per solved year;
    ``title`` overrides the header text and ``log=False`` silences both, which is
    what parallel sweeps use — year lines from several workers at once interleave
    into noise, so the parent process reports one line per finished scenario.
    """
    # One header per scenario, then one line per year: with the batch solving 28
    # scenarios x 26 years, the year lines are meaningless without it. Callers that
    # already have a nicer label (the dashboard passes its scenario key) override it.
    if log:
        print(f"\n{title or run_title(winter, lng, baseline, dunkelflaute, reservation, elastic_demand, foresight, datacentre, netback_pricing)}",
              flush=True)
    data = load_data(baseline)
    years = list(range(HORIZON_START, HORIZON_END + 1))
    if foresight:
        return _solve_foresight(data, years, winter, lng, baseline,
                                dunkelflaute, mip_gap, discount_rate, callback,
                                reservation, elastic_demand, log, datacentre,
                                netback_pricing)
    return _solve_myopic(data, years, winter, lng, baseline,
                         dunkelflaute, mip_gap, callback, reservation,
                         elastic_demand, log, datacentre, netback_pricing)


def _solve_myopic(data, years, winter, lng, baseline, dunkelflaute,
                  mip_gap, callback, reservation=0.0, elastic_demand=False, log=True,
                  datacentre=None, netback_pricing=False):
    """Reactive year-by-year solve: each year decides builds with no foresight."""
    contracts_all = data['contracts']
    built_projects, results = [], []
    for i, year in enumerate(years):
        if callback:
            callback(year, i / len(years))
        demand_yr, diverted, reserved_day = _year_demand(data, year, winter, lng, reservation)
        gm = GasMarketModel(
            data['nodes'], data['arcs'], data['supply'], demand_yr, data['expansion'],
            contracts_df=contracts_all if year <= 2040 else None, year=year,
            already_built=built_projects,
            baseline=baseline, dunkelflaute=dunkelflaute,
            elastic_demand=elastic_demand, reserved_by_day=reserved_day,
            datacentre=datacentre, netback_pricing=netback_pricing)
        gm.build_model()
        status = gm.solve(mip_gap=mip_gap)
        if status != "ok":
            raise RuntimeError(f"Solver failed in {year}: {status}")
        yr_res = gm.get_results()
        yr_res['Year'] = year
        yr_res['reservation_share'] = reservation
        yr_res['lng_reserved_tj'] = diverted
        yr_res['elastic_demand'] = elastic_demand
        results.append(yr_res)
        built_projects.extend([b for b in yr_res['builds'] if b not in built_projects])
        if log:
            print(f"    Year {year} complete", flush=True)
    if callback:
        callback(years[-1], 1.0)
    return results


def _solve_foresight(data, years, winter, lng, baseline, dunkelflaute,
                     mip_gap, discount_rate, callback, reservation=0.0,
                     elastic_demand=False, log=True, datacentre=None,
                     netback_pricing=False):
    """Two-stage full-horizon solve: perfect-foresight capacity + 365-day dispatch."""
    from capacity_model import CapacityExpansionModel, build_representative_days
    contracts_all = data['contracts']
    start_year, end_year = years[0], years[-1]

    # --- Pass 1: assemble every year's demand + GPG/industrial (events applied) ---
    dispatch_models, demand_all, gpg_all, ind_all = {}, {}, {}, {}
    diverted_by_year, reserved_all, dc_all = {}, {}, {}
    for year in years:
        # Applied here, before the representative days are built, so the capacity
        # layer sizes the network against the same post-reservation demand the
        # dispatch layer will face.
        demand_yr, diverted_by_year[year], reserved_day = _year_demand(
            data, year, winter, lng, reservation)
        for d, v in reserved_day.items():
            reserved_all[(year, d)] = v
        gm = GasMarketModel(
            data['nodes'], data['arcs'], data['supply'], demand_yr, data['expansion'],
            contracts_df=contracts_all if year <= 2040 else None, year=year,
            baseline=baseline, dunkelflaute=dunkelflaute,
            elastic_demand=elastic_demand, reserved_by_day=reserved_day,
            datacentre=datacentre, netback_pricing=netback_pricing)
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
    mm_blocks = dispatch_models[start_year].mm_blocks
    rep = build_representative_days(years, demand_all, gpg_all, ind_all,
                                    data['nodes'], mm_blocks=mm_blocks,
                                    reserved_all=reserved_all, dc_all=dc_all)
    cap = CapacityExpansionModel(
        data['nodes'], data['arcs'], data['supply'], data['expansion'], years, rep,
        discount_rate=discount_rate,
        strike_gpg=dispatch_models[start_year].strike_gpg,
        strike_ind=dispatch_models[start_year].strike_ind,
        mm_blocks=mm_blocks,
        ind_raise=dispatch_models[start_year].ind_raise,
        gpg_raise=dispatch_models[start_year].gpg_raise,
        gpg_capacity=dispatch_models[start_year].gpg_capacity,
        lng_arcs=dispatch_models[start_year].lng_arcs,
        lng_source=dispatch_models[start_year].lng_source,
        # One netback and one import cost per year: both track the international
        # LNG price, so unlike a field cost they cannot be a single number.
        netback_by_year={y: dispatch_models[y].netback for y in years}
                        if netback_pricing else None,
        import_cost_by_year={
            y: float(dispatch_models[y].lng_prices['Import_Injection_AUD_GJ'])
            for y in years} if netback_pricing else None,
        export_headroom=dispatch_models[start_year].export_headroom)
    cap.build_model()
    status = cap.solve(mip_gap=mip_gap)
    if status != "ok":
        raise RuntimeError(f"Capacity model failed: {status}")
    build_year = cap.get_build_schedule()
    active_by_year = {y: {e for e, by in build_year.items() if by is not None and by <= y} for y in years}

    # --- Pass 3: full 365-day dispatch each year with builds fixed ----------------
    scenario_results = []
    for i, year in enumerate(years):
        if callback:
            callback(year, (i + 1) / len(years))
        gm = dispatch_models[year]
        gm.builds_fixed = active_by_year[year]
        gm.build_model()
        status = gm.solve(mip_gap=mip_gap)
        if status != "ok":
            raise RuntimeError(f"Dispatch failed in {year}: {status}")
        yr_res = gm.get_results()
        yr_res['Year'] = year
        yr_res['reservation_share'] = reservation
        yr_res['lng_reserved_tj'] = diverted_by_year[year]
        yr_res['elastic_demand'] = elastic_demand
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
                        help="First year the data centre demand appears (default 2030)")
    parser.add_argument("--netback-pricing", action="store_true",
                        help="ACIL Allen LNG netback price formation: LNG exports "
                             "become willingness-to-pay blocks at the netback and "
                             "imports are priced at the injection cost, instead of "
                             "exports being must-serve demand")
    parser.add_argument("--elastic-demand", action="store_true",
                        help="Price-responsive mass-market demand: use the step "
                             "demand curve in curtailment_params.csv instead of "
                             "must-serve distribution volumes")
    parser.add_argument("--myopic", action="store_true",
                        help="Year-by-year solve instead of the two-stage foresight method")
    parser.add_argument("--mip-gap", type=float, default=0.005)
    solvers.add_solver_argument(parser)
    args = parser.parse_args()

    if args.solver:
        solvers.set_solver_name(args.solver)
    print(f"Solver: {solvers.describe()}")
    solvers.require_available()

    datacentre = None
    if args.dc_nsw > 0 or args.dc_vic > 0:
        datacentre = {'NSW': args.dc_nsw, 'VIC': args.dc_vic,
                      'start_year': args.dc_start}

    t0 = time.time()
    results = solve_scenario(
        args.winter, args.lng, mip_gap=args.mip_gap,
        baseline=args.baseline, dunkelflaute=args.dunkelflaute,
        foresight=not args.myopic, reservation=args.reservation / 100.0,
        elastic_demand=args.elastic_demand, datacentre=datacentre,
        netback_pricing=args.netback_pricing)
    print(f"\nSolved {len(results)} years in {time.time() - t0:.1f}s "
          f"using {solvers.describe()}")
    builds = sorted({b for yr in results for b in yr['builds']})
    print(f"Projects built: {', '.join(builds) if builds else 'none'}")


if __name__ == "__main__":
    main()
