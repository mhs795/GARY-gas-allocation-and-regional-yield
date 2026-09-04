# Network expansion candidates

[← back to README](../README.md)

`src/data/expansion_options.csv` is the menu the capacity layer chooses from. Each row is
a real project with a public source. The **`Source`** column records **where the candidate
came from** — `GSOO` if AEMO *names* it in its 2026 GSOO material (the G26 *Field
Developments* sheet for supply, the GSOO / Victorian Gas Planning Report Update project set
for pipelines), `Market` if GARY researched it from public announcements or built it
itself. It is **not** a statement about status: an AEMO-named project tagged `GSOO` may be
Committed, Anticipated, Proposed or Undeveloped in AEMO's own sheet.

That is exactly what the **GSOO expansions only** toggle selects — the AEMO-sourced menu.
Off (the default) it offers that plus everything GARY has researched on top.

Each row also carries a short **`Label`** — the name the dashboard shows in the Expansions
tab and on the map. `Name` stays the stable key that results and lookups join on, so a
label can be reworded without invalidating anything.

**Nothing here is a placeholder.** Where a figure is not public it is labelled as GARY's
own in the row's `Note` and in the tables below — the same discipline the netback deduction
and the foundation share follow.

- [Named in the GSOO — pipelines](#named-in-the-gsoo--pipelines)
- [Named in the GSOO — field developments](#named-in-the-gsoo--field-developments)
- [Named in the GSOO — import terminals](#named-in-the-gsoo--import-terminals)
- [Outside the GSOO (GARY's market scan)](#outside-the-gsoo-garys-market-scan)
- [Turning import terminals off](#turning-import-terminals-off)
- [Four new arcs and one new node](#four-new-arcs-and-one-new-node)
- [What the `Cost` column in `arcs.csv` is](#what-the-cost-column-in-arcscsv-is)

---

## Named in the GSOO — pipelines

| Project | GARY target | Capacity | CapEx | Date | Source |
|---|---|---|---|---|---|
| `ECGG_3A_SWQP` | `SWQP_Rev` | +58 TJ/d | $141m † | Winter 2028 | APA ECGG Stage 3A, FID Feb 2026 |
| `ECGG_3A_MSP` | `MSP` | +10 TJ/d | $24m † | Winter 2028 | APA ECGG Stage 3A |
| `ECGG_3A_Culcairn` | `VNI_Rev` | +39 TJ/d | $95m † | Winter 2028 | APA ECGG Stage 3A, Young–Culcairn lateral |
| `MSEP_Conversion` | `MSP` | +25 TJ/d | $25m | Winter 2026 | Moomba–Sydney Ethane Pipeline converted to gas; NSW approval Oct 2025. APA: total southbound 565 → 590 TJ/d |
| `EGP_Reversal` | `EGP_Rev` **(new arc)** | +200 TJ/d **south** | $220m ‡ | Winter 2026 | Jemena EGP reversal stage 1 |
| `SWP_Compression` | `SWP` | +45 TJ/d | $213m | Winter 2029 | APA rule 80; Irrewillipe + Stonehaven + Winchelsea; Iona injection 570→615 TJ/d; AER approved 2026 |
| `SEA_Gas_Reversal` | `SEA_Gas_Rev` **(new arc)** | 250 TJ/d | $150m ‡ | With Outer Harbor | *Port Campbell to Adelaide pipeline reversal* in AEMO's options report, which publishes **250 TJ/d** and attributes it to the GSOO. **Reclassified from `Market` to `GSOO`** on that finding |

† The three Stage 3A legs share one published $260m. GARY splits it pro-rata by capacity —
the split is GARY's own, the total is APA's.
‡ Not public. GARY's own, at the MSEP conversion unit rate ($1.11m per TJ/d).

> **Two boundary cases worth knowing about.** `SEA_Gas_Reversal` sat under `Market` on
> GARY's own reasoning until AEMO's options report turned up with the same 250 TJ/d and
> its Source column reading GSOO — so the capacity was AEMO's all along and the row moved.
> And `SWP_Compression` was *not* committed in the
> March 2026 GSOO/VGPR — AEMO's text says so explicitly — but the AER approved the $213m
> spend afterwards. It is tagged `GSOO` because AEMO names the project. **The GSOO is a
> snapshot with a cut-off, not a standing fact**, which is why `Source` tracks provenance
> rather than status.

## Named in the GSOO — field developments

Every one of these is a row on AEMO's **G26 *Field Developments*** sheet, extracted to
`data/gsoo/field_developments.csv` by `build_field_developments.py`. Each unlocks part of
its basin's **2C** tranche in `supply.csv`, which cannot produce at all until one is built.

| Project | Basin | Capacity | CapEx | Date | AEMO status |
|---|---|---|---|---|---|
| `Golden_Beach` | Gippsland | 375 TJ/d | **none** ¶ | 2029 | Anticipated |
| `Judith` | Gippsland | 125 TJ/d | **none** ¶ | 2029 | Undeveloped |
| `Bowen_Gas_Project` | Surat/Bowen | 3,148 TJ/d ◊ | **none** ¶ | 2030 | Proposed |
| `Mahalo_CSG` | Surat/Bowen | 50 TJ/d | **none** ¶ | 2026 | Not approved for development (FEED) |
| `Mt_St_Martin` | Surat/Bowen | 22 TJ/d | **none** ¶ | 2027 | Uncertain project |
| `Otway_Annie` | Otway | 12 TJ/d ◊ | **none** ¶ | 2028 | Potential development |
| `Otway_Juliet` | Otway | 12 TJ/d ◊ | **none** ¶ | 2028 | Potential development |
| `Otway_Nestor` | Otway | 10 TJ/d ◊ | **none** ¶ | 2033 | Potential development |
| `Otway_Elanora` | Otway | 9 TJ/d ◊ | **none** ¶ | 2035 | Potential development |
| `Otway_Wobbegong` | Otway | 9 TJ/d ◊ | **none** ¶ | 2037 | Potential development |
| `Beetaloo_Dev` | Beetaloo | 450 TJ/d | **none** ¶ ¤ | — | Appraisal |
| `Beetaloo_Pilot` | Beetaloo | 40 TJ/d | **none** ¶ ¤ | 2026 | Appraisal (pilot figure, not full field) |
| `Carpentaria_Pilot` | Beetaloo | 25 TJ/d | **none** ¶ ¤ | 2025 | Committed |

◊ AEMO publishes no deliverability for this development. See
[`model.md`](model.md#sizing-a-2c-tranches-deliverability) for the sizing rule — AEMO's own
production forecast where one covers the basin, else the 2C/2P resource ratio.

¶ **A field development carries no CapEx, deliberately.** Its capital is already inside
AEMO's blended $/GJ and is left there: the basin's 2C supply row carries the published full
cost, recovered **per GJ as gas is produced**. `build[e]` on one of these rows buys
permission for the tranche to flow, not a physical asset — see
[`model.md`](model.md#a-field-development-is-a-capacity-gate-not-a-capital-decision).

> The capital used to be split out as a lump — `(AEMOFullCost − Cost) × Reserves_PJ` for the
> basin, shared pro rata on deliverability. The numbers were right; the structure was wrong.
> A per-GJ cost recovers capital as gas is produced, but a binary build charges **100% of a
> basin's development capital to reach any of it**. For Surat that was $69.8bn as one
> indivisible decision, and the MIP never took it: Surat 2C sat at 0% used in every scenario.
> `build_field_developments.py --capex` still prints what those lumps would have been, as a
> check that they are no longer being charged.

¤ AEMO publishes no 2P cost for the Beetaloo, so there is nothing to split even for that
report. Its supply row has always carried the full $9.15/GJ, and these three rows have
always carried zero CapEx — they are simply no longer the exception.

> **The Beetaloo rows are useless without `NEAP`.** `Beetaloo_Pipe` is the real Sturt
> Plateau Pipeline (37 km, 40 TJ/d) and the corridor beyond it is capped at 65 TJ/d by the
> Carpentaria southbound leg, which the GSOO says will not be expanded. So of the 515 TJ/d
> these three unlock, only 40 reaches anywhere until APA's North East Australia Pipeline is
> built — and `NEAP` is `Market`, not `GSOO`. A GSOO-only run therefore has the Beetaloo
> gas but not the pipe to move it.

## Named in the GSOO — import terminals

AEMO's **G26 *Processing*** sheet names all four regasification terminals, each with
status *Proposed*. They are `GSOO` on provenance, and the
[import-terminal switch](#turning-import-terminals-off) is the separate control over
whether they may be built at all.

| Project | GARY target | Capacity | CapEx | Date | AEMO row |
|---|---|---|---|---|---|
| `Port_Kembla_Terminal` | `Port_Kembla` | 500 TJ/d | $250m | ≥2027 | *LNG Regasification Terminal - Port Kembla*. Squadron Energy PKET; mechanically complete, FSRU redeployed to Egypt |
| `Viva_Geelong_FSRU` | `Geelong` **(new node)** | 750 TJ/d | $1.0bn ‡ | Winter 2029 | *…Viva Energy Gas Terminal*, Corio Bay; EPBC approval Apr 2026. AEMO: a Geelong terminal lifts total SWP capacity to ~770 TJ/d |
| `Vopak_Victoria_FSRU` | `Geelong` | 750 TJ/d ‡ | $1.0bn ‡ | Pre-winter 2029 | *…Vopak Victoria LNG*, Port Phillip Bay; FSRU secured Sep 2025. **Mutually exclusive** with Viva |
| `Outer_Harbor_LNG` | `Adelaide` **(new supply)** | 422 TJ/d | $900m ‡ | Winter 2028 | *…Outer Harbor LNG Project*, Port Adelaide; 400 mmscfd. ~90 of its ~110 PJ/yr is aimed at Victoria |

## Outside the GSOO (GARY's market scan)

Eight candidates the GSOO does not carry — five pipelines and two field developments
GARY built itself for basins AEMO gives no discrete project for. Four of the pipelines
GARY researched from public announcements; the fifth, `MAPS_Compression`, AEMO *does*
name, but in its options-report consultation rather than in the GSOO, which is what keeps
it on this side of the line.

| Project | GARY target | Capacity | CapEx | Date | Source |
|---|---|---|---|---|---|
| `Bulloo_Interlink` | `Bulloo` **(new arc)** | 800 TJ/d N→S | $220m | End 2028, pre-FID | APA ECGG Stage 3B; new SWQP→MSP link, ~240 km shorter corridor; line pipe purchased |
| `ECGG_VTS_Expansion` | `VNI_Rev` | +93 TJ/d § | $226m ‡ | Winter 2029 | APA ECGG Stage 5; MSP+VTS to 350 TJ/d Young→Wollert |
| `SWP_Looping` | `SWP` | +45 TJ/d | $340m ‡ | 2029 | APA's alternative to `SWP_Compression`: 88 km of looping; more linepack. **Mutually exclusive** with it |
| `MAPS_Compression` | `MAPS` | +52 TJ/d ◊ | $246m ‡ | 2029 ◊ | AEMO's *2025 Gas Infrastructure Options Report* option **MAPS to PCA connection**, compression half only — see below |
| `Cooper_2C` | `Moomba` | 282 TJ/d ◊ | $5.1bn ¶ | 2030 | **GARY's own.** AEMO names no discrete Cooper/Eromanga development, but the basin holds 1,603 PJ of 2C that Figure 27's Uncertain category plainly produces |
| `Amadeus_2C` | `Amadeus` | 47 TJ/d ◊ | $2.0bn ¶ | 2030 | **GARY's own.** The Amadeus fields are "Commercial in confidence" in AEMO's sheet |
| `NGP_Reversal` | `NGP_Rev` | 60 TJ/d | $67m ‡ | 2029 ◊ | Reverse flow on Jemena's NGP. **Was an existing free arc at 106 TJ/d** — see below |
| `NEAP` | `NEAP` **(new arc)** | 200 TJ/d ‡ | $2.0bn ‡ | 2030s, investigation | APA's North to East Australia Pipeline; 1561 km Beetaloo→SWQP, 100% APA. **The only candidate that relieves the Beetaloo corridor** — it bypasses the NGP and the 65 TJ/d Carpentaria southbound leg. APA publishes no capacity; the 50 TJ/d figure in circulation is survey-permit material and implies $40m per TJ/d, so GARY sizes it itself. CapEx at Jemena's NGP unit rate ($800m / 622 km) |

‡ Not public — GARY's own, derived as stated in the row's `Note`.
◊ Sized by GARY where AEMO publishes no figure — see the row's `Note` for the basis.
§ Sized to land GARY's corridor on APA's stated **350 TJ/d** endpoint: 350 less the 218
TJ/d `VNI_Rev` base less `ECGG_3A_Culcairn`'s 39. APA calls the project an ~84% increase,
which implies a current corridor near 190 TJ/d — so the endpoint is the sourced number and
the increment follows from GARY's own base, not the reverse.

## The Moomba–Adelaide corridor

GARY's supply-curve tab put a persistent congestion rent on the **MAPS** — Adelaide's
price sitting $1.32–1.43/GJ above Moomba's plus the $0.97 tariff, on a pipe running at
its 249 TJ/d cap 365 days a year from 2030 to 2039 and still 234 days in 2050. That is
$84–173m a year of scarcity value, and until now the model had **no candidate on the arc
at all**: the rent had nowhere to go, so it simply accumulated.

AEMO does have one. Appendix A2 of the [2025 Gas Infrastructure Options
Report](https://www.aemo.com.au/-/media/files/stakeholder_consultation/consultations/nem-consultations/2025/2025-gas-infrastructure-options-report/final/2025-gas-infrastructure-options-report.pdf)
lists *"Moomba Adelaide Pipeline System to Port Campbell to Adelaide pipeline
connection"* — a dedicated MAPS-to-PCA connection plus compression — sourced to the
options-report consultation rather than to the GSOO, with capacity **not published**.

`MAPS_Compression` is the **compression half only**. The dedicated connection is already
implicit in GARY's topology, where the Adelaide node is exactly where the MAPS meets
`SEA_Gas_Rev`, so a candidate for it would model nothing.

**The capacity is a published endpoint, not a GARY guess.** The SA Department for Energy
and Mining gives the mainline's fully compressed capacity, all seven compressor stations
online, as **~110 PJ/yr**, against **~91 PJ/yr** in its current four-station
configuration. 91 PJ/yr is 249 TJ/d — exactly the [AEMC pipeline
register's](https://www.aemc.gov.au/energy-system/gas/gas-pipeline-register/sa-moomba-adelaide-pipeline-system)
rating and GARY's own arc — so the two figures sit on one basis and the increment
follows: 110 PJ/yr = 301 TJ/d, less the 249 base, is **+52 TJ/d**. It is conservative
against the pipe's own history: Epic Energy's NCC coverage-revocation application put the
system's firm capacity at 348 TJ/d as configured in December 2004, and its maximum at
418 TJ/d.

**The CapEx is GARY's, at the dear end deliberately.** $4.73m per TJ/d, the AER-approved
SWP compression rate ($213m for 45 TJ/d) and the closest analogue in the model — new
compression on an existing southern transmission line. The seven MAPS stations already
exist and four of them run, so recommissioning should cost *less* than greenfield units;
a candidate that relieves the largest congestion rent in the model should not also be
flattered on price. Its earliest year is likewise GARY's convention, not a published
date: AEMO gives none, and 2029 keeps a consultation-stage option from being built next
year.

It is `Market`, not `GSOO` — AEMO names it, but in the options report rather than in the
GSOO material — so a GSOO-only run still has no way to relieve the corridor, which is
itself worth seeing.

## Terminal value on the assets

The capacity MILP charges a project's **whole CapEx, discounted, in the year it is
built** — there is no capital recovery factor in that layer, and the 8%
`capex_annualisation_rate` the dispatch layer charges is a separate proxy that plays no
part in the build decision. Against a 2050 horizon that meant a pipeline commissioned in
2029 paid fifty years of steel for twenty-two years of service and was worth nothing at
the end of them — while the **gas** it was built to move *was* credited at the horizon by
the salvage term. That asymmetry is a bias, not a conservatism: it pushed the model away
from long-lived and late-built infrastructure, the class of candidate a 2050 horizon most
needs to judge fairly.

`AssetLife` in `expansion_options.csv` closes it. A built asset now keeps the part of its
capital the horizon cuts off, valued as the present value at the horizon of its remaining
capital charges:

```
fraction of CapEx credited = (1 − (1+r)^−(life − used)) / (1 − (1+r)^−life)
```

which is the capital recovery factor times the annuity of the remaining years, with the
CRF and the `r` cancelling. It is 1 for an asset built at the horizon, 0 for one that has
lived out its life, and it is **not** straight-line book value — in a model that
discounts, remaining service is worth what it earns, not what it cost. (A 50-year pipe
used 22 years: 88% here, 56% straight-line.) The switch is `asset_salvage` on the
Parameters sheet; `FALSE` reproduces runs from before the credit existed.

| Class | `AssetLife` | Basis |
|---|---|---|
| Pipe in the ground — new pipelines, looping, the MSEP conversion | **50 yr** | The AER's standard asset-life class for transmission pipelines |
| Compression and reversal plant | **25 yr** | ‖ General industry practice for compressor/pumping stations (22–25 yr). **Not an AER figure** — the weakest number here, and the one to change first |
| FSRU import terminals | **20 yr** | ‖ A chartered vessel, not a fixed asset with a pipeline's life |
| Field developments | *(blank)* | **Deliberately none.** Their capital is subsurface, and the gas salvage already values what is left in the ground — crediting them here would pay for the same barrel twice |

‖ GARY's own, disclosed as such.

What it is worth in practice, discounted back to 2025: for a 2029 build, 87.9% of CapEx
remains on a 50-year asset but only 15.5% on a 25-year one and nothing on a 20-year FSRU
— so ~$299m of `NEAP`'s $2.0bn, ~$33m of `Bulloo_Interlink`'s $220m, ~$7m of
`MAPS_Compression`'s $246m. For a **2040** build the ordering changes completely, which
is the whole point: an FSRU built in 2040 now recovers 56% rather than being written off,
and `MAPS_Compression` recovers 72% instead of 16%.

## The Northern Territory

Three corrections on 4 Sep 2026, after an audit against the GBB extracts in `src/data/`.

**`NGP_Rev` was an existing arc at 106 TJ/d and is now a gated project at 60.** The 106 is
the GSOO's figure for the NGP, which describes gas leaving the NT, and it had been applied
to the arc bringing gas *in*. The GBB rates Mt Isa → Tennant Creek at **60 TJ/d nameplate**
and publishes **0.000** for that direction across the whole medium-term outlook, every day
of the short-term outlook, and every month of uncontracted capacity — while the forward
direction runs at 80–90. The AER says the same thing in words: reverse flow into the NT is
*"not a normal operational case… expected to only be utilised in emergencies"* (AAR
2026-31). It was carrying 16,107 TJ north over the horizon in the central case and 157,000
in LNG Low. It is now a candidate like any other reversal. Closes [`TODO`](../TODO.md)
item 5.

**Beetaloo now joins the AGP at Daly Waters, not at Tennant Creek.** The Sturt Plateau
Pipeline ties into the AGP near Daly Waters, ~380 km *north* of Tennant Creek. GARY hung it
straight off Tennant Creek, and its $0.6234 tariff was exactly 382 km at the short-lateral
rate — so the arc was not the SPP at all, it was **a phantom 40 TJ/d copy of the AGP's own
Daly Waters → Tennant Creek section**, running in parallel with it and letting Beetaloo gas
reach the NGP without touching the AGP. `nodes.csv` gains a `Daly_Waters` junction, `AGP_N`
now ends there, a new `AGP_DW` carries on to Darwin, and `Beetaloo_Pipe` is the short tie-in
it always claimed to be. The AGP's posted 0.40 is still split by route length, three ways
now instead of two — which inherits [`TODO`](../TODO.md) item 8's unverified assumption
that 0.40 is a full-haul figure.

**What it changes.** Beetaloo can no longer reach the NGP, because that would need
southbound flow on the AGP and the AGP runs north. AEMO's options report does catalogue a
*Northern Gas Pipeline Beetaloo lateral* — "new pipeline from Beetaloo to NGP" — so the
route east exists as a **candidate**, not as existing infrastructure. It is not in GARY
yet: AEMO publishes no capacity for it, and GARY should not invent one without a basis.

## Turning import terminals off

`allow_import_terminals` on the Parameters sheet, the **Allow LNG import terminals** switch
in the sidebar, and `--no-import-terminals` on the command line all do the same thing: drop
every LNG import terminal from the menu the capacity layer chooses from, so the east coast
has to be supplied from domestic fields and pipe. Scenario keys gain a `_NoImports` segment,
so a run with imports blocked sits alongside its counterpart in the cache rather than
overwriting it.

An import terminal is identified as a `Type == 'Terminal'` row whose `Target` is one of
`import_nodes` — **derived, not flagged**. That is already how `model.py` decides which
supply rows to reprice at ACIL Allen's injection cost, so the two cannot disagree; a second
hand-maintained column could. It drops exactly four candidates:

| dropped | kept (Type=Terminal, but a field, not an import) |
|---|---|

Filtering happens in `filter_expansions`, before either stage sees the candidate set, so
the investment and dispatch layers are offered exactly the same menu.

## Four new arcs and one new node

Three of the strongest southbound candidates had **no path in GARY at all** before this
work, which meant they could not be tested even in principle:

| Added | Why |
|---|---|
| `EGP_Rev` (Sydney→Gippsland) | `EGP` was one-way north. A committed 200 TJ/d southbound path could not be represented |
| `SEA_Gas_Rev` (Adelaide→Melbourne) | `SEA_Gas` was one-way west. An Adelaide FSRU backfilling Victoria had nowhere to flow |
| `Bulloo` (Surat→Moomba) | The Bulloo Interlink is a *new* route, not extra capacity on an existing one. Cost 0.25 vs `SWQP_Rev`'s 0.30 reflects the ~240 km shorter haul — GARY's own |
| `GEE2MEL` + node `Geelong` | A Geelong FSRU lands on the Lara–Brooklyn corridor, not at Iona, so it needed its own node and lateral rather than being folded into `SWP` |

Reversal arcs carry base capacity 0 and exist only if their project is built, so none of
them changes a run in which the project is not selected.

## What the `Cost` column in `arcs.csv` is

**It depends on whether the arc already exists**, and the two classes must not be mixed.

| Arc class | `Cost` is | Capital comes from |
|---|---|---|
| **Existing** (base capacity > 0) | the **posted GSOO reference tariff** | already inside that tariff |
| **New** (base capacity 0, exists only if built) | **variable haulage only**, ~26% of a posted equivalent | `exp_capex` = CapEx × 0.08 |

This follows ACIL Allen, whose GasMark model GARY is calibrated against and whose scenario
assumptions list pipeline tariffs as *"According to 2023 GSOO"* — the same sheet GARY reads.
Their objective maximises producer + consumer surplus *"minus the sum of the transportation,
conversion, and storage costs"*, with transport priced at those tariffs. GasMark has no
pipeline capital term at all because its pipeline set is exogenous; GARY builds pipelines
endogenously, so new arcs need one.

**Why new arcs must stay on variable cost:** their capital is charged once, explicitly,
through `exp_capex`. Putting a capital-recovering posted tariff in `Cost` as well would
charge it twice. `Bulloo`, `EGP_Rev`, `SEA_Gas_Rev` and `NEAP` are all variable-basis.

**Where the tariffs come from.** 20 of the 28 existing arcs map to a published GSOO tariff (the
Victorian DTS at 0.6965 covers `Longford`, `SWP` and `VGP`). `NGP`/`NGP_Rev` are two
published legs in series — NGP plus the Carpentaria northern or southern flow. `AGP_S` and
`AGP_N` split the AGP's single posted 0.40 by route length. The remaining eight are short
laterals with no published tariff, derived at the posted median of **$1.63/GJ per 1000 km**:
the three LNG feeders, Port Kembla (`SYD2PK`), Silver Springs (`SS2Surat`/`Surat2SS`),
Beetaloo (`Beetaloo_Pipe`) and Geelong (`GEE2MEL`).

> **Known bias.** An expansion on an *existing* arc pays that arc's posted tariff on its
> incremental flow as well as its own CapEx, so the existing pipe's capital is recovered
> across more throughput than the tariff was struck for. **It biases against brownfield
> expansion.** After committed projects are forced in, the only materially affected
> candidate is `ECGG_VTS_Expansion`. See [`TODO.md`](../TODO.md) item 1.
