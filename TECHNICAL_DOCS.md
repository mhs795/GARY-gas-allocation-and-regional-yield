# Nodal Gas Market Optimization Model: Data & Technical Documentation

This document provides a comprehensive overview of the data sources, modeling assumptions, and technical implementation of the Australian Nodal Gas Market Optimizer.

---

## 1. Data Sources & References

The model is designed to align with the **Australian Energy Market Operator (AEMO)** forecasting and planning frameworks.

### 1.1 Demand Profiles (2025–2050)
The model's demand is built from the **AEMO 2026 Gas Statement of Opportunities
(GSOO)** (the Draft-2026-ISP-derived, gas-regionalised forecast; the final 2026 ISP,
published 25 June 2026, defers to the GSOO for gas demand). The daily *shape* of
demand comes from empirical **Gas Bulletin Board (GBB) Actual Flow and Storage**
data; the GSOO sets the *annual level*.

*   **Selectable baselines (2026-06-30):** The user picks one of the three AEMO 2026 GSOO demand scenarios as the model **baseline** via a dropdown in the dashboard:
    *   **Step Change** — central case, AEMO's most likely path to Net Zero.
    *   **Accelerated Transition** — faster electrification: steepest ResComm decline, highest green-industrial growth, fastest LNG run-down, strongest near-term GPG firming.
    *   **Slower Growth** — demand held higher for longer (ResComm/LNG decline more slowly).
    The Winter, LNG and ADGSM scenario levers then layer multiplicatively on top of whichever baseline is chosen. Each baseline has its own generated demand files (`demand_<baseline>.csv`, `gpg_demand_profile_<baseline>.csv`, `industrial_demand_profile_<baseline>.csv`), and `model.py` / `solve.py` load the set matching the selection. *Note:* "Progressive Change" is **not** a 2026 GSOO scenario (it belongs to the older 2025 GSOO vintage), so the model uses the consistent 2026 GSOO trio above.
*   **Re-basing method (2026-06-29):** Each demand sector is re-based onto its GSOO annual trajectory (for the chosen baseline) while the empirical daily profile shape is preserved (shape correlation 1.0 vs the GBB trace; only the annual level is scaled). This replaced the earlier arbitrary per-node growth rates (e.g. fixed Melbourne −4%/yr, Sydney −3%/yr) with empirically-grounded GSOO indices applied relative to 2026 (clamped to the 2026–2045 GSOO horizon, then held flat to 2050).
    *   **City nodes (Sydney, Melbourne, Adelaide, Brisbane):** city-gate distribution demand (dominantly Tariff V residential & small commercial) follows the GSOO **ResComm** trajectory — a steep electrification-driven decline. (`build_demand_gsoo.py`)
    *   **GPG:** annual GPG demand anchored to the GSOO **NEM** trajectory, split regionally and made winter-peaking from the GSOO regional summer/winter peaks. AEMO publishes only a single 2026 GSOO GPG annual trajectory, so for the Accelerated/Slower-Growth baselines that trajectory is scaled by the ratio of the scenario's total modelled-region GPG peak to Step Change's (the regional peaks *are* published per scenario). Yarwun was reclassified GPG → industrial to match the GSOO. (`build_gpg_demand_gsoo.py`)
    *   **Industrial:** the GSOO industrial trajectory (per baseline) is applied as a year index on the empirical BBLARGE levels (industrial here is a subset of the whole-sector GSOO industrial, so it is indexed rather than re-levelled). (`build_industrial_demand_gsoo.py`)
*   **LNG Transition:**
    *   **Gladstone Cluster (APLNG, GLNG, QCLNG):** Modeled as three distinct nodes sharing the Curtis Island shape, split per facility and scaled to the GSOO **LNG** trajectory (Figure 19, per baseline) — replacing the previous flat assumption. 2026 is calibrated to ~3,650 TJ/day total QLD LNG demand.
