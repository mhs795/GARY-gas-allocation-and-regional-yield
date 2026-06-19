# Gas Market Model TODOs

- [ ] Deep dive into the Big-M approach for linearizing bilinear terms in optimization models (added 2026-06-06).
- [x] ~~Refactor demand generation in 'gas_market_model/src/generate_data_2050.py' to transition from sine-curve seasonality to a trace-based system (from GEMINI.md).~~
- [x] ~~Verify Gladstone Export Trunk Capacity: Re-assess the pipeline capacities for APLNG/GLNG/QCLNG feed lines based on the assumption that the 3600+ TJ/day demand figure is the correct AEMO benchmark (added 2026-06-06).~~
- [x] ~~Calibrate Surat capacity and LNG facility demand against AEMO benchmarks (added 2026-06-15).~~
- [x] ~~Review pipeline capacity, supply, and LNG demand aggregates and daily figures (added 2026-06-08).~~
- [ ] Confirm data source/methodology for splitting daily demand between LNG facilities (added 2026-06-08).
- [x] ~~Review SGM baseline decline logic and production-adjustment reporting in `model.py` to ensure long-term alignment with Net Zero 2050 trajectory (added 2026-06-13).~~
- [ ] Calibrate LNG capacity and demand figures against the latest AEMO GSOO and facility-specific reports to ensure long-term accuracy (added 2026-06-14).
