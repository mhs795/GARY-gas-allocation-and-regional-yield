"""Solve the standard scenario set and write it to the results cache.

The 3x3x3 grid regen_results.py produces spends 18 of its 27 runs on winter stress
multipliers and never touches the import-terminal, expansion-filter or data centre
axes, which are the ones that discriminate now that supply is stock-limited. This
is the rationalised set: fewer runs, more axes.

Blocks: OUTLOOK at Winter Medium (the central GSOO case -- AEMO's weather-averaged
series), ADEQUACY stress at Winter High plus the warm-winter downside at Low,
STRUCTURAL/policy, and DATA CENTRES.

    python src/run_standard_set.py                 # all of it, into the cache
    python src/run_standard_set.py --keep          # skip keys already cached
    python src/run_standard_set.py --list          # print the set and exit

Every run finishes by writing output/domestic_price_summary.xlsx off the cache --
levels and percent-change-against-central, with a chart on each tab. See
export_price_summary.py; --no-export skips it.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import datacentre_series
import export_price_summary
import results_io
import sweep
from dashboard import scenario_key, pretty_key

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
CACHE = os.path.join(DATA, 'precalculated_results.pkl')
DC_FILE = os.path.join(DATA, 'datacentre_demand_NSW.csv')


def dc_spec():
    """The linked NSW data centre pipeline, as solve_scenario wants it."""
    series = datacentre_series.load(DC_FILE)
    return {'NSW': 0.0, 'VIC': 0.0, 'start_year': 2030, 'series': series,
            'source': DC_FILE,
            'fingerprint': datacentre_series.fingerprint(series),
            'label': datacentre_series.label(DC_FILE)}


def build():
    """(block, kwargs) for every run in the set. Netback pricing on throughout."""
    dc = dc_spec()
    B, L, M, H = 'StepChange', 'Low', 'Medium', 'High'
    common = dict(netback_pricing=True)
    S = []
    # --- A. OUTLOOK: Winter Medium, the central GSOO case ---------------------
    S += [('A outlook', dict(baseline=B, winter=M, lng=M, **common)),
          ('A outlook', dict(baseline='Accelerated', winter=M, lng=M, **common)),
          ('A outlook', dict(baseline='SlowerGrowth', winter=M, lng=M, **common)),
          ('A outlook', dict(baseline=B, winter=M, lng=L, **common)),
          ('A outlook', dict(baseline=B, winter=M, lng=H, **common))]
    # --- B. WEATHER: the warm downside and the cold stress ---------------------
    S += [('B weather', dict(baseline=B, winter=L, lng=M, **common)),
          ('B weather', dict(baseline=B, winter=H, lng=M, **common)),
          ('B weather', dict(baseline=B, winter=M, lng=M, dunkelflaute=True, **common))]
    # --- C. STRUCTURAL: what the system may build, and policy ------------------
    S += [('C structural', dict(baseline=B, winter=M, lng=M,
                                allow_import_terminals=False, **common)),
          ('C structural', dict(baseline=B, winter=M, lng=M,
                                gsoo_expansions_only=True, **common)),
          ('C structural', dict(baseline=B, winter=M, lng=M,
                                reservation=0.20, **common))]
    # --- D. DATA CENTRES: firm load that cannot shed ---------------------------
    S += [('D datacentre', dict(baseline=B, winter=M, lng=M, datacentre=dc, **common)),
          ('D datacentre', dict(baseline=B, winter=H, lng=M, datacentre=dc, **common)),
          ('D datacentre', dict(baseline=B, winter=M, lng=M, datacentre=dc,
                                allow_import_terminals=False, **common))]
    # --- E. RESERVATION THAT BREAKS CONTRACTS ---------------------------------
    # A 20% reservation capped at the uncontracted tail can only ever deliver 7%
    # while the foundation SPAs run, so respect_contracts=False is the only way to
    # see what 20% actually does before 2036. The pair differs ONLY by data centre
    # load, so the two read against each other directly.
    S += [('E reservation', dict(baseline=B, winter=M, lng=M, reservation=0.20,
                                 respect_contracts=False, **common)),
          ('E reservation', dict(baseline=B, winter=M, lng=M, reservation=0.20,
                                 respect_contracts=False, datacentre=dc, **common))]
    # --- F. THE SAME RESERVATION, CONTRACTS RESPECTED --------------------------
    # Block E's twins under the legally realistic rule: the reservation may only
    # take UNCONTRACTED export volume, so the 20% ask is capped at the spot tail.
    # E minus F is the cost of breaking the foundation SPAs; F on its own is what
    # a reservation announced today would actually deliver. The no-data-centre
    # member of the pair is the 'C structural' reservation run above -- same
    # kwargs, same cache key -- so it is not repeated here; --keep will skip it if
    # you ever re-solve the set.
    S += [('F reservation', dict(baseline=B, winter=M, lng=M, reservation=0.20,
                                 datacentre=dc, **common))]
    return S


def key_for(kw):
    """Cache key, built by the dashboard's own builder so the dropdown reads it."""
    return scenario_key(kw['baseline'], kw['winter'], kw['lng'],
                        dunkelflaute=kw.get('dunkelflaute', False),
                        reservation=kw.get('reservation', 0.0),
                        foresight=kw.get('foresight', True),
                        discount=kw.get('discount_rate'),
                        datacentre=kw.get('datacentre'),
                        netback=kw.get('netback_pricing', False),
                        respect_contracts=kw.get('respect_contracts', True),
                        gsoo_exp=kw.get('gsoo_expansions_only', False),
                        allow_imports=kw.get('allow_import_terminals', True))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--workers', type=int, default=None)
    ap.add_argument('--keep', action='store_true',
                    help='Merge into the cache, skipping keys already present')
    ap.add_argument('--list', action='store_true', help='Print the set and exit')
    ap.add_argument('--mip-gap', type=float, default=None,
                    help='Relative MIP gap (default: mip_gap_default on the workbook)')
    ap.add_argument('--no-export', action='store_true',
                    help='Skip the domestic price summary workbook')
    args = ap.parse_args()

    spec = build()
    if args.list:
        for block, kw in spec:
            print(f"  [{block:<13}] {pretty_key(key_for(kw))}")
        print(f"\n{len(spec)} scenarios")
        return

    existing = {}
    if args.keep and os.path.exists(CACHE):
        existing = results_io.load(CACHE).get('all_scenarios', {})

    jobs = []
    for block, kw in spec:
        key = key_for(kw)
        if args.keep and key in existing:
            print(f"  skip (cached)  {pretty_key(key)}")
            continue
        jobs.append((key, f"[{block}] {pretty_key(key)}",
                     dict(kw, mip_gap=args.mip_gap)))

    print(f"Solving {len(jobs)} scenarios\n")
    out = dict(existing)
    t0 = time.time()

    def done(i, n, key, results, secs):
        out[key] = results
        print(f"  [{i}/{n}] {secs/60:5.1f} min  {pretty_key(key)}", flush=True)
        results_io.save({'all_scenarios': out}, CACHE)   # checkpoint every run

    sweep.run_jobs(jobs, workers=args.workers, on_done=done)
    results_io.save({'all_scenarios': out}, CACHE)
    print(f"\n{len(out)} scenarios in the cache; {(time.time()-t0)/60:.1f} min total")

    # Off the cache, not off `out`, so a --keep run exports the whole set rather
    # than only what it just solved. Never fatal: the solves are the expensive
    # part and they are already safely on disk by here.
    if not args.no_export:
        try:
            export_price_summary.write_summary()
        except Exception as exc:
            print(f"  price summary export failed: {exc}")


if __name__ == '__main__':
    main()