*   **GSOO extraction:** `build_gsoo_scenarios.py` reads the two GSOO workbooks (`daily-max-demand-summary.xlsx`, `report-figures-and-data.xlsx`) into `data/gsoo/` CSVs for **all three baselines** (`annual_sector.csv`, `regional_peak.csv` — each carrying a `Scenario` column — and `gpg_seasonal_max.csv`). `model.py` loads year-aware profiles (clamped 2026–2045, held to 2050) and falls back to the flat GBB trace if a profile is missing.
*   **Source Links:**
    *   [AEMO 2026 GSOO Report Figures & Data](https://www.aemo.com.au/-/media/files/gas/national_planning_and_forecasting/gsoo/2026/2026-gas-statement-of-opportunities-report-figures-and-data.xlsx)
    *   [AEMO 2026 GSOO Supply Data](https://www.aemo.com.au/-/media/files/gas/national_planning_and_forecasting/gsoo/2026/2026-gas-statement-of-opportunities-supply-data.xlsx)

### 1.2 Basin Supply & Depletion
Basin capacities and decline rates are modeled to match the projected depletion of the Bass Strait and the maturation of the Cooper Basin.

*   **Gippsland (Bass Strait):** Modeled with a **-12% annual decline**, representing the rapid depletion reported in the 2026 GSOO (50% drop by 2030).
*   **Moomba (Cooper Basin):** Modeled with a **-3% annual decline**.
*   **Surat (Queensland CSG):** Modeled with a **-1% annual decline**, reflecting more stable long-term CSG reserves. Currently calibrated to a **4,000 TJ/day** total production benchmark.
*   **Source Link:** [AEMO 2026 GSOO Supply Data](https://www.aemo.com.au/-/media/files/gas/national_planning_and_forecasting/gsoo/2026/2026-gas-statement-of-opportunities-supply-data.xlsx)

### 1.2b Northern Territory Extension (2026-07-10)
The network was extended beyond the east coast to include the **Northern Territory**. WA was evaluated but **not** included — it is a physically isolated gas island (no east–west pipeline) and connects to the world only via LNG, so it adds little to an east-coast dispatch/price model.

*   **Nodes:** `Amadeus` (supply — Mereenie / Palm Valley / Dingo, Amadeus Basin), `Beetaloo` (potential shale supply, gated by a Terminal-type expansion option so it only produces once developed), `Darwin` (demand — power generation & industry, roughly flat year-round), and `Tennant_Creek` (a pipeline-junction hub).
*   **Pipelines (`arcs.csv`):** the **Amadeus Gas Pipeline** is split at the junction as `AGP_S` (Amadeus → Tennant Creek) and `AGP_N` (Tennant Creek → Darwin, via Katherine); `Beetaloo_Pipe` ties the Beetaloo Basin into the corridor at Tennant Creek; and the **Northern Gas Pipeline** (`NGP`, with reverse `NGP_Rev`) is the NT↔east-coast interconnector, modelled as a single Tennant Creek ↔ Moomba arc.
*   **Demand:** NT gas demand is **principally power generation** (AER Amadeus Gas Pipeline demand review), so it is modelled as a curtailable **GPG tier**, not generic core demand — the Darwin-Katherine Interconnected System stations (**Channel Island**, **Weddell**, **Katherine**, **Pine Creek**) at the `Darwin` node and **Owen Springs** (Alice Springs) at the `Amadeus` node, ~48 TJ/day total, held roughly flat with a gentle ~0.8%/yr solar-driven decline (`NT_GPG_BASE_TJD` in `build_gpg_demand_gsoo.py`; per-station split in `gpg_facilities.csv` via `build_curtailable_demand.py`). Because NT gas (Amadeus ~$6.5/GJ) sits well below the $22 GPG strike it is served in normal conditions and only "sheds" as a scarcity signal. A small flat city-gate commercial/light-industrial load (~4 TJ/day) remains as core Darwin demand. The large Darwin LNG export terminals (Ichthys, Darwin LNG) are **not** modelled as domestic demand — they run on their own dedicated offshore fields.
*   **Map geometry:** node coordinates and pipeline routes in `dashboard.py` were validated against the **AEMO gas infrastructure map v2021** (`gas-map-v2021-v16.pdf`). The NGP line is traced along the real corridor — Tennant Creek → **Mt Isa** (NGP) → **Ballera** (Carpentaria Pipeline) → Moomba — rather than a straight run south, even though the model collapses it to one arc.

### 1.3 Market Mechanisms
The model includes realistic constraints to reflect bilateral agreements and regulatory triggers.

*   **Contractual Minimums:** Controlled via `src/data/contracts.csv`. This enforces lower-bound flow constraints on specific arcs (e.g., MSP, LNG exports), simulating base-load commitments.

### 1.4 Infrastructure & Storage
Network constraints and potential expansions are derived from current pipeline capacities and proposed major projects.

*   **SWQP:** 512 TJ/d Western-haul, 340 TJ/d Eastern-haul.
*   **MSP:** 590 TJ/d Southbound (upgraded in 2025).
*   **EGP:** 350 TJ/d Northbound.
*   **Iona Storage:** Modeled with 25 PJ capacity and 570 TJ/d withdrawal capability, matching the 2026 GSOO data.

---

## 2. Technical Code Explainers

### 2.1 The Optimization Objective (`model.py`)
The model minimizes total system costs over a 365-day horizon.

```python
m.obj = pyo.Objective(expr=
    prod_cost +          # gas production at each basin
    trans_cost +         # pipeline transport tariffs
    exp_capex +          # amortised CapEx for new builds
    shortage_penalty +   # unserved core (mass-market) demand @ VoLL $300/GJ
    gpg_pen + ind_pen +  # curtailable GPG ($22) & industrial ($120) demand-response tiers
    storage_cost         # small storage-cycling penalty
)
```

Large gas-powered-generation (GPG) and large-industrial loads are modelled as **curtailable demand-response tiers**: each is served unless the nodal price exceeds its strike price, forming a merit order — GPG ($22/GJ) sheds before industrial ($120/GJ), which sheds before core mass-market demand (value-of-lost-load $300/GJ).

### 2.2 Storage Continuity Logic
To model storage, we link every day to the previous day so the "inventory" is tracked accurately.

```python
if t == 1:
    return m.inventory[sn, t] == (data['StorageCapacity'] * 0.5) + m.injection[sn, t] - m.withdrawal[sn, t]
else:
    return m.inventory[sn, t] == m.inventory[sn, m.T.prev(t)] + m.injection[sn, t] - m.withdrawal[sn, t]
```

### 2.3 Multi-Year Solve — Two-Stage Full-Horizon (`solve.py`, `capacity_model.py`)
`solve_scenario(..., foresight=True)` (the default) runs a **two-stage, full-horizon** optimisation:

1.  **Capacity layer (perfect foresight)** — `CapacityExpansionModel` co-optimises *what to build and when* across the whole 2025–2050 horizon on a reduced temporal grid (12 monthly representative days + 1 annual peak day per year), minimising a proper **NPV** objective: each year's operating cost is discounted, and each project's CapEx is charged once at its build year (discount rate default 7%, adjustable in the dashboard). Output: a `build[project, year]` schedule.
2.  **Dispatch layer** — each year is then dispatched at full **365-day** resolution with the builds fixed to that schedule (`GasMarketModel(builds_fixed=…)`). With no free binaries this is a pure LP, so HiGHS returns the duals used for nodal prices.

A **myopic** alternative (`foresight=False`, exposed as a dashboard toggle) is also available: each year decides its own builds reactively with no knowledge of future years, carrying built projects forward. It is preferred for *unanticipated* shocks (e.g. the SA dunkelflaute) where perfect foresight would unrealistically pre-build ahead of the event.

### 2.4 Price Discovery (Shadow Prices)
Nodal prices are extracted from the **Dual Variables** of the Nodal Balance constraints.

---

## 3. Directory Structure Summary

*   `/src/data/`: The "Brain." Contains CSVs for nodes, supply, demand, expansion, and contracts. Source inputs (GBB actuals, GSOO workbooks, configs) are tracked; derived demand files are gitignored and rebuilt from source.
*   `model.py`: The "Optimizer." Defines the variables, objective, and constraints (Pyomo + HiGHS via `appsi_highs`). Build binaries are relaxed to continuous after the MIP step so HiGHS returns the duals used for nodal prices.
*   `dashboard.py`: The "Interface." A Dash/Plotly app that handles user inputs, multi-year loops, and charts, and exposes the **Regenerate All Data** and **Run All Scenarios** buttons.
*   `regenerate_data.py`: The "Builder." `regenerate_all()` runs the full demand-build pipeline from source in dependency order — this is what the **Regenerate All Data** button calls.
*   `build_gsoo_scenarios.py`, `build_gpg_demand_gsoo.py`, `build_industrial_demand_gsoo.py`, `build_demand_gsoo.py`, `build_curtailable_demand.py`: The "Forecasters." `build_gsoo_scenarios.py` extracts all three GSOO baseline trajectories (Step Change / Accelerated Transition / Slower Growth); the others re-base each demand sector onto each baseline (`build()` for one baseline, `build_all()` for all three), preserving the empirical GBB daily shapes. (These replaced the deleted `generate_data_2050.py`.)
