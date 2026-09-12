# GARY — open items

Things known to be wrong, unfinished, or resting on an assumption worth revisiting.
Each entry says what the issue is, how big it is, and what closing it would take.

## CLOSED 29 Aug 2026 — price-responsive demand removed

The mass-market step demand curve and the industrial and GPG raise blocks are gone,
along with build_demand_curves.py and the ten calibration parameters behind them.
Demand is a fixed volume again, with GPG and industrial curtailment at their flat
$22 and $120 strikes and VOLL behind them.

Two of this file's open items go with it: the GPG shed strike sitting 3-15x above
its own raise ladder, and the ladder having become unable to fire at any price the
model produces. Both were symptoms of the same thing -- blocks calibrated against a
reference price that had to be re-struck by hand whenever the model's price level
moved, which the supply-side depletion work then roughly doubled.

Recoverable from git history if it is ever wanted back.

## CLOSED 29 Aug 2026 — items 2, 3, 4 and the contracts input

**Item 2 (Committed projects are not committed) — CLOSED.** `Status == 'Committed'`
rows are now forced to build by their stated year. This also neutralises item 1's
brownfield tariff bias for exactly the projects where it was worst, since a forced
build does not care that its economics are understated.

**Item 3 (`Surat_Potential` is permanently zero) — HALF CLOSED, and the diagnosis
was wrong.** Both potential rows named a `Node` absent from `nodes.csv`, so
`supply_at` (built by iterating `m.Nodes`) never picked them up and their
production appeared in NO balance constraint. `Golden_Beach` could be built and
still deliver nothing — the note here claimed Gippsland was fixed by repointing
`Golden_Beach` at it, but the pointer was never the fault. Both rows now sit at the
real node with `IsPotential=True`, the pattern the import terminals already used.
Golden Beach delivers. **Surat's 3,000 TJ/d is still gated at zero** because no
Terminal targets it — that remains a modelling decision (what unlocks it, at what
capex), but it is now a data row rather than a code change.
>
> **Superseded 30 Aug 2026 — item 3 is now fully closed.** `Bowen_Gas_Project`,
> `Mahalo_CSG` and `Mt_St_Martin` target the Surat node, all three from AEMO's G26
> *Field Developments* sheet, and the capex question is answered the same way as
> every other basin. See item 3 below.

**Item 4 (No earliest-build year) — CLOSED.** `expansion_options.csv` gains
`EarliestYear`, honoured by both models. `terminal_earliest` stays as the fallback
floor for terminals without one.

**`contracts.csv` — DELETED.** Read, threaded through two call layers behind a
meaningless `year <= 2040` gate, assigned to an attribute nothing read. Its MSP and
MAPS baseload minimums were never enforced and its three export rows named nothing
that exists in the model.

## NEW — the storage layer was not physical

Found and fixed 29 Aug 2026, recorded because the size of it is worth remembering.
Nothing required a store to be at any level on day 365 while every year opened at
half full, so the solver emptied all three stores every year and got them back each
January: **46,200 TJ/yr from nothing, about 11% of domestic supply**, at the
$0.50/GJ cycling charge — cheaper than any field in the model. And
`MaxInjection`/`MaxWithdrawal` had sat unread in `nodes.csv` from the beginning, so
Moomba withdrew at 506 TJ/d against a published 120 and injected at 399 into a
store AEMO lists as withdrawal-only.

Measured on two full re-solves before the other fixes landed: closing the year and
enforcing the rates moves the central case **+$0.58/GJ on the system mean** and
+$0.58 on Melbourne in 2050, with no shortage and no change to the build schedule.
So it was a correctness and credibility defect rather than the explanation for the
ACIL gap — but it will matter more in a Winter High run, where storage is marginal
on the days that set the price. **That has not been tested.**

**Still missing from the storage set:** Dandenong LNG (237 TJ/d withdrawal, the
Victorian peaking facility), Heytesbury/HUGS (45 TJ/d), and Roma Underground
Storage (54 PJ, 92/75 TJ/d).

## NEW — the 93% foundation share is extrapolated 25 years past the contracts

`lng_foundation_share` = 0.93 comes from one quarter of ACCC uncontracted-gas data
(22 PJ against ~325 PJ, Q1 2026) and is applied flat to 2050. From about 2035 that
makes GARY export ~1,000 PJ/yr at a netback of $7-8/GJ while Surat's own marginal
cost is above $10 — fifteen years of exporting below cost, because the volume is
must-serve. Faithful to a take-or-pay SPA; increasingly unfaithful to the 2040s,
when the foundation contracts behind that share have largely expired. It is also
what stops the netback disciplining domestic prices after 2035, which is the
opposite of what the mechanism was added to do.

## NEW — 2046-2050 is extrapolation and nothing says so

The GSOO horizon ends at 2045, so every demand index clamps there and holds flat.
ACIL Allen's price series runs to 2050 with real anchors at 2050. The last five
years of every run therefore pair a frozen 2045 demand shape with a moving netback
and a moving import injection cost. The dashboard does not mark them.

## NEW — GARY carries 99.9% of AEMO's 2P but only 80% of its 2C

Missing: Gunnedah/Narrabri (2,156 PJ), Galilee-Drummond (2,788 PJ), McArthur
(2,836 PJ), Bass (135 PJ). ACIL Allen's Step Change assumes Narrabri from 2030.
This is the same absence as item 3: the backfill tranche that makes AEMO's southern
supply hold up rather than collapse.

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

## 2. ~~Committed projects are not committed~~ — FIXED, verified 4 Sep 2026

`expansion_options.csv` has a `Status` column carrying `Committed` / `Pre-FID` / `Proposed`
/ `Built`. **No code reads it.** `already_built` is only the accumulator of what the model
chose in previous years, so it makes a build persist — it does not force anything in.

So a project with FID taken and steel in the ground is optimised on exactly the same terms
as a speculative one, and can simply not be built. `ECGG_3A_MSP` and `EGP_Reversal` are
both committed and both dropped sharply when the tariffs changed (item 1).

