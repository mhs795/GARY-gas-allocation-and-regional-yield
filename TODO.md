# GARY — open items

Things known to be wrong, unfinished, or resting on an assumption worth revisiting.
Each entry says what the issue is, how big it is, and what closing it would take.

## 1. Brownfield expansions over-recover existing pipeline capital

Since arcs moved to posted tariffs (World B), an expansion that adds capacity to an
**existing** arc pays that arc's posted tariff on the incremental flow *and* its own
`CapEx × 0.08`. The posted tariff recovers the existing pipe's capital, struck against
the pipe's existing throughput — so expanding it collects that capital component on a
larger volume than the tariff was set for. A regulator would reset the tariff down.
GARY holds it fixed.

Not a double-charge of the *same* capital, but a real over-recovery. Upper bound if the
increment runs flat out all year:

| Expansion | Arc | +TJ/d | CapEx/yr | Phantom/yr | Ratio |
|---|---|---|---|---|---|
| `MSEP_Conversion` | MSP | 25 | $2.0m | $9.4m | 4.72× |
| `ECGG_3A_SWQP` | SWQP_Rev | 58 | $11.3m | $23.9m | 2.12× |
| `ECGG_3A_MSP` | MSP | 10 | $1.9m | $3.8m | 1.97× |
| `ECGG_VTS_Expansion` | VNI_Rev | 93 | $18.1m | $35.2m | 1.95× |
| `ECGG_3A_Culcairn` | VNI_Rev | 39 | $7.6m | $14.8m | 1.94× |
| `SWP_Compression` | SWP | 45 | $17.0m | $8.5m | 0.50× |
| `SWP_Looping` | SWP | 45 | $27.2m | $8.5m | 0.31× |

**Exposure is much wider than first thought, and it is measured.** An earlier version of
this note claimed committed projects were force-built and so immune. They are not — see
item 2 below. Comparing the 28 Aug runs before and after the tariff change, across all
47 scenarios:

| Project | Built before | Built after | Change |
|---|---|---|---|
| `ECGG_3A_MSP` (committed) | 38 | 22 | **−16** |
| `EGP_Reversal` (committed) | 10 | 3 | **−7** |
| `Bulloo_Interlink` (new arc) | 3 | 6 | **+3** |
| everything else | — | — | unchanged |

The direction is exactly the predicted distortion: brownfield expansions on posted-tariff
arcs get built less, new arcs (which carry variable cost and are clean) get built more.
Prices barely moved — 2050 mean +$0.17/GJ — and shortage is identical at 17,146 TJ over
9 scenarios. **The tariff rebasing changed which pipelines get built, not what gas costs.**

New arcs (`Bulloo`, `EGP_Rev`, `SEA_Gas_Rev`, `NEAP`) carry variable cost and are clean.

**To close it:** make an arc's cost depend on build state, so an expanded arc charges a
blended post-expansion tariff instead of the pre-expansion one plus separate capex.
Touches `flow_cap_rule` and the objective in both `model.py` and `capacity_model.py`.

## 2. Committed projects are not committed

`expansion_options.csv` has a `Status` column carrying `Committed` / `Pre-FID` / `Proposed`
/ `Built`. **No code reads it.** `already_built` is only the accumulator of what the model
chose in previous years, so it makes a build persist — it does not force anything in.

So a project with FID taken and steel in the ground is optimised on exactly the same terms
as a speculative one, and can simply not be built. `ECGG_3A_MSP` and `EGP_Reversal` are
both committed and both dropped sharply when the tariffs changed (item 1).

**To close it:** force `Status == 'Committed'` rows to build by their commissioning year.
That needs the per-row year column item 3 also wants. It would fix reality *and* neutralise
the brownfield bias for exactly the projects where it is worst, since a forced build does
not care that its economics are understated.

## 3. `Surat_Potential` is permanently zero

