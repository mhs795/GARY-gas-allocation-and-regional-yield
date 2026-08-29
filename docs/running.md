# Running GARY

[← back to README](../README.md)

Command line, solver backends, parallel sweeps, and the results cache.

- [Project layout](#project-layout)
- [Command-line reference](#command-line-reference)
- [Choosing a solver](#choosing-a-solver)
- [Parallel sweeps](#parallel-sweeps)
- [The results cache](#the-results-cache)

---

## Project layout

| Path | Description |
|---|---|
| `src/dashboard.py` | Dash web app |
| `src/model.py` | Pyomo dispatch model — one year, 365 days |
| `src/capacity_model.py` | Perfect-foresight capacity-expansion MILP |
| `src/solve.py` | Scenario solver — orchestrates the two stages |
| `src/solvers.py` | Solver backend selection (HiGHS / GLPK) |
| `src/sweep.py` | Runs independent scenarios across worker processes |
| `src/results_io.py` | Compressed, column-oriented scenario-results cache |
| `src/params.py` | The only reader of `gary_parameters.xlsx` |
| `src/acil_segment_prices.py` | ACIL Allen customer-segment price post-processing |
| `src/regenerate_data.py` | One-button rebuild of all derived data from source |
| `src/build_*.py` | The GSOO/GBB demand-build pipeline |
| `src/data/gary_parameters.xlsx` | Every model parameter |
| `src/data/expansion_options.csv` | Network expansion candidates, tagged GSOO / market |
| `src/data/` | Network nodes, pipelines, supply, demand |
| `gas`, `run_dashboard.sh`, `run_dashboard.bat` | Launchers |
| `requirements.txt` | Python dependencies |

The `./gas` launcher keeps the machine awake while GARY runs (via `systemd-inhibit`) so a
long batch survives closing the lid. `./gas --allow-sleep` opts out.

> The `GAS` terminal command only works on Linux/Mac after adding the project folder to
> your PATH. Everyone else should use the `run_dashboard` scripts.

## Command-line reference

Every scenario the dashboard can run can be solved headless.

```bash
python src/solve.py [flags]
```

| Flag | Default | Effect |
|---|---|---|
| `--baseline` | `StepChange` | GSOO baseline: `StepChange`, `Accelerated`, `SlowerGrowth` |
| `--winter` | `Medium` | Southern winter stress: `Low` / `Medium` / `High` |
| `--lng` | `Medium` | Global LNG market: `Low` / `Medium` / `High` |
| `--dunkelflaute` | off | SA wind-and-solar drought in 2027 |
| `--reservation PCT` | `0` | Reserve this share of planned LNG export volume |
| `--break-lng-contracts` | off | Let the reservation take foundation (take-or-pay) volume too |
| `--netback-pricing` | *(on by default in the app)* | Trains bid at the export netback; imports priced at injection cost |
| `--no-import-terminals` | off | Drop every LNG import terminal from the candidate set |
| `--gsoo-expansions-only` | off | Only expansions AEMO counts as committed |
| `--dc-nsw PJ`, `--dc-vic PJ` | `0` | Flat data centre gas demand at Sydney / Melbourne |
| `--dc-start YEAR` | `2030` | Year the flat volumes switch on |
| `--dc-file PATH` | none | Linked year-by-year series; `path#SheetName` to name a sheet |
| `--myopic` | off | Year-by-year investment instead of perfect foresight |
| `--mip-gap` | `0.005` | Optimality gap for the capacity MILP |
| `--solver` | `highs` | `highs` or `glpk` |

Other entry points: `src/main.py` (single solve), `src/batch_solve.py` (batch),
`src/regenerate_data.py` (rebuild derived data), `src/migrate_results.py` (convert an old
cache).

## Choosing a solver

The optimisation is written in Pyomo, so the underlying solver is swappable.

| Backend | Pyomo interface | Notes |
|---|---|---|
| `highs` (default) | `appsi_highs` | Multi-threaded, installed via pip (`highspy`). **All published GARY results use this.** |
| `glpk` | `glpsol` | Single-threaded, pure system package. Slower on the full-horizon dispatch, but no Python extension module needed. |

GLPK is **not** a pip dependency — install the system package:

```
sudo apt install glpk-utils          # Debian / Ubuntu / Pop!_OS
brew install glpk                    # macOS
```

Select a backend with `--solver`, the `--glpk` shorthand, or `GARY_SOLVER`:

```bash
./gas                                # HiGHS (default)
./gas --glpk                         # GLPK, dashboard and all
GARY_SOLVER=glpk ./gas               # via the environment
python src/solve.py --solver glpk    # single scenario, headless
```

Optionally cap each individual solve with `GARY_SOLVER_TIMELIMIT` (seconds), mainly useful
for GLPK:

```bash
GARY_SOLVER_TIMELIMIT=600 ./gas --glpk
```

### Do the two agree?

**Yes, to within the MIP gap.** On a 2030 single-year dispatch both solvers reach an
identical optimum (objective 7,611,822,687.71) when the gap is tightened to 0.01%.

At the default 0.5% gap they can stop at different incumbents of near-equal cost, so the
*build schedule can differ* where projects are close to a tie — e.g. the capacity model
puts `MSP_Expansion` at 2025 under HiGHS and 2027 under GLPK, with `SWQP_Expansion` at 2025
in both. Tighten `mip_gap` if you need the two to line up exactly.

### Speed

GLPK is slower, but on a full run the difference is modest — the 26-year solve is dominated
by the 365-day LP dispatch, where the two are close; the gap would widen on a harder MILP.
Measured on the Step Change baseline, Medium winter / Medium LNG, full 2025–2050 two-stage
solve:

| Stage | HiGHS | GLPK |
|---|---|---|
| Capacity-expansion MILP | 5.1 s | 5.7 s |
| Single-year dispatch (2030) | 10.8 s | 13.5 s |
| Full 26-year run | 302 s | 328 s |

## Parallel sweeps

A sweep is a set of scenarios that share nothing — each loads its own data, solves its own
capacity model and dispatches its own 26 years — so **the scenario is the unit of
parallelism.** `src/sweep.py` runs them in worker processes, one solve per worker, and both
**Run Scenarios** and **Run Reservation Scenarios** go through it. A single **Run Scenario**
is unaffected: it stays in-process, with its per-year progress and terminal log.

Workers default to **half the logical CPUs** (the physical core count on a normal machine).
Override it:

```bash
GARY_WORKERS=6 ./gas
```

### Why threads are not the lever

Workers deliberately pin `GARY_SOLVER_THREADS=1`. HiGHS's simplex is serial in practice, and
the duals GARY needs for nodal prices come from simplex, so handing one dispatch solve four
threads measures no faster than handing it one — on a 2030 dispatch LP, **9.9 s at one
thread against 14.5 s at four**. Only the capacity MIP parallelises at all (26.5 s → 21.6 s
on two physical cores), and it is under 10% of a scenario.

**Several single-threaded solves at once is the win; oversubscribing the box makes both
slower.**

### Where a scenario's time goes

Measured on a 2-core i5-5257U:

| stage | time |
|---|---|
| build 26 year-models + representative days + capacity MIP | ~28 s (under 10%) |
| 26 × dispatch solve (HiGHS) | ~10 s each |
| 26 × `get_results()` (pure Python, no solver) | ~10–12 s each |

Two consequences. **Memory, not cores, sets the ceiling on a small machine**: each worker
peaks around 0.5 GB. And roughly **half of stage 2 is result extraction**, which no solver
setting touches — the obvious target if single-scenario speed ever matters more than sweep
throughput.

> **Writing a script that calls `run_jobs`?** Guard the entry point with
> `if __name__ == '__main__':`. Workers are spawned, not forked (the caller is a Dash
> background process holding sqlite handles), and a spawned worker re-imports the caller's
> `__main__`; without the guard the children re-run the sweep on import and the pool dies
> during bootstrap.

## The results cache

Solved scenarios are cached in `src/data/precalculated_results.pkl` so the dashboard can
redraw without re-solving. Each solved year is stored as one DataFrame per result series
(prices, flows, production, storage, curtailment), packed down — node and pipeline names as
categoricals, values as `float32` — and the whole file compressed with zstd (gzip if
`zstandard` is not installed).

The frames stay packed in memory; `results_io.unpack` widens them if you want plain
`object`/`float64` columns.

This replaced a cache of per-day Python dicts (`{'Day': 1, 'Node': 'Melbourne', 'Price':
7.35}`, ~13.5 million of them across the full 28-scenario batch). Measured on that cache:

| | Before | After |
|---|---|---|
| File size | 640 MB | 23.4 MB |
| Load time | 46.8 s | 2.3 s |
| Peak memory | ~5 GB | 0.58 GB |

Values are `float32`, so figures differ from the old `float64` cache in the 8th significant
figure (worst relative difference 1e-7 across 158,000 plotted values — well below display
precision). **Model solving is unaffected**: only stored results are packed, never the
optimisation itself.

`results_io.load` sniffs the file, so caches written in the old format still open. To
convert one without re-solving:

```bash
python src/migrate_results.py            # rewrites in place, keeps a .bak
```

Both sweeps skip scenarios already in the cache and save each one as it finishes, so an
interrupted sweep keeps everything that completed. **Clear Results** forces a full rebuild.
