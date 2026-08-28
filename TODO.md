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

## 9. Long-run prices are about half ACIL Allen's

Benchmarked 28 Aug 2026 against ACIL Allen, *Gas, liquid fuel, coal and renewable gas
projections*, 25 February 2025 (report to AEMO), Step Change:

| | ACIL Feb 2025 | GARY |
|---|---|---|
| Brent | US$65 **flat** | 70 → 68 → 63 → **58** |
| Asian LNG | A$11.00 **flat** | 13.99 → 11.27 → 10.50 → **9.99** |
| Early 2030s delivered | $11–13/GJ | ~$7.3–8.4/GJ |
| End of projection | **$14–15/GJ** | **$7.82/GJ** |

Two causes, and the second is most of it.

**The anchors do not match.** `LNG_Anchors` in the parameters workbook is sourced
"ACIL Allen (Nov 2025)", but no such report could be found — the latest ACIL report to
AEMO is Feb 2025, and its Step Change is a FLAT Brent 65 / LNG A$11.00, not GARY's
declining path. Either the source is a non-public vintage or the citation is wrong.
Worth resolving, because the netback is A$ LNG price less the $2.87 export deduction and
therefore maps 1:1 onto every long-run price.

**Nothing in GARY makes prices rise.** ACIL has prices climbing to $14–15/GJ because the
southern cities end up on "LNG netback plus transport and higher cost LNG imports" —
import parity sets their price. In GARY, import nodes ($14/GJ) are almost never marginal:
Surat runs at capacity every year but is still 3,111 TJ/d in 2050, and divertible export
volume covers the rest more cheaply, so price tracks the netback DOWN. Combined with
items 1, 3 and the absence of reserve depletion or cost escalation, GARY has no upward
mechanism at all.

The tell: ACIL's Brisbane "hovers at a price around the LNG netback price", which is
exactly what GARY does — everywhere. GARY reproduces ACIL's Brisbane across the whole
east coast because nothing forces the southern divergence ACIL models.

**To close it:** (a) resolve the anchor provenance; (b) give supply a reserve stock that
depletes and a cost that escalates with it; (c) check why import parity never binds —
if LNG foundation volume is genuinely 93% contracted, the model should not be able to
divert as freely as it does.
