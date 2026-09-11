"""Standalone runner for the GARY 'Run Scenarios' batch, outside the dashboard UI.

Replicates dashboard.run_batch() with the dashboard's default sidebar settings:
StepChange/Accelerated/SlowerGrowth x Winter(Low/Med/High) x LNG(Low/Med/High),
plus one StepChange + SA Dunkelflaute case -- 28 scenarios total. Progress is
printed to stdout and each scenario is cached to data/precalculated_results.pkl
as it completes (same file the dashboard reads), so the dashboard will show
results as they land even while this keeps running.
"""
import sys
import time

import dashboard as D

def _progress(update):
    pct, msg = update
    print(f"[{time.strftime('%H:%M:%S')}] {pct:3d}%  {msg}", flush=True)

def main():
    foresight = True
    reservation = D.reservation_share([], 1)          # reservation-toggle off
    datacentre = D.datacentre_spec(0, 0, 2030, D.DC_FILE_DEFAULT)
    netback = D.NETBACK_DEFAULT
    gsoo_exp = False
    allow_imports = D.IMPORTS_DEFAULT
    respect_contracts = True
    dr = 0.07
    gap = D._MIP_GAP_DEFAULT
    rep_bins = D._REP_BINS_DEFAULT

    combos = [(b['value'], w, l, False) for b in D.BASELINES for w in D.LEVELS for l in D.LEVELS]
    combos.append(('StepChange', 'Medium', 'Medium', True))

    data = D.load_results()
    jobs = []
    for b, w, l, dunkel in combos:
        key = D.scenario_key(b, w, l, dunkel, reservation, foresight, dr, datacentre,
                              netback, respect_contracts, gsoo_exp, allow_imports, rep_bins)
        if dunkel or key not in data['all_scenarios']:
            jobs.append((key, D.pretty_key(key),
                         dict(winter=w, lng=l, mip_gap=gap, rep_bins=rep_bins, baseline=b,
                              dunkelflaute=dunkel, discount_rate=dr,
                              foresight=foresight, reservation=reservation,
                              datacentre=datacentre, netback_pricing=netback,
                              respect_contracts=respect_contracts,
                              gsoo_expansions_only=gsoo_exp,
                              allow_import_terminals=allow_imports)))

    print(f"{len(combos)} combos, {len(jobs)} to solve "
          f"({len(combos) - len(jobs)} already cached)", flush=True)
    D._run_sweep(jobs, data, _progress)
    print("Batch complete.", flush=True)

if __name__ == '__main__':
    sys.exit(main())
