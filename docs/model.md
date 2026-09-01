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

### Why the stock limit works now and did not before

It was tried on 28 Aug 2026 and reverted the same day: Iona went to zero by 2036,
Moomba by 2048, and the model produced 6,419 TJ of shortage at $104–115/GJ. The
limit was not the problem. GARY had no **backfill** — the undeveloped Surat row
could not produce at all, and Gippsland's only unlocked through a single project.
So the limit reproduced AEMO's southern collapse without AEMO's replacement.

AEMO's Figure 27 has southern *existing* production falling 304 → 5 PJ/yr by 2044
while developments backfill it to a 230–280 PJ/yr plateau. Read against the reserve
table that plateau is simply the 2C tranche being produced: southern 2C totals
3,889 PJ, and ~250 PJ/yr for ~16 years is the same number. The envelope and the
reserves are one story.

So the 2C rows now sit behind **AEMO's own named field developments** in
`expansion_options.csv` — Judith, the five Otway projects, Bowen Gas Project,
Mahalo, Mt St Martin, the Beetaloo pilots — and the south has something to build.

### Costing a development: what is published and what is not

**AEMO publishes no development capital anywhere in the GSOO supply data.** What it
publishes is a single blended $/GJ per tranche, and its own note on the *Production
Costs* sheet says what is inside it:

> "Costs include **operating cost, capital costs, royalty, tax and a return on
> capital**... For developed reserves production costs include largely marginal
> operating costs, royalties and tax. For undeveloped reserves, marginal costs also
> include the cost of **drilling and completion and marginal gas processing plant
> costs**."

So the 2P cost is an operating basis and the 2C cost is a full cost. GARY splits
them on exactly that reading:

* the **2C supply row** carries the basin's **operating** basis -- its 2P cost --
  in `Cost`, with AEMO's published full cost kept alongside in `AEMOFullCost`;
* the **capital** comes out in `expansion_options.csv`, derived as
  `(AEMOFullCost - Cost) x Reserves_PJ` for the basin and shared across that
  basin's developments pro rata on `NewCapacity`.

| Basin | derived development capital |
|---|---|
| Surat/Bowen | $69.8bn |
| Gippsland | $21.1bn |
| Cooper/Eromanga | $5.1bn |
| Otway | $2.4bn |
| Amadeus | $2.0bn |
| **Beetaloo** | **not split -- see below** |

The split is applied **only where AEMO publishes both a 2P and a 2C cost.** Beetaloo
has no published 2P at all, so there is nothing to split the capital out with: its
supply row carries AEMO's full $9.15/GJ and its developments carry no derived
capital. Scaling the full cost on other basins' 2P/2C ratios would put a GARY number
where AEMO has published none, so it is not done. Different basins get different
treatment because different data exists, which is the correct outcome rather than an
inconsistency to paper over.

> **Why the magnitude matters.** GARY's project CapEx idiom is $0.25-1bn, and an
> earlier version of this split used it: field developments were put on operating
> cost and charged a project-scale lump sum at Golden Beach's $1.6m/TJ-d unit rate.
> That understates development capital by more than an order of magnitude -- $0.8bn
> against $21.1bn for Gippsland -- and it hands domestic backfill an unbeatable
> advantage over an import terminal, which *is* charged its full cost. If the
> domestic-versus-import trade-off ever looks lopsided, this is the first thing to
> check.

**A field development row therefore does not mean what a pipeline row means.** Every
candidate targeting a basin draws on one shared reserve row, so building 375 of
Gippsland's 500 TJ/d buys ~75% of the pool and pays ~75% of its capital.
`Golden_Beach` carries $15.8bn on that basis, against an announced project cost near
$600m. Read those rows as "this project and the share of the basin's contingent
development it carries", not as a build cost. The `Note` column says so on each.

**Import terminals are the other side of the same rule.** They carry their `CapEx`
explicitly, so ACIL Allen's **$1.50/GJ regasification** allowance comes back off the
injection price -- a tolling fee is how a terminal recovers exactly that capital, and
charging both bills it twice. See `_import_injection_cost`.

### Sizing a 2C tranche's deliverability

AEMO publishes a deliverability for only some developments. Where it does, GARY uses
it. Where it does not, one of two fallbacks applies, in this order:

1. **AEMO's own production forecast, where one covers the basin.** Figure 27 forecasts
   annual production from southern gas fields and its *Uncertain* category is the 2C
   tranche being produced -- a 250 PJ/yr mean over 2030-45, or 685 TJ/d. That is
   allocated across the southern basins by 2C resource share: Gippsland 51.3%,
   Cooper/Eromanga 41.2% (282 TJ/d), Otway 7.5% (52 TJ/d).
2. **The resource ratio, where no forecast covers the basin.** The 2C row is scaled off
   the developed row by the ratio of the two tranches' resources -- Surat/Bowen
   4,000 x 23,270/28,911 = 3,220 TJ/d, Amadeus 55 x 195.4/230.0 = 47 TJ/d.

Gippsland is the case where AEMO publishes project capacities (Golden Beach 375 TJ/d,
Judith 125 TJ/d), so those are used in preference to its 351 TJ/d envelope share.

## Reading a dual

**A GARY price is a marginal cost, not a price anyone pays.** It is what it would cost
the system to push one more TJ into that node that day — the cost of the cheapest thing
not yet being done: run a dearer field, pay a tariff, pull from storage, outbid an export
cargo, or shed a tier at its strike.

For the full treatment of when that is and is not comparable to a wholesale price, see
[`pricing.md`](pricing.md#what-a-gary-price-is-and-when-it-is-not-a-wholesale-price).

### Nodes without a meaningful price

A node that never has demand and never carries gas has a **degenerate dual**: its balance
constraint reads `0 == 0`, so the solver may report anything within a range — and it
reports the shortage penalty. Beetaloo (undeveloped supply, no demand assigned) sat at a
flat **$300/GJ for the whole horizon** that way, which is not a price: it pulled a naive
cross-node mean from $5.81 to $22.15.

Price rows are now emitted **only for nodes that have demand or carry gas**, so such
nodes simply do not appear in price outputs. The headline `Avg_Price` KPI was always
production-weighted and so was never affected; per-node price charts were.
