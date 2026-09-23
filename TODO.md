# GARY — outstanding work

**The single source of truth for what is left to do on the model.** Nothing else in the
repository tracks work. The docs explain behaviour and point here, and they hold no to-do
lists of their own.

Rules:
- **Open items only.** When an item is fixed, delete it. The commit message records the
  fix, and git history keeps the old write-up. Closed items are not kept here.
- **Numbers are permanent IDs.** They are never reused, so a doc or commit that says
  "TODO #16" always means the same thing. Gaps in the numbering are closed items.
- **Every item says what it is, how big it is, and what closing it would take.** If an
  item can't say how big it is, measuring that is the first step.

Last reviewed 23 Sep 2026, against the re-solved standard set.

---

## A. Changes what the model can say

### #16 — Late-horizon results depend on where the model stops
The capacity MIP holds each tranche's reserves as **one budget over the whole horizon**
(`reserve_limit`). One constraint has one dual, so the scarcity rent is forced to grow at
exactly the discount rate (7.000%/yr in every run). The rent carries no information about
*when* a field runs down.
- **How big:** moving the stop year from 2051 to 2055 shifts total exports over 2025–50 by
  about 2% and moves the last export year by about a year (measured 12 Sep 2026;
  `docs/depletion.md` §2).
- **Symptoms that go when this is fixed:**
  - The Surat 2P rent climbs past its own economic ceiling (2C cost less 2P cost, $3.00)
    from 2047, reaching $3.79 in 2050. It prices nothing, because 2P is exhausted by then,
    but it fails the obvious test.
  - AEMO's decline rates and reserves disagree (the decline curves would produce 1.2–10.9×
    the stated reserves). GARY survives this because the stock limit binds, but it is why
    a per-year stock needs a plateau rule.
  - τ in the salvage value is struck on nameplate reserves and ignores decline, which errs
    towards a higher terminal credit.
- **To close:** a per-year stock in the MIP, with a plateau-then-decline deliverability
  rule. It was prototyped on 3 Sep 2026 and works mechanically. It failed only because
  declining from year one is wrong. The unanswered question is **where each field's
  plateau ends**, and neither AEMO input supplies it. A rolling horizon (about 26× the
  solve time) is the expensive alternative. See `docs/depletion.md` §4 and
  `docs/scarcity-rent.md` §8.
- **Acceptance test:** the 2051/2055/2060 stop years agree on the 2025–50 window, and the
  2P rent stays under its ceiling.

### #22b — The late export tail is set by one unpublished number
Whether Queensland exports continue into the late 2040s turns on
`export_netback_deduction` (the liquefaction and shipping cost taken off the Asian LNG
price). Its midpoint is A$2.87/GJ, and the public range is A$2.15–3.59.
- **How big:** at the low end of the range, 2025–50 exports rise from 24,656 to 31,840 PJ
  and the trains run to 2050 instead of stopping in 2046 (measured 12 Sep 2026).
- **To close:** this is a reporting fix, not a model fix. Add low/high deduction runs to
  the standard set, and show late-horizon exports as a range on the dashboard and in the
  price summary.

### #24 — Narrabri's cost and pipeline capital are GARY's own numbers
Added 23 Sep 2026.
- **Gas cost:** AEMO's G26 cost table has no Gunnedah row. The $9.64/GJ used is AEMO's
  Surat 2C cost × 1.45, the Narrabri-to-Queensland ratio the Future Gas Strategy is
  reported (by IEEFA) to give. That figure has not been found in a primary source.
  Published estimates run from $6.40 (Santos, GSOO 2020) through $7.28–9.36 (Core Energy).
- **Pipeline capital:** the Hunter Gas Pipeline's $548m is the Narrabri Lateral's
  published ~$90m, plus the rest at NEAP's unit rate. Santos gives only a combined ~$1.7bn
  for the field, lateral and pipeline together.
- **How big:** unmeasured. Narrabri is the only NSW supply source, so its cost relative to
  imports decides whether it is built.
- **To close:**
  - Find the Future Gas Strategy Analytical Report figure, or ACIL Allen's Narrabri
    assumption.
  - Run a sensitivity at $6.40 and $10.
  - Replace the pipeline capital with a sourced figure if one is published.