`supply_cap_rule` matches `Target == Node`, and no Terminal option targets
`Surat_Potential`, so its 3,000 TJ/d at $10/GJ can never be produced. `Gippsland_Potential`
had the same fault and was fixed by repointing `Golden_Beach` at it. Wiring 3,000 TJ/d of
undeveloped Queensland CSG live is a modelling decision, not a bug fix — it needs a view
on what unlocks it and at what capex.

## 4. No earliest-build year for pipeline candidates

`terminal_earliest` (2028) gates Type=Terminal only. `NEAP` is a 2030s project per APA
and nothing stops the model building it in 2026. Needs a per-row year column in
`expansion_options.csv` honoured by both models.

## 5. NGP reversal is modelled as normal supply

The AER is explicit that reverse flow into the NT is "not a normal operational case…
expected to only be utilised in emergencies when gas producers are unable to supply gas
into the AGP" (AAR 2026-31). GARY runs `NGP_Rev` as ordinary least-cost supply every year.
Real in 2025 — PWC is doing exactly this because Blacktip has collapsed — but the model
treats an emergency arrangement as the steady state.

## 6. Wickham Point / Weddell is outside the network

Weddell Power Station now takes most of its gas direct from the LNG producers at Wickham
Point rather than through the AGP, which is why its AGP delivery is only 1.4 TJ/d. That
route bypasses every pipe GARY models. Defensible to exclude (as Darwin LNG, Ichthys and
WA are excluded), but it means the Darwin node carries AGP-delivered load, not total NT
gas burn — so NT demand here is not comparable to published NT consumption figures.

## 7. Blacktip cost is GARY's own

$8.00/GJ, chosen because the field is running at roughly 15% of design through the same
fixed plant. No published figure. It sets Darwin's price directly whenever Blacktip is
marginal, so it is worth replacing with a sourced number.

## 8. AGP posted tariff split by length is unverified

The GSOO lists one AGP tariff (0.40, both directions). GARY splits the pipeline into
`AGP_S`/`AGP_N` and pro-rates by route length, which assumes 0.40 is a full-haul figure.
The AER access arrangement would settle it. Note 0.40 over 1,658 km is $0.24/1000 km
against a posted median of $1.63 — seven times cheap, which is itself odd and may mean
the number is zonal rather than full-haul.

## 9. ~~Long-run prices are about half ACIL Allen's~~ — DEMAND SIDE FIXED 28 Aug 2026

Benchmarked 28 Aug 2026 against ACIL Allen, *Wholesale natural gas prices for AEMO*,
Final Report, **14 November 2025** (the report behind the 2026 GSOO). Step Change, 2050:

| | ACIL Nov 2025 | GARY |
|---|---|---|
| Asian LNG price | A$9.99 | A$9.99 — matches |
| Import injection cost (LNG + 0.80 shipping + 1.50 regas) | **$12.29** | $12.29 — matches |
| Export netback (LNG less the $2.87 deduction) | — | $7.12 |
| Melbourne | ~$13–14 | **$7.70** |
| Mean nodal | $12–14 | **$7.82** |

**The inputs are faithful.** An earlier version of this note claimed the `LNG_Anchors`
sheet did not match ACIL; that was wrong — it was checked against the Feb 2025 vintage.
Against the Nov 2025 report the anchors match Table B.4 exactly for all three scenarios,
the LNG price formula matches Appendix B.9 (fixed 0.40, slope 0.12, FX 0.66, 1.055
GJ/mmbtu), the spot shares match Table B.3, and `model.py` already reprices the import
nodes on Table 2.1's injection cost rather than the flat $14 in supply.csv.

**The gap is structural.** ACIL's southern markets price off IMPORT PARITY; GARY's price
off EXPORT NETBACK. That spread — $12.29 against $7.12, about $5.17/GJ — is essentially
the whole difference. ACIL: *"prices in southern markets in particular generally do not
follow the LNG netback lower… increasingly reliant on northern gas (LNG netback plus
transport) and higher cost LNG imports"*, while *"Brisbane hovers at a price around the
LNG netback price"*. **GARY reproduces ACIL's Brisbane across the entire east coast.**

