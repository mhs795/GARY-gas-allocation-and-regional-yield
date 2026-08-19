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
2. Optionally switch on **Gas reservation** and pick the share of LNG exports to reserve (5/10/20/30%),
   and/or **Price-responsive mass-market demand**
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

## Gas reservation

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

It replaces the old **ADGSM** toggle, which was an unfinished attempt at the same
thing: its constraint (LNG flow ≤ 85% of Surat production) was commented out, so the
switch never affected a solve. That code and the `_ADGSM_<x>` segment it wrote into
every scenario key are gone.

It is implemented on the demand side. The LNG trains enter the network as ordinary
demand nodes and the objective is pure cost minimisation with no export revenue
term, so scaling their demand *is* the constraint above, with the shortage variable
still absorbing genuine under-supply on top. Keeping exports out of the objective is
deliberate: it is what the removed WA DomGas reservation got wrong, where an export
revenue term coupled through the reservation constraint and produced negative nodal
prices at Perth.

**Reading the results — the reserved gas is not produced.** Domestic demand in the
model is exogenous: a fixed volume per node per day, already met before the
reservation applies. The freed gas therefore has no domestic buyer, and the
cost-minimising solution simply leaves it in the ground. On a stressed 2030 (Step
Change, High winter) a 20% reservation cuts total production by 263,018 TJ against
263,883 TJ reserved — essentially all of it — while domestic gas actually consumed
changes by 0.0 TJ.

What is implemented is therefore an **export cap**, not a redirection of gas to
domestic buyers. The domestic price still falls, $20.19 → $17.65/GJ, but through
scarcity rather than volume: less total call on the system means a cheaper marginal
supply source and less congestion, so the nodal duals fall.

One channel could absorb the gas domestically — GPG and large industrial shed load
when the nodal price exceeds their strike ($22 and $120/GJ), and would take it back
if the price fell far enough. It does not fire here. All the shed load sits in
Victoria and SA (Melbourne and Gippsland GPG at ~$53/GJ, Adelaide GPG), the freed gas
is in Queensland, and the southbound corridor is full: at 30% `SWQP_Rev` is at
capacity on 63% of days and `VGP` on 45%. Curtailment and winter shortage are
unchanged at every share.

The benefit therefore **saturates at about 20%**, and the finding is that a
reservation on its own does not fix the southern winter without pipeline capacity to
move the gas.

Price-responsive mass-market demand was the obvious candidate for the missing
channel, so it was built (see below) and tested. **It does not change the answer.**
On the same stressed 2030 with the demand curve switched on, a 20% reservation still
cuts production by 263,018 TJ and still moves domestic gas consumed by 0.0 TJ. The
reason is visible in the flows: all mass-market shedding is at Melbourne, on 58
winter days, at $27-54/GJ — and on those exact node-days the price is *identical*
with and without the reservation ($32.73 mean either way). The relief lands in
Queensland instead (APLNG, Brisbane, GLNG, QCLNG, Gladstone and Surat all -$4.41/GJ)
because `SWQP_Rev` runs at its full 512 TJ/d on all 58 of those days once the
reservation is applied, against 358 TJ/d and only 2 days at capacity without it.

The binding constraint is **transport, not molecules**. A reservation frees gas in
Queensland that physically cannot reach the market willing to pay for it on the days
that matter, so no formulation of domestic demand will make it deliver volume. What
elastic demand changes is *who gives way*: unserved load and industrial curtailment
both go to zero and mass-market shedding takes their place, and average prices fall
(system mean $29.72 -> $25.11). It is a better model of the market, not a fix for the
reservation.

> **System Cost is not comparable across reservation levels.** The objective carries
> no export revenue, so removing export demand always lowers it — the figure is the
> cost of serving what is left, not a welfare measure. The dashboard relabels the KPI
> **System Cost (served gas only)** and adds a **Gas Reserved** card when a
> reservation is active.

## Price-responsive mass-market demand

