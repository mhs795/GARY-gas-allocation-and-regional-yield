"""Regenerate precalculated_results.pkl using the live solve path, so results
include the GPG/industrial curtailment streams. Matches the dashboard key
format exactly: Base_<baseline>_Winter_<w>_LNG_<l>.

By default this regenerates the 9 Winter x LNG scenarios for the
StepChange baseline only. Pass baselines on the command line to do more, e.g.
  python regen_results.py StepChange Accelerated SlowerGrowth
The cache is stored compressed and column-oriented (see results_io.py).
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import results_io
from solve import solve_scenario

baselines = sys.argv[1:] or ['StepChange']
out = {'all_scenarios': {}, 'current_key': None}
for baseline in baselines:
    for winter in ['Low', 'Medium', 'High']:
        for lng in ['Low', 'Medium', 'High']:
            key = f"Base_{baseline}_Winter_{winter}_LNG_{lng}"
            print('solving', key, flush=True)
            out['all_scenarios'][key] = solve_scenario(winter, lng, baseline=baseline)
            out['current_key'] = key

path = os.path.join(os.path.dirname(__file__), 'data', 'precalculated_results.pkl')
results_io.save(out, path)
print('DONE -> ' + path, flush=True)
