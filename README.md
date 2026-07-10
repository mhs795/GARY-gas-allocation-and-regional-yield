# GARY — Gas Allocation and Regional Yield Model

A nodal, least-cost gas market optimisation model for the Australian energy transition (2025–2050), with an interactive scenario explorer dashboard.

## Requirements

The only thing you need to install manually is **Python 3.10 or later**:

- Windows: https://www.python.org/downloads/ — tick **"Add Python to PATH"** during install
- Mac: https://www.python.org/downloads/ or `brew install python`
- Linux: `sudo apt install python3 python3-venv` (Ubuntu/Debian)

All other dependencies (Dash, Plotly, Pyomo, HiGHS, etc.) are installed **automatically** the first time you run the app.

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

## Technical Details

- **Optimisation:** Pyomo with the HiGHS solver (`appsi_highs`)
- **Network:** Nodal pipeline model covering eastern Australia **plus the Northern Territory** — the Amadeus and Beetaloo basins feed Darwin, and the NT links to the east-coast grid via the Northern Gas Pipeline (Tennant Creek → Mt Isa → Ballera → Moomba). Western Australia is a separate, physically isolated gas market and is **not** included.
- **Horizon:** 2025–2050 (annual dispatch, 365 days/year)
- **Baselines:** selectable AEMO **2026 GSOO** scenario — **Step Change** (central), **Accelerated Transition**, or **Slower Growth** (demand re-based on the GSOO; daily shapes from GBB actuals)
- **Scenario levers:** Winter stress × LNG demand (9 combinations) layered on the chosen baseline; the batch runs all 3 baselines × 9 = 27 scenarios
- **Market mechanisms:** ADGSM domestic reservation, LNG netback pricing, long-term contracts