### #1 and #18 — Existing and new pipelines are priced on different bases
Existing arcs charge the **posted tariff**, which includes the pipe's capital. New arcs
charge **running cost only**, with capital charged separately. Two things follow:
- **#1:** expanding an existing pipe collects its posted tariff on the new volume *and*
  charges the expansion's own capital. That over-recovers capital, so brownfield
  expansions get built less often than they should.
- **#18:** where old and new pipes run in parallel, dispatch compares a price that
  includes capital against one that doesn't. The 512 TJ/d SWQP reversal sits unused from
  the year Bulloo is built. `tests/audit_inputs.py` fails on this deliberately. The audit
  also warns that the three LNG feed pipes are priced 1.7× apart per km.
- **How big:** it changes which pipelines are built, not price levels. The 2050 mean moved
  $0.17/GJ when the tariffs were rebased (28 Aug 2026).
- **To close:** this is a methodology decision. Either move every arc to running-cost
  pricing with capital charged separately (this breaks calibration to ACIL Allen, which
  uses posted tariffs), or make an expanded arc charge a blended post-expansion tariff.
  It touches `flow_cap_rule` and both objectives.

### #25 — Galilee–Drummond's 2C (about 7% of AEMO's total) is not modelled
Narrabri, McArthur and Bass were added on 23 Sep 2026. **Galilee–Drummond** (2,788 PJ,
$10.17/GJ) is still out: AEMO's G26 Field Developments sheet records Comet Ridge's Galilee
tenure as cancelled by the Queensland Government, so there is no development to gate it
on.
- **To close:** only if a Galilee development re-emerges. Re-check at the next GSOO.

---

## B. Correctness and completeness

### #14 — Is the unreported 2051 year still short?
The model solves to 2051 and reports to 2050 so that the terminal year's artefacts fall
outside the window. Since the salvage value went in, 2025–50 shows zero shortage in every
scenario, but nobody has looked at 2051 since.
- **To close:** read 2051 from one central run. If it is clean, delete this item. If it is
  short, the salvage value is not doing its whole job at the horizon.

### #22 — Beetaloo's terminal credit is based on a price it has no pipeline to
Every route out of the Beetaloo is an unbuilt arc, so the salvage value falls back to the
gross $12.29/GJ landed import price at a terminal the basin cannot reach. The terminal
credit is $1.08/GJ.
- **How big:** small today (Beetaloo produced 144 PJ over the horizon in the central
  case), but the tranche grew from 5,109 to 7,945 PJ when McArthur was merged in. It would
  matter in any scenario that opens the NT corridor.
- **To close:** the one-line conservative fix is to credit a basin with no route at zero.
  The better fix is to also walk candidate arcs, and accept that the salvage value then
  depends on the build schedule.

### #26 — Port Kembla is `Status = Built` but charged as a new investment
The capacity MIP charges its full $250m CapEx as a fresh decision. Winter High now builds
it in 2034, so the answer matters there.
- **To close:** establish what the $250m is. If it is the cost of securing a new FSRU,
  relabel it. If it is the sunk jetty and pipeline, set it to zero and make the terminal
  available from 2027.

### #27 — 2046–50 demand is held flat, and the dashboard doesn't say so
The GSOO horizon ends at 2045, so demand holds flat at 2045 levels for 2046–50, while the
ACIL netback and import prices keep moving.
- **To close:** shade or label 2046–50 on every time-series chart and in the price summary
  workbook.

### #28 — Storage the model does not carry
- **HUGS** (Heytesbury, Lochard): 45 TJ/d withdrawal, 1.8 PJ, in execute phase. It has no
  start date, and AEMO gives it 0 TJ/d injection, so under the rule that every year must
  close with its storage refilled it could never be used. Add it as a candidate or as part
  of Iona once it has a date.
- **Kurri Kurri Lateral** (72 TJ of linepack): too small to matter. Noted so nobody goes
  looking for it.
- **Moomba:** AEMO lists it as "withdrawal only" but also gives a 100 TJ/d injection rate,
  which GARY uses. Check which is right.