Mass-market (distribution-level residential and commercial) gas is normally a fixed
must-serve volume. The **Price-responsive mass-market demand** switch replaces it
with a **step demand curve**: the load at each distribution node is split into
blocks, each with a strike price, and a block sheds rather than being supplied once
the nodal price exceeds its strike. LNG trains are excluded — their volume is an
export commitment, not household load.

```bash
python src/solve.py --elastic-demand --winter High
```

The blocks are **derived, not asserted**. `src/build_massmarket_blocks.py` fits a
constant-elasticity curve `Q(P) = Q0 (P/P0)^e` on a log-spaced price grid and writes
the result into `data/curtailment_params.csv`:

| input | value | source |
| --- | --- | --- |
| `P0` reference price | $13.56/GJ | ACCC Gas Inquiry 2017-2030, producer offers for 2026 supply (the east coast contract range has held at $13-15/GJ since 2023) |
| `e` own-price elasticity | -0.180 (short run) | Labandeira, Labeaga & López-Otero (2017), "A meta-analysis on the price elasticity of energy demand", *Energy Policy* 102, 549-568, Table 6 — significant at 1%; long-run counterpart -0.684, 230-estimate sample mean -0.184 |

which gives strikes at 2×, 4× and 8× the reference price:

| block | strike | share of mass-market load |
| --- | --- | --- |
| `MassMarket_B2` | $27.12/GJ | 11.73% |
| `MassMarket_B3` | $54.24/GJ | 10.35% |
| `MassMarket_B4` | $108.48/GJ | 9.14% |
| inelastic core | value of lost load | 68.78% |

The inelastic core is **not** a block. It is the existing `shortage` variable priced
at `VOLL_PER_GJ`, which already means "served unless nothing can reach it, valued at
the value of lost load" — a block at VOLL would just duplicate it. Both solve stages
see the same curve: the capacity layer averages each block's availability over the
real days in a representative-day bucket, so it cannot size the network against
demand the dispatch layer will then shed.

Run this again if the ACCC reference price moves materially:

```bash
python src/build_massmarket_blocks.py
```

**Three caveats worth carrying into any result.**

1. **Extrapolation.** -0.18 is estimated on the modest price variation in the
   historical record, not on prices 8× the contract price. The upper blocks
   extrapolate the fitted curve rather than measuring anything. The inelastic core
   caps how far that extrapolation can run.
2. **Frequency mismatch.** The literature elasticity is monthly or annual; GARY
   applies it to a daily nodal price. Kanellakis et al. find European gas demand
   shows little or no daily price response *through the heating season*, with the
   response concentrated in the shoulder months ("The daily price and income
   elasticity of natural gas demand in Europe", *Energy Reports* 8, 2022). GARY's
   winter peak is exactly where it sheds, so this matters. The `WinterScale` column
   in `curtailment_params.csv` damps each block's response over the winter window
   and **defaults to 1.0 — no adjustment** — so the shipped calibration is exactly
   what the elasticity implies and nothing more. Set it to ~0.3-0.5 to test the
   daily-frequency evidence.
3. **VOLL.** GARY penalises unserved gas at $300/GJ. The National Gas Rules set VoLL
   at **$800/GJ** in the Victorian DWGM and the STTM market price cap at **$400/GJ**
   (AEMO, *Gas Market Parameters Review 2022 — Final Recommendations*, Feb 2023).
   GARY's figure is conservative and is left alone because changing it moves every
   historical result, but it is the number to revisit if VOLL drives a conclusion.

The lever is **off by default**, so the 28 cached scenarios stay valid and the
inelastic case remains the comparison baseline. Scenario keys gain an `_Elastic`
segment, results carry a `massmarket` series, and the dashboard adds a **Demand
Response** KPI in PJ.

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
- **Scenario levers:** Winter stress × LNG demand (9 combinations) layered on the chosen baseline, plus the SA Dunkelflaute event, the gas reservation and price-responsive mass-market demand; the batch runs all 3 baselines × 9 = 27 scenarios

