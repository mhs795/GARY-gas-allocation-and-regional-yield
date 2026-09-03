# Checks

Not a pytest suite — plain scripts, run them directly:

```
python tests/check_asset_salvage.py     # fast; no solve
python tests/check_salvage_objective.py # solves the investment MIP twice (~5 min)
```

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
