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

Each field carries a 2P cost and reserve, and a 2C cost and reserve. Cumulative
production is tracked across the horizon; once 2P reserves are exhausted the field steps
to its 2C cost, and deliverability declines at the field's own decline rate.

This is why GARY's price level rises through the horizon, and it is the single most
important thing to understand about **why early-year prices are low**: before roughly
2032 nothing has depleted, so every basin sits on its 2P cost, which AEMO note is
*"largely marginal operating costs"*. From about 2032 the basins step to their 2C costs
(Gippsland $5.16 → $15.76, Otway $7.27 → $15.62, Surat $3.65 → $6.65) — full-cost
numbers, including drilling, completion and processing plant capital.

See [`pricing.md`](pricing.md#1-cost-basis--the-big-one-and-it-is-time-varying) for what
that does to comparability with published price forecasts.

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
