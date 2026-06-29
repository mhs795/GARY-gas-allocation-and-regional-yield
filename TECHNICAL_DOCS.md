# Nodal Gas Market Optimization Model: Data & Technical Documentation

This document provides a comprehensive overview of the data sources, modeling assumptions, and technical implementation of the Australian Nodal Gas Market Optimizer.

---

## 1. Data Sources & References

The model is designed to align with the **Australian Energy Market Operator (AEMO)** forecasting and planning frameworks.

### 1.1 Demand Profiles (2025–2050)
The model's central case is the **AEMO 2026 Gas Statement of Opportunities (GSOO)
"Step Change"** scenario (the Draft-2026-ISP-derived, gas-regionalised forecast; the
final 2026 ISP, published 25 June 2026, defers to the GSOO for gas demand). The
daily *shape* of demand comes from empirical **Gas Bulletin Board (GBB) Actual Flow
and Storage** data; the GSOO sets the *annual level*.

*   **Primary Scenario:** **"Step Change"** (AEMO's most likely path to Net Zero), used as the deterministic central case. The Winter, LNG and ADGSM scenario levers then layer multiplicatively on top.
*   **Re-basing method (2026-06-29):** Each demand sector is re-based onto its GSOO Step Change annual trajectory while the empirical daily profile shape is preserved (shape correlation 1.0 vs the GBB trace; only the annual level is scaled). This replaced the earlier arbitrary per-node growth rates (e.g. fixed Melbourne −4%/yr, Sydney −3%/yr) with empirically-grounded GSOO indices applied relative to 2026 (clamped to the 2026–2045 GSOO horizon, then held flat to 2050).
    *   **City nodes (Sydney, Melbourne, Adelaide, Brisbane):** city-gate distribution demand (dominantly Tariff V residential & small commercial) follows the GSOO **ResComm** trajectory — a steep electrification-driven decline. (`build_demand_gsoo.py`)
    *   **GPG:** annual GPG demand anchored to the GSOO **NEM** trajectory, split regionally and made winter-peaking from the GSOO regional summer/winter peaks. Yarwun was reclassified GPG → industrial to match the GSOO. (`build_gpg_demand_gsoo.py`)
    *   **Industrial:** the GSOO industrial trajectory is applied as a year index on the empirical BBLARGE levels (industrial here is a subset of the whole-sector GSOO industrial, so it is indexed rather than re-levelled). (`build_industrial_demand_gsoo.py`)
*   **LNG Transition:**
    *   **Gladstone Cluster (APLNG, GLNG, QCLNG):** Modeled as three distinct nodes sharing the Curtis Island shape, split per facility and scaled to the GSOO **LNG** trajectory (Figure 19) — replacing the previous flat assumption. 2026 is calibrated to ~3,650 TJ/day total QLD LNG demand.
*   **GSOO extraction:** `build_gsoo_stepchange.py` reads the two GSOO workbooks (`daily-max-demand-summary.xlsx`, `report-figures-and-data.xlsx`) into `data/gsoo/` CSVs (annual sector trajectories, regional summer/winter peaks). `model.py` loads year-aware profiles (clamped 2026–2045, held to 2050) and falls back to the flat GBB trace if a profile is missing.
*   **Source Links:**
    *   [AEMO 2026 GSOO Report Figures & Data](https://www.aemo.com.au/-/media/files/gas/national_planning_and_forecasting/gsoo/2026/2026-gas-statement-of-opportunities-report-figures-and-data.xlsx)
    *   [AEMO 2026 GSOO Supply Data](https://www.aemo.com.au/-/media/files/gas/national_planning_and_forecasting/gsoo/2026/2026-gas-statement-of-opportunities-supply-data.xlsx)

### 1.2 Basin Supply & Depletion
Basin capacities and decline rates are modeled to match the projected depletion of the Bass Strait and the maturation of the Cooper Basin.

*   **Gippsland (Bass Strait):** Modeled with a **-12% annual decline**, representing the rapid depletion reported in the 2026 GSOO (50% drop by 2030).
*   **Moomba (Cooper Basin):** Modeled with a **-3% annual decline**.
*   **Surat (Queensland CSG):** Modeled with a **-1% annual decline**, reflecting more stable long-term CSG reserves. Currently calibrated to a **4,000 TJ/day** total production benchmark.
*   **Source Link:** [AEMO 2026 GSOO Supply Data](https://www.aemo.com.au/-/media/files/gas/national_planning_and_forecasting/gsoo/2026/2026-gas-statement-of-opportunities-supply-data.xlsx)

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
    prod_cost +          # Cost of gas production at basins
    trans_cost +         # Transport/pipeline tariffs
    expansion_cost +     # Amortized CapEx for new builds
    penalty_cost +       # Cost of unserved demand (shortages)
    storage_cost         # Small penalty for storage cycling
)
```

### 2.2 Storage Continuity Logic
To model storage, we link every day to the previous day so the "inventory" is tracked accurately.

```python
if t == 1:
    return m.inventory[sn, t] == (data['StorageCapacity'] * 0.5) + m.injection[sn, t] - m.withdrawal[sn, t]
else:
    return m.inventory[sn, t] == m.inventory[sn, m.T.prev(t)] + m.injection[sn, t] - m.withdrawal[sn, t]
```

### 2.3 sequential Multi-Year Logic (`gui.py`)
The GUI runs the simulation year-by-year, passing the "memory" of built infrastructure forward.

### 2.4 Price Discovery (Shadow Prices)
Nodal prices are extracted from the **Dual Variables** of the Nodal Balance constraints.

---

## 3. Directory Structure Summary

*   `/src/data/`: The "Brain." Contains CSVs for nodes, supply, demand, expansion, and contracts. Source inputs (GBB actuals, GSOO workbooks, configs) are tracked; derived demand files are gitignored and rebuilt from source.
*   `model.py`: The "Optimizer." Defines the variables, objective, and constraints (Pyomo + HiGHS via `appsi_highs`). Build binaries are relaxed to continuous after the MIP step so HiGHS returns the duals used for nodal prices.
*   `gui.py` / `dashboard.py`: The "Interface." Handle user inputs, multi-year loops, and Plotly charts. Both expose the **Regenerate All Data** and **Run All Scenarios** buttons.
*   `regenerate_data.py`: The "Builder." `regenerate_all()` runs the full demand-build pipeline from source in dependency order — this is what the **Regenerate All Data** button calls.
*   `build_gsoo_stepchange.py`, `build_gpg_demand_gsoo.py`, `build_industrial_demand_gsoo.py`, `build_demand_gsoo.py`, `build_curtailable_demand.py`: The "Forecasters." Extract the GSOO Step Change trajectories and re-base each demand sector onto them, preserving the empirical GBB daily shapes. (These replaced the deleted `generate_data_2050.py`.)
