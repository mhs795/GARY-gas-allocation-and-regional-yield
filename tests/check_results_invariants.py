"""Check the properties every cached result has to hold, whatever the scenario.

Fast: reads src/data/precalculated_results.pkl and solves nothing. Run it after any
batch; it exits non-zero on a FAIL.

    python tests/check_results_invariants.py

Each check is a defect that was found in a real cache, not a hypothetical:

  solved        every year's price LP reached `optimal` (feasible is not enough
                for duals that get published as prices)
  prices        no negative nodal price and none above VOLL
  reserves      no tranche, 2P or 2C, produces more over the horizon than it
                holds (dispatch did not check 2C reserves until 23 Sep 2026)
  reservation   a reservation run was solved WITH reserved-gas tracking (every
                run carrying a provenance stamp was). Before it, reserved gas
                was laundered to the trains by same-day round trips -- 91.6 PJ
                in the contract-breaking run of 21 Sep 2026 -- so an unstamped
                reservation run FAILS.
  counterflow   same-day flow both ways down a reversible corridor (WARN).
                With tracking it can no longer launder anything; what remains
                is a swap the model pays both tariffs for -- reserved gas south
                to domestic load while commercial gas comes north to a train
                (12 PJ on Bulloo/SWQP in 2029 on the 23 Sep 2026 set).
  rents         a foresight run carries scarcity rents (an empty set used to
                be swallowed silently, and dispatch then ran without them)
  gap           the capacity MIP closed to within its configured tolerance
  provenance    the result was solved against the inputs and model code in the
                working tree (WARN, not FAIL: an old result can be wanted)
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import params as P            # noqa: E402
import results_io             # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src', 'data')
# A cache path on the command line checks that file instead -- e.g. an old copy,
# to see which of these a previous vintage broke.
CACHE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA, 'precalculated_results.pkl')
TOL_PJ = 0.5          # reporting threshold, PJ: get_results drops rows below 0.01 TJ


def reversible_pairs():
    arcs = pd.read_csv(os.path.join(DATA, 'arcs.csv'))
    by_ends = {(r.From, r.To): r.Name for r in arcs.itertuples()}
    return sorted({tuple(sorted((a, by_ends[(t, f)])))
                   for (f, t), a in by_ends.items() if (t, f) in by_ends})


def main():
    fails, warns = [], []
    scen = results_io.load(CACHE)['all_scenarios']
    supply = pd.read_csv(os.path.join(DATA, 'supply.csv'))
    reserves = {(r.Node, bool(r.IsPotential)): r.Reserves_PJ
                for r in supply.itertuples() if r.Reserves_PJ == r.Reserves_PJ}
    voll = P.get('voll_per_gj')
    pairs = reversible_pairs()
    current = results_io.provenance()

    for key, years in scen.items():
        tag = key.replace('Base_', '')
        if not all(y.get('solved') for y in years):
            fails.append(f"{tag}: unsolved years")

        lo = min(float(pd.DataFrame(y['prices'])['Price'].min()) for y in years)
        hi = max(float(pd.DataFrame(y['prices'])['Price'].max()) for y in years)
        if lo < -1e-6 or hi > voll + 1e-6:
            fails.append(f"{tag}: prices outside [0, VOLL]: {lo:.2f}..{hi:.2f}")

        cum = {}
        for y in years:
            p = pd.DataFrame(y['production'])
            p['Potential'] = p['Potential'].astype(str)
            for (n, pot), v in p.groupby(['Node', 'Potential'], observed=True)['Value'].sum().items():
                k = (str(n), pot == 'True')        # 'Reserved' depletes the 2P row
                cum[k] = cum.get(k, 0.0) + v / 1000.0
        for k, r in reserves.items():
            if cum.get(k, 0.0) > r * 1.0005:
                fails.append(f"{tag}: {k} produced {cum[k]:,.0f} PJ against {r:,.0f}")

        reserving = sum(y.get('reserved_offered_tj', 0) for y in years) > 0
        both = 0.0
        for y in years:
            f = pd.DataFrame(y['flow'])
            f['Arc'] = f['Arc'].astype(str)
            pv = f.pivot_table(index='Day', columns='Arc', values='Value', aggfunc='sum').fillna(0)
            for a, b in pairs:
                if a in pv and b in pv:
                    both += float(pv[[a, b]].min(axis=1).sum()) / 1000.0
        if both > TOL_PJ:
            warns.append(f"{tag}: {both:,.1f} PJ of same-day counterflow on reversible corridors")
        if reserving and not any(y.get('run_meta') for y in years):
            fails.append(f"{tag}: reservation run solved before reserved-gas tracking "
                         f"(reserved gas could reach the trains)")

        myopic = '_Myopic' in key
        if not myopic and not any(y.get('scarcity_rent') for y in years):
            fails.append(f"{tag}: foresight run with no scarcity rents")

        meta = next((y.get('run_meta') for y in years if y.get('run_meta')), {}) or {}
        gap = next((y.get('capacity_gap') for y in years if y.get('capacity_gap')), None)
        if gap and gap.get('abs_aud') is not None and meta:
            ok = (gap['abs_aud'] <= meta['mip_abs_gap_aud'] * 1.001
                  or (gap.get('rel') or 1) <= meta['mip_gap'] * 1.001)
            if not ok:
                fails.append(f"{tag}: capacity MIP stopped ${gap['abs_aud']/1e6:,.0f}m "
                             f"from its bound, looser than configured")

        stale = results_io.stale_reason(years, current)
        if stale:
            warns.append(f"{tag}: {stale}")

    for w in warns:
        print(f"WARN  {w}")
    for f_ in fails:
        print(f"FAIL  {f_}")
    print(f"\n{len(scen)} scenarios checked: {len(fails)} failures, {len(warns)} warnings")
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
