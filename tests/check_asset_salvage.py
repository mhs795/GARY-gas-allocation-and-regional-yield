"""Checks on the asset terminal-value credit. Run: python tmp/check_asset_salvage.py

No solve here -- these are the properties the credit has to hold whatever the
scenario, plus the data integrity the objective term assumes.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import pandas as pd

import capacity_model as cm
from model import IMPORT_NODES

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src', 'data')
R = 0.07
fails = []


def check(name, ok, detail=''):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  -- ' + detail) if detail else ''}")
    if not ok:
        fails.append(name)


print('\n_asset_residual -- properties')
check('blank life earns nothing', cm._asset_residual(float('nan'), 5, R) == 0.0)
check('missing life earns nothing', cm._asset_residual(None, 5, R) == 0.0)
check('non-numeric life earns nothing', cm._asset_residual('', 5, R) == 0.0)
check('zero/negative life earns nothing',
      cm._asset_residual(0, 5, R) == 0.0 and cm._asset_residual(-10, 5, R) == 0.0)
check('life fully used earns nothing',
      cm._asset_residual(20, 20, R) == 0.0 and cm._asset_residual(20, 35, R) == 0.0)
check('built at the horizon keeps ~all of it',
      0.99 < cm._asset_residual(50, 1, R) <= 1.0,
      f'{cm._asset_residual(50, 1, R):.4f}')
check('bounded in [0, 1]',
      all(0.0 <= cm._asset_residual(l, u, R) <= 1.0
          for l in (1, 20, 25, 50, 80) for u in range(0, 60)))
check('falls monotonically as the asset is used',
      all(cm._asset_residual(50, u, R) >= cm._asset_residual(50, u + 1, R)
          for u in range(0, 60)))
check('a longer life leaves more at the same age',
      cm._asset_residual(50, 22, R) > cm._asset_residual(25, 22, R) >
      cm._asset_residual(20, 22, R))
check('r=0 degenerates to straight line',
      abs(cm._asset_residual(50, 20, 0.0) - 0.6) < 1e-12,
      f'{cm._asset_residual(50, 20, 0.0):.4f}')

print('\n_asset_residual -- equals the remaining capital charges, computed independently')
for life, used in ((50, 22), (25, 22), (20, 10), (80, 5)):
    crf = R / (1 - (1 + R) ** -life)               # capital recovery factor
    left = life - used
    pv_remaining = crf * (1 - (1 + R) ** -left) / R   # PV at horizon, per $1 of CapEx
    got = cm._asset_residual(life, used, R)
    check(f'life {life}, used {used}: annuity identity',
          abs(got - pv_remaining) < 1e-12, f'{got:.6f} vs {pv_remaining:.6f}')

print('\nexpansion_options.csv -- the data the term reads')
exp = pd.read_csv(os.path.join(DATA, 'expansion_options.csv'))
check('AssetLife column present', 'AssetLife' in exp.columns)
lives = exp['AssetLife']
check('every stated life is positive and finite',
      bool((lives.dropna() > 0).all()) and bool(pd.notna(lives.dropna()).all()))
check('every stated life is plausible (5-100 yr)',
      bool(lives.dropna().between(5, 100).all()))

is_import = (exp['Type'].astype(str).str.strip() == 'Terminal') & \
            (exp['Target'].astype(str).str.strip().isin(IMPORT_NODES))
is_pipe = exp['Type'].astype(str).str.strip() == 'Pipeline'
credited = lives.notna()
check('only pipelines and import terminals are credited',
      bool((credited & ~(is_pipe | is_import)).sum() == 0),
      ', '.join(exp.loc[credited & ~(is_pipe | is_import), 'Name']) or 'none')
check('no field development is credited (its capital is subsurface)',
      bool((credited & ~is_pipe & ~is_import).sum() == 0))
check('every pipeline and import terminal HAS a life',
      bool((~credited & (is_pipe | is_import)).sum() == 0),
      ', '.join(exp.loc[~credited & (is_pipe | is_import), 'Name']) or 'none')

print('\nthe credit never exceeds what was paid')
worst = 0.0
for _, e in exp[credited].iterrows():
    for by in range(2025, 2052):
        f = cm._asset_residual(e['AssetLife'], 2051 - by + 1, R)
        # discounted credit at the horizon vs discounted capex in the build year
        credit = float(e['CapEx']) * f / (1 + R) ** (2051 - 2025)
        paid = float(e['CapEx']) / (1 + R) ** (by - 2025)
        worst = max(worst, credit / paid if paid else 0.0)
check('net capex stays positive for every project and build year', worst < 1.0,
      f'worst credit/paid ratio {worst:.3f}')

print('\nswitch')
check('asset_salvage resolves from the workbook', isinstance(cm.ASSET_SALVAGE, bool),
      f'ASSET_SALVAGE={cm.ASSET_SALVAGE}')

print(f"\n{'ALL CHECKS PASS' if not fails else str(len(fails)) + ' FAILED: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
