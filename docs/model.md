# The model

[← back to README](../README.md)

GARY is a deterministic, least-cost linear program over a nodal gas network, wrapped in
a mixed-integer capacity-expansion layer. This page is the formulation.

- [Sets and variables](#sets-and-variables)
- [The objective](#the-objective)
- [Constraints](#constraints)
- [The two-stage solve](#the-two-stage-solve)
- [The curtailment ladder](#the-curtailment-ladder)
- [Supply cost and depletion](#supply-cost-and-depletion)
- [Reading a dual](#reading-a-dual)

---

## Sets and variables

`src/model.py`, class `GasMarketModel`. One instance solves **one year** at 365-day
resolution.

| Set | Members |
|---|---|
| `T` | days 1…365 |
| `Nodes` | 20 network nodes |
| `Arcs` | 32 directed pipeline arcs |
| `Supply` | (node, is_potential) pairs — 12 rows, developed fields and potential developments |
| `Expansion` | expansion candidates from `expansion_options.csv` |
| `StorageNodes` | nodes with `StorageCapacity > 0` — Iona, Silver Springs, Moomba |
| `GPGNodes` / `INDNodes` | nodes carrying gas-powered generation / large-industrial load |
| `LNGNodes` | export trains, **only when netback pricing is on** — otherwise empty |

| Variable | Domain | Meaning |
|---|---|---|
| `production[s, t]` | ≥ 0 | Gas produced from source `s` on day `t` (TJ) |
| `flow[a, t]` | ≥ 0 | Gas moved down arc `a` |
| `shortage[n, t]` | ≥ 0 | Demand simply not served, at VOLL |
| `inventory / injection / withdrawal[sn, t]` | ≥ 0 | Storage state and movements |
| `gpg_curtail[n, t]` | ≥ 0 | Gas-powered generation stood down |
| `ind_curtail[n, t]` | ≥ 0 | Large industrial stood down |
| `reserved_prod[t]` | ≥ 0 | The reserved tranche, when a gas reservation is running |
| `lng_export[n, t]` | ≥ 0 | The contestable export spot tail, bid at the netback |
| `build[e]` | **binary** | Whether candidate `e` is built |

`build[e]` is the only integer variable, and it is what makes the capacity layer a MILP.
In the dispatch stage it is fixed, leaving a pure LP.

## The objective

Every term is in **dollars**. Volumes are TJ and prices are $/GJ, hence the ×1000 on
almost every line — so the dual comes out in $/TJ and is divided by 1000 again on the way
out.

| Term | Formula | What it is |
|---|---|---|
| `prod_cost` | Σ `production` × field cost × 1000 | Getting gas out of the ground, per field, per day |
| `trans_cost` | Σ `flow` × arc tariff × 1000 | Moving it. **This is the term that makes a Melbourne price differ from a Surat price.** |
| `shortage_penalty` | Σ `shortage` × $300/GJ × 1000 | Unserved demand at VOLL. The backstop that keeps the LP feasible. |
| `storage_cost` | Σ (`injection` + `withdrawal`) × $0.50/GJ × 1000 | A round-trip charge, so inventory cycles only when the seasonal spread justifies it |
| `exp_capex` | Σ `build[e]` × CapEx × 0.08 | Annualised capital at 8%/yr on anything built |
| `gpg_pen` | Σ `gpg_curtail` × $22/GJ × 1000 | GPG shed at its strike |
| `ind_pen` | Σ `ind_curtail` × $120/GJ × 1000 | Large industrial shed at its strike |
| `lng_benefit` | Σ `lng_export` × netback × 1000 | Export revenue. **Subtracted**, not added. |
| `reserved_cost` | Σ `reserved_prod` × field cost × 1000 | **Capacity MIP only.** Lifting the reserved tranche. Dispatch prices it at $0 — that is the reservation, and there the price only sets merit order. The MIP weighs its objective against CapEx, so a $0 tranche would be a discount on the build decision rather than a merit-order device. See `capacity_model._reserved_cost`. |

```
minimise   prod_cost + trans_cost + shortage_penalty + storage_cost
         + exp_capex + gpg_pen + ind_pen  −  lng_benefit
```

Read the signs as *"what would the system pay to avoid this"*: it pays to produce and to
ship, it pays dearly to shed, and it is **paid** by an export cargo. Minimising the whole
thing is equivalent to maximising producer plus consumer surplus — the same objective
ACIL Allen's GasMark solves, which is the point of modelling the netback at all.

Because every benefit block is **bounded above** — an export block cannot exceed the
train's own liquefaction headroom — the sum cannot run away negative.

> **This is not the WA negative-price trap.** An earlier, removed WA DomGas build put
> export revenue in the objective *and* coupled it through a reservation constraint that
> **forced** domestic service — unbounded benefit, and Perth nodal prices went negative.
> Here nothing forces uptake and every block is capped. Verified on stressed 2030 and
> over the full horizon: no negative nodal prices. Re-check this if the formulation
> changes.

## Constraints

### Nodal balance — the important one

One constraint per node per day. Everything arriving equals everything leaving:

```
   production at this node
 + reserved tranche (if this is the field it is carved from)
 + flow in
 + net storage withdrawal
 + shortage                    ← demand not met
 + gpg_curtail                 ← GPG stood down
 + ind_curtail                 ← industry stood down
 ==
   distribution demand + GPG demand + industrial demand
 + lng_export                  ← the contestable spot tail
 + flow out
```

The shed variables sit on the **supply** side, which looks odd until you read it as
*"demand I did not have to meet counts the same as gas I found"*. That is exactly why
shedding must be priced in the objective — otherwise the solver would shed everything
for free.

**The dual of this constraint is the nodal price.** Every price GARY reports is read off
this equation.

### The rest

| Constraint | Rule |
|---|---|
| `gpg_curtail_cap` | A tier can shed at most its own demand |
| `ind_curtail_cap` | Industrial shedding is capped at `ind_demand − data_centre_demand` — data centre load is **firm** and cannot be shed at the industrial strike |
| `supply_cap` | Production ≤ declined deliverability. A **potential** source produces only if a terminal fronting it was built. With a reservation running, `production + reserved_prod ≤ deliverability` — the reserved gas is the *same* gas, not extra |
| `flow_cap` | `flow ≤ base capacity + Σ built expansions targeting this arc` |
| `export_eligibility` | Flow down the three LNG feed pipes ≤ commercial gas reaching Surat (own production **plus transit inflows**). Without this the free reserved gas would flow straight to the trains and a reservation would achieve nothing |
| `storage_cont` | Inventory balance; day 1 opens at half full |
| `storage_cap`, `inj_rate`, `wd_rate` | Volume and plant-rate limits |
| `storage_close` | Inventory on day 365 ≥ the opening level |
| `build_group_once` | Mutually exclusive candidates (rival FSRUs at one landing point) cannot both be built |

**Why `storage_close` exists.** Each year is solved independently and opens at half full.
Before this constraint nothing required the store to be at any level on day 365, so the
solver emptied all three stores every year and got them back each January — **46,200
TJ/yr from nothing**, about 11% of domestic supply, at the $0.50/GJ cycling charge, which
made it cheaper than any field in the model. The opening level is an assumption either
way; requiring the year to close is what stops the assumption being a subsidy. It is `>=`
rather than `==` so ending fuller stays legal — it costs money, so the solver will not do
it without a reason.

**Why `ind_curtail_cap` nets out data centre load.** Data centre volume rides *inside*
industrial demand so it is transported and priced like any other large industrial load,
but it is not price-responsive: its consumption is set by its compute. That does not make
it unshortable — if gas physically cannot reach it, `shortage` still absorbs the volume,
at VOLL rather than at the industrial strike, which is the right price for load that had
no choice but to keep running. The practical effect is that a data centre **outbids the
refinery next to it** for scarce winter gas instead of being shed alongside it.

## The two-stage solve

`src/solve.py` orchestrates; `src/capacity_model.py` is stage 1.

**Stage 1 — capacity expansion.** `CapacityExpansionModel` builds all 26 years at once
on **representative days** rather than 365, discounts to NPV at the discount rate, and
solves as a MILP. Perfect foresight: it knows 2050's demand when it decides 2028's
pipeline. Output is a **build schedule** — candidate → year.

**Stage 2 — dispatch.** For each year 2025…2050, one `GasMarketModel` at full 365-day
resolution with `build[e]` **fixed** to the schedule. Pure LP.

Splitting it this way is not a performance shortcut, it is what makes the prices usable.
An investment decision is a binary variable and **a MILP has no meaningful duals**.
Fixing the schedule first leaves an LP whose duals genuinely are marginal costs.

A **myopic** mode (`--myopic`, or the sidebar toggle off) runs investment year by year
with no foresight, for comparison.

Where the time goes on a scenario, measured on a 2-core i5-5257U:

| Stage | Time |
|---|---|
| Build 26 year-models + representative days + capacity MIP | ~28 s (under 10%) |
| 26 × dispatch solve (HiGHS) | ~10 s each |
| 26 × `get_results()` (pure Python, no solver) | ~10–12 s each |

Roughly half of stage 2 is **result extraction**, which no solver setting touches — the
obvious target if single-scenario speed ever matters more than sweep throughput.

## The curtailment ladder

Demand is a fixed volume — whatever the GSOO baseline says, at any price. Two large-user
tiers may stand down rather than be supplied, each at a flat strike, and anything still
unserved falls to the value of lost load:

| Tier | Strike | Meaning |
|---|---|---|
| GPG | **$22/GJ** | The generator switches fuel, or another generator runs instead |
| Large industrial | **$120/GJ** | The process line stops |
| Everything else | **$300/GJ** (VOLL) | Nothing left to shed; load simply goes unserved |

That ladder is also **the path domestic prices climb in a tight year**: a scarce node
settles at whichever tier is marginal. Firm load has no rung on it — data centre load and
foundation LNG cargoes cannot shed at a strike and go straight to VOLL, which is what
makes them outbid everything else for scarce gas.

Strikes live on the Parameters sheet (`strike_gpg_default`, `strike_ind_default`) and are
written into `curtailment_params.csv` by `build_curtailable_demand.py`.

> **Distribution demand is not price-responsive.** A step demand curve for the mass
> market — with industrial and GPG blocks that *raised* demand when gas was cheap — was
> built and then removed on 29 Aug 2026. Every block was calibrated against a reference
> price that had to be re-struck by hand each time the model's price level moved, and
> after the supply-side depletion work roughly doubled that level, the GPG ladder (the
> largest of the three responses) could no longer fire at any price the model produced.
> Recoverable from git history as `src/build_demand_curves.py`.

> **VOLL.** GARY penalises unserved gas at $300/GJ. The National Gas Rules set VoLL at
> **$800/GJ** in the Victorian DWGM and the STTM market price cap at **$400/GJ** (AEMO,
> *Gas Market Parameters Review 2022 — Final Recommendations*, Feb 2023). GARY's figure
> is conservative and left alone because changing it moves every historical result.

## Supply cost and depletion

**One row per tranche, and each tranche is a stock.** A basin appears in
`supply.csv` twice: a developed row holding its **2P** reserves at the 2P cost, and
an undeveloped row holding its **2C** contingent resource at the 2C cost. Each
carries its own `Reserves_PJ`, and neither can produce more gas than it holds —
`_declined_capacity` returns zero once cumulative production reaches the reserve,
tapering through the year it runs out.

That makes depletion a real **supply curve** rather than a price adjustment. The
cheap tranche runs out; the dear one behind it has to be *built* to replace it.

| Basin | 2P cost | 2P reserves | 2C cost | 2C resource |
|---|---|---|---|---|
| Surat/Bowen | $3.65 | 28,911 PJ | $6.65 | 23,270 PJ |
| Cooper/Eromanga (Moomba) | $8.45 | 850 PJ | $11.63 | 1,603 PJ |
| Gippsland | $5.16 | 1,108 PJ | $15.76 | 1,993 PJ |
| Otway (Iona) | $7.27 | 304 PJ | $15.62 | 293 PJ |
| Amadeus | $6.50 | 230 PJ | $16.94 | 195 PJ |
| Beetaloo | — | — | $9.15 | 5,109 PJ |

Both layers enforce it. The dispatch model applies the limit year by year off
cumulative production; the capacity MIP sees all 26 years at once and states it as
a single constraint per row, which is what makes it build backfill *before* the
tranche it replaces runs out. The old two-pass capacity solve is gone with it —
it existed only to carry a path-dependent cost step that no longer exists.

### The scarcity rent

A stock limit alone is not enough, and the reason is worth understanding because it
is a property of the two-layer design rather than of the data.

The capacity MIP has **perfect foresight** and one horizon-wide reserve constraint,
so it can *ration*: spread Surat's 28,911 PJ thinly across 26 years and never hit a
wall. The dispatch layer is **myopic** — it solves one year at a time and takes the
cheapest gas first at full rate. So it exhausted Surat's 2P by 2046 and then had
nothing, because the backfill the MIP saw no need to build was never built.

Measured on 30 Aug 2026, before the fix:

| | |
|---|---|
| 2029–30 | southern spike as Otway, Gippsland and Cooper ran dry ahead of their backfill — 50,371 TJ short, Melbourne $238/GJ |
| 2047–50 | **758,248 TJ short every year**, mean price $168/GJ, as Surat's 2P went and Bowen Gas Project had not been built |

The missing signal is the **opportunity cost of depletion**. A myopic dispatch facing
no cost for using up a finite resource will always burn it cheapest-first. The dual
on the MIP's `reserve_limit` is exactly that cost — what one more PJ in the ground is
worth to the system — and adding it to the field's marginal cost makes the cheap
tranche price like the scarce thing it is.

`get_scarcity_rents()` extracts it by fixing the build binaries, relaxing them to
Reals and re-solving as a pure LP (neither backend returns duals while an integer
variable is present, even a fixed one — the same trick `model.py` uses for nodal
prices). The MIP objective discounts each year, so the dual is on an NPV basis;
dividing by the year's discount factor puts it back on a cash basis, which makes the
rent **grow at the discount rate**. That is the Hotelling result for an exhaustible
resource, arrived at rather than imposed.

It also puts the late-horizon price rise where it economically belongs. Capital is no
longer in the marginal cost, so the thing that lifts prices as the cheap tranches
deplete is scarcity rent on a finite resource — not a cost step bolted onto a basin.

> **`--myopic` does not get a rent.** That mode runs no capacity MIP, so there is no
> reserve dual to take. It will burn each tranche cheapest-first and hit the wall
> described above. Treat its late-horizon results accordingly.

### The terminal value, and what the rent is actually made of

**The rent above is not just the reserve dual.** It has two components, and on
current data the second is the larger by an order of magnitude:

```
rent(node, tranche, y) = ( reserve_dual + salvage_npv ) / discount_factor(y)
```

Without a terminal value the objective prices leftover gas at **zero**, so the
optimal plan empties every tranche in the last year it can see and that year shorts
at VOLL — 207,569 TJ, measured 30 Aug 2026. The salvage credit is what stops that.

**How the credit is built, in three steps.** Each nets something real off the one
before, and each is load-bearing:

| step | what it is | Surat 2P |
|---|---|---|
| `salvage_price` | ACIL Allen's landed **import injection** cost in the final solved year — the backstop, i.e. what the substitute costs. Published, not chosen. | **$12.29**/GJ |
| `_backstop_at_wellhead` | less the cheapest run from this node **to a regasification terminal**. The backstop is a price at Port Kembla, Geelong or Adelaide, not at a wellhead; a basin only earns it by delivering there. Surat's cheapest is Adelaide, $1.53 up the SWQP reversal plus $0.97 on MAPS. | −$2.50 → **$9.79** |
| `_salvage_rate`, step 1 | less the row's own **extraction cost**, floored at zero. The gas is un-extracted, so the delivered price overstates what it is worth in the ground. | −$3.65 → **$6.14** |
| `_salvage_rate`, step 2 | scaled by **`Λ = 1/(1 + r·τ)`**, the closed-form value of a stock produced on a decline over `τ = Reserves/deliverability` years. A stock is not sold at the horizon instant; Surat holds 19.8 years of production. | ×0.419 → **$2.57** |

Both nettings are what make the credit basin-specific, which is the whole point — a
uniform credit collapses every rent onto the same floor and stops distinguishing a
nearly-exhausted basin from an abundant one. The 2 Sep 2026 values:

| | Surat | Moomba | Gippsland | Iona | Amadeus | Beetaloo |
|---|---|---|---|---|---|---|
| **τ, years** | 19.8 | 5.8 | 4.0 | 2.4 | 11.5 | 27.2 |
| **2P salvage** | 2.57 | 2.04 | 4.20 | 2.76 | 0.79 | — |
| **2C salvage** | 1.32 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |

Beetaloo at zero is the netting working: $9.15/GJ to lift against a $7.46 delivered
backstop is not worth holding. And τ is what separates Iona — 2.4 years of production
left, so its stock is nearly cash and keeps 86% of its margin — from Surat, which is
holding twenty years of inventory and keeps 42%.

**Where it enters.** Twice, and they must agree:

* **Investment.** `obj_rule` subtracts `df[last] × salvage_rate × (Reserves − produced)`,
  so the MIP weighs leaving gas in the ground directly against CapEx.
* **Dispatch.** `get_scarcity_rents` adds `salvage_npv` to the reserve dual before
  de-discounting, because passing the dual alone under-prices gas relative to the
  plan dispatch is executing, and it over-produces.

> **FIXED 3 Sep 2026 — the credit used to cancel the field cost.** Without the `Λ`
> step the rate is exactly `backstop_at_node − Cost`, so `Cost + rate == backstop`
> for every row: the lifting cost **cancelled**, and every tranche was worth the same
> at the horizon regardless of what it cost to lift, including free gas.
>
> ```
> produce iff  Cost + rent(y) + transport <= price(y)
> at horizon:  Cost + (backstop - Cost) + transport <= price
>              backstop + transport <= price     <- Cost has cancelled
> ```
>
> Measured before the fix: Surat 2P, Surat 2C and Moomba 2P — spanning $3.65–8.45/GJ
> — all closed 2050 at $11.72–12.04, and LNG exports went 901 PJ to 0 between 2037
> and 2038 and never resumed. Since the backstop is an import price and the netback an
> export price, exports at the horizon were excluded by construction, at any cost.
>
> With `Λ` the horizon value is `Cost×(1−Λ) + backstop×Λ`, a weighted average, so a
> `Cost` term survives: Surat 2P closes at **$6.22** against nearly-exhausted Moomba's
> **$10.49**. The supply curve survives the horizon, and Surat's delivered export cost
> of $7.00 sits just under the $7.12 netback — so whether exports happen is an
> economic outcome again rather than a foregone one.
>
> **What is still approximate.** `τ` is struck on nameplate reserves and base capacity,
> so it ignores decline and ignores how much has already been produced. Both push `τ`
> down and the credit up, i.e. toward the old behaviour. Tightening it means
> recomputing `τ` from the solved remainder and re-solving. See [`TODO.md`](../TODO.md)
> item 15.
