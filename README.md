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
> derived data file from source, then **Run Scenarios** to populate the results
> cache. After that the two-button workflow only needs repeating when source inputs change.

1. Pick a **GSOO Baseline** scenario — **Step Change**, **Accelerated Transition**, or **Slower Growth** — in the sidebar
2. Set **Winter Stress** and **LNG Demand** levels (these layer on top of the chosen baseline)
2. Optionally switch on **Gas reservation** and pick the share of LNG exports to reserve (5/10/20/30%),
   and/or **Price-responsive demand**
3. Click **Run Scenario** to solve one combination
4. Click **Run Scenarios** to pre-calculate **every combination** — all 3 baselines × 3 Winter × 3 LNG = 27 scenarios, plus the SA dunkelflaute case
5. Click **Run Reservation Scenarios** to sweep **every reservation level × every GSOO baseline** — 5 levels (0/5/10/20/30%) × 3 baselines = 15 runs — at the Winter and LNG levels currently selected. This button sweeps the baseline dropdown and the reservation toggle itself, so both are ignored while it runs; every other sidebar setting is honoured. Each baseline gets its own 0% run, because a reservation is only readable against the same case without one
6. Click **Regenerate All Data** to rebuild all derived demand data from source (GBB + GSOO, all three baselines)
7. Explore results across 6 tabs: Network Map, Production, Storage, Prices, Expansions, Industrial Use

Both sweeps skip scenarios already in the cache and save each one as it finishes, so
an interrupted sweep keeps everything that completed. **Clear Results** forces a
full rebuild.

### How long a sweep takes

