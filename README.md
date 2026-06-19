# Gas Market Optimization Model

This project is a nodal, least-cost gas market model for the Australian energy transition (2025–2050).

## Project Structure
- `/src`: Source code and core model logic.
- `/src/data`: CSV files containing network, supply, demand, expansion, and contracts data.
- `requirements.txt`: List of dependencies.
- `gas`: Primary CLI command to launch the dashboard.
- `run_dashboard.sh`: Shell script to launch the app (Linux/macOS).

## Dashboard (Scenario Explorer)
The dashboard has been upgraded to a **Scenario Explorer**. Instead of running simulations on demand, it now uses a pre-calculated cache of all possible market combinations (18 scenarios total).

- **How to run:** Use the `GAS` command in your terminal.
- **Pre-calculation:** On the first run, the model will solve all 468 years of market data (~10-15 mins).
- **Instant Feedback:** Once calculated, moving the sliders for ADGSM, Winter Stress, or LNG Demand in the sidebar will instantly update all maps and charts.
- **Refresh:** To force a re-calculation (e.g., after editing CSV data), run `GAS --recalc`.

## Technical Details
- **Optimization:** Uses Pyomo with the HiGHS solver for high-performance dispatch.
- **Model Logic:** Located in `src/model.py`.
- **GPG Volatility:** Includes randomized "Renewable Drought" events based on AEMO 2024 GSOO metrics.
- **Market Mechanisms:** Includes long-term contractual minimums and the ADGSM domestic reservation mechanism.
