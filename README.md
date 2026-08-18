# GARY — Gas Allocation and Regional Yield Model

# Draft model 

A nodal, least-cost gas market optimisation model for the Australian energy transition (2025–2050), with an interactive scenario explorer dashboard.

## Requirements

The only thing you need to install manually is **Python 3.10 or later**:

- Windows: https://www.python.org/downloads/ — tick **"Add Python to PATH"** during install
- Mac: https://www.python.org/downloads/ or `brew install python`
- Linux: `sudo apt install python3 python3-venv` (Ubuntu/Debian)

All other dependencies (Dash, Plotly, Pyomo, HiGHS, etc.) are installed **automatically** the first time you run the app.

GARY can also run on the **GLPK** solver instead of HiGHS — see [Choosing a solver](#choosing-a-solver).

## Installation & Running

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

The first run will take 2–3 minutes while dependencies install. After that, open your browser to:

**http://127.0.0.1:8050**

> **Note:** The `GAS` terminal command only works on Linux/Mac after adding the project folder to your PATH. Everyone else should use the scripts above.

## Using the Dashboard

> **First run on a fresh clone:** the model's generated data files are not committed
> (only source inputs are). Click **Regenerate All Data** once to rebuild every
> derived data file from source, then **Run All Scenarios** to populate the results
> cache. After that the two-button workflow only needs repeating when source inputs change.

1. Pick a **GSOO Baseline** scenario — **Step Change**, **Accelerated Transition**, or **Slower Growth** — in the sidebar
2. Set **Winter Stress** and **LNG Demand** levels (these layer on top of the chosen baseline)
2. Optionally switch on **Domestic gas reservation** and pick the share of LNG exports to reserve (5/10/20/30%)
3. Click **Run Scenario** to solve one combination (~1–2 min)
4. Click **Run All Scenarios** to pre-calculate **every combination** — all 3 baselines × 3 Winter × 3 LNG = 27 scenarios (~45 min)
5. Click **Regenerate All Data** to rebuild all derived demand data from source (GBB + GSOO, all three baselines)
6. Explore results across 6 tabs: Network Map, Production, Storage, Prices, Expansions, Industrial Use

## Project Structure

| Path | Description |
|---|---|
| `src/dashboard.py` | Dash web app |
| `src/model.py` | Pyomo optimisation model |
| `src/solve.py` | Scenario solver |
| `src/solvers.py` | Solver backend selection (HiGHS / GLPK) |
| `src/results_io.py` | Compressed, column-oriented scenario-results cache |
| `src/regenerate_data.py` | One-button rebuild of all derived data from source |
| `src/build_*.py` | GSOO/GBB demand-build pipeline (see below) |
| `src/data/` | Network nodes, pipelines, supply, demand, contracts |
| `requirements.txt` | Python dependencies |

The demand data is rebuilt from source by `src/regenerate_data.py` (the **Regenerate
All Data** button), which runs the build pipeline in dependency order:
`build_curtailable_demand` → `build_gsoo_scenarios` → `build_gpg_demand_gsoo` →
`build_industrial_demand_gsoo` → `build_demand_gsoo`. The GSOO extract pulls all
three baseline scenarios, and the demand builders emit one set of demand files per
baseline (e.g. `demand_StepChange.csv`, `demand_Accelerated.csv`, `demand_SlowerGrowth.csv`).

## Choosing a solver

The optimisation is written in Pyomo, so the underlying solver is swappable. Two
backends are supported:

| Backend | Pyomo interface | Notes |
|---|---|---|
| `highs` (default) | `appsi_highs` | Multi-threaded, installed via pip (`highspy`). All published GARY results use this. |
| `glpk` | `glpsol` | Single-threaded, pure system package. Slower on the full-horizon dispatch, but no Python extension module needed. |

GLPK is **not** a pip dependency — install the system package:

```
sudo apt install glpk-utils          # Debian / Ubuntu / Pop!_OS
brew install glpk                    # macOS
```

Select a backend with the `--solver` flag or the `GARY_SOLVER` environment variable:

```
./gas                                # HiGHS (default)
./gas --glpk                         # GLPK, dashboard and all
./gas --solver glpk                  # same thing, long form
GARY_SOLVER=glpk ./gas               # via the environment

python src/solve.py --solver glpk    # single scenario, headless
python src/main.py --solver glpk
python src/batch_solve.py --solver glpk
```

Optionally cap each individual solve with `GARY_SOLVER_TIMELIMIT` (seconds), which
is mainly useful for GLPK:

```
GARY_SOLVER_TIMELIMIT=600 ./gas --glpk
```

**Do the two agree?** Yes, to within the MIP gap. On a 2030 single-year dispatch both
solvers reach an identical optimum (objective 7,611,822,687.71) when the gap is
tightened to 0.01%. At the default 0.5% gap they can stop at different incumbents of
near-equal cost, so the *build schedule can differ* where projects are close to a tie
— e.g. the capacity model puts `MSP_Expansion` at 2025 under HiGHS and 2027 under
GLPK, with `SWQP_Expansion` at 2025 in both. Tighten `mip_gap` if you need the two to
line up exactly.

**Speed.** GLPK is slower, but on a full run the difference is modest — the
26-year solve is dominated by the 365-day LP dispatch, where the two are close;
the gap would widen on a harder MILP. Measured on the Step Change baseline,
Medium winter / Medium LNG, full 2025–2050 two-stage solve:

| Stage | HiGHS | GLPK |
|---|---|---|
| Capacity-expansion MILP | 5.1 s | 5.7 s |
| Single-year dispatch (2030) | 10.8 s | 13.5 s |
| Full 26-year run | 302 s | 328 s |

## Domestic gas reservation

An optional policy lever: a share of east-coast LNG export volume is required to be
released to the domestic market instead of liquefied.

    served_LNG[t]  <=  (1 - share) * LNG_demand[t]

Switch it on in the sidebar and pick 5, 10, 20 or 30%. Headless:

```
python src/solve.py --reservation 20
```

The share is applied to the export volume *planned under the scenario*, so it
compounds with the Global LNG Demand lever rather than ignoring it. It is applied
before the representative days are built, so the perfect-foresight capacity layer
and the 365-day dispatch both size against the same post-reservation demand.

It is implemented on the demand side. The LNG trains enter the network as ordinary
demand nodes and the objective is pure cost minimisation with no export revenue
term, so scaling their demand *is* the constraint above, with the shortage variable
still absorbing genuine under-supply on top. Keeping exports out of the objective is
deliberate: it is what the removed WA DomGas reservation got wrong, where an export
revenue term coupled through the reservation constraint and produced negative nodal
prices at Perth.

**Reading the results.** The freed gas is not new domestic demand — domestic demand
is exogenous. The effect is that cheap Surat/Bowen gas, and the pipeline capacity
carrying it, are released to domestic users. On a stressed 2030 (Step Change, High
winter) the average domestic price falls from $20.19 to $17.65/GJ, but the benefit
**saturates at about 20%**: beyond that the southbound corridor is full, with
`SWQP_Rev` at capacity on 63% of days and `VGP` on 45%, so further reserved gas
cannot reach the southern market. Winter shortage and GPG/industrial curtailment are
unchanged at every share — a reservation on its own does not fix the southern
winter, without pipeline capacity to move the gas.

> **System Cost is not comparable across reservation levels.** The objective carries
> no export revenue, so removing export demand always lowers it — the figure is the
> cost of serving what is left, not a welfare measure. The dashboard relabels the KPI
> **System Cost (served gas only)** and adds a **Gas Reserved** card when a
> reservation is active.

## The results cache

Solved scenarios are cached in `src/data/precalculated_results.pkl` so the dashboard
can redraw without re-solving. Each solved year is stored as one DataFrame per
result series (prices, flows, production, storage, curtailment), packed down — node
and pipeline names as categoricals, values as `float32` — and the whole file is
compressed with zstd (gzip if `zstandard` is not installed). The frames stay packed
in memory; `results_io.unpack` widens them if you want plain `object`/`float64`
columns.

This replaced a cache of per-day Python dicts (`{'Day': 1, 'Node': 'Melbourne',
'Price': 7.35}`, ~13.5 million of them across the full 28-scenario batch).
Measured on that cache:

| | Before | After |
|---|---|---|
| File size | 640 MB | 23.4 MB |
| Load time | 46.8 s | 2.3 s |
| Peak memory | ~5 GB | 0.58 GB |

Values are `float32`, so figures differ from the old `float64` cache in the 8th
significant figure (worst relative difference 1e-7 across 158,000 plotted values —
well below display precision). Model solving is unaffected: only stored results are
packed, never the optimisation itself.

`results_io.load` sniffs the file, so caches written in the old format still open.
To convert one without re-solving:

```
python src/migrate_results.py            # rewrites in place, keeps a .bak
```

## Technical Details

- **Optimisation:** Pyomo, with a selectable solver backend — HiGHS (`appsi_highs`, default) or GLPK (`glpsol`)
- **Network:** Nodal pipeline model covering eastern Australia **plus the Northern Territory** — the Amadeus and Beetaloo basins feed Darwin, and the NT links to the east-coast grid via the Northern Gas Pipeline (Tennant Creek → Mt Isa → Ballera → Moomba). Western Australia is a separate, physically isolated gas market and is **not** included.
- **Horizon:** 2025–2050 (annual dispatch, 365 days/year)
- **Solve method:** two-stage full-horizon — a perfect-foresight capacity-expansion layer (NPV over representative days) sets the build schedule, then each year is dispatched at 365-day resolution as a pure LP for nodal prices; a myopic year-by-year mode is also available as a toggle
- **Baselines:** selectable AEMO **2026 GSOO** scenario — **Step Change** (central), **Accelerated Transition**, or **Slower Growth** (demand re-based on the GSOO; daily shapes from GBB actuals)
- **Scenario levers:** Winter stress × LNG demand (9 combinations) layered on the chosen baseline, plus the SA Dunkelflaute event and a domestic gas reservation; the batch runs all 3 baselines × 9 = 27 scenarios

