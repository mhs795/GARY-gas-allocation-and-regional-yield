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

## 9. Long-run prices are about half ACIL Allen's — the south never gets short

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

**What is still open.** GARY's south simply is not short: by 2050 domestic east-coast demand
is roughly 400 TJ/d against 3,423 TJ/d of production, and Gippsland (31) + Iona (96) +
Moomba (185) covers the southern load without help. ACIL's south *is* short enough to need
$12.29 import cargoes. The difference must therefore sit in southern demand levels,
southern supply decline rates, or storage — and which of those it is has NOT been isolated.
That is the next thing to check, and it should be checked rather than assumed: three
successive hypotheses about this gap (divertibility, corridor, supply volume) have each
been tested and each turned out to be wrong.

Item 1's Surat point still stands as a contributing factor — 3,111 TJ/d in 2050 at
$4.00/GJ with no reserve stock — but on its own it does not explain a southern market that
never calls on an import terminal it can already afford.

**To close it:** compare GARY's southern demand and southern supply trajectories directly
against ACIL's, year by year, before changing anything. Then give supply a depleting
reserve stock with a cost that escalates as it draws down (item 1).
