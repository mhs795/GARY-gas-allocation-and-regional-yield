"""Prove the asset credit is actually in the capacity objective, and worth what it should be.

Solves the investment MIP only (stopping before the 26 dispatch solves) with the
credit on and off, and compares the objective value against the credit computed
independently from the schedule each run chose.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import pyomo.environ as pyo
import capacity_model as cm


class Stop(Exception):
    def __init__(self, obj, sched, r, years):
        self.obj, self.sched, self.r, self.years = obj, sched, r, years


def run(flag):
    cm.ASSET_SALVAGE = flag
    real_solve = cm.CapacityExpansionModel.solve

    def patched(self, *a, **kw):
        st = real_solve(self, *a, **kw)
        raise Stop(float(pyo.value(self.model.obj)),
                   {e: y for e in self.model.Expansion for y in self.years
                    if pyo.value(self.model.build[e, y]) > 0.5},
                   self.r, list(self.years))
    cm.CapacityExpansionModel.solve = patched
    try:
        from solve import solve_scenario
        solve_scenario(baseline='StepChange', winter='Medium', lng='Medium',
                       netback_pricing=True, mip_gap=0.005, log=False)
        raise SystemExit('capacity solve never ran')
    except Stop as s:
        return s
    finally:
        cm.CapacityExpansionModel.solve = real_solve


def credit_of(s, exp):
    last = s.years[-1]
    df = 1.0 / (1.0 + s.r) ** (last - 2025)
    return sum(float(exp.loc[e, 'CapEx'])
               * cm._asset_residual(exp.loc[e, 'AssetLife'], last - y + 1, s.r) * df
               for e, y in s.sched.items() if e in exp.index)


import pandas as pd
exp = pd.read_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               '..', 'src', 'data', 'expansion_options.csv')).set_index('Name')

on, off = run(True), run(False)
gap = off.obj - on.obj
expected = credit_of(on, exp)

print(f"\ncapacity objective  ON  ${on.obj/1e9:14.6f}bn   ({len(on.sched)} builds)")
print(f"capacity objective  OFF ${off.obj/1e9:14.6f}bn   ({len(off.sched)} builds)")
print(f"difference               ${gap/1e6:14.1f}m")
print(f"credit computed from ON's own schedule  ${expected/1e6:.1f}m")

ok = gap > 1e6 and abs(gap - expected) / max(expected, 1) < 0.02
print(f"\n  {'PASS' if gap > 1e6 else 'FAIL'}  the credit moves the objective at all")
print(f"  {'PASS' if abs(gap-expected)/max(expected,1) < 0.02 else 'FAIL'}"
      f"  and by the amount the formula says (within 2%)")
same = on.sched == off.sched
print(f"  INFO  build schedule {'unchanged' if same else 'CHANGED'} by the credit")
if not same:
    for e in sorted(set(on.sched) | set(off.sched)):
        a, b = on.sched.get(e), off.sched.get(e)
        if a != b:
            print(f"         {e:24s} ON {a}   OFF {b}")
sys.exit(0 if ok else 1)