**Closed.** `capacity_model.build_committed` now forces every `Status == 'Committed'` row
with an `EarliestYear` to be built by that year, exactly as this item asked. Verified in a
2025-51 central solve: all seven committed rows appear on their stated year —
`Carpentaria_Pilot` 2025, `MSEP_Conversion` and `EGP_Reversal` 2026, the three ECGG 3A legs
2028, `SWP_Compression` 2029. As predicted, this also neutralises the item-1 brownfield bias
for the projects where it bites hardest, because a forced build does not care that its
economics are understated.

## 3. ~~`Surat_Potential` is permanently zero~~ — FIXED 30 Aug 2026

`supply_cap_rule` gates an undeveloped row on a `Type=Terminal` candidate targeting
its node, and nothing targeted Surat, so its 3,000 TJ/d could never be produced.

Fixed by the supply restructure. `expansion_options.csv` now carries **Bowen Gas
Project** (3,000 TJ/d, 2030), plus **Mahalo CSG** (50 TJ/d) and **Mt St Martin**
(22 TJ/d), all three from AEMO's G26 *Field Developments* sheet. The capex question
this item flagged is answered the same way as every other basin: derived from AEMO's
own 2C−2P cost gap over the resource, **$69.8bn** for Surat/Bowen, shared pro rata.
See [`docs/model.md`](docs/model.md#costing-a-development-what-is-published-and-what-is-not).

Bowen Gas Project's 3,000 TJ/d is GARY's own — AEMO records the project as Proposed
with capacity "Not Currently Available" — sized to draw the 23,270 PJ of Surat/Bowen
2C down over the remaining horizon.

> **Superseded 2 Sep 2026 on the capex half.** The $69.8bn lump is gone. A per-GJ cost
> recovers capital as gas is produced; a binary build charged 100% of the basin's
> development capital to reach any of it, and the MIP never took it — Surat 2C sat at 0%
> used in every scenario. The gap now lives in the 2C supply row's `Cost`, reproducing
> AEMO's published full cost, and all three developments carry **zero CapEx**: they gate
> the tranche, they do not buy it. The sizing half stands, at the current 3,148 TJ/d.
> See [`docs/model.md`](docs/model.md#a-field-development-is-a-capacity-gate-not-a-capital-decision).

## 4. ~~No earliest-build year for pipeline candidates~~ — FIXED 4 Sep 2026

`terminal_earliest` (2028) gated Type=Terminal only, so `NEAP` — a 2030s project per APA —
could be and was built in 2026.

**Closed.** `expansion_options.csv` carries a per-row `EarliestYear`, and **both** layers
honour it for every `Type`: `capacity_model._earliest` fixes `build[e, y]` to zero for
`y < EarliestYear`, and `model.build_model` does the same on the myopic path, with
`terminal_earliest` left as the fallback floor for terminals that state no year of their
own. The last gap was `NEAP` itself, whose own row still had the column blank — it now
carries **2030**, APA's stated opening. Audited 4 Sep 2026: no pipeline candidate is left
without a year.

## 5. ~~NGP reversal is modelled as normal supply~~ — FIXED 4 Sep 2026

The AER is explicit that reverse flow into the NT is "not a normal operational case…
expected to only be utilised in emergencies when gas producers are unable to supply gas
into the AGP" (AAR 2026-31). GARY ran `NGP_Rev` as ordinary least-cost supply every year —
16,107 TJ north over the horizon in the central case, 157,000 in LNG Low.

**Closed, and it was worse than this item said.** The arc was also *oversized*: it carried
106 TJ/d, which is the GSOO's figure for the NGP — a number describing gas leaving the NT —
applied to the arc bringing gas in. The GBB extracts already in the repo rate
Mt Isa → Tennant Creek at **60 TJ/d nameplate** and publish **0.000** for it across the
whole medium-term outlook (2026-06-04 to 2028-06-03), 0.000 every day of the short-term
outlook and 0.000 uncontracted every month, while the forward direction runs at 80–90.

`NGP_Rev` is now an ordinary gated reversal like `EGP_Rev`, `SEA_Gas_Rev`, `Bulloo` and
`NEAP`: base capacity **0**, variable-basis tariff, and a new `NGP_Reversal` candidate at
the GBB's 60 TJ/d. An emergency arrangement is now a project the model has to choose, not
the steady state.

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

**FIXED (supply side too), 28 Aug 2026.** Reserve depletion and a two-tranche cost curve
were added from AEMO's own numbers (G26 Reserves Costs assumptions): each basin carries 2P
and 2C reserves in PJ, produces the cheap tranche first, and steps to the 2C cost when it
is gone. Surat runs out of 2P around 2035 on 33,506 PJ of cumulative production and steps
$3.65 -> $6.65; Gippsland steps $5.16 -> $15.76, Otway $7.27 -> $15.62.

Central scenario now, against ACIL Nov 2025 Step Change:

| $/GJ | 2026 | 2032 | 2040 | 2050 |
|---|---|---|---|---|
| Brisbane | 5.55 | 11.34 | 9.61 | **8.36** |
| Sydney | 8.45 | 13.57 | 12.65 | **11.77** |
| Melbourne | 7.57 | 15.64 | 14.19 | **13.13** |
| netback | 10.58 | 8.25 | 7.63 | 7.12 |

Zero shortage in all 26 years. Brisbane sits nearest the netback, Melbourne is the dearest
market, and `Port_Kembla_Terminal` builds — import parity binds, the mechanism ACIL
describes and GARY previously lacked. Moomba, Gippsland and Iona now shrink ECONOMICALLY,
stepping to their 2C cost and pricing themselves out, rather than being cut off.

**A hard reserve cutoff was tried first and was wrong.** Capping deliverability by
remaining 2P+2C drove Iona to zero by 2036 and Moomba by 2048, produced 6,419 TJ of
shortage and Melbourne prices of $104-115/GJ — value-of-lost-load curtailment, not a
market. AEMO's Figure 27 shows why: southern EXISTING production really does collapse on
its numbers, 304 PJ/yr in 2025 to 5 PJ/yr by 2044, but committed and anticipated
developments backfill it to ~280 PJ/yr and hold there. GARY has no backfill (item 3), so a
hard cutoff models the collapse without the replacement. Depletion is therefore carried as
a cost step only.

**This makes item 3 load-bearing, not cosmetic.** Until `Surat_Potential` can produce and
the south has an equivalent of AEMO's committed/anticipated tranche, GARY cannot represent
depletion physically — only as a price signal.

> **Superseded 30 Aug 2026.** Both halves of that are now done and this section is
> history, not current behaviour. The backfill exists — thirteen AEMO field
> developments, extracted from the G26 *Field Developments* sheet — so the hard stock
> limit went back in and holds, with a scarcity rent carrying the opportunity cost of
> depletion into the myopic dispatch layer. There is no cost step any more: each
> tranche is its own row at its own cost, and depletion is physical. See
> [`docs/model.md`](docs/model.md#supply-cost-and-depletion).

**Still open:** the early years. ACIL has ~$12-13/GJ by 2027; GARY has a 6.41 mean in 2026.
See item 11 — the $12 Code price cap is implemented as a ceiling, and ACIL's current view
is that it behaves as a floor.


## 10. ~~`industrial_facilities.csv` is dead~~ — CLOSED 4 Sep 2026

An 8-row hand-built file (QAL, Yarwun, Tomago, Whyalla, Orica Kooragang, Incitec Pivot
Brisbane, Iona Industrial, Port Kembla Steel) that **no code reads**. It was superseded by
`industrial_facilities_bbg.csv`, generated from the Gas Bulletin Board.

The switch silently dropped load the old file carried and the GBB does not: Incitec Pivot
Brisbane at 35 TJ/d, Iona Industrial at 15, Port Kembla Steel at 2.7. Brisbane and Surat
still have no metered industrial demand.

**Closed:** the dead file is gone from `src/data/` and an audit on 4 Sep 2026 found no
reference to it in any module or doc. The warning below stands for anyone tempted to
restore its contents.

**Do NOT simply fold these back in.** Two reasons. The city-gate calibration added under
item 9 now absorbs *all* non-metered industrial load implicitly, so adding a facility to
the generated file without re-deriving that calibration would double count it — the
builder holds metered industrial out of the embedded total, so the arithmetic stays
consistent only if both are regenerated together. And the file is stale: Incitec Pivot's
Gibson Island plant ceased manufacturing at the end of 2022, which is why the GBB no
longer registers it. **Verify each facility is still operating before restoring any of
them.** The safe action is to delete the dead file so it stops looking authoritative.

## 11. The $12 Code cap as ceiling vs floor — REAL, but NOT the near-term gap

**Corrected 29 Aug 2026.** This item previously carried the near-term price gap
with ACIL Allen. It cannot: **the cap never binds in Step Change.** Checked across
the generated series — `Netback_Uncapped_AUD_GJ` equals `Netback_Capped_AUD_GJ` in
all 26 years of Step Change, and in all 26 of Accelerated; it binds only in Slower
Growth, 2040-2050. Step Change's netback starts at $11.12 and falls.

Switching to a floor reading would lift the 2026 netback from $10.58 to $12 and
change nothing downstream, because the contestable spot tail already clears in
full (95 PJ against a 95 PJ ceiling) — a higher bid cannot pull more gas out of
the domestic market than the trains can liquefy.

The near-term gap is a COST BASIS gap instead, and it is now documented in the
README under *What a GARY price is, and when it is not a wholesale price*: before
~2031 no basin has depleted, so every field sits on AEMO's 2P cost (which AEMO
defines as "largely marginal operating costs, royalties and tax") and neither
parity anchor binds, so a 2026 dual is opex plus a tariff — about half a contract
price. Still worth building the floor/ceiling switch as a documented option; just
do not expect it to close anything.

### The original note follows

GARY applies the Code's $12/GJ cap the way the Code is written and the way ACIL's 2023
report described it — as a ceiling on the LNG netback (`code_price_cap`). ACIL's November
2025 report revises that view:

> "Our analysis of the code's operation suggests the price cap has not necessarily acted
> as a price cap, but more like a price floor. Wholesale gas offers and bids have generally
> been made above $12/GJ, with a minimal number of contracts being struck at $12/GJ or
> below. The cap has not acted as a cap. It has acted arguably more like a price floor."

This is the most likely remaining cause of GARY's early-year prices sitting below ACIL's:
they have most markets at $12-13/GJ through to 2027, GARY has a 2026 mean of $6.41.

**To close it:** decide whether GARY should reproduce the Code as written or as observed.
They are different models of the same policy and the difference is worth $5/GJ in the near
term, so it should be a documented switch rather than a silent choice.

## 14. Terminal-year shortage — PAPERED OVER, not fixed

A finite-horizon model exhausts its reserve tranches exactly at the last year it can
see: gas left in the ground past the horizon is worth nothing to the objective. That
last year then absorbs every accounting discrepancy between the capacity layer's
representative days and the dispatch layer's 365 real days, and shows shortage at
value-of-lost-load. Measured 30 Aug 2026: **207,569 TJ** in 2050, against **zero in
2025-2049**.

**Current treatment.** The model SOLVES to `horizon_end` (2051) and REPORTS to
`horizon_report_end` (2050), so the artefact lands in a year nobody reads. The extra
year is not wasted — it sits inside the capacity MIP's foresight, so builds and
scarcity rents are struck against it. But this moves the artefact; it does not remove
it, and 2051 would show the same shortage if anyone looked.

**The proper fix is a terminal salvage value** on reserves remaining at the horizon,
so the objective stops valuing leftover gas at zero. Tried on 30 Aug 2026 and
reverted, for a reason worth recording:

* Anchored on the published landed import cost ($12.29/GJ in 2050), it cut the 2050
  shortage 207,569 -> 139,736 TJ but **collapsed the scarcity rents to a flat $2.26
  for every row** — exactly `df[2050] x 12.29`, the salvage floor. The reserve duals
  went to ~zero, so the salvage term had *displaced* the scarcity signal rather than
  complementing it. Without it the rents differentiate properly: Gippsland $4.26,
  Surat $3.14, Otway $2.75, Cooper $1.45, Amadeus $1.29. A flat rent cannot tell
  nearly-exhausted Otway from abundant Surat, which is the whole point.

So the salvage anchor is too high relative to the duals it has to coexist with. The
next thing to try is a lower, still-sourced anchor — the export netback rather than
landed import parity, or the tranche's own cost.

**Two dead ends already ruled out, so nobody repeats them:**

1. *Normalising the representative-day weights.* A year's weights sum to ~370, not
   365, so the MIP books ~1.4% more production against each reserve than dispatch
   draws. Scaling that to 365 made 2050 **worse** (139,736 -> 480,425 TJ): the
   over-booking makes the MIP conservative, and loosening it let the MIP plan more
   production than dispatch could sustain. The 1.4% errs in the safe direction.
2. *Extending the horizon to 2070 and reporting to 2050.* Mechanically it works, but
   AEMO's demand data ends at **2045** and ACIL Allen's last price anchor is **2050**,
   so it means inventing ~25 years of both — the model's two most important drivers —
   and it would change the 2050 answer rather than clean it up. One extra year is a
   flat hold on published data; twenty-five is a forecast GARY has no basis for.

   > **REOPENED AND ADOPTED 2 Sep 2026, to 2065 — read this before trusting a late
   > year.** The objection above is still right about what it costs, and the cost is
   > now being paid deliberately, because the salvage value that item 14 wanted has
   > landed and brought a worse artefact with it (item 15). Two things changed:
   >
   > * **It is a hold, not a forecast.** Nothing is invented. GARY already held the
   >   GPG and industrial profiles flat from 2045 across 2046-51 and already held the
   >   nearest LNG price outside the published range — `_load_year_profile` clamps,
   >   `load_lng_prices` holds, `datacentre_series.value_for` holds. Annual demand was
   >   the one series that did NOT hold (a bare `Year == year` filter returned an
   >   empty frame, i.e. ZERO demand, the most extreme assumption available, not a
   >   neutral one); it now holds too. The pad is the existing convention run longer.
   > * **Changing the 2050 answer is the point.** Item 15 shows the terminal condition
   >   contaminates roughly the decade before `horizon_end` whatever the salvage price
   >   is. Leaving 2050 one year from the horizon does not preserve a clean answer,
   >   it preserves a contaminated one.
   >
   > **What it costs, stated plainly.** The reported 2050 now rests on ~15 years of
   > flat-held demand and price beyond the published data, and on ~20 years of flat
   > GPG/industrial profile. Reserves are drawn against 41 years of demand instead of
   > 27, so every basin rations harder than it did — that is a real change to the
   > reported window and not a neutral one. A late-horizon result is a statement about
   > the terminal condition as much as about the gas.


## 15. ~~The salvage credit cancels the field cost at the horizon~~ — FIXED 3 Sep 2026

**At `horizon_end` every molecule was worth the backstop regardless of what it cost to
lift.** `get_scarcity_rents` de-discounts the salvage credit, so the rent reached the
full salvage rate at the last solved year. Put that into the production test and `Cost`
dropped out:

```
produce iff   Cost + rent(y) + transport <= price(y)
at horizon:   Cost + (backstop - Cost) + transport <= price
              backstop + transport <= price          <- Cost has cancelled
```

Measured 2 Sep 2026, Step Change central, 2050 wellhead value as `Cost + rent`: Surat 2P
$11.72, Surat 2C $11.92, Moomba 2P $12.04 — three tranches spanning $3.65-8.45/GJ in
lifting cost, all landing on the backstop. Since the backstop is an IMPORT price and the
netback an EXPORT price, exports at the horizon were excluded by construction, at any
cost, including free gas. LNG exports went 901 PJ -> 0 between 2037 and 2038 and never
resumed, against a demand frame planning 1,000-1,134 PJ/yr to 2050.

**The fix, in three nettings.** The credit is now
`(backstop_at_node - Cost) / (1 + r*tau)`:

| netting | what it takes off | Surat 2P |
|---|---|---|
| haul | the run to a regasification terminal — the backstop is a price there, not at a wellhead | $12.29 -> $9.79 |
| extraction | the row's own lifting cost | -> $6.14 |
| **the wait** | **`1/(1+r.tau)`, tau = Reserves/deliverability** | **-> $2.57** |

The third is the one that breaks the cancellation. A stock is not sold at the horizon
instant: it is produced on a decline over `tau` years, and `V = m.S0/(1+r.tau)` is the
closed form of that. Horizon value becomes `Cost*(1-L) + backstop*L`, so a `Cost` term
survives and the tranches separate again -- Surat 2P $6.22, Surat 2C $7.97, Gippsland 2P
$9.36, Iona 2P $10.03, Moomba 2P $10.49. `tau` is what tells a nearly-empty basin from an
abundant one: Iona holds 2.4 years and keeps 86% of its margin, Surat holds 19.8 and
keeps 42%.

**Still approximate, in a known direction.** `tau` is struck on NAMEPLATE reserves and
BASE capacity, so it ignores decline (Surat 2P's real 2051 tau is 25.7 years, not 19.8)
and ignores how much has already been produced. Both understate `tau`, which overstates
the credit -- erring toward the behaviour that was wrong. Tightening it means recomputing
`tau` from the solved remainder and re-solving, which is a second pass nobody has costed.

**Not attempted: closing on the model's own final-year price.** The theoretically right
terminal value is the model's own marginal value at the horizon, not an exogenous
backstop, so that holding and selling are indifferent by construction. That needs a
fixed point and was not tried.



## 16. The reserve limit is a budget, not a stock — the rent has no time structure

**Measured 3 Sep 2026 by the three-horizon acceptance test** (`tmp/horizon_test.py`),
Step Change central, after the tau fix landed. Solve the same scenario stopping at 2051,
2055 and 2060; read only what each says about the window to 2050:

| | 2051 | 2055 | 2060 |
|---|---|---|---|
| Surat rent 2050, $/GJ | 4.17 | 4.93 | **6.18** |
| — salvage part | 2.40 | 1.83 | 1.31 |
| — reserve dual part | 1.77 | 3.10 | **4.87** |
| Last year with exports | 2044 | 2042 | **2040** |
| Total LNG exported to 2050, PJ | 23,101 | 21,245 | **19,358** |
| Surat 2P produced to 2050, PJ | 28,139 | 24,894 | 21,691 |
| Surat 2C produced to 2050, PJ | 2,154 | 3,529 | 4,828 |
| Shortage, TJ | 0 | 0 | 0 |

**The test fails, monotonically, and does not converge.** The salvage half decays with
distance exactly as a terminal effect should — the tau and transport fixes work. The
DUAL half nearly triples, and its increments grow rather than shrink.

**The cause, and the smoking gun.** `reserve_limit` is one constraint per tranche
summing production across the whole horizon: `sum_y q(y) <= Reserves`. One constraint,
one dual, and that single number prices every year alike. `get_scarcity_rents` divides
by each year's discount factor, so the cash rent *must* grow at exactly the discount
rate. Measured: **7.000%/yr in all three runs, to three decimals**, identical to
`discount_rate_default`. The rent path's shape is fixed before any data is read.

So the rent carries no information about WHEN the stock runs down, only about how tight
the budget is over the window solved. Add years of demand against the same fixed
reserves and the budget tightens, so the whole path lifts. There is no mechanism for a
terminal condition's influence to decay with distance, which is precisely what
truncation relies on.

**The fix is to give the capacity MIP a per-year stock, the way dispatch already has
one.** `_declined_capacity` carries `cumulative_pj` forward and hard-stops an exhausted
row; the MIP is the only layer without that. With

```
S(t+1) = S(t) - q(t)          q(t) <= S(t) / tau
```

scarcity binds locally, where the stock has actually run down, and the terminal value's
influence decays as `(1+r)^-(T-t)` — geometric, which is what makes a longer horizon
converge instead of merely moving. It also removes the reason the scarcity rent was
invented: the rent exists to reconcile a budget-holding MIP with a stock-holding
dispatch, and giving both a stock makes them agree structurally.

Cost: ~300 extra variables and constraints (trivial to solve), but it changes every year
of every result. Not attempted. The same three-horizon test decides whether it works.

**Do not read a late-horizon result as an economic finding until this is closed.** The
2050 export path is a statement about where the horizon was placed.

### Item 16, addendum 3 Sep 2026 — the terminal value cannot fix this, measured

The obvious next move after the tau fix was to measure tau on the stock ACTUALLY LEFT at
the horizon rather than on nameplate reserves, iterating to a fixed point (nameplate asks
how long a tranche would take to sell if none of it had been produced -- the wrong
question at the horizon, where Surat 2P finishes with 772 PJ of 28,911 left). Built it,
ran it, and it is a dead end. Recorded so nobody rebuilds it.

It converges properly -- damped 50%, tau moves 1.48, 0.74, 0.37, 0.19 years over four
passes -- and it is the better specification. It changes nothing: **exports 23,101 ->
23,070 PJ, every scarcity rent identical to two decimals**, for 40% more solve time.

**Why, and this is the finding.** The salvage credit and the reserve dual are
**perfectly substitutable**. The credit is `rate x (Reserves - sum q)`, whose only
non-constant part is `+rate` per unit produced; the constraint contributes `+lambda` per
unit at the margin. The MIP picks total production where the marginal cost clears, so
lowering `rate` simply raises `lambda` one for one:

| run | salvage rate | salvage @2050 | reserve dual @2050 | TOTAL rent |
|---|---|---|---|---|
| shipped, gross backstop | 8.64 | 8.07 | 0.00 | **8.07** |
| nameplate tau | 2.57 | 2.40 | 1.77 | **4.17** |
| fixed point, measured tau | 0.00 | 0.00 | 4.17 | **4.17** |

The tau fix mattered only because it moved the credit from a level where the reserve
constraint was SLACK -- lambda exactly 0.00, the model hoarding, 72% of 2P drawn -- to one
where it BINDS. Once binding, the total is pinned by the budget and further reductions in
the credit are absorbed. There is a floor, and no terminal value reaches below it.

**So the horizon sensitivity is the constraint's shape, not the terminal value's level**,
and item 16's fix stands: give the MIP a per-year stock. `sum q <= R` over a window makes
gas produced in 2025 and 2050 perfect substitutes against one budget, with no notion of
WHEN the stock runs out, so the budget's tightness -- and hence the whole rent path -- is
set by how many years were solved.

**One caveat on that fix, stated honestly.** Part of this may not be a bug at all. If
demand over a longer window genuinely exceeds recoverable reserves then gas genuinely IS
scarcer, and the 2050 price genuinely does depend on post-2050 demand, which GARY has no
source for. A per-year stock would make depletion physical and time-specific, which is
worth having on its own merits (requirement B), but it will not make the answer
independent of an assumption nobody has yet written down.

The iteration is left in the code and OFF (`salvage_max_passes = 0`). Turn it on if
`reserve_limit` is ever replaced by a per-year stock, where the substitution above no
longer holds.


## 17. AEMO's reserves and its decline rates do not reconcile

**Measured 3 Sep 2026.** Follow each 2P field along AEMO's own `DeclineRate` from its
own nameplate capacity, and total what it produces over 2025-50. Compare that with the
reserves AEMO states for the same field:

| tranche | PJ/yr | decline | 26yr output | reserves | ratio |
|---|---|---|---|---|---|
| Surat 2P | 1,460 | 1.0% | 33,574 | 28,911 | **1.2x** |
| Amadeus 2P | 20 | 3.0% | 366 | 230 | **1.6x** |
| Gippsland 2P | 280 | 12.0% | 2,246 | 1,108 | **2.0x** |
| Moomba 2P | 146 | 3.0% | 2,662 | 850 | **3.1x** |
| Iona 2P | 128 | 0.0% | 3,322 | 304 | **10.9x** |

Every field's decline curve would produce more gas than the field holds -- Iona by
nearly eleven times. Both numbers are AEMO's, from the same 2026 GSOO supply workbook.

**This is not a bug in GARY, and GARY already survives it**, because `_declined_capacity`
caps production at the remaining stock. But it means something worth knowing: **the
reserve limit is what binds, and the decline curve is close to decorative** for the
southern fields. Depletion in GARY has always been reserve-driven, not decline-driven.

**Where it bites.** It forces a choice about the SHAPE of the production path that
neither input settles, and the two available shapes are both wrong at the ends:

* **Plateau-then-cliff** (what GARY does now). Produce flat at capacity, hit the reserve
  wall, stop. Trusts the decline rate for the early path and the reserves for the total.
* **Decline-from-day-one** (`prod <= stock/tau`, built and measured 3 Sep 2026, then deleted).
  Smooth, physical, and far too aggressive: 1/tau is 5x AEMO's decline for Surat and
  infinitely faster for Iona, which AEMO holds flat. Southern supply collapses early and
  QCLNG foundation contracts go 134,496 TJ short over 2029 and 2033-35.

A real reservoir does neither. It holds a plateau set by facility capacity, then declines
when pressure can no longer sustain it. **Implementing plateau-then-decline needs a
threshold -- the reserves-to-production ratio at which plateau ends -- and neither AEMO
input supplies one.** Deriving it from the decline rates means building on the half of
the data that does not reconcile with reserves; picking it by which value makes the
export path look right is fitting the model to a conclusion.

**Open question for the modeller, not for the code:** when the two conflict, which does
GARY trust? Everything about the shape of late-horizon supply follows from that answer.


### Item 17, addendum — what the deleted experiment established

The per-year-stock branch was removed on 3 Sep 2026 to keep the model simple. Two
things it proved are worth keeping, because they would otherwise be rediscovered the
hard way:

1. **The architecture works.** Replacing the horizon-wide budget with
   `stock_open` / `stock_balance` / `stock_close` plus a deliverability rule tied to
   remaining stock builds and solves cleanly in about the same time. Nothing about the
   MIP resists it.
2. **The rent must be split, and the split is knowable.** Once deliverability depends on
   the stock, stationarity gives `mu(y-1) = mu(y) + nu(y)/tau`, so the stock dual
   ACCUMULATES every future deliverability dual. Handing that whole number to the myopic
   dispatch layer double-counts: measured at $17.71/GJ of rent on $3.65 gas, pricing
   Surat at $21.36 in 2025 against $7.35. The fix is to give dispatch the same
   deliverability rule and pass only `kappa + salvage` -- the dual on "cannot overdraw
   the tranche" plus the terminal credit, which is the part with no deliverability in it.
   That restored Surat 2025 to $6.88.

What killed it was the parameterisation, not either of those: `tau` from the INITIAL
reserves-to-production ratio forces exponential decline from year one at up to 5x AEMO's
own rates (infinitely faster for Iona, which AEMO holds flat), no field can hold a
plateau, southern supply collapses and QCLNG foundation contracts go 134,496 TJ short
over 2029 and 2033-35.

**So anyone picking this up again does not start from scratch — they start from the
plateau threshold question above, which is the only thing still unanswered.**

## 18. Parallel arcs priced on different bases strand the existing pipe — NEW 4 Sep 2026

`SWQP_Rev` and `Bulloo` both run Surat → Moomba. Both are real pipes, so the parallel
topology is right: `Bulloo` is APA's ECGG Stage 3B interlink, a genuinely separate and
~240 km shorter route. What is wrong is that the dispatch compares their tariffs directly
when the two are struck on **different bases** — `SWQP_Rev` carries the posted $1.5265,
which recovers the existing pipe's capital, and `Bulloo` carries variable-only $0.2500,
because its capital is charged separately through `exp_capex`. Six times cheaper, and only
partly because it is shorter.

The consequence is visible in every run. Measured on the central case, 4 Sep 2026:

| Year | `SWQP_Rev` | `Bulloo` |
|---|---|---|
| 2028 (before Bulloo) | 99.7 TJ/d | — |
| 2030 | **0.0** | 755.9 TJ/d |
| 2035 | **0.0** | 787.2 TJ/d |
| 2050 | **0.0** | 583.7 TJ/d |

A 512 TJ/d existing pipeline is abandoned for twenty-one straight years by an artefact of
pricing convention rather than by economics.

This is a **sibling of item 1, not the same thing.** Item 1 is about *expanding* an
existing arc and over-recovering its capital. This is about *dispatching* between two
parallel arcs where one price includes capital and the other excludes it.

**Not fixed, deliberately.** The clean answer is that dispatch should see variable cost on
every arc, with capital charged only through `exp_capex` — existing capital is sunk and a
dispatch model should not re-decide it. But GARY uses posted tariffs for existing arcs
because ACIL Allen does, and it is calibrated against them; moving every arc to a variable
basis would change every price in the model and break that calibration. That is a
methodology decision, not a bug fix. `tests/audit_inputs.py` fails on it so it cannot be
forgotten.

## 19. ~~Day 366 was built and never dispatched~~ — FIXED 4 Sep 2026

`demand_profiles.csv`, the empirical GBB shape, spans a leap year and carries a **day
366**. The builder passed it through, so `demand_<scenario>.csv` held 366 days for every
node except Darwin — which is built with an explicit `range(1, 366)` and so escaped it.
The dispatch model solves `RangeSet(1, 365)`. The extra day was therefore written, never
solved, and silently discarded: **114.4 PJ across the horizon, 0.31% of all demand**, of
which 103 PJ was LNG.

Fixed in `build_demand_gsoo.to_365`, which folds the trace onto 365 days by scaling each
node's days 1-365 so its **annual total is preserved** — the right way round, because every
sector is calibrated to a GSOO annual level while the shape is empirical. What the model
actually dispatches moved +0.034%; no series is defined off the solved horizon any more.

## 20. ~~The 2C backfill tranche was barred from the LNG trains~~ — FIXED 12 Sep 2026

Both layers wrote export eligibility as

```python
commercial_at_source = [s_ for s_ in m.Supply if s_[0] in lng_source and not s_[1]]
```

and `not s_[1]` is `not IsPotential`. The filter was written to keep the free reserved
tranche out of the export stream, and it never did that: the reserved gas is
`reserved_prod`, a **separate variable**, excluded simply by not appearing in the sum.
What the filter actually excluded was **Surat's 2C row** — 23,270 PJ, the Bowen Gas
Project the capacity layer builds in 2030 — from the three feed pipes.

So exports could be supplied only by the **depleting** 2P tranche plus whatever transited
into Surat up the SWQP and from Silver Springs. The backfill could backfill a domestic
customer and never an export, and once 2P was drawn down the trains had nowhere to turn.

**Where it bit.** Nowhere in Step Change Medium: measured 12 Sep 2026, an eligibility-only
run reproduces the shipped export path to the PJ in every year, because Surat 2C at AEMO's
$6.65/GJ full cost plus a feed-pipe tariff is above the Step Change netback anyway and the
model would not have chosen it. It bit in **LNG High**, where the netback is at the $12
Code cap: exports there fell to 134 PJ in 2047-48 and 55 PJ in 2049-50 — the transit
inflows (SWQP 365 + SS2Surat 150 TJ/d) and nothing else, with Surat 2P exhausted and 15,315
PJ of 2C sitting unused behind a constraint that could not see it.

**Measured after the fix, LNG High** (with item 21, which does not bind at a $12 netback):

| | before | after |
|---|---|---|
| 2044 | 1,322 | 1,343 |
| 2045-46 | 901 | **1,343** |
| 2047-48 | 134 | **1,343** |
| 2049-50 | 55 | **1,343** |
| total 2025-50 | 28,960 PJ | **34,928 PJ** |

Full planned volume every year to 2050, shortage zero in every year. **+5,968 PJ, +21%.**

Fixed in `model.py` and `capacity_model.py` by dropping the `not s_[1]`. The reserved
tranche is still excluded, by the same mechanism it always actually was.

## 21. Terminal value struck on a route with no capacity behind it — FIXED 12 Sep 2026

`_backstop_at_wellhead` valued gas left in the ground at the dearest place it could go
less the **tariff** to get there, and never asked how much could go. For Surat that is a
southern regasification terminal: $12.29 landed less $2.50 of haul = **$9.79/GJ**. The
tariff is right. The capacity is 349 TJ/day — 249 on the cheapest run to Adelaide, 100 to
Port Kembla — against a Surat that can deliver **6,300**. The old rule paid the whole basin
a price 6% of its gas could reach.

The other 94% can reach a liquefaction train, at the export netback less the feed-pipe
tariff: **$6.37/GJ** in 2051. The gap to $9.79 is the liquefaction-shipping-regasification
wedge, and GARY already carries both sides of it — exports at ACIL Allen's netback, imports
at ACIL Allen's injection cost. The terminal value was using only the dearer one, which is
the asymmetry `_salvage_rate`'s own docstring warned about ("the backstop is an import
price and the netback an export price, so it excludes exports at the horizon by
construction") without closing it.

`_realisable_backstop` enumerates every disposal route a node has, takes each route's
**bottleneck** capacity from `_least_cost_paths(with_capacity=True)`, fills them dearest
first out of the node's own deliverability, and returns the capacity-weighted price. Surat
comes out at $6.37 rather than $9.79.

**Only the basins feeding the trains are re-struck, and that restriction is the point.** A
basin inside the market it supplies — Gippsland, Otway, Cooper — has ONE disposal price,
and the haul in `_backstop_at_wellhead` is a proxy for where it sits rather than a corridor
it has to fit through. Blending a long-haul export route into those nodes reads a
bottleneck into a market they are already in (measured: Gippsland $10.53 → $7.59, Otway
$10.49 → $9.19, purely from routes those basins would never use). Surat is the only node
with two materially different disposal prices and a real corridor between it and the dearer
one.

Behind `salvage_route_capacity` on the Parameters sheet, ON. Properties checked in
`tests/check_realisable_backstop.py` (no solve).

**Why this is not item 16's Option E.** Option E showed the 2P terminal credit and the 2P
reserve dual are perfectly substitutable, so no *level* of the 2P credit changes the total.
This changes a different row: **Surat 2C's credit goes $1.32 → $0.00**, and 2C's reserve
dual is exactly zero (91% of the tranche is unproduced at the horizon), so there is nothing
to absorb it. What the $1.32 was doing was holding the backfill tranche *above* the
depleting tranche it exists to replace.

**It does NOT improve the horizon sensitivity, measured both ways 12 Sep 2026.** Running
the acceptance test with the stop year moved 2051 -> 2055 and still reporting to 2050:

| | old terminal value | route-capacity credit |
|---|---|---|
| total exports 2025-50 | +1.8% | -2.0% |
| last year with LNG exports | 2044 -> 2045 | 2046 -> 2045 |

Same magnitude, opposite sign. Expected on reflection: the horizon's grip is
`reserve_limit` being ONE budget spread across every year solved (item 16), not the level
of the terminal credit — which is the same thing item 16's Option E said. **Do not compare
either column with the 8.0% in docs/depletion.md issue 1**; that was measured on an earlier
code vintage and is not like-for-like.

## 22. Beetaloo's terminal credit is struck on a price it has no pipeline to — NEW 12 Sep 2026

`_least_cost_paths` deliberately walks only arcs that have capacity **today**, because a
reversal or a greenfield route the model has not built is not a path a molecule can take.
Every route out of the Beetaloo is exactly that: `NEAP` (Beetaloo→Surat) and the Sturt
Plateau tie-in both sit at base capacity 0. So the node reaches no regasification terminal,
`_backstop_at_wellhead` falls through to its "no route" branch, and Beetaloo 2C is credited
against the **gross** $12.29/GJ landed import price — a price at an Adelaide terminal it has
no pipe to. Terminal credit $1.08/GJ, rent $1.01/GJ at 2050.

Same class of error as item 21 and worse in kind: a narrow route there, no route at all
here. Not fixed with item 21 because the two want different answers. Surat's fix is to
blend the routes it HAS; the Beetaloo's question is whether an *unbuilt* route counts, and
the honest answer is "only if the model builds it" — which makes the terminal value depend
on the build schedule and so on the solve it feeds.

**Small, for now.** Beetaloo produces 144 PJ over the whole horizon in the central case
(2.8% of its 5,109 PJ 2C), so nothing reported turns on it. It would stop being small in
any scenario that opens the NT corridor.

**To close it:** either credit an unrouted basin at zero (conservative, and consistent with
"gas you cannot move is not worth holding"), or walk `expansion_options.csv` capacity as
well as base capacity and accept the build-schedule dependence. The first is a one-line
change and a defensible reading; the second is more right and more work.

## 22b. The export tail is decided by `export_netback_deduction`, not by the model — MEASURED 12 Sep 2026

After items 20 and 21 the central case still stops exporting in 2046, and the reason is not
a defect: with **every rent set to zero**, Surat 2C at AEMO's published $6.65/GJ full cost
plus the cheapest feed-pipe tariff ($0.754) is **$7.40 against ACIL Allen's $7.12 Step
Change netback in 2050**. 2P pays at every train in every year but is finite, so the
marginal Queensland molecule in the 2040s IS 2C whatever the rent formulation does.
Crossings at zero rent: QCLNG 2035, APLNG 2044, GLNG 2045.

Both numbers are AEMO-commissioned and neither is GARY's. **AEMO never tests them against
each other** — the GSOO supply forecast is producer-submitted project data, not an economic
optimisation. Its own Figure 45 shows a zero Queensland supply gap every year to 2045,
while Figure 28 shows **42% of 2045 northern production (425.6 of 1,017.8 PJ) classified
"Uncertain"**, up from 0.3% in 2026. AEMO is counting gas that exists; GARY is asking
whether it pays to drill it.

**And the whole residual sits inside the one number nobody publishes.**
`export_netback_deduction` is the midpoint (A$2.872/GJ) of the public US$1.5-2.5/mmbtu
range, which at ACIL Allen's own FX 0.66 and C 1.055 is **A$2.154-3.590 — plus or minus
72c**. Re-solved the whole horizon at the LOW end, nothing else changed:

| year | GSOO path | deduction at midpoint | deduction at low end |
|---|---|---|---|
| 2043 | 1,000 | 948 | 1,343 |
| 2045 | 1,000 | 886 | 935 |
| 2046 | 1,000 | 406 | 901 |
| 2047 | 1,000 | **0** | **901** |
| 2050 | 1,000 | **0** | **880** |
| total 2025-50 | 30,427 PJ | 24,656 PJ | **31,840 PJ** |

Shortage zero in every year of both runs. The low-deduction run runs the trains to 2050 and
comes out ABOVE the GSOO path, because the spot tail fills spare liquefaction capacity in
the 2040s rather than merely meeting planned volume.

**So the export tail is not a model result. It is an assumption about avoidable
liquefaction cost wearing a model's clothes.** Quote it as a range or not at all. This is
the parameter sheet's own instruction being followed: "a CHOICE, NOT A SOURCE - the first
number to test if a netback result matters."

## 23. The 2P rent crosses its own backstop ceiling in the last years — NEW 12 Sep 2026

**A cheap diagnostic for item 16, and it fails.** In a two-tranche basin with an
exhaustible cheap row and an abundant dearer row behind it, the depletion rent on the cheap
row has a hard economic ceiling: **the cost of replacing that gas from the next tranche.**
Nobody pays a premium for 2P gas larger than the extra it costs to lift 2C gas instead. So

```
rent(node, 2P, y)  <=  Cost(node, 2C) - Cost(node, 2P)
```

wherever the 2C row is developable and still holds stock. For Surat that ceiling is
**$6.65 - $3.65 = $3.00/GJ**, and it applies from 2030, when the capacity layer builds the
Bowen Gas Project.

Measured 12 Sep 2026 after items 20 and 21, Step Change / Winter Medium / LNG Medium:

| year | 2P rent | ceiling | |
|---|---|---|---|
| 2045 | 2.70 | 3.00 | ok |
| 2046 | 2.89 | 3.00 | ok |
| 2047 | 3.09 | 3.00 | **over** |
| 2050 | 3.79 | 3.00 | **over by 26%** |

**This is item 16's shape problem made visible, not a new defect.** `reserve_limit` is one
constraint over the whole horizon, so it has one dual, and `get_scarcity_rents` spreads it
as `lambda / df(y)` — a path that grows at exactly the discount rate whatever the gas is
doing. It can therefore be right in the years 2P is genuinely on the margin against 2C
(the mid-2040s, where it lands within a few cents of $3.00) and wrong either side of them
by construction.

**Nothing turns on it at all — measured 12 Sep 2026, and this corrects an earlier guess in
this item that it reached prices.** Surat 2P is exhausted from 2047 in the central case, so
a zero-production row is never marginal and its rent prices nothing. The Surat dual pins at
exactly **$6.65/GJ from 2047 to 2050** — Surat 2C's cost, flat — while the 2P rent climbs
to $3.79. Brisbane sits at $7.35 and Gladstone $7.90 on the same flat line.

That is also the textbook answer arriving on its own: once the cheap grade is gone the
price IS the backstop cost, with no rent on top. So the over-ceiling tail is a reporting
blemish in `get_scarcity_rents`, not an error in any number GARY publishes.

**Do not fix this by clamping the rent.** The rent exists to reconcile a budget-holding MIP
with a stock-holding dispatch layer; capping it lets dispatch draw a tranche faster than
the plan assumed, which is the failure mode the rent was introduced to cure (758 PJ/yr of
shortage over 2047-50). The ceiling is a **test**, not a constraint — the right use of it
is as an acceptance criterion on item 16's per-year stock, which should satisfy it without
being told to.
