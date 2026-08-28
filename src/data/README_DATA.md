# `src/data` — what is in here and where it comes from

Files fall into three groups. The split is enforced by `.gitignore`: **source inputs are
committed, generated data is not.** See the header of `src/params.py` for the reasoning.

## 1. Source inputs — committed, edited by hand

| File | What it is |
|---|---|
| `nodes.csv` | The network's nodes: name, type, storage capability |
| `arcs.csv` | Pipelines. `Cost` is a posted tariff for existing arcs and variable haulage for new ones — see the README's *What the `Cost` column in `arcs.csv` is* |
| `supply.csv` | Fields: capacity, cost, decline rate, optional `EndYear` |
| `expansion_options.csv` | Every expansion candidate, one row each, with its source and basis in `Note` |
| `contracts.csv` | LNG foundation contract volumes |
| `demand_profiles.csv` | The **raw** GBB city-gate + APLNG daily trace that the demand builders index forward |
| `lng_parameters.csv` | LNG train split factors and the daily target the trace is scaled to |
| `acil_lng_anchors.csv`, `acil_lng_params.csv`, `acil_segment_weights.csv` | ACIL Allen netback inputs (also mirrored in the parameters workbook) |
| `gary_parameters.xlsx` | Every scalar parameter, scenario lever and price anchor. Read only through `params.py` |
| `pipeline_geometry.json` | Real OpenStreetMap pipeline routes for the map, built by `build_pipeline_geometry.py` |
| `datacentre_demand_*.csv` | Example / working data centre demand series for the linked-series lever |

### Raw Gas Bulletin Board extracts

| File | Read? |
|---|---|
| `GasBBActualFlowStorage.CSV` | **Yes** — the historical trace behind every base profile |
| `GasBBNameplateRatingCurrent.csv` | **Yes** — GPG nameplate for the expansion cap |
| `GasBBMediumTermCapacityOutlookFuture.csv` | No |
| `GasBBShortTermCapacityOutlookFuture.csv` | No |
| `GasBBUncontractedCapacityOutlookFuture.csv` | No |

The last three are kept deliberately: they are legitimate Bulletin Board extracts and the
uncontracted-capacity outlook in particular is the natural source for the reservation
work. Nothing reads them today.

## 2. Reference material — committed, read by people not code

`other data/2026 GSOO/` holds AEMO's published 2026 GSOO workbooks. These are the source
for the NT pipeline capacities and tariffs, Beetaloo production costs, and the demand
benchmark in `TODO.md` item 9. They are consulted by hand, not parsed at runtime.

## 3. Generated — gitignored, rebuilt by `regenerate_data.py`

`demand_*.csv`, `gpg_demand_profile*.csv`, `industrial_demand_profile*.csv`,
`gpg_facilities.csv`, `industrial_facilities_bbg.csv`, `curtailment_params.csv`,
`gpg_capacity.csv`, `gpg_raise_blocks.csv`, `lng_prices.csv`,
`demand_decomposition_validation.csv`, `gsoo/`, `precalculated_results.pkl`.

**Never edit these by hand** — the next regeneration overwrites them. Change the source
input or the builder instead.

> `demand_decomposition_validation.csv` checks the **raw** `demand_profiles.csv` trace
> against GBB city-gate and industrial meters. Since the city-gate calibration was added
> (README, *Domestic demand and the GSOO sectors*) the modelled series is scaled ~1.23x
> above that raw trace, so this file no longer describes what the model solves. It remains
> a useful check on the underlying data.

## Removed

`industrial_facilities.csv`, `abatement_tech.csv` and `emissions_baselines.csv` were
deleted on 28 Aug 2026. The first was superseded by the GBB-derived
`industrial_facilities_bbg.csv` and had gone stale (it still carried Incitec Pivot's
Gibson Island plant, which ceased manufacturing at the end of 2022). The other two, and
the emissions/CCS assumptions table that used to be this file's only content, belonged to
a Safeguard Mechanism feature that was never built — no code has ever referenced them.
All three are recoverable from git history if that work is picked up.