### #29 — Reservation runs pay two tariffs on swaps
Reserved gas is tracked as its own commodity, so the model can't net a swap: reserved gas
going south while commercial gas comes north to a train. It ships both and pays both
tariffs, about 12 PJ in 2029 (roughly $20m), and 11–14 PJ in each reservation run.
- **To close:** optional. The strict version is conservative, and relaxing it risks
  reopening the old leak.

### #32 — Free field developments are "built" without producing
Field developments carry no CapEx, because their capital is inside the 2C gas cost. So the
capacity MIP can switch one on at zero cost whether or not its gas is ever used. In the
central case (re-solve of 24 Sep 2026), **14 of the 17 developments built produced nothing
over 2025–50**: the five Otway projects, Golden Beach, Judith, Cooper_2C, Amadeus_2C,
Bass_2C and the three Beetaloo rows. Only Bowen, Mahalo and Mt St Martin produced.
Narrabri was the sharpest case (built in 16 of 17 runs with no pipeline to take its gas),
and `Requires` now fixes that one.
- **How big:** no effect on prices, flows or costs, since an idle build changes nothing.
  But every build list and the dashboard's "New Projects" count overstate what gets
  developed, and a build year for these rows means nothing.
- **To close, options:**
  1. Report a zero-CapEx development as built only in the first year its tranche
     produces (reporting only, simplest).
  2. Give each development a small, sourced fixed cost, so building an idle one is never
     free.
  3. Gate 2C production on the build *and* use the MIP's planned production to drop
     builds with none.

  Option 1 fixes the reporting without touching the optimisation.

### #30 — Code paths not yet exercised end to end
- The dashboard hasn't been opened in a browser since the 23 Sep changes: the stale-result
  card, the 0–1% gap slider, the discount default, and the new nodes and routes on the map.
- Myopic mode has only had a 6-year smoke test, not a full-horizon run.
- The GLPK backend is untested with the tighter gap. It has no absolute gap, so its
  capacity solves may be slow.

---

## C. Sourcing and presentation

### #7 — Blacktip's $8.00/GJ is GARY's own figure
It sets Darwin's price whenever Blacktip is marginal. Replace it with a sourced number.

### #8 — The AGP tariff split by length is unverified
GARY splits AEMO's single AGP tariff ($0.40) across `AGP_S`/`AGP_N` by length. That
assumes $0.40 is a full-haul figure, but at $0.24/GJ per 1000 km it is seven times cheaper
than the posted median, which suggests it is zonal. The AER access arrangement would
settle it.

### #9 — Early-year prices are about half of ACIL Allen's
ACIL has about $12–13/GJ in 2027; GARY has about $6.4. The gap is structural, and
`docs/pricing.md` explains it:
- Before about 2032 every field is on AEMO's 2P cost, which is mostly opex.
- GARY does not reproduce ACIL's market-power overlay.

The $12 Code cap read as a floor (#11) would not close it.
- **To close:** decide whether GARY should publish a contract-price layer at all, or keep
  reporting system marginal cost with the caveat it already carries.

### #11 — The Code price cap as a floor instead of a ceiling
Optional. Measured to change nothing, because the cap never binds in Step Change, and in
Slower Growth only from 2040. Worth adding as a documented switch; don't expect it to move
results.

### #31 — Map routes that are traced rather than surveyed
Every arc OSM maps is drawn from OSM (`build_pipeline_geometry.py`). The rest are traced
along their corridors:
- **The unmapped part of an otherwise mapped route:**
  - `HGP`: the proposed Leewood → Hexham section, through the towns on its approved route.
  - `PLLP`: the 35 km Lang Lang → Pakenham lateral.
- **Hand-traced whole:** `RBP`, `QGP`, `VNI`, `SWP`, `VGP`, `PK2SYD`, `NGP`, the AGP legs,
  `BGP`, `Bulloo`, `NEAP`.
- **Still a straight line:** `Beetaloo_Pipe`.

Replace any of these as OSM coverage improves (re-run `build_pipeline_geometry.py`), or
from the operators' route maps.

### #6 — Wickham Point / Weddell is outside the network
Weddell Power Station now takes most of its gas directly from the LNG producers, bypassing
every pipe GARY models. The Darwin node therefore carries only gas delivered through the
AGP, not total NT burn. This is a comparability caveat rather than an error: NT figures
from GARY can't be compared with published NT consumption.
