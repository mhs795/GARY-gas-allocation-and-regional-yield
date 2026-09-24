# GARY — Gas Allocation and Regional Yield Model

A nodal, least-cost optimisation model of the eastern Australian and Northern Territory
gas market, 2025–2050, with an interactive scenario dashboard.

> **Status: draft model.** The formulation, data and calibration are all documented and
> sourced, but several known limitations are open — see [`TODO.md`](TODO.md). In
> particular, read [what a GARY price is](docs/pricing.md#what-a-gary-price-is-and-when-it-is-not-a-wholesale-price)
> before quoting a number as a wholesale gas price.

---

## Contents

| | |
|---|---|
| [What GARY does](#what-gary-does) | the question it answers and how |
| [How the model works](#how-the-model-works) | network, objective, two-stage solve |
| [Quick start](#quick-start) | clone and run |
| [The dashboard](#the-dashboard) | every control and every tab |
| [Key inputs](#key-inputs) | where the numbers come from |
| [Pricing](#pricing) | how a price is formed, in brief |
| [Command line](#command-line) | headless runs |
| [Full documentation](#full-documentation) | the deep pages |

---

## What GARY does

GARY answers one question: **given what the east coast can produce, what it can move,
and what it has to serve, what is the cheapest way to meet demand each day — and what
does the marginal molecule cost at each node?**

From that single answer fall the things the model is actually used for:

- **Prices.** Every node's daily price is the dual of its supply-demand balance — the
  marginal cost of one more TJ there, that day.
- **Adequacy.** Where and when the system cannot serve load, and which tier gives way.
- **Investment.** Which pipelines, terminals and field developments are worth building,
  and in which year.
- **Policy.** What a gas reservation, an export price signal, an import-terminal ban or
  a new data centre load actually does to all of the above.

It covers eastern Australia **plus the Northern Territory** — the Amadeus and Beetaloo
basins feed Darwin, and the NT links to the east coast over the Northern Gas Pipeline.
Western Australia is a physically separate market and is not included.

## How the model works

### The network

23 nodes and 35 pipeline arcs (29 existing, 6 that exist only if built), with 18 supply
rows across 12 nodes (fields, plus the three import terminals):

| Node type | Nodes |
|---|---|
| **Supply** | Surat, Gippsland, Amadeus, Beetaloo, Blacktip, Bass, Narrabri (only if the Narrabri Gas Project and Hunter Gas Pipeline are built) |
| **Demand** | Sydney, Melbourne, Adelaide, Brisbane, Darwin |
| **Hub** | Moomba, Gladstone, Tennant Creek, Daly Waters |
| **LNG export** | APLNG, GLNG, QCLNG (Gladstone trains) |
| **LNG import** | Port Kembla, Geelong |
| **Storage** | Iona and Silver Springs are storage nodes. Moomba, Surat (Roma Underground Storage), Melbourne (Dandenong LNG) and Sydney (Newcastle Gas Storage) also store |

Nodes, arcs, supply and expansion candidates are one CSV row each in `src/data/` — see
[Key inputs](#key-inputs).

### What it minimises

The objective is total system cost over 365 days:

```
minimise    production cost          field opex, stepping to full cost as reserves depletes
          + transport cost           arc tariff × flow
          + storage cost             $0.50/GJ round trip, so inventory cycles only when the spread pays
          + annualised capex         capital recovery factor over each asset's life
          + curtailment penalties    GPG at $22/GJ, large industrial at $120/GJ
          + unserved load            at VOLL, $300/GJ
          − LNG export revenue       contestable export volume valued at the netback
```

Subject to a **balance constraint at every node on every day** — everything arriving
equals everything leaving. That one equation is the heart of the model: **its dual is
the nodal price.** Everything the Prices tab shows is read off it.

Minimising cost net of export value is equivalent to maximising producer plus consumer
surplus, which is the objective ACIL Allen's GasMark solves — GARY is deliberately the
same class of model. See [`docs/model.md`](docs/model.md) for the full formulation.

> **Before quoting anything GARY says about the 2040s, read
> [`docs/depletion.md`](docs/depletion.md).** How the model values gas left in the ground
> is the single assumption every price and export volume rests on, and the late-horizon
> years are partly an artefact of where the model stops. The 2030s are sound; the 2040s
> need the caveat. That page is the plain-language version — what GARY does now, the three
> things still wrong with it, what you can trust anyway, and what fixing it would cost.

### The two-stage solve

```
Stage 1 — capacity expansion (MILP, perfect foresight)
    All 26 years at once, on representative days, discounted to NPV.
    Chooses which expansion candidates get built, and when.
        ↓  build schedule fixed
Stage 2 — dispatch (LP, one solve per year)
    Each year at full 365-day resolution, builds held fixed.
    Pure LP, so the duals are clean → nodal prices.
```

Splitting it this way is what makes the prices usable: an investment decision is a
binary variable, and a MILP has no meaningful duals. Fixing the build schedule first
leaves a pure LP whose duals *are* marginal costs.

A **myopic** mode (year-by-year investment, no foresight) is available as a toggle for
comparison.

## Quick start

The only thing to install by hand is **Python 3.10+**. Everything else — Dash, Plotly,
Pyomo, HiGHS — installs automatically on first run.

**Windows**
```
git clone https://github.com/mhs795/GARY-gas-allocation-and-regional-yield.git
cd GARY-gas-allocation-and-regional-yield
run_dashboard.bat
```

**Mac / Linux**
```
git clone https://github.com/mhs795/GARY-gas-allocation-and-regional-yield.git
cd GARY-gas-allocation-and-regional-yield
./run_dashboard.sh
```

First run takes 2–3 minutes while dependencies install, then open
**http://127.0.0.1:8050**.

**On a fresh clone, do this once:** generated data files are not committed, only source
inputs. Click **Regenerate All Data**, then **Run Scenarios**. After that the two only
need repeating when a source input changes.

## The dashboard

### Actions

| Button | What it does |
|---|---|
| **▶ Run Scenario** | Solve the one combination currently set in the sidebar. Runs in-process with per-year progress. |
| **⚡ Run Scenarios** | Pre-solve **every** combination: 3 baselines × 3 winter × 3 LNG = 27, plus the SA dunkelflaute case. |
| **⛽ Run Reservation Scenarios** | Sweep 5 reservation levels (0/5/10/20/30%) × 3 baselines = 15 runs, at the winter and LNG levels currently set. Ignores the baseline dropdown and reservation toggle; honours every other setting. Each baseline gets its own 0% run, because a reservation is only readable against the same case without one. |
| **↺ Regenerate All Data** | Rebuild every derived demand file from source (GBB + GSOO, all three baselines). |
| **✕ Clear Results** | Empty the cache and force a full rebuild. |

Both sweeps skip scenarios already cached and save each result as it finishes, so an
interrupted sweep keeps everything that completed. They run several scenarios at once in
worker processes — see [`docs/running.md`](docs/running.md#parallel-sweeps).

### Baseline

| Control | Options | Effect |
|---|---|---|
| **GSOO Baseline Scenario** | Step Change · Accelerated Transition · Slower Growth | Picks which AEMO **2026 GSOO** demand trajectory the run is built on. Step Change is central. |

### Scenario levers

| Control | Options | Effect |
|---|---|---|
| **Southern Winter** | Low 0.91× · **Medium 1.0×** · High 1.5× | Multiplies Melbourne / Adelaide / Sydney distribution demand over the winter window. **Medium is the central GSOO case** and the default — AEMO's series as published, weather-averaged. **Low is an unseasonably warm winter**, measured as the warmest of seven Bulletin Board winters (2019–2025) after removing the 2.6%/yr structural decline. **High is a deliberate stress beyond observed weather** — the coldest winter on record is only 1.08×. See [`scenarios.md`](docs/scenarios.md#southern-winter-stress). |
| **Global LNG Market** | Low · **Medium** · High | With netback pricing **on** (default) it selects the netback *price path*: Low = Accelerated Transition, Medium = this run's own baseline, High = Slower Growth. With netback pricing **off** it scales export *volume* instead. |
| **SA Dunkelflaute (2027)** | off / on | A wind-and-solar drought in South Australia in 2027, forcing GPG to cover. |
| **Gas reservation** | off / on + 5/10/20/30% | Carves a share of planned LNG export volume into the domestic market at $0/GJ. See [`docs/scenarios.md`](docs/scenarios.md#gas-reservation). |
| **Respect LNG foundation contracts** | **on** / off | *(appears with the reservation)* On: the reservation may only take **uncontracted** export gas, so it is capped at **7%** however high the slider goes — how the Heads of Agreement actually works. Off: it breaks take-or-pay SPAs and takes its share of everything. |
| **Data centre gas demand (PJ/yr)** | NSW · VIC boxes | Adds firm hyperscale load to large-industrial demand at Sydney and Melbourne, spread across the year on each node's own GPG shape. |
| **Data centre demand starts** | 2025–2050 | The year the flat volumes switch on. |
| **Or link a demand series** | file path | A `.csv`/`.xlsx` with `Year` and `NSW`/`VIC` columns in PJ/yr, for a build-out with a shape rather than one flat volume. Overrides the boxes for the states it covers. See [`docs/scenarios.md`](docs/scenarios.md#linking-a-demand-series). |
| **Allow LNG import terminals** | **on** / off | Off: Port Kembla, both Geelong FSRUs and Outer Harbor are dropped from the candidate set, so the east coast must be supplied from domestic fields and pipe. Field developments such as Golden Beach are unaffected. |
| **GSOO expansions only** | off / **on** | On: only candidates AEMO **names** in its 2026 GSOO material — the G26 *Field Developments* sheet for supply, the GSOO/VGPR project set for pipelines — whatever status AEMO gives them. Off (the default): that menu plus everything GARY has researched from public announcements or built itself. `Source` records where a candidate came from, not whether it is committed. |
| **LNG netback pricing (ACIL Allen)** | **on** / off | On: trains bid for gas at the export netback and imports are priced at ACIL Allen's injection cost, so the international price disciplines domestic prices. Off: exports revert to must-serve demand at any price. See [Pricing](#pricing). |
| **Perfect-foresight capacity build** | **on** / off | Off switches to myopic year-by-year investment. |
| **Discount Rate** | 0–12%, default **7%** | Discount rate on the capacity-expansion NPV. |
| **Optimality Gap** | 0–1%, default **0.01%** | Relative MIP gap for the capacity layer. A $10m absolute gap (`mip_abs_gap_aud`) also applies, and HiGHS stops at whichever is met first. The relative gap is measured against the whole-system NPV (~$94bn), so anything looser than about 0.05% can leave build decisions unsettled. The gap each run actually achieved is recorded with its result. |

Every **scenario setting** is written into the **scenario key**, so runs with different
settings sit alongside each other in the cache instead of overwriting. The key does not
carry the inputs or the model code. Instead, every result is stamped with a fingerprint
of both (plus the solver, the MIP gap and the gap actually achieved). A result whose
fingerprint no longer matches the working tree shows a **⚠ Stale result** card until it
is re-solved.

### Results

| Control | Effect |
|---|---|
| **Active Scenario** | Which cached result the tabs are drawing. |
| **Analysis Horizon** | Truncate the charts at a year short of 2050. |

### KPI cards

**Final Price**, **System Cost**, **Total Supply** and **New Projects** always show.
Context-sensitive cards appear alongside them: **Gas Reserved** (with the share actually
taken up), **LNG Exported**, **LNG Netback** and **Data Centre Load**.

> **System Cost is not comparable across reservation levels**, and how badly depends on
> the netback switch. The card carries the applicable caveat under the number — the
> reasoning is in [`docs/scenarios.md`](docs/scenarios.md#why-system-cost-is-not-comparable).

### Tabs

| Tab | Shows |
|---|---|
| **Network Map** | Geographic map on real OpenStreetMap pipeline routes, for a chosen year, with optional node labels and capacity shading. Its own KPI strip sits above it. |
| **Production & Dispatch** | Annual production by source, daily dispatch, arc flows, and any shortage. |
| **Storage Dynamics** | Inventory trajectories at every store (Iona, Silver Springs, Moomba, Roma, Dandenong LNG, Newcastle), plus injection/withdrawal activity. |
| **Price Outcomes** | Daily and annual nodal prices, highest and lowest nodes, quarterly aggregations, and the ACIL Allen **customer-segment prices** at the bottom. |
| **Supply Curves** | A grid — a row per demand node, a column every five years — of the delivered supply curve each node faced, with that year's demand and GARY's own nodal price drawn on it. See below. |
| **Expansions** | The build schedule the capacity layer chose: which candidate, which year, what it cost. |
| **GPG & Large Users** | Gas-powered generation and large-industrial consumption and curtailment. |

Every chart has a **⬇ Data (Excel)** button beneath it that exports exactly what is
plotted.

#### Supply Curves

GARY never builds a supply curve — it solves an LP and reports the dual — so this
tab **reconstructs** one from each solved year (`src/supply_curve.py`). Every
tranche of gas that was available that year is stacked cheapest-first at its
**delivered** cost: field cost, plus the year's scarcity rent, plus the tariff on
the cheapest route to the node that still had capacity on it. A basin therefore
reappears further up its own curve once its cheap corridor fills, which is what
makes a pipeline limit visible as a step rather than as a missing source.

* **Colour is the basin**, in a fixed order, so a hue means the same gas in every
  panel. **Texture is the tranche**: solid for developed (2P) gas, hatched for
  the undeveloped (2C) tranche or an import terminal (both need a project built
  in front of them before they deliver anything), dotted for a **domestic
  reservation's carve-out**. The carve-out is not a supply row at all — the
  model states it as its own $0/GJ variable withheld from the export stream — so
  it is folded in separately and deducted from the commercial rows at the same
  field, because it is the same gas rather than extra gas. Note it still lands
  on a demand node's curve at the **tariff of the route it took** (~$0.70/GJ at
  Brisbane, ~$1.65 at Sydney) and not at $0: free gas still costs what the pipe
  charges. In the residual view it often does not appear at all, because it is
  the cheapest gas in the system and the other demand centres take it first —
  switch to Gross to see the whole carve-out on every panel.
* The **dotted vertical line** is the node's demand that year (mass market + GPG
  + industrial, as posted rather than as served). The **dashed horizontal line**
  is the price GARY reported on a **typical (median) day** at the node, sitting
  in a band spanning its 10th–90th percentile daily price. Where the line meets
  the curve is the check that the reconstruction is reading the same model — it
  does so to the cent in 14 of the 30 panels, and a median $0.19/GJ from it.
  Where an inbound corridor ran at its limit, the panel says so in its bottom
  corner (`MAPS full 365 d · 131 TJ/d transit`), which is usually the whole
  explanation for a price line floating above the steps: **Adelaide** is the
  clean case, its price being Moomba's plus the $0.97 MAPS tariff plus a
  $1.32–1.43/GJ congestion rent, every year from 2030 on, while over half of
  what the MAPS delivers there leaves again down the reversed SEA Gas for
  Melbourne. A supply block carries a field cost and a tariff; it cannot carry a
  rent, and no rearrangement of the stack makes it.
  The median rather than the annual mean because a panel is a typical-day
  construction (capacity and demand are both annual-average flat rates) and a
  mean of 365 daily duals is pulled up by the winter days; comparing it to the
  mean mismatches the time dimension, not the supply, and costs about $0.08/GJ
  of fit. Where the **band rides above the curve**, those are days an annual
  average cannot hold: a winter peak, or a congestion rent on a full corridor
  that no supply block carries.
* **Residual** (the default) strips the gas the LNG trains and the other demand
  centres took, cheapest-first, so the curve is the one this node's demand
  actually faced. **Gross** leaves it in and answers the different question of
  what gas existed and what it would have cost to bring here — its price line
  sits above its curve, and the gap is the export opportunity cost that netback
  pricing puts on Queensland gas.

Two other structures were built, measured and rejected — a **dispatch stack**
off realised production (median gap $0.57/GJ, because a stack sized exactly to
demand leaves the node the dearest gas once other buyers are stripped) and
**network-aware stripping** that spends pipeline capacity serving the other
buyers (better in the tail, no better in the middle, unstable at day
resolution). Both are written up in `src/supply_curve.py` so they are not tried
again. The lesson: the remaining gap is not in how the supply side is stacked —
matching a dual exactly would mean re-solving the system.

Both views are annual averages, so neither shows a winter peak, and each panel
treats its node as the only buyer of what is left — in the LP, Sydney and
Melbourne compete for the same corridor. The Excel export gives the whole grid
block by block, with the route, the field cost and the tariff split out.

## Key inputs

Inputs are split by **kind**, not lumped into one file:

| Group | Where | What it is | Committed? |
|---|---|---|---|
| **Parameters** | `src/data/gary_parameters.xlsx` | Scalars and short lists an analyst tunes — VOLL, curtailment strikes, the winter window, discount rate, scenario levers, ACIL Allen price anchors | Yes, source |
| **Structure** | CSVs in `src/data/` | The network itself — `nodes.csv`, `arcs.csv`, `supply.csv`, `expansion_options.csv`, `demand_profiles.csv` — plus raw `GasBB*.CSV` and GSOO workbooks | Yes, source |
| **Derived** | generated CSVs in `src/data/` | Everything `regenerate_data.py` writes: `demand_*.csv`, `curtailment_params.csv`, `lng_prices.csv` | **No** — gitignored, never hand-edit |

The split is deliberate. A parameter is a *value*, and one place to set it beats hunting
through modules. A node, an arc or an expansion candidate is a *row* — with a name, a
capacity, a cost and a source citation — and rows diff, review and cite far better as
plain text than as spreadsheet cells.

**Every model parameter lives on a sheet in `gary_parameters.xlsx`**, never as a constant
in a module. `src/params.py` is the only reader.

Where the substance comes from:

| Input | Source |
|---|---|
| Demand trajectories | AEMO **2026 GSOO**, all three scenarios, indexed onto Gas Bulletin Board daily shapes |
| Field capacity, cost, reserves | AEMO G26 *Reserves Costs assumptions* (2P and 2C) |
| Pipeline capacity and tariffs | AEMO G26 *Processing Transmission Storage Facilities*; posted GSOO reference tariffs |
| Storage capacity | G26, same workbook |
| LNG prices, import parity, segment weights | **ACIL Allen**: *Wholesale natural gas prices for AEMO* (14 Nov 2025, the report behind the 2026 GSOO) and *Natural gas price forecasts for the Final 2023 IASR* (14 Jul 2023). Both are public on AEMO's site; every value is checked in [`docs/pricing.md`](docs/pricing.md). The export netback is GARY's own, derived from ACIL's LNG price |
| Expansion candidates | Public project announcements, one sourced row each; every non-public figure labelled as GARY's own |

Full detail — including the demand build pipeline and the GSOO sector split — is in
[`docs/inputs.md`](docs/inputs.md) and [`src/data/README_DATA.md`](src/data/README_DATA.md).

## Pricing

There are three price concepts in GARY, and confusing them is the single easiest way to
misread a result.

**1. The nodal price** is the dual of the balance constraint — the marginal cost of one
more TJ at that node that day. It is what the Prices tab plots. It is a **system
marginal cost, not a wholesale price**: before roughly 2032 the marginal field is
carrying AEMO's 2P cost, which is largely opex, so GARY's early-year prices are about
half a contract price *for a structural reason*. Do not quote a GARY 2026 number as a
wholesale gas price. The full explanation, with the year-by-year comparison against ACIL
Allen, is in [`docs/pricing.md`](docs/pricing.md#what-a-gary-price-is-and-when-it-is-not-a-wholesale-price).

**2. The LNG netback** is what an export train can pay for a TJ, and it is what connects
the domestic market to the world price. Built following ACIL Allen's published
methodology:

```
Brent  →  oil-linked contract LNG price  ┐
                                          ├→  Asian LNG price  ─┬─ − liquefaction & shipping →  export netback  → capped at $12/GJ
          spot share (implied spot price)┘                      └─ + shipping + regas       →  import injection price
```

With netback pricing on (the default), the trains stop being must-serve demand and start
**bidding**. Most export volume — 93% — is locked into take-or-pay foundation contracts
and goes whatever the price. The remaining ~7% spot tail bids at the netback, so a
domestic buyer who values the gas more can outbid an export cargo: the gas stays home and
the cargo does not sail. That gives domestic prices a **ceiling**, and gives imports a
**real, time-varying price** instead of a flat $14/GJ.

**3. Customer-segment prices** are a post-processing layer over a solve that already
happened, reproducing ACIL Allen's contract/spot blend per segment (residential 100%
contract, industrial 90/10, CCGT 80/20, OCGT 20/80 plus a premium). They appear at the
bottom of the Prices tab. They are **ACIL Allen's mechanical layer only** — the market
power and vertical integration overlay ACIL Allen apply on top ("Step 2", 14 Jul 2023 §2.4) is not reproduced, so
GARY's segment prices sit *below* their published forecasts wherever that overlay adds.

All three, with sources and caveats, are in [`docs/pricing.md`](docs/pricing.md).

## Command line

The dashboard is a front end; every scenario can be solved headless.

```bash
python src/solve.py --baseline StepChange --winter High --lng Medium
python src/solve.py --reservation 20 --break-lng-contracts
python src/solve.py --netback-pricing --no-import-terminals
python src/solve.py --dc-nsw 50 --dc-vic 30 --dc-start 2030
python src/solve.py --dc-file ~/work/pipeline.xlsx#Sydney
python src/solve.py --gsoo-expansions-only --myopic --mip-gap 0.001
```

Full flag reference, solver selection (HiGHS / GLPK), parallel sweeps and the results
cache: [`docs/running.md`](docs/running.md).

## Full documentation

**[`docs/README.md`](docs/README.md) is the index** — it says which page to read and in
what order. The short version:

| Page | What is in it |
|---|---|
| [`docs/the-2040s.md`](docs/the-2040s.md) | **Read before quoting anything after ~2040.** Why exports stop, what the GSOO does differently, and which of the two to believe about what |
| [`docs/model.md`](docs/model.md) | The formulation — sets, variables, objective term by term, constraints, the two-stage solve, the curtailment ladder, and where a dual is meaningless |
| [`docs/pricing.md`](docs/pricing.md) | LNG netback formation, the Gas Market Code cap, foundation contracts, what a GARY price is and is not, and customer-segment prices |
| [`docs/inputs.md`](docs/inputs.md) | The three input groups, the parameters workbook, the demand build pipeline, and the GSOO sector split that fixed GARY's price level |
| [`docs/expansions.md`](docs/expansions.md) | The candidate menu — every project, its source and its basis — GSOO vs market scan, the arc cost convention, and the import-terminal switch |
| [`docs/scenarios.md`](docs/scenarios.md) | Every lever in detail, with measured results: winter, LNG market, reservation, dunkelflaute, data centre load |
| [`docs/running.md`](docs/running.md) | CLI reference, solver backends, parallel sweeps, the results cache, project layout |
| [`docs/scarcity-rent.md`](docs/scarcity-rent.md) | What the surcharge on scarce gas is, why it has to exist, and how close GARY's is to what theory says |
| [`docs/depletion.md`](docs/depletion.md) | What is still wrong with depletion, what you can trust anyway, and what fixing it would cost |
| [`TODO.md`](TODO.md) | **The single list of outstanding work** — open items only, numbered with permanent IDs |
| [`src/data/README_DATA.md`](src/data/README_DATA.md) | File-by-file account of `src/data/` |
