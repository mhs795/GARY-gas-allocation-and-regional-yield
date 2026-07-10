# Gas Market Model TODOs

- [x] ~~Make the GSOO baseline selectable (Step Change / Accelerated Transition / Slower Growth) via a dropdown, with the Winter/LNG levers layering on top of the chosen baseline (added & done 2026-06-30).~~
- [ ] Deep dive into the Big-M approach for linearizing bilinear terms in optimization models (added 2026-06-06).
- [x] ~~Refactor demand generation in 'gas_market_model/src/generate_data_2050.py' to transition from sine-curve seasonality to a trace-based system (from GEMINI.md).~~
- [x] ~~Verify Gladstone Export Trunk Capacity: Re-assess the pipeline capacities for APLNG/GLNG/QCLNG feed lines based on the assumption that the 3600+ TJ/day demand figure is the correct AEMO benchmark (added 2026-06-06).~~
- [x] ~~Calibrate Surat capacity and LNG facility demand against AEMO benchmarks (added 2026-06-15).~~
- [x] ~~Review pipeline capacity, supply, and LNG demand aggregates and daily figures (added 2026-06-08).~~
- [ ] Confirm data source/methodology for splitting daily demand between LNG facilities (added 2026-06-08).
- [x] ~~Review SGM baseline decline logic and production-adjustment reporting in `model.py` to ensure long-term alignment with Net Zero 2050 trajectory (added 2026-06-13).~~
- [ ] Calibrate LNG capacity and demand figures against the latest AEMO GSOO and facility-specific reports to ensure long-term accuracy (added 2026-06-14).
- [ ] Revisit the CBC -> HiGHS (appsi_highs) solver switch: confirm it's the appropriate choice — check results parity vs CBC, solve times, and that the binary-relaxation-for-duals approach is sound (added 2026-06-29).
- [x] ~~Extend the network to the Northern Territory (Amadeus/Beetaloo → Darwin, Northern Gas Pipeline interconnector to the east coast); NT map geometry checked against the AEMO gas map v2021 (added & done 2026-07-10).~~
- [x] ~~Prototype a Western Australia module (isolated island, broken-out LNG trains, price-elastic exports at a slider-set netback, 15% DomGas reservation) — built, then **removed**: Perth showed negative nodal prices from the export-revenue term coupling through the reservation. If revisited, price exports via a fixed sink rather than an objective revenue term (added & reverted 2026-07-10).~~