The netback mechanism itself is working exactly as designed. In 2050 Surat prices at
$6.34 against a netback of $7.12 and an `APLNG_Pipe` tariff of $0.7842 — netback less the
feed-pipe tariff, to the cent. Exports run at or near liquefaction nameplate throughout
(3,016 TJ/d exported in 2050 against 3,680 nameplate).

**What has been ruled out** (checked 28 Aug 2026, Step Change / Winter Medium / LNG Medium):

- *Not* export volume being freely divertible. With `respect_contracts` true — the default,
  and true in all 47 cached runs — `from_foundation` is 0, so 93% of planned export volume
  stays in `node_demand` as must-serve and cannot be diverted at any price. Only the spot
  tail is contestable. An earlier version of this note claimed otherwise; it was wrong.
- *Not* the north–south corridor. In 2050 MSP runs a mean of 173 TJ/d against 590 of
  capacity, MAPS 44 against 249, SWP 130 against 570 — **zero days at capacity on any of
  them** — and SWQP carries nothing. Gas is not being held back by pipes.
- *Not* supply volume. ACIL's Step Change assumes MORE supply than GARY has (Beetaloo at
  ~50 PJ/a, Narrabri by 2030) and still lands $5–6/GJ higher.

**FOUND IT — GARY carries a third less domestic demand than the GSOO by the 2040s, and
it is a bucket-assignment error.** Measured 28 Aug 2026, Step Change:

| TJ/d | 2026 | 2030 | 2035 | 2040 | 2045 |
|---|---|---|---|---|---|
| GARY domestic (ECGM) | 1,096 | 960 | 830 | 561 | **496** |
| 2026 GSOO domestic | 1,253 | 1,145 | 1,057 | 817 | **750** |
| ratio | 0.87 | 0.84 | 0.79 | 0.69 | **0.66** |

GPG matches the GSOO exactly (195 TJ/d in 2026). The gap is entirely in the other two
sectors, and it is not a level error — it is which bucket the load sits in.

| 2026, TJ/d | fast-declining bucket | slow-declining bucket | total |
|---|---|---|---|
| GSOO | ResComm **454** | Industrial **604** | 1,058 |
| GARY | city-gate **695** | metered industrial **205** | 900 |

The decline indices themselves are applied correctly — GARY's city-gate index over
2026→2045 is 0.210 against the GSOO's ResComm 0.210, and its industrial index is 0.766
against the GSOO's 0.763. The problem is that `build_demand_gsoo.py` applies the ResComm
index to **all** city-gate demand (`factor = ci if node in CITY_NODES else 1.0`), and
`demand_decomposition_validation.csv` shows that city-gate node demand is *distribution
delivery* — which carries a large amount of embedded commercial and small-industrial load
that the GSOO counts as Industrial and declines at 0.763, not 0.210.

So roughly 240 TJ/d of slow-declining load is being sent down the steep residential curve.
Holding the GSOO's own indices constant and only fixing the buckets closes the 2045 gap
exactly: 95 + 461 + 193 = **749** against the GSOO's 750, versus GARY's actual 495.

**This is why the south never gets short**, why import parity never binds, and therefore
why GARY prices the whole east coast at export netback like ACIL's Brisbane. 

**The separate 158 TJ/d level gap in 2026 is a COVERAGE problem.** GARY's non-GPG domestic
demand is 900 TJ/d against the GSOO's 1,058. Chased 28 Aug 2026; the causes are:

