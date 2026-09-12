"""Properties the route-capacity terminal value has to hold, whatever the scenario.

Fast -- no solve. Covers `_least_cost_paths(with_capacity=True)` and
`_realisable_backstop`: that the bottleneck really is a bottleneck, that only the
basins feeding the trains are re-struck, that the blend sits inside the route
prices it is blended from, and that it degenerates to the old
`_backstop_at_wellhead` exactly when it should.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import pandas as pd
import capacity_model as cm
import solve as S

FAIL = []


def check(name, cond, detail=''):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  -- ' + detail if detail and not cond else ''}")
    if not cond:
        FAIL.append(name)


data = S.load_data('StepChange')
arcs, supply = data['arcs'], data['supply']
prices = pd.read_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  '..', 'src', 'data', 'lng_prices.csv'))
row = prices[(prices.Scenario == 'StepChange') & (prices.Year == prices.Year.max())].iloc[0]
IMPORT, NETBACK = float(row.Import_Injection_AUD_GJ), float(row.Netback_Capped_AUD_GJ)
LAST = int(row.Year)
SRC = ['Surat']

deliv = {}
for _, r in supply.iterrows():
    deliv[r['Node']] = deliv.get(r['Node'], 0.0) + cm._declined_capacity(r, LAST, 0.0)

print("\n1. least-cost paths carry the bottleneck of the path they cost")
sp_only = cm._least_cost_paths(arcs)
sp, caps = cm._least_cost_paths(arcs, with_capacity=True)
check("with_capacity does not change the distances", sp == sp_only)
check("self-pairs are free and unconstrained",
      all(sp[(n, n)] == 0.0 and caps[(n, n)] == float('inf')
          for n in {a for a, _ in sp}))
widest = float(arcs[arcs.Capacity > 0].Capacity.max())
check("no bottleneck exceeds the widest arc in the network",
      all(c <= widest for (a, b), c in caps.items() if a != b),
      f"widest arc {widest}")
# A one-hop path's bottleneck IS that arc's capacity.
one_hop = [(a['From'], a['To'], float(a['Capacity'])) for _, a in arcs.iterrows()
           if float(a['Capacity']) > 0
           and abs(sp[(a['From'], a['To'])] - float(a['Cost'])) < 1e-9]
check("a one-hop least-cost path's bottleneck is that arc",
      all(abs(caps[(f, t)] - c) < 1e-9 for f, t, c in one_hop),
      f"{len(one_hop)} single-hop pairs")

print("\n2. only the basins that feed the trains are re-struck")
old = cm._backstop_at_wellhead(None, arcs, IMPORT)
new = cm._realisable_backstop(arcs, IMPORT, NETBACK, deliv, lng_source=SRC)
moved = {n for n in old if abs(old[n] - new.get(n, old[n])) > 1e-9}
check("every node outside lng_source is byte-identical to the old rule",
      moved <= set(SRC), f"moved: {sorted(moved)}")
check("the LNG source IS re-struck", set(SRC) <= moved | {n for n in SRC if n not in old})

print("\n3. the blend degenerates to the old rule where it should")
check("no netback (netback pricing off) -> old rule exactly",
      cm._realisable_backstop(arcs, IMPORT, None, deliv, lng_source=SRC) == old)
check("no lng_source -> old rule exactly",
      cm._realisable_backstop(arcs, IMPORT, NETBACK, deliv, lng_source=[]) == old)
check("no backstop price -> old rule exactly (empty)",
      cm._realisable_backstop(arcs, 0.0, NETBACK, deliv, lng_source=SRC) == {})

print("\n4. the blend sits inside the routes it blends")
for n in SRC:
    routes = []
    for i in cm.IMPORT_NODES:
        if (n, i) in sp and n != i:
            routes.append(IMPORT - sp[(n, i)])
    for t in cm.LNG_NODES:
        if (n, t) in sp and n != t:
            routes.append(NETBACK - sp[(n, t)])
    routes = [p for p in routes if p > 0]
    check(f"{n}: min route <= blend <= max route",
          min(routes) - 1e-9 <= new[n] <= max(routes) + 1e-9,
          f"routes {min(routes):.2f}-{max(routes):.2f}, blend {new[n]:.2f}")
    check(f"{n}: blend is BELOW the old import-only value",
          new[n] < old[n], f"{new[n]:.2f} vs {old[n]:.2f}")

print("\n5. the blend responds to route capacity the way it must")
narrow = cm._realisable_backstop(arcs, IMPORT, NETBACK, {**deliv, 'Surat': 1.0},
                                 lng_source=SRC)
check("a basin that can only deliver a trickle gets its DEAREST route",
      abs(narrow['Surat'] - max(
          [IMPORT - sp[('Surat', i)] for i in cm.IMPORT_NODES if ('Surat', i) in sp]
          + [NETBACK - sp[('Surat', t)] for t in cm.LNG_NODES if ('Surat', t) in sp])) < 1e-6,
      f"{narrow['Surat']:.2f}")
huge = cm._realisable_backstop(arcs, IMPORT, NETBACK, {**deliv, 'Surat': 1e9},
                               lng_source=SRC)
check("more deliverability than routes can carry never RAISES the blend",
      huge['Surat'] <= new['Surat'] + 1e-9, f"{huge['Surat']:.2f} vs {new['Surat']:.2f}")
check("blend falls monotonically as deliverability grows",
      narrow['Surat'] >= new['Surat'] >= huge['Surat'] - 1e-9)

print("\n6. the consequence the change was made for")
srow = supply[(supply.Node == 'Surat') & (~supply.IsPotential)].iloc[0]
crow = supply[(supply.Node == 'Surat') & (supply.IsPotential)].iloc[0]
for label, r in (('2P', srow), ('2C', crow)):
    tau = cm._reserve_to_production_years(r)
    o = cm._salvage_rate(r, old['Surat'], 0.07, tau)
    nw = cm._salvage_rate(r, new['Surat'], 0.07, tau)
    print(f"     Surat {label}: cost ${r['Cost']:.2f}  salvage ${o:.2f} -> ${nw:.2f}")
check("the 2C backfill tranche costs more to lift than its gas can be sold for, "
      "so its terminal credit is zero",
      cm._salvage_rate(crow, new['Surat'], 0.07,
                       cm._reserve_to_production_years(crow)) == 0.0)
check("the 2P tranche keeps a positive but smaller credit",
      0 < cm._salvage_rate(srow, new['Surat'], 0.07,
                           cm._reserve_to_production_years(srow))
      < cm._salvage_rate(srow, old['Surat'], 0.07,
                         cm._reserve_to_production_years(srow)))

print(f"\n{'ALL PASS' if not FAIL else str(len(FAIL)) + ' FAILED: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
