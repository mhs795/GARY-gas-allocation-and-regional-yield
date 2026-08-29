# Network expansion candidates

[← back to README](../README.md)

`src/data/expansion_options.csv` is the menu the capacity layer chooses from. Each row is
a real project with a public source; the `Source` column records whether AEMO counts it in
the **2026 GSOO / Victorian Gas Planning Report Update** as *committed*, or whether it came
from GARY's own market scan and sits outside that boundary (pre-FID, proposed, or committed
after the GSOO's cut-off).

Each row also carries a short **`Label`** — the name the dashboard shows in the Expansions
tab and on the map. `Name` stays the stable key that results and lookups join on, so a
label can be reworded without invalidating anything.

**Nothing here is a placeholder.** Where a figure is not public it is labelled as GARY's
own in the row's `Note` and in the tables below — the same discipline the netback deduction
and the foundation share follow.

- [In the GSOO (committed)](#in-the-gsoo-committed)
- [Outside the GSOO (GARY's market scan)](#outside-the-gsoo-garys-market-scan)
- [Turning import terminals off](#turning-import-terminals-off)
- [Four new arcs and one new node](#four-new-arcs-and-one-new-node)
- [What the `Cost` column in `arcs.csv` is](#what-the-cost-column-in-arcscsv-is)

---

## In the GSOO (committed)

| Project | GARY target | Capacity | CapEx | Date | Source |
|---|---|---|---|---|---|
| `ECGG_3A_SWQP` | `SWQP_Rev` | +58 TJ/d | $141m † | Winter 2028 | APA ECGG Stage 3A, FID Feb 2026 |
| `ECGG_3A_MSP` | `MSP` | +10 TJ/d | $24m † | Winter 2028 | APA ECGG Stage 3A |
| `ECGG_3A_Culcairn` | `VNI_Rev` | +39 TJ/d | $95m † | Winter 2028 | APA ECGG Stage 3A, Young–Culcairn lateral |
| `MSEP_Conversion` | `MSP` | +25 TJ/d | $25m | Winter 2026 | Moomba–Sydney Ethane Pipeline converted to gas; NSW approval Oct 2025. APA: total southbound 565 → 590 TJ/d |
| `EGP_Reversal` | `EGP_Rev` **(new arc)** | +200 TJ/d **south** | $220m ‡ | Winter 2026 | Jemena EGP reversal stage 1 |
| `SWP_Compression` | `SWP` | +45 TJ/d | $213m | Winter 2029 | APA rule 80; Irrewillipe + Stonehaven + Winchelsea; Iona injection 570→615 TJ/d; AER approved 2026 |

† The three Stage 3A legs share one published $260m. GARY splits it pro-rata by capacity —
the split is GARY's own, the total is APA's.
‡ Not public. GARY's own, at the MSEP conversion unit rate ($1.11m per TJ/d).

> **A boundary case worth knowing about.** `SWP_Compression` was *not* committed in the
> March 2026 GSOO/VGPR — AEMO's text says so explicitly — but the AER approved the $213m
> spend afterwards. GARY tags it `GSOO` because it is now committed. It is the clearest
> illustration of why the toggle exists: **the GSOO's committed set is a snapshot with a
> cut-off, not a standing fact.**

## Outside the GSOO (GARY's market scan)

| Project | GARY target | Capacity | CapEx | Date | Source |
|---|---|---|---|---|---|
| `Bulloo_Interlink` | `Bulloo` **(new arc)** | 800 TJ/d N→S | $220m | End 2028, pre-FID | APA ECGG Stage 3B; new SWQP→MSP link, ~240 km shorter corridor; line pipe purchased |
| `ECGG_VTS_Expansion` | `VNI_Rev` | +93 TJ/d § | $226m ‡ | Winter 2029 | APA ECGG Stage 5; MSP+VTS to 350 TJ/d Young→Wollert |
| `SWP_Looping` | `SWP` | +45 TJ/d | $340m ‡ | 2029 | APA's alternative to `SWP_Compression`: 88 km of looping; more linepack. **Mutually exclusive** with it |
| `Viva_Geelong_FSRU` | `Geelong` **(new node)** | 750 TJ/d | $1.0bn ‡ | Winter 2029, FID H2 2026 | Viva Energy Gas Terminal, Corio Bay; EPBC approval Apr 2026. AEMO: a Geelong terminal lifts total SWP capacity to ~770 TJ/d |
| `Vopak_Victoria_FSRU` | `Geelong` | 750 TJ/d ‡ | $1.0bn ‡ | Pre-winter 2029 | Vopak Victoria Energy Terminal, Port Phillip Bay; FSRU secured Sep 2025. **Mutually exclusive** with Viva — AEMO states the two behave similarly for the DTS |
| `Golden_Beach` | `Gippsland_Potential` | 375 TJ/d | $600m ‡ | Late 2029, FID H2 2026 | GB Energy Golden Beach Energy Storage; 125 TJ/d production late 2028 → 300 → 375 TJ/d; 42 PJ store |
| `Outer_Harbor_LNG` | `Adelaide` **(new supply)** | 422 TJ/d | $900m ‡ | Winter 2028 | AG&P Outer Harbor FSRU, Port Adelaide; 400 mmscfd. ~90 of its ~110 PJ/yr is aimed at Victoria (60 PJ to Iona + 30 PJ to the Port Campbell pipeline) |
| `SEA_Gas_Reversal` | `SEA_Gas_Rev` **(new arc)** | 300 TJ/d | $150m ‡ | With Outer Harbor | SEA Gas compression + reverse flow on the Port Campbell–Adelaide pipeline, to move Outer Harbor gas east |
| `Port_Kembla_Terminal` | `Port_Kembla` | 500 TJ/d | $250m | ≥2027 | Squadron Energy PKET; mechanically complete, FSRU redeployed to Egypt |
| `NEAP` | `NEAP` **(new arc)** | 200 TJ/d ‡ | $2.0bn ‡ | 2030s, investigation | APA's North to East Australia Pipeline; 1561 km Beetaloo→SWQP, 100% APA. **The only candidate that relieves the Beetaloo corridor** — it bypasses the NGP and the 65 TJ/d Carpentaria southbound leg. APA publishes no capacity; the 50 TJ/d figure in circulation is survey-permit material and implies $40m per TJ/d, so GARY sizes it itself. CapEx at Jemena's NGP unit rate ($800m / 622 km) |
| `Beetaloo_Dev` | `Beetaloo` | 450 TJ/d | $900m | Proposed | Beetaloo development. **Without `NEAP` this field cannot deliver more than 40 TJ/d anywhere** — `Beetaloo_Pipe` is the real Sturt Plateau Pipeline (37 km, 40 TJ/d, $66.5m, in service 2026) and the corridor beyond it is capped at 65 TJ/d by the Carpentaria southbound leg, which the GSOO says will not be expanded |

‡ Not public — GARY's own, derived as stated in the row's `Note`.
§ Sized to land GARY's corridor on APA's stated **350 TJ/d** endpoint: 350 less the 218
TJ/d `VNI_Rev` base less `ECGG_3A_Culcairn`'s 39. APA calls the project an ~84% increase,
which implies a current corridor near 190 TJ/d — so the endpoint is the sourced number and
the increment follows from GARY's own base, not the reverse.

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
| `Port_Kembla_Terminal`, `Viva_Geelong_FSRU`, `Vopak_Victoria_FSRU`, `Outer_Harbor_LNG` | `Golden_Beach`, `Beetaloo_Dev` |

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
