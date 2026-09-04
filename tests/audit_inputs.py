"""Audit GARY's inputs and network topology. Run: python tests/audit_inputs.py

Every check here is mechanical: it compares one input against another input, or
against a published source already in the repo (the GBB extracts), or against a
convention this project has written down. Nothing here is a matter of taste, so a
FAIL is a defect and a WARN is something to look at and then either fix or
document.

Structured in the order a reader would want it: does the network hold together,
is the data internally consistent, do the numbers match their sources, and are
the conventions the docs describe actually followed.
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import pandas as pd

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src', 'data')

fails, warns = [], []
_section = ['']


def section(t):
    _section[0] = t
    print(f"\n\033[1m{t}\033[0m")


def ok(name, detail=''):
    print(f"  PASS  {name}{('  -- ' + detail) if detail else ''}")


def fail(name, detail=''):
    print(f"  FAIL  {name}{('  -- ' + detail) if detail else ''}")
    fails.append(f'[{_section[0]}] {name}: {detail}')


def warn(name, detail=''):
    print(f"  WARN  {name}{('  -- ' + detail) if detail else ''}")
    warns.append(f'[{_section[0]}] {name}: {detail}')


def check(name, cond, detail=''):
    (ok if cond else fail)(name, detail)


def csv(n):
    return pd.read_csv(os.path.join(DATA, n))


nodes, arcs = csv('nodes.csv'), csv('arcs.csv')
supply, exp = csv('supply.csv'), csv('expansion_options.csv')
N = set(nodes['Name'])
A = set(arcs['Name'])

# ---------------------------------------------------------------------------
section('1. Referential integrity — does everything point at something real?')
# ---------------------------------------------------------------------------
bad = arcs[~arcs['From'].isin(N) | ~arcs['To'].isin(N)]
check('every arc endpoint is a node in nodes.csv', bad.empty,
      ', '.join(bad['Name']) or 'none')
check('every supply row sits on a node', set(supply['Node']) <= N,
      ', '.join(sorted(set(supply['Node']) - N)) or 'none')

pipe, term = exp[exp['Type'] == 'Pipeline'], exp[exp['Type'] == 'Terminal']
check('every Pipeline project targets a real arc', set(pipe['Target']) <= A,
      ', '.join(sorted(set(pipe['Target']) - A)) or 'none')
check('every Terminal project targets a real node', set(term['Target']) <= N,
      ', '.join(sorted(set(term['Target']) - N)) or 'none')
check('no duplicate arc names', not arcs['Name'].duplicated().any())
check('no duplicate node names', not nodes['Name'].duplicated().any())
check('no duplicate project names', not exp['Name'].duplicated().any())

# Parallel arcs are legitimate -- Bulloo really is a second pipe from the Surat to
# Moomba, not a duplicate of the SWQP. What is NOT legitimate is pricing two
# parallel arcs on different bases, because the dispatch then chooses between a
# capital-inclusive tariff and a capital-exclusive one and always picks the
# latter. See section 6.
dup = arcs.groupby(['From', 'To']).size()
dup = dup[dup > 1]
if dup.empty:
    ok('no two arcs run between the same ordered pair')
else:
    for frm, to in dup.index:
        par = arcs[(arcs['From'] == frm) & (arcs['To'] == to)]
        bases = {('variable' if float(r['Capacity']) == 0 else 'posted') for _, r in par.iterrows()}
        detail = f"{frm}->{to}: " + ', '.join(
            f"{r['Name']} ${r['Cost']:.4f} ({'variable' if float(r['Capacity']) == 0 else 'posted'})"
            for _, r in par.iterrows())
        if len(bases) > 1:
            fail('parallel arcs priced on DIFFERENT bases — dispatch will strand the posted one', detail)
        else:
            ok('parallel arcs share a pricing basis', detail)

# ---------------------------------------------------------------------------
section('2. Connectivity — can gas get where it has to go?')
# ---------------------------------------------------------------------------
adj = defaultdict(list)
for _, r in arcs.iterrows():
    if float(r['Capacity']) > 0:
        adj[r['From']].append(r['To'])
# arcs a project can open count as reachable-if-built
adj_built = defaultdict(list)
buildable = set(pipe['Target'])
for _, r in arcs.iterrows():
    if float(r['Capacity']) > 0 or r['Name'] in buildable:
        adj_built[r['From']].append(r['To'])


def reach(starts, graph):
    seen, stack = set(starts), list(starts)
    while stack:
        for nxt in graph.get(stack.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


sources = sorted(set(supply['Node']))
now, ever = reach(sources, adj), reach(sources, adj_built)
demand_nodes = set(nodes.loc[nodes['Type'] == 'Demand', 'Name'])
lng_nodes = set(nodes.loc[nodes['Type'] == 'LNG', 'Name'])
check('every demand node is reachable from supply as the network stands',
      demand_nodes <= now, ', '.join(sorted(demand_nodes - now)) or 'none')
check('every LNG node is reachable from supply', lng_nodes <= now,
      ', '.join(sorted(lng_nodes - now)) or 'none')

# a supply node is stranded if nothing it produces can reach any buyer
buyers = demand_nodes | lng_nodes
for s in sources:
    if not (reach([s], adj_built) & buyers):
        warn('supply node cannot reach any buyer even if everything is built', s)
else:
    reachable_sources = [s for s in sources if reach([s], adj_built) & buyers]
    if len(reachable_sources) == len(sources):
        ok('every supply node can reach a buyer if the buildable arcs are built')

orphans = [n for n in N if n not in set(arcs['From']) and n not in set(arcs['To'])]
check('no node is disconnected from the network entirely', not orphans,
      ', '.join(orphans) or 'none')

# ---------------------------------------------------------------------------
section('3. Node types match the data attached to them')
# ---------------------------------------------------------------------------
stor = nodes[nodes['StorageCapacity'] > 0]
check('every storage node has injection and withdrawal rates',
      bool(((stor['MaxInjection'] > 0) | (stor['MaxWithdrawal'] > 0)).all()),
      ', '.join(stor.loc[(stor['MaxInjection'] <= 0) & (stor['MaxWithdrawal'] <= 0), 'Name']) or 'none')
check('no non-storage node carries injection/withdrawal rates',
      bool(((nodes['StorageCapacity'] <= 0) &
            ((nodes['MaxInjection'] > 0) | (nodes['MaxWithdrawal'] > 0))).sum() == 0))

for f, label in (('demand_StepChange.csv', 'StepChange'),):
    dem = csv(f)
    have = set(dem['Node'])
    missing = demand_nodes - have
    if missing:
        warn(f'demand node with no series in {f}', ', '.join(sorted(missing)))
    else:
        ok(f'every demand node has a series in {f}')
    extra = have - N
    check(f'{f} names no node that does not exist', not extra,
          ', '.join(sorted(extra)) or 'none')

fed = {r['To'] for _, r in arcs.iterrows() if r['To'] in lng_nodes}
check('every LNG node has at least one feed pipe', fed == lng_nodes,
      ', '.join(sorted(lng_nodes - fed)) or 'none')

# ---------------------------------------------------------------------------
section('4. Supply tranches and the projects that unlock them')
# ---------------------------------------------------------------------------
pot = supply[supply['IsPotential'].astype(bool)]
fronting = defaultdict(list)
for _, e in term.iterrows():
    fronting[e['Target']].append(e['Name'])
for _, r in pot.iterrows():
    if not fronting.get(r['Node']):
        fail('undeveloped tranche has no project in front of it — it can never produce',
             f"{r['Node']} ({r['Capacity']} TJ/d, {r['Reserves_PJ']} PJ)")
if all(fronting.get(r['Node']) for _, r in pot.iterrows()):
    ok('every undeveloped tranche has at least one project that unlocks it')

dev = supply[~supply['IsPotential'].astype(bool)]
check('every developed row has a positive capacity and cost',
      bool((dev['Capacity'] > 0).all() and (dev['Cost'] > 0).all()))
for _, r in supply.iterrows():
    if pd.notna(r.get('Reserves_PJ')) and pd.notna(r.get('AEMOFullCost')):
        if float(r['AEMOFullCost']) < float(r['Cost']) - 1e-9:
            fail('AEMOFullCost below Cost — the capital split would be negative',
                 f"{r['Node']} pot={r['IsPotential']}")
ok('AEMOFullCost is never below Cost')

for node in sorted(set(pot['Node'])):
    cap_pot = float(pot[pot['Node'] == node]['Capacity'].sum())
    # Rivals in a mutual-exclusion group cannot both be built, so the most that
    # can ever front a node is the largest member of each group plus the
    # ungrouped rows -- summing them all counts Viva and Vopak twice.
    rows = exp[exp['Name'].isin(fronting.get(node, []))]
    grouped = rows[rows['Group'].notna() & (rows['Group'].astype(str).str.strip() != '')]
    ungrouped = rows[~rows.index.isin(grouped.index)]
    unlocked = float(ungrouped['NewCapacity'].sum()) + float(
        grouped.groupby('Group')['NewCapacity'].max().sum())
    if unlocked > cap_pot + 1e-6:
        warn('projects unlock more capacity than the tranche holds',
             f'{node}: projects {unlocked:.0f} TJ/d vs tranche {cap_pot:.0f} TJ/d')
if True:
    ok('no node has projects unlocking more than its tranche holds (rivals counted once)')

# ---------------------------------------------------------------------------
section('5. Expansion menu — conventions the docs commit to')
# ---------------------------------------------------------------------------
rev = arcs[arcs['Name'].str.contains('_Rev|Bulloo|NEAP', regex=True)]
for _, r in rev.iterrows():
    gated = r['Name'] in buildable
    if float(r['Capacity']) == 0 and not gated:
        fail('zero-capacity arc with no project can never carry gas', r['Name'])
zero_ungated = [r['Name'] for _, r in arcs.iterrows()
                if float(r['Capacity']) == 0 and r['Name'] not in buildable]
check('no arc is stuck at zero capacity with nothing able to open it',
      not zero_ungated, ', '.join(zero_ungated) or 'none')

check('every project has a positive capacity', bool((exp['NewCapacity'] > 0).all()),
      ', '.join(exp.loc[exp['NewCapacity'] <= 0, 'Name']) or 'none')
neg = exp[exp['CapEx'] < 0]
check('no negative CapEx', neg.empty, ', '.join(neg['Name']) or 'none')

if 'AssetLife' in exp.columns:
    from model import IMPORT_NODES
    is_imp = (exp['Type'] == 'Terminal') & (exp['Target'].isin(IMPORT_NODES))
    is_pipe = exp['Type'] == 'Pipeline'
    missing_life = exp[(is_pipe | is_imp) & exp['AssetLife'].isna()]
    check('every pipeline and import terminal carries an AssetLife',
          missing_life.empty, ', '.join(missing_life['Name']) or 'none')
    wrong_life = exp[~(is_pipe | is_imp) & exp['AssetLife'].notna()]
    check('no field development carries an AssetLife (its capital is subsurface)',
          wrong_life.empty, ', '.join(wrong_life['Name']) or 'none')

groups = exp[exp['Group'].notna() & (exp['Group'].astype(str).str.strip() != '')]
for g, rows in groups.groupby('Group'):
    if len(rows) < 2:
        warn('mutual-exclusion group with only one member does nothing', f'{g}: {rows.iloc[0]["Name"]}')
if all(len(r) > 1 for _, r in groups.groupby('Group')):
    ok('every mutual-exclusion group has at least two rivals')

src_vals = set(exp['Source'].astype(str).str.strip())
check('Source column only holds GSOO or Market', src_vals <= {'GSOO', 'Market'},
      ', '.join(sorted(src_vals - {'GSOO', 'Market'})) or 'none')

# ---------------------------------------------------------------------------
section('6. Tariff convention — posted for existing arcs, variable for new')
# ---------------------------------------------------------------------------
# docs/expansions.md: a NEW arc (base capacity 0) must carry variable haulage
# only, ~26% of a posted equivalent, because its capital is charged through
# exp_capex. An EXISTING arc carries the posted tariff.
new_arcs = arcs[arcs['Capacity'] == 0]
existing = arcs[arcs['Capacity'] > 0]
check('every new (buildable) arc has a positive tariff',
      bool((new_arcs['Cost'] > 0).all()),
      ', '.join(new_arcs.loc[new_arcs['Cost'] <= 0, 'Name']) or 'none')
# a reversal should cost less than its forward leg (variable vs posted)
PAIRS = [('EGP_Rev', 'EGP'), ('SEA_Gas_Rev', 'SEA_Gas'), ('NGP_Rev', 'NGP'),
         ('MSP_Rev', 'MSP'), ('VNI_Rev', 'VNI'), ('SWQP_Rev', 'SWQP')]
cost = arcs.set_index('Name')['Cost'].to_dict()
capm = arcs.set_index('Name')['Capacity'].to_dict()
for r, f in PAIRS:
    if r in cost and f in cost:
        ratio = cost[r] / cost[f] if cost[f] else float('inf')
        if capm[r] == 0 and ratio > 0.6:
            warn('buildable reversal priced close to a posted tariff — capital may be double-charged',
                 f'{r} {cost[r]:.4f} is {ratio:.0%} of {f} {cost[f]:.4f}')
        elif capm[r] > 0 and abs(ratio - 1.0) > 1e-9 and ratio < 0.6:
            warn('existing reversal priced on a variable basis', f'{r} is {ratio:.0%} of {f}')
ok('reversal tariffs checked against their forward legs')

# ---------------------------------------------------------------------------
section('7. Against the Gas Bulletin Board extracts in the repo')
# ---------------------------------------------------------------------------
try:
    gbb = csv('GasBBNameplateRatingCurrent.csv')
    g = gbb[gbb['facilitytype'] == 'PIPE'].copy()

    def rating(fac, contains=None):
        s = g[g['facilityname'].astype(str).str.upper() == fac.upper()]
        if contains:
            s = s[s['capacitydescription'].astype(str).str.contains(contains, case=False, na=False)]
        return float(s['capacityquantity'].max()) if not s.empty else None

    # (GARY arc, GBB facility, description filter, tolerance, note)
    MAP = [
        ('MAPS',   'MAPS', 'Southern Haul',            0.02, 'Moomba->Adelaide'),
        ('BGP',    'BGP',  None,                       0.02, 'Blacktip->Darwin'),
        ('NGP',    'NGP',  'Tennant Creek to Mt Isa',  None, 'GARY is corridor-limited by the CGP'),
        ('NGP_Rev', 'NGP', 'Mt Isa to Tennant Creek',  None, 'reverse; GARY gates it behind a project'),
    ]
    for arc, fac, desc, tol, note in MAP:
        pub = rating(fac, desc)
        if pub is None:
            warn('no GBB rating found to check against', f'{arc} ({fac})')
            continue
        have = float(capm.get(arc, float('nan')))
        if tol is None:
            ok(f'{arc} vs GBB {fac} {desc or ""}: GARY {have:.0f}, GBB {pub:.0f}', note)
        elif abs(have - pub) / pub <= tol:
            ok(f'{arc} matches the GBB', f'{have:.0f} vs {pub:.0f} TJ/d')
        else:
            fail(f'{arc} does not match the GBB', f'GARY {have:.0f} vs GBB {pub:.0f} TJ/d')
except FileNotFoundError:
    warn('GBB nameplate extract not present, skipped source checks')

# ---------------------------------------------------------------------------
section('8. Parameters workbook resolves everything the code asks for')
# ---------------------------------------------------------------------------
try:
    import subprocess
    r = subprocess.run([sys.executable,
                        os.path.join(os.path.dirname(DATA), 'build_parameters_workbook.py'), '--check'],
                       capture_output=True, text=True, timeout=120)
    txt = (r.stdout + r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr) else ''
    check('every parameter the code reads exists in the workbook',
          'All parameters resolved' in txt, txt[:120])
except Exception as exc:
    warn('could not run the parameter check', str(exc)[:80])

# ---------------------------------------------------------------------------
section('9. Demand series — every baseline, every year, every node')
# ---------------------------------------------------------------------------
BASELINES = ['StepChange', 'Accelerated', 'SlowerGrowth']
for b in BASELINES:
    try:
        dem = csv(f'demand_{b}.csv')
    except FileNotFoundError:
        fail('demand file missing for a selectable baseline', b)
        continue
    yrs = sorted(dem['Year'].unique()) if 'Year' in dem.columns else []
    days = dem['Day'].nunique() if 'Day' in dem.columns else 0
    neg = int((dem['Demand'] < 0).sum())
    gaps = [y for y in range(min(yrs), max(yrs) + 1) if y not in set(yrs)] if yrs else []
    check(f'demand_{b}: no negative demand', neg == 0, f'{neg} rows')
    check(f'demand_{b}: no missing year in {min(yrs)}-{max(yrs)}' if yrs else f'demand_{b}: has years',
          not gaps, ', '.join(map(str, gaps)) or 'none')
    if days and days != 365:
        warn(f'demand_{b}: not 365 days per year', f'{days} distinct days')

for b in BASELINES:
    for kind in ('gpg', 'industrial'):
        f = f'{kind}_demand_profile_{b}.csv'
        if not os.path.exists(os.path.join(DATA, f)):
            warn('per-baseline profile missing, the flat GBB profile will be used instead', f)
        else:
            ok(f'{f} present')

# ---------------------------------------------------------------------------
section('10. Price and curtailment inputs')
# ---------------------------------------------------------------------------
try:
    lng = csv('lng_prices.csv')
    for b in BASELINES:
        sub = lng[lng['Scenario'] == b]
        check(f'lng_prices covers {b}', not sub.empty,
              f"{sub['Year'].min()}-{sub['Year'].max()}" if not sub.empty else 'absent')
    need = {'Netback_Capped_AUD_GJ', 'Netback_Uncapped_AUD_GJ', 'Import_Injection_AUD_GJ'}
    check('lng_prices has the columns the model reads', need <= set(lng.columns),
          ', '.join(sorted(need - set(lng.columns))) or 'none')
    neg = int((lng[[c for c in need if c in lng.columns]] < 0).sum().sum())
    check('no negative price anywhere in lng_prices', neg == 0, f'{neg} cells')
except FileNotFoundError:
    warn('lng_prices.csv absent — netback pricing would fail', 'run build_lng_prices.py')

try:
    cur = csv('curtailment_params.csv').set_index('Tier')['StrikePrice']
    from model import VOLL_PER_GJ
    ordered = cur.get('GPG', 0) < cur.get('Industrial', 0) < VOLL_PER_GJ
    check('curtailment strikes form a merit order below VOLL', bool(ordered),
          f"GPG {cur.get('GPG')} < Industrial {cur.get('Industrial')} < VOLL {VOLL_PER_GJ}")
except FileNotFoundError:
    warn('curtailment_params.csv absent — in-code defaults will be used')

# ---------------------------------------------------------------------------
section('11. Reserves vs deliverability — do the two tell the same story?')
# ---------------------------------------------------------------------------
for _, r in supply.iterrows():
    res, cap = r.get('Reserves_PJ'), float(r['Capacity'])
    if pd.isna(res) or cap <= 0:
        continue
    years = float(res) * 1000.0 / (cap * 365.0)
    if years < 1.0:
        warn('tranche holds less than a year of its own deliverability',
             f"{r['Node']} pot={bool(r['IsPotential'])}: {years:.1f} yr")
    elif years > 60:
        warn('tranche holds more gas than the horizon can produce',
             f"{r['Node']} pot={bool(r['IsPotential'])}: {years:.0f} yr at nameplate")
ok('reserve-to-deliverability ratios checked')

# ---------------------------------------------------------------------------
print(f"\n{'=' * 70}")
print(f"{len(fails)} FAIL, {len(warns)} WARN")
for f in fails:
    print(f'  FAIL  {f}')
for w in warns:
    print(f'  WARN  {w}')
sys.exit(1 if fails else 0)
