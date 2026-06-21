"""Regenerate precalculated_results.pkl (9 ADGSM=False scenarios) using the live
solve path, so results include the new GPG/industrial curtailment streams.
Matches the dashboard's expected format exactly."""
import sys, os, pickle
sys.path.insert(0, os.path.dirname(__file__))
from solve import solve_scenario

ADGSM = False
out = {'all_scenarios': {}, 'current_key': None}
for winter in ['Low', 'Medium', 'High']:
    for lng in ['Low', 'Medium', 'High']:
        key = f"ADGSM_{ADGSM}_Winter_{winter}_LNG_{lng}"
        print('solving', key, flush=True)
        out['all_scenarios'][key] = solve_scenario(winter, lng, adgsm_enabled=ADGSM)
        out['current_key'] = key

path = os.path.join(os.path.dirname(__file__), 'data', 'precalculated_results.pkl')
with open(path, 'wb') as f:
    pickle.dump(out, f)
print('DONE -> ' + path, flush=True)