- **Industrial is Gas Bulletin Board only.** `industrial_demand_profile` is built from
  `industrial_facilities_bbg.csv` — **12 facilities, 210 TJ/d**, at just five nodes
  (Gladstone 106, Sydney 61, Adelaide 19, Melbourne 14, Gippsland 11). The GBB registers
  large *transmission-connected* users. Every distribution-connected industrial user is
  therefore either embedded in city-gate demand (and wrongly declined at the ResComm rate,
  above) or absent entirely.
- **No Queensland industrial outside Gladstone.** Brisbane and Surat carry zero industrial
  load. `industrial_facilities.csv` — a hand-built 8-row file listing Incitec Pivot
  Brisbane at 35 TJ/d, Iona Industrial at 15 and Port Kembla Steel — is **dead: no code
  reads it** (see item 10). That load was lost when the GBB-derived file superseded it.
- **Only four city-gate nodes** (Melbourne 340, Sydney 237, Brisbane 64, Adelaide 54).
  Regional NSW / VIC / QLD distribution load has nowhere to sit.
- **No Tasmania node at all**, though the GSOO's ECGM includes TAS. Small — about 1.1% of
  regional RCI peak, so roughly 10 TJ/d — but it is a real omission.

Corroboration that the GSOO's south is genuinely tighter than GARY's: the 2026 GSOO
Figure 5/38 forecasts southern annual supply gaps under Step Change of 1.3 PJ (2029),
12.2 PJ (2030), 28.2 PJ (2031) and 11.4 PJ (2032) with existing, committed and anticipated
supply. GARY's central Step Change case has **zero** shortage in every year.

**FIXED (demand side), 28 Aug 2026.** `build_demand_gsoo.py` now splits the city-gate
bucket between the GSOO's ResComm and Industrial trajectories, and calibrates it to the
GSOO base-year level. Domestic demand now tracks the GSOO within 0.2% in every year:

| TJ/d | 2026 | 2030 | 2035 | 2040 | 2045 |
|---|---|---|---|---|---|
| GARY, before | 1,096 | 960 | 830 | 561 | 496 |
| GARY, after | 1,255 | 1,147 | 1,058 | 819 | **751** |
| 2026 GSOO | 1,253 | 1,145 | 1,057 | 817 | **750** |

The calibration scales the observed GBB city-gate trace by ~1.23, which puts load the
Bulletin Board cannot see — distribution-connected users, regional networks, Tasmania —
onto the four capital-city nodes. Regional load therefore sits in the capitals, and the
scaled series no longer agrees node-by-node with `demand_decomposition_validation.csv`
(that file validates the raw trace).

**Still open:** whether this alone closes the price gap to ACIL. It should push the south
toward needing import cargoes, but that has to be measured on the re-run, not assumed —
three earlier hypotheses about this gap were each tested and each turned out wrong. If
prices are still materially below ACIL after this, supply-side depletion (item 1) is next.


## 10. `industrial_facilities.csv` is dead

An 8-row hand-built file (QAL, Yarwun, Tomago, Whyalla, Orica Kooragang, Incitec Pivot
Brisbane, Iona Industrial, Port Kembla Steel) that **no code reads**. It was superseded by
`industrial_facilities_bbg.csv`, generated from the Gas Bulletin Board.

The switch silently dropped load the old file carried and the GBB does not: Incitec Pivot
Brisbane at 35 TJ/d, Iona Industrial at 15, Port Kembla Steel at 2.7. Brisbane and Surat
still have no metered industrial demand.

**Do NOT simply fold these back in.** Two reasons. The city-gate calibration added under
item 9 now absorbs *all* non-metered industrial load implicitly, so adding a facility to
the generated file without re-deriving that calibration would double count it — the
builder holds metered industrial out of the embedded total, so the arithmetic stays
consistent only if both are regenerated together. And the file is stale: Incitec Pivot's
Gibson Island plant ceased manufacturing at the end of 2022, which is why the GBB no
longer registers it. **Verify each facility is still operating before restoring any of
them.** The safe action is to delete the dead file so it stops looking authoritative.