Sweeps run several scenarios at once, one process each — see
[Parallel sweeps](#parallel-sweeps).

## Project Structure

| Path | Description |
|---|---|
| `src/dashboard.py` | Dash web app |
| `src/model.py` | Pyomo optimisation model |
| `src/solve.py` | Scenario solver |
| `src/solvers.py` | Solver backend selection (HiGHS / GLPK) |
| `src/sweep.py` | Runs independent scenarios across worker processes |
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

## Parallel sweeps

A sweep is a set of scenarios that share nothing — each loads its own data, solves
its own capacity model and dispatches its own 26 years — so the scenario is the unit
of parallelism. `src/sweep.py` runs them in worker processes, one solve per worker,
and the dashboard's **Run Scenarios** and **Run Reservation Scenarios** buttons both
go through it. A single **Run Scenario** is unaffected: it stays in-process, with its
per-year progress and terminal log.

Workers default to **half the logical CPUs** (the physical core count on a normal
machine). Override it:

```
GARY_WORKERS=6 ./gas
```

Threads inside the solver are deliberately *not* the lever, and workers pin
`GARY_SOLVER_THREADS=1`. HiGHS's simplex is serial in practice, and the duals GARY
needs for nodal prices come from simplex, so handing one dispatch solve four threads
measures no faster than handing it one — on a 2030 dispatch LP, 9.9 s at one thread
against 14.5 s at four. Only the capacity MIP parallelises at all (26.5 s → 21.6 s on
two physical cores), and it is under 10% of a scenario. Several single-threaded
solves at once is the win; oversubscribing the box makes both slower.

Where a scenario's time actually goes, measured on a 2-core i5-5257U:

| stage | time |
|---|---|
| build 26 year-models + representative days + capacity MIP | ~28 s (under 10%) |
| 26 × dispatch solve (HiGHS) | ~10 s each |
| 26 × `get_results()` (pure Python, no solver) | ~10–12 s each |

Two consequences. Memory, not cores, sets the ceiling on a small machine: each worker
peaks around 0.5 GB. And roughly half of stage 2 is result extraction, which no
solver setting touches — the obvious target if single-scenario speed ever matters
more than sweep throughput.

**Writing a script that calls `run_jobs`?** Guard the entry point with
`if __name__ == '__main__':`. Workers are spawned, not forked (the caller is a Dash
background process holding sqlite handles), and a spawned worker re-imports the
caller's `__main__`; without the guard the children re-run the sweep on import and
the pool dies during bootstrap.

## Gas reservation

A reservation carves a share of planned LNG export volume out of the export stream
and puts it into the domestic market **at zero cost**, so it is the cheapest gas in
the system and is taken up ahead of everything else.

```bash
python src/solve.py --reservation 20
```

Four pieces, all applied in `_year_demand` (solve.py) and `model.py` so the foresight
capacity layer and the 365-day dispatch see the same policy:

1. `served_LNG[t] <= (1 - share) * LNG_demand[t]` — the carve-out; the trains may
   only liquefy what is left. Applied *after* the Winter/LNG levers, so the share
   bites on the export volume actually planned under the scenario.
2. `reserved_prod[t] <= share * LNG_demand[t]`, priced at **$0/GJ** — the carved-out
   volume, offered to the domestic market for nothing.
3. `production[source, t] + reserved_prod[t] <= source capacity` — the reserved gas
   is the *same* gas, not extra. Total physical deliverability is unchanged; a slice
   of it is simply free.
4. `sum(flow over LNG feed pipes)[t] <= commercial gas reaching the source` — exports
   may draw only on commercial gas. Without this the free gas would flow straight to
   the trains, which are ordinary demand nodes and cannot tell one molecule from
   another, and the reservation would do nothing. This is exact rather than an
   approximation because the trains have exactly three feed pipes (`APLNG_Pipe`,
   `GLNG_Pipe`, `WGP_Pipe`), all from Surat. "Commercial gas" must include transit
   inflows — Surat takes gas from Moomba over the SWQP and from Silver Springs, and
   LNG demand exceeds Surat's own deliverability on ~30 days a year, so restricting
   exports to Surat's own production would strand the trains on those days and
   change the no-reservation base case.

There is still **no export revenue term in the objective**. That coupling is what
produced negative nodal prices at Perth in the removed WA DomGas build; here the
reservation acts entirely through supply cost and flow eligibility.

> This replaces an earlier **pure export-cap** formulation (piece 1 alone). Under
> that version the reserved gas was never produced at all: domestic demand was
> already met, so cost minimisation left it in the ground, and the reservation was
> an export cap by another name. Pricing the gas at zero is what makes it move.

### What it does

Stressed 2030 (Step Change, Winter High, LNG Medium), demand **inelastic** so the
price effect is not confounded by demand response:

| reservation | offered | taken up | production | mean price | QLD price | Melbourne |
| --- | --- | --- | --- | --- | --- | --- |
| 0% | — | — | 1,711,690 TJ | $13.82 | $8.57 | $53.45 |
| 5% | 65,971 TJ | 99.7% | −65,755 TJ | −$1.73 | −$2.57 | −$0.71 |
| 10% | 131,942 TJ | 99.7% | −131,509 TJ | −$2.61 | −$3.78 | −$1.28 |
| 20% | 263,883 TJ | 99.7% | −263,018 TJ | −$3.13 | −$4.59 | −$1.42 |
| 30% | 395,825 TJ | **80.3%** | −377,028 TJ | −$4.03 | −$8.41 | −$1.43 |

**The gas now moves.** Take-up is 99.7% up to a 20% reservation — priced at zero it
is dispatched ahead of everything else. Beyond that the domestic market cannot
absorb it: at 30% nearly a fifth of the offered volume finds no buyer it can reach.

**But it displaces rather than adds.** With demand inelastic, domestic consumption
is fixed, so the free gas substitutes one-for-one for commercial gas that would have
been produced anyway. Total production falls by exactly the export cut. What changes
is the *price*, because the marginal molecule at Surat is now free.

**And the relief does not travel.** Price falls decay with distance from Surat:

| Brisbane / Gladstone / Surat | LNG nodes | Moomba / Darwin | Adelaide | Sydney | **Melbourne / Gippsland** | Iona |
| --- | --- | --- | --- | --- | --- | --- |
| −$4.59 | −$4.41 | −$3.62 | −$2.59 | −$1.93 | **−$1.42** | −$0.97 |

The smallest relief lands exactly where prices are highest. Melbourne and Gippsland
sit at ~$53/GJ and move $1.42. The corridor is doing far more work than under the
export-cap version — `SWQP_Rev` goes from 257 TJ/d mean and 2 days at capacity to
**505 TJ/d and 280 days at capacity**, and the `VGP` from 1.9 to 168.9 TJ/d — but it
fills, and then nothing more gets through.

**Curtailment does not move at all.** GPG shedding (11,877 TJ), industrial shedding
(1,448 TJ) and winter shortage (4,302 TJ) are *identical at every reservation level*,
0% through 30%. Crashing the Queensland gas price to $0.17/GJ relieves not one TJ of
southern curtailment.

So the conclusion from the export-cap version survives the change of mechanism, and
in sharper form: **a reservation is a Queensland price policy, not a southern supply
policy.** Forcing the gas into the market at zero cost gets it produced and consumed,
which the export cap never did, but it cannot put it where the shortage is.

> **System Cost is not comparable across reservation levels.** The objective carries
> no export revenue and costs the reserved gas at zero, so both removing export demand
> and reserving more always lower it — the figure is the cost of serving what is left,
> not a welfare measure. The dashboard relabels the KPI **System Cost (served gas
> only)** and shows **Gas Reserved** with the percentage actually taken up.

## Endogenous demand

Demand is normally a fixed volume: whatever the GSOO baseline says, at any price.
The **Price-responsive demand** switch replaces that with a **step demand curve**
around the baseline for each tier — blocks that **shed** when the nodal price rises
above their willingness to pay, and blocks that **raise** demand when it falls below.

In the dashboard it is the **Price-responsive demand** switch in the sidebar; on the
command line:

```bash
python src/solve.py --elastic-demand --winter High
```

**Off** (the default) is the original model: fixed demand volumes with GPG and
industrial curtailment at their flat $22 and $120/GJ strikes. **On** adds the step
curves below. Runs are cached separately — scenario keys gain an `_Elastic` segment —
so the two can be compared side by side without re-solving either.

Every block is derived by `src/build_demand_curves.py`, which writes
`data/curtailment_params.csv` and `data/gpg_capacity.csv`.

### The reference price, and why it is the model's own

An elasticity measures response to a price *deviation*, so it needs the price being
deviated from. **GARY's nodal prices are LP duals** — short-run marginal cost plus
transport — and come out at **$4–8/GJ** against field costs of $4–14/GJ. They are
*not* east coast contract prices ($13–15/GJ), which are set by LNG export parity and
carry capital recovery and resource rent on top of SRMC.

Calibrating against a contract price was the original mistake here. It put **93.8% of
node-days below the reference**, so the shed blocks almost never fired, and an upward
response would have inflated demand ~16% everywhere, permanently — a pure artefact of
comparing two different price series. There is a second reason to use the model's own
price: the GSOO baselines already embed AEMO's assumed price path, so an elasticity
applied to the absolute level would count the price response twice.

`REFERENCE_PRICE` is therefore the **demand-weighted mean nodal dual of the inelastic
baseline run**, $5.49/GJ. The generator recomputes it from the cached baseline and
warns if it has drifted, so a change that moves baseline prices cannot quietly leave
the curves calibrated against a stale number.

### Calibration inputs

| input | value | source |
| --- | --- | --- |
| `REFERENCE_PRICE` | $5.49/GJ | demand-weighted mean dual, inelastic baseline (StepChange, Winter Medium, LNG Medium) |
| `ELASTICITY` (shed) | −0.180 short run | Labandeira, Labeaga & López-Otero (2017), "A meta-analysis on the price elasticity of energy demand", *Energy Policy* 102, 549–568, Table 6 — significant at 1%; long-run −0.684, 230-estimate sample mean −0.184 |
| `ASYMMETRY` (raise) | 0.5 × shed | Direction from Gately & Huntington (2002), "The asymmetric effects of changes in price and income on energy and oil demand", *The Energy Journal* 23(1), 19–55 — efficiency investment and plant closure triggered by high prices do not reverse when prices fall. **The ratio is a judgement, not a measurement** |
| `HEAT_RATES` | 7 / 10.64 / 13 GJ/MWh | CCGT, NEM capacity-weighted average, OCGT (AEMO 2021, via Griffith University 2022-08) |
| `DISPLACED_SRMC` | $/MWh **by jurisdiction** — see below | what extra gas generation actually pushes out at the margin. **The single most influential assumption behind the GPG response** |

### Elasticities — every value in one place

All own-price elasticities of **natural gas** demand. GARY's shed side uses the
short-run figure; the raise side scales it by `ASYMMETRY`.

| elasticity | value | used for | source |
| --- | --- | --- | --- |
| Short-run, own-price | **−0.180** | **the shed blocks** (all tiers) | Labandeira et al. (2017) Table 6, meta-regression estimate, significant at 1% |
| Long-run, own-price | −0.684 | not used — see note below | Labandeira et al. (2017) Table 6, significant at 10% |
| Sample mean, short run | −0.184 | robustness check on the figure used | Labandeira et al. (2017), mean of 230 natural gas estimates |
| Sample mean, long run | −0.568 | context | Labandeira et al. (2017), same 230 estimates |
| **Raise-side, short run** | **−0.090** | **the industrial raise blocks** | `ASYMMETRY (0.5) × −0.180`; direction from Gately & Huntington (2002), ratio is a judgement |

For comparison, the same meta-analysis puts short-run electricity at −0.126,
gasoline at −0.293, diesel at −0.153 and heating oil at −0.017 (not significant).
Gas is the second most price-responsive fuel in that set in the short run, and the
most responsive of all in the long run.

**Why the long-run elasticity is not used.** GARY dispatches daily and the shed
decision is a daily one — a cold Tuesday in Melbourne, not a decade of appliance
turnover. Using −0.684 would attribute a capital-stock response to a single day's
price. The long-run figure is the right number for a question GARY does not ask:
how the *baseline* demand trajectory itself responds to a sustained price level.
That trajectory comes from the AEMO GSOO instead, which is also why the elasticity
here applies to deviations from the baseline rather than to the price level.

**What each block implies.** The blocks are increments of the fitted curve, so the
elasticity is embedded rather than restated per block:

| block | price vs reference | cumulative demand change | implied by |
| --- | --- | --- | --- |
| `MassMarket_B2` | 2× ($10.98) | −11.73% | `1 − 2^−0.18` |
| `MassMarket_B3` | 4× ($21.96) | −22.08% | `1 − 4^−0.18` |
| `MassMarket_B4` | 8× ($43.92) | −31.22% | `1 − 8^−0.18` |
| `Industrial_U1` | 0.85× ($4.67) | +1.47% | `0.85^−0.09 − 1` |
| `Industrial_U2` | 0.70× ($3.84) | +3.26% | `0.70^−0.09 − 1` |
| `Industrial_U3` | 0.55× ($3.02) | +5.53% | `0.55^−0.09 − 1` |

So the mass-market curve gives up ~31% of load by an eightfold price rise, with the
remaining **68.78% inelastic** — served, or unserved at the value of lost load. The
industrial curve adds ~5.5% at 55% of the reference price. Both are deliberately
modest: gas demand is inelastic, and the numbers say so.

**GPG has no elasticity.** Its raise ladder is an engineering substitution
threshold — `COAL_SRMC / heat_rate`, the gas price at which a generator can afford
to displace coal — not an econometric response. Its shed side is the existing flat
$22/GJ strike. Do not read the GPG blocks as an elasticity estimate; they are the
largest demand response in the model and none of it comes from the elasticity
literature.

### The blocks

| tier | direction | blocks | basis |
| --- | --- | --- | --- |
| Mass-market | shed | $10.98 / $21.96 / $43.92 for 11.7% / 10.4% / 9.1% of load | elasticity at 2×, 4×, 8× reference |
| Mass-market | **none** | — | see below |
| Industrial | shed | flat $120/GJ strike (unchanged) | existing tier |
| Industrial | raise | $4.67 / $3.84 / $3.02 for 1.5% / 1.8% / 2.3% | asymmetric elasticity at 0.85×, 0.70×, 0.55× reference |
| GPG | shed | flat $22/GJ strike (unchanged) | existing tier |
| GPG | raise | **per jurisdiction** — see below, a third of node headroom each | `DISPLACED_SRMC[state] / heat_rate` |

**Mass-market demand does not rise when gas gets cheap.** Victoria
[banned gas connections in new homes from 1 January 2024](https://www.abc.net.au/news/2023-07-28/victoria-bans-gas-new-homes-housing-developments-emissions/102659636)
and ~80% of Victorian homes were on gas; the sector is in policy-driven structural
decline and a lower commodity price does not reverse a connection ban. Households
shed but never expand (`MASSMARKET_RAISES = False` to revisit).

**The GPG ladder is engineering, not econometrics, and it is regional.** A generator's
willingness to pay per GJ is the cost of the generation it displaces divided by its
heat rate. What it displaces differs by jurisdiction, so a single national coal figure
is wrong — it priced SA and NT gas off a coal fleet that does not exist and put 27.9%
of the GPG response at nodes with nothing to displace.

| | displaced | $/MWh | resulting WTP (CCGT / avg / OCGT) | nodes |
| --- | --- | --- | --- | --- |
| QLD | black coal | 45 | $6.43 / $4.23 / $3.46 | Surat, Brisbane, Gladstone |
| NSW | black coal | 45 | $6.43 / $4.23 / $3.46 | Sydney |
| VIC | brown coal, mine-mouth | 10 | $1.43 / $0.94 / $0.77 | Melbourne, Gippsland |
| SA | imports over Heywood / Project EnergyConnect | 10 | $1.43 / $0.94 / $0.77 | Adelaide |
| NT | **nothing** | 0 | **no expansion blocks at all** | Darwin, Amadeus |

- **VIC** burns *brown* coal, which is mine-mouth and far cheaper to run than black:
  Hazelwood's private SRMC was put at ~$3/MWh. Gas essentially cannot displace brown
  coal on running cost, and the model now says so instead of pretending otherwise.
- **SA** has had no coal since Northern (784 MW, Port Augusta)
  [ceased generation on 9 May 2016](https://www.abc.net.au/news/2016-05-09/port-augustas-coal-fired-power-station-closes/7394854).
  Gas there competes against imports, so it is priced off the exporting region's coal.
- **NT** gets nothing at all. Darwin–Katherine is a small isolated system outside the
  NEM, with no interconnection and already >80% gas, so extra gas generation displaces
  nothing and has nowhere to sell it. GARY does not model that system; **zero is an
  honest "cannot say", not an estimate.**

In practice the VIC and SA thresholds sit below every price the model produces, so
expansion happens only in QLD and NSW.

Volume is bounded by nameplate capacity from the Gas Bulletin Board register (36 of 37
modelled facilities matched) — but GARY models gas, **not the NEM**, and the fleet runs
at **8.9% of nameplate** (321 TJ/d against 3,619 TJ/d). Unbounded expansion would let an
unmodelled electricity market set gas demand, so `GPG_EXPANSION_CAP` also limits
expansion to a multiple of baseline GPG demand (default 1.0 — GPG may at most double).
That is a modelling guardrail, not a finding.

### How expansion works, and why it does not repeat the WA mistake

Shedding is *penalised*, so serving is implicitly worth the strike price. Expansion is
the mirror image: a block adds demand and pays its value into the objective as a
**negative cost**, so the solver takes it up only while supplying it costs less than
it is worth. At an interior optimum the nodal price equals the block's value — exactly
a demand curve.

This is the same shape as the export-revenue term that drove Perth prices negative in
the removed WA DomGas build, so it is worth being precise about the difference: there,
revenue was coupled through a *reservation constraint* that forced domestic service.
Here **every block is bounded above** by physical headroom, and no constraint forces
uptake. Verified across the stressed 2030: minimum nodal price $4.90/GJ, **no negative
prices**. Check this again if the formulation changes.

### Three caveats worth carrying into any result

1. **Extrapolation.** −0.18 is estimated on the modest price variation in the historical
   record. The upper shed blocks extrapolate the fitted curve rather than measuring
   anything; the inelastic core caps how far that runs.
2. **Frequency mismatch.** The literature elasticity is monthly or annual; GARY applies
   it to a daily nodal price. Kanellakis et al. find European gas demand shows little or
   no daily price response *through the heating season*, with the response concentrated
   in the shoulder months ("The daily price and income elasticity of natural gas demand
   in Europe", *Energy Reports* 8, 2022). GARY sheds almost entirely in winter, so this
   matters. `WinterScale` damps it and **defaults to 1.0 — no adjustment**; set ~0.3–0.5
   to test the daily-frequency evidence.
3. **VOLL.** GARY penalises unserved gas at $300/GJ. The National Gas Rules set VoLL at
   **$800/GJ** in the Victorian DWGM and the STTM market price cap at **$400/GJ** (AEMO,
   *Gas Market Parameters Review 2022 — Final Recommendations*, Feb 2023). GARY's figure
   is conservative and left alone because changing it moves every historical result.

The lever is **off by default**, so the 28 cached scenarios stay valid and the inelastic
case remains the comparison baseline. Scenario keys gain an `_Elastic` segment; results
carry `massmarket` and `demand_raise` series; the dashboard adds **Demand Shed** and
**Demand Raised** KPIs in PJ.

> **System Cost is not comparable with an inelastic run.** The objective now carries a
> negative benefit term for demand taken up cheaply, which is a surplus, not a cost. The
> dashboard relabels the KPI **System Cost (net of demand benefit)**.

### Nodes without a meaningful price

A node that never has demand and never carries gas has a **degenerate dual**: its
balance constraint reads `0 == 0`, so the solver may report anything within a range,
and it reports the shortage penalty. Beetaloo (undeveloped supply, no demand assigned)
sat at a flat **$300/GJ for the whole horizon** that way, which is not a price — it
pulled a naive cross-node mean from $5.81 to $22.15. Price rows are now emitted only
for nodes that have demand or carry gas, so such nodes simply do not appear in price
outputs. The headline `Avg_Price` KPI was always production-weighted and so was never
affected; per-node price charts were.

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
- **Scenario levers:** Winter stress × LNG demand (9 combinations) layered on the chosen baseline, plus the SA Dunkelflaute event, the gas reservation and price-responsive demand; the batch runs all 3 baselines × 9 = 27 scenarios

