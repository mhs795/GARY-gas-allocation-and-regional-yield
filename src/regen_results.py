"""Rebuild `data/precalculated_results.pkl` from the command line.

This is the **Run All Scenarios (Batch)** button without the dashboard, and it
produces the same set the button does: every GSOO baseline x Winter x LNG (27
runs) plus one Step Change + SA Dunkelflaute case at the central Winter/LNG
level, so the dunkelflaute run sits alongside its own comparison scenario.

Scenarios are independent, so they run across worker processes via sweep.py.
That module spawns rather than forks, which re-imports this file in every child
-- hence the `if __name__ == '__main__'` guard. Without it the children re-run
the sweep on import and the pool dies during bootstrap.

    python src/regen_results.py                        # all three baselines
    python src/regen_results.py StepChange             # just one
    python src/regen_results.py --gsoo-expansions-only # committed expansions only

By default the cache is REPLACED, which is what you want after changing a source
input: keys are unchanged by most edits, so results solved against the old inputs
would otherwise sit in the cache looking current. `--keep` merges into the
existing cache instead, skipping keys already present.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import results_io
import sweep
from solve import HORIZON_END, HORIZON_START, run_title

BASELINES = ['StepChange', 'Accelerated', 'SlowerGrowth']
LEVELS = ['Low', 'Medium', 'High']
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     'data', 'precalculated_results.pkl')


def scenario_key(baseline, winter, lng, dunkelflaute=False, gsoo_exp=False):
    """The dashboard's key format, for the segments this script can produce.

    Kept deliberately narrow: dashboard.scenario_key is the authority on the full
    format, and duplicating all of it here would be a second place to get segment
    order wrong. This script only ever varies baseline/winter/LNG/dunkelflaute and
    the expansion filter, so it only builds those segments.
    """
    return (f'Base_{baseline}_Winter_{winter}_LNG_{lng}'
            + ('_Dunkelflaute' if dunkelflaute else '')
            + ('_GSOOExp' if gsoo_exp else ''))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('baselines', nargs='*', default=None,
                    help='GSOO baselines to solve (default: all three)')
    ap.add_argument('--gsoo-expansions-only', action='store_true',
                    help='Restrict the capacity layer to expansions AEMO counts '
                         'as committed in the 2026 GSOO/VGPR')
    ap.add_argument('--workers', type=int, default=None,
                    help='Worker processes (default: half the logical CPUs)')
    ap.add_argument('--keep', action='store_true',
                    help='Merge into the existing cache and skip keys already in '
                         'it, instead of replacing the cache outright')
    ap.add_argument('--mip-gap', type=float, default=0.005)
    args = ap.parse_args()

    baselines = args.baselines or BASELINES
    gsoo_exp = args.gsoo_expansions_only

    out = {'all_scenarios': {}, 'current_key': None}
    if args.keep and os.path.exists(CACHE):
        out = results_io.load(CACHE)
        print(f'Merging into {len(out["all_scenarios"])} cached scenarios')

    combos = [(b, w, l, False) for b in baselines for w in LEVELS for l in LEVELS]
    if 'StepChange' in baselines:
        combos.append(('StepChange', 'Medium', 'Medium', True))

    jobs = []
    for b, w, l, dunkel in combos:
        key = scenario_key(b, w, l, dunkel, gsoo_exp)
        if args.keep and key in out['all_scenarios'] and not dunkel:
            continue
        jobs.append((key, run_title(w, l, b, dunkel, gsoo_expansions_only=gsoo_exp),
                     dict(winter=w, lng=l, baseline=b, dunkelflaute=dunkel,
                          mip_gap=args.mip_gap, gsoo_expansions_only=gsoo_exp)))

    workers = args.workers or sweep.default_workers()
    print(f'{len(jobs)} scenarios x {HORIZON_END - HORIZON_START + 1} years '
          f'on {workers} workers'
          + ('  ·  GSOO expansions only' if gsoo_exp else ''), flush=True)
    t0 = time.time()

    def _done(i, n, key, results, secs):
        out['all_scenarios'][key] = results
        out['current_key'] = key
        # Save as each one finishes, so an interrupted rebuild keeps its work --
        # 28 scenarios is a long time to lose to one Ctrl-C.
        results_io.save(out, CACHE)
        print(f'[{i}/{n}] {key}  ({secs:.0f}s)', flush=True)

    sweep.run_jobs(jobs, workers=workers, on_done=_done)
    print(f'DONE in {(time.time() - t0) / 60:.1f} min -> {CACHE}  '
          f'({len(out["all_scenarios"])} scenarios cached)', flush=True)


if __name__ == '__main__':
    main()
