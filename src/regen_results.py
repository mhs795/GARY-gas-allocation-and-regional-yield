"""Regenerate precalculated_results.pkl using the live solve path, so results
include the GPG/industrial curtailment streams. Matches the dashboard key
format exactly: Base_<baseline>_ADGSM_<x>_Winter_<w>_LNG_<l>.

By default this regenerates the 9 ADGSM=False Winter x LNG scenarios for the
StepChange baseline only. Pass baselines on the command line to do more, e.g.
  python regen_results.py StepChange Accelerated SlowerGrowth
NOTE: each baseline adds ~150 MB to the pkl; check free disk before doing all three.
"""
import sys, os, pickle
sys.path.insert(0, os.path.dirname(__file__))
from solve import solve_scenario

ADGSM = False
baselines = sys.argv[1:] or ['StepChange']
out = {'all_scenarios': {}, 'current_key': None}
for baseline in baselines:
    for winter in ['Low', 'Medium', 'High']:
        for lng in ['Low', 'Medium', 'High']:
            key = f"Base_{baseline}_ADGSM_{ADGSM}_Winter_{winter}_LNG_{lng}"
            print('solving', key, flush=True)
            out['all_scenarios'][key] = solve_scenario(winter, lng, adgsm_enabled=ADGSM, baseline=baseline)
            out['current_key'] = key

path = os.path.join(os.path.dirname(__file__), 'data', 'precalculated_results.pkl')
with open(path, 'wb') as f:
    pickle.dump(out, f)
print('DONE -> ' + path, flush=True)
