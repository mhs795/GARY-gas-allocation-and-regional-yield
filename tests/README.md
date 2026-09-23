# Checks

Not a pytest suite — plain scripts, run them directly:

```
python tests/check_results_invariants.py  # fast; reads the results cache, no solve
python tests/check_asset_salvage.py       # fast; no solve
python tests/check_salvage_objective.py   # solves the investment MIP twice (~5 min)
python src/build_parameters_workbook.py --check   # every parameter is on the workbook
```

`check_results_invariants.py` is the one to run after every batch. It checks the
properties every cached result has to hold: optimal price LPs, prices within
[0, VOLL], no tranche (2P or 2C) overdrawn, no same-day counterflow on a reversible
corridor in a reservation run (how reserved gas used to reach the trains),
scarcity rents present, the capacity MIP settled to its configured gap, and
results solved against the current inputs and code. Give it a cache path to check
an older copy instead.

`check_asset_salvage.py` covers the properties the terminal asset credit has to
hold whatever the scenario — boundaries, monotonicity, bounds, the r=0
straight-line degeneration, the annuity identity against an independently
computed capital recovery factor — plus the `expansion_options.csv` integrity the
objective term assumes (only pipelines and import terminals carry an
`AssetLife`, no field development does, none is missed).

`check_salvage_objective.py` is the one that proves the term is actually wired
into the capacity objective rather than silently inert. It solves the investment
MIP with the credit on and off and checks the objective moves by exactly what the
formula says on the schedule chosen. **A dispatch-level A/B will not show this**:
`total_cost` is the dispatch objective, which charges `capex_annualisation_rate`
and never sees the capacity layer's salvage at all, so it comes out identical
either way and proves nothing.
