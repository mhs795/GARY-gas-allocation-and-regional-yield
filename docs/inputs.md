# Inputs and data

[← back to README](../README.md)

Where every number in GARY comes from, and how the derived data is built.

- [The three groups](#the-three-groups)
- [The parameters workbook](#the-parameters-workbook)
- [The network files](#the-network-files)
- [The demand build pipeline](#the-demand-build-pipeline)
- [Domestic demand and the GSOO sectors](#domestic-demand-and-the-gsoo-sectors)

A file-by-file account of `src/data/` — including which raw extracts are actually read and
what has been deleted and why — is in
[`src/data/README_DATA.md`](../src/data/README_DATA.md).

---

## The three groups

GARY's inputs are split by **kind**, not lumped into one file:

| Group | Where | What it is |
|---|---|---|
| **Parameters** | `src/data/gary_parameters.xlsx` | scalars and short lists an analyst tunes — VOLL, strikes, discount rate, scenario levers, ACIL Allen price anchors |
| **Structure** | committed CSVs in `src/data/` | the network itself — `nodes.csv`, `arcs.csv`, `supply.csv`, `expansion_options.csv`, `demand_profiles.csv` — plus the raw `GasBB*.CSV` and GSOO workbooks the generators read |
| **Derived** | generated CSVs in `src/data/` | everything `regenerate_data.py` writes (`demand_*.csv`, `curtailment_params.csv`, `lng_prices.csv`). Gitignored; **never hand-edit** |

The split is deliberate. A parameter is a *value*, and one place to set it beats hunting
through modules. A node, an arc or an expansion candidate is a *row* — with a name, a
capacity, a cost and a source citation — and rows diff, review and cite far better as plain
text than as spreadsheet cells. So the workbook is named for what it actually holds:
parameters, not "all inputs".

The split is enforced by `.gitignore`: **source inputs are committed, generated data is
not.**

## The parameters workbook

**Every model parameter lives on a sheet in `src/data/gary_parameters.xlsx`**, not as a
constant in a module, so a parameter can be changed, reviewed and diffed in one place
without touching code. It is a **committed source input** — it is not generated, and
`regenerate_data.py` never rewrites it.

| Sheet | Holds |
|---|---|
| `Parameters` | scalars and short lists: VOLL, curtailment strikes, the winter window, the SA dunkelflaute event, reservation levels, network roles, data centre nodes and the optional linked series path, capacity-model settings, and every ACIL Allen pricing assumption |
| `Scenario_Levers` | the winter multipliers, the LNG volume multipliers used when netback pricing is off, and the `LNG_Netback` price-path mapping used when it is on |
| `LNG_Anchors` | ACIL Allen's per-scenario Brent / LNG price / spot share anchors |
| `Segment_Weights` | ACIL Allen's contract/spot weights per customer segment |

`src/params.py` is the only reader. Every lookup carries the old in-code value as a
fallback, so a clone without the workbook still runs — **which also means a mistyped
parameter name silently returns the default rather than raising.** That is what `--check`
is for:

```bash
python src/build_parameters_workbook.py --check   # list parameters the workbook lacks
python src/build_parameters_workbook.py           # create it on a fresh clone
```

The create path **refuses to overwrite an existing workbook** without `--force`;
regenerating it would discard hand edits. The workbook is read once and cached, so edits
take effect **on restart**, not mid-run.

## The network files

| File | What it is | Source |
|---|---|---|
| `nodes.csv` | 23 nodes: name, type, region, storage capability and plant rates | AEMO G26 *Processing Transmission Storage Facilities* |
| `arcs.csv` | 32 directed pipeline arcs: capacity and `Cost` | G26 capacities; posted GSOO reference tariffs. **`Cost` means different things for existing and new arcs** — see [`expansions.md`](expansions.md#what-the-cost-column-in-arcscsv-is) |
| `supply.csv` | 15 supply rows, **one per tranche**: capacity, cost, `Tranche` (2P/2C), `Reserves_PJ`, decline rate, optional `EndYear`. A developed row holds a basin's 2P reserves at its 2P cost; the undeveloped row behind it holds the 2C contingent resource at the 2C cost and produces only once an AEMO field development is built | AEMO G26 *Reserves Costs assumptions* |
| `expansion_options.csv` | 30 expansion candidates, one sourced row each — pipelines, terminals and the field developments that unlock each basin's 2C tranche | Public project announcements + AEMO G26 *Field Developments* — see [`expansions.md`](expansions.md) |
| `demand_profiles.csv` | The **raw** GBB city-gate + APLNG daily trace the demand builders index forward | Gas Bulletin Board actuals |
| `acil_lng_anchors.csv`, `acil_lng_params.csv`, `acil_segment_weights.csv` | ACIL Allen LNG price and segment inputs, mirrored on the workbook | ACIL Allen (14 Nov 2025) for LNG prices; (14 Jul 2023) for segment weights. See the verification table in [`pricing.md`](pricing.md) |
| `pipeline_geometry.json` | Real OpenStreetMap pipeline routes for the map | Built by `build_pipeline_geometry.py` |

Capacities and tariffs for both the east coast and the NT are the 2026 GSOO's. The NT links
to the east-coast grid via the Northern Gas Pipeline (Tennant Creek → Mt Isa → Ballera →
Moomba), whose **southbound capacity is set by the Carpentaria leg at 65 TJ/d**. APA's
proposed NEAP is carried as a candidate that bypasses that corridor.

## The demand build pipeline

The **Regenerate All Data** button runs `src/regenerate_data.py`, which executes the
builders in dependency order:

```
build_curtailable_demand  →  build_gsoo_scenarios  →  build_gpg_demand_gsoo
                                                   →  build_industrial_demand_gsoo
                                                   →  build_demand_gsoo
```

`build_gsoo_scenarios` parses AEMO's published 2026 GSOO workbooks
(`2026-gsoo-report-figures-and-data.xlsx` for annual sector consumption and GPG seasonal
maxima; `2026-gsoo-daily-maximum-demand-summary.xlsx` for regional summer/winter peaks), so
a clone needs those present to regenerate. The GSOO extract pulls **all three baseline
scenarios**, and the demand builders emit one set of demand files per baseline.

`build_lng_prices.py` builds the netback and import-parity series separately — see
[`pricing.md`](pricing.md#the-price-series).

## Domestic demand and the GSOO sectors

GARY's demand is built bottom-up from the Gas Bulletin Board — city-gate deliveries,
registered industrial facilities, GPG stations — and then indexed forward on the GSOO's
sector trajectories. Two things about that need stating, because **getting them wrong is
what made GARY's prices half of ACIL Allen's** until 28 Aug 2026.

### A city node is distribution delivery, not residential load

`demand_decomposition_validation.csv` confirms it per node: Melbourne's node demand is the
DTS delivery, with registered industrial and GPG *additive* on top. So the bucket carries a
large amount of commercial and small-industrial gas that the GSOO counts under
**Industrial**, not ResComm — and the two decline at very different rates. Over 2026→2045
in Step Change, **ResComm falls to 0.21 of its base level while Industrial only falls to
0.76.**

`build_demand_gsoo.py` therefore splits the city-gate bucket and indexes each half on its
own sector. The split is derived, not assumed: whatever the model already meters separately
as industrial is held out, and the remainder of the GSOO's Industrial sector is what must be
embedded in distribution delivery.

### The Bulletin Board does not see everything

The observed city-gate trace is ~693 TJ/d against a GSOO-implied ~852: the GBB does not
register distribution-connected users, regional networks outside the four city nodes, or
Tasmania at all. The builder scales the trace by **~1.23** to close that, which puts the
unobserved load on the nodes GARY does have.

> **This is a real simplification.** Regional load ends up in the capital-city nodes, and
> the scaling breaks the node-level agreement with `demand_decomposition_validation.csv` —
> that file validates the **raw** trace, not the calibrated series.

### The result

Together these land domestic demand within **0.2% of the GSOO in every year**:

| TJ/d | 2026 | 2030 | 2035 | 2040 | 2045 |
|---|---|---|---|---|---|
| GARY domestic | 1,255 | 1,147 | 1,058 | 819 | 751 |
| 2026 GSOO domestic | 1,253 | 1,145 | 1,057 | 817 | 750 |

Before the split, the same figures ran 1,096 / 960 / 830 / 561 / **496** — 66% of the GSOO
by 2045. **A market that short of load never calls on an import cargo, which is why every
node priced at the export netback.** See [`TODO.md`](../TODO.md) item 9.
