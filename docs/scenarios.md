# Scenarios and levers

[← back to README](../README.md)

Every lever, what it does mechanically, and what it does to the answer.

- [Baselines](#baselines)
- [Southern winter stress](#southern-winter-stress)
- [Global LNG market](#global-lng-market)
- [SA dunkelflaute](#sa-dunkelflaute)
- [Gas reservation](#gas-reservation)
- [Data centre gas demand](#data-centre-gas-demand)
- [Scenario keys and the cache](#scenario-keys-and-the-cache)

---

## Baselines

The **GSOO Baseline Scenario** dropdown picks which AEMO **2026 GSOO** demand trajectory
the run is built on:

| Baseline | Character |
|---|---|
| **Step Change** | Central case. The default. |
| **Accelerated Transition** | Faster electrification, weaker gas demand, weaker world LNG market |
| **Slower Growth** | Slower transition, stronger gas demand, stronger world LNG market |

The demand builders emit one full set of files per baseline (`demand_StepChange.csv`,
`demand_Accelerated.csv`, `demand_SlowerGrowth.csv`). ACIL Allen's three price scenarios
carry the same names, so the price path and the demand path map one to one with no
interpretation — see [`pricing.md`](pricing.md#the-price-series).

## Southern winter stress

| Level | Multiplier | What it is |
|---|---|---|
| Low | 0.91× | **Unseasonably warm winter** — the warmest of the seven full Bulletin Board winters (2019–2025), detrended |
| **Medium** | 1.0× | **The central GSOO case** (default) — AEMO's series as published, weather-averaged |
| High | 1.5× | **Stress**, deliberately beyond observed weather |

Multiplies Melbourne / Adelaide / Sydney distribution demand over the winter window.

**Low and Medium are weather cases; High is not.** Low is measured rather than chosen:
southern PIPE demand over the winter window across 2019–2025, detrended first because the
raw series falls 2.6%/yr on structural decline, which is not weather. The detrended
residuals run **0.911 (2023) to 1.076 (2022)**, standard deviation 0.046.

> **High is an adequacy test, not a cold winter.** At 1.5× it puts annual domestic energy
> 8–12% above the GSOO and Melbourne's peak day at ~1.8× AEMO's VIC RC&I peak. The
> *coldest* winter in the record is only **1.08×** detrended. If you want a
> weather-realistic cold case rather than a stress test, set High to 1.08 on the
> `Scenario_Levers` sheet.

## Global LNG market

The lever changes *instrument* depending on the netback switch.

**With netback pricing on (the default)** it selects which of ACIL Allen's published
price paths the netback is struck off — it does not touch volume at all:

| Global LNG Market | Price path | Netback 2030 |
|---|---|---|
| **Low** | Accelerated Transition — weak global demand | $7.33/GJ |
| **Medium** | the run's own GSOO baseline | $8.40/GJ |
| **High** | Slower Growth — strong global demand | $10.39/GJ |

Mapping to published scenario paths rather than an invented percentage shift keeps every
number in the chain sourced. It deliberately **decouples the price path from the demand
baseline**, so "Step Change demand with Slower Growth LNG prices" is expressible — that is
the sensitivity, not a mistake.

**With netback pricing off** it reverts to the old behaviour and scales export *volume*.

### Why the instrument changed

The old volume lever produced an artefact. High multiplied train demand by 1.6×, which
pushed planned exports well above physical liquefaction nameplate — and must-serve demand
could only report the excess as **553,543 TJ of domestic lost load at VOLL**, with a
domestic mean price of $115.79/GJ.

Stressed **2030, Winter High**, Step Change demand, under netback pricing:

| Global LNG | Netback | LNG exported | of which spot | Shortage | Domestic mean |
|---|---|---|---|---|---|
| Low | $7.33 | 1,313 PJ | 90 PJ | 4,302 TJ | $13.90/GJ |
| Medium | $8.40 | 1,315 PJ | 92 PJ | 4,302 TJ | $14.31/GJ |
| High | $10.39 | 1,315 PJ | 92 PJ | 4,302 TJ | $14.71/GJ |

The **shortage artefact is gone** — all three sit at the same 4,302 TJ as an unstressed
run, because exports can no longer be asserted above what the trains can physically
liquefy. Volume barely moves across the three because the netback beats Surat's ~$4/GJ
marginal cost in all of them, so the whole contestable tail clears either way. **The
lever's effect is on price, which is the point**: a stronger world market makes trains bid
harder, and domestic buyers pay more.

## SA dunkelflaute

A wind-and-solar drought in South Australia in 2027, forcing gas-powered generation to
cover the gap. Included in the **Run Scenarios** batch as a standalone case alongside the
27 baseline × winter × LNG combinations.

## Gas reservation

```bash
python src/solve.py --reservation 20
python src/solve.py --reservation 20 --break-lng-contracts
```

A reservation carves a share of planned LNG export volume out of the export stream and
puts it into the domestic market **at zero cost**, so it is the cheapest gas in the system
and is taken up ahead of everything else.

### The four pieces

Applied in `_year_demand` (`solve.py`) and `model.py` so the foresight capacity layer and
the 365-day dispatch see the same policy:

1. **`served_LNG[t] ≤ (1 − share) × LNG_demand[t]`** — the carve-out; the trains may only
   liquefy what is left. Applied *after* the winter/LNG levers, so the share bites on the
   export volume actually planned under the scenario.
2. **`reserved_prod[t] ≤ share × LNG_demand[t]`, priced at $0/GJ** — the carved-out volume,
   offered to the domestic market for nothing.
3. **`production[source, t] + reserved_prod[t] ≤ source capacity`** — the reserved gas is
   the *same* gas, not extra. Total physical deliverability is unchanged; a slice of it is
   simply free.
4. **`Σ flow over LNG feed pipes[t] ≤ commercial gas reaching the source`** — exports may
   draw only on commercial gas. Without this the free gas would flow straight to the
   trains, which are ordinary demand nodes and cannot tell one molecule from another, and
   the reservation would do nothing.

Piece 4 is exact rather than an approximation because the trains have exactly three feed
pipes (`APLNG_Pipe`, `GLNG_Pipe`, `WGP_Pipe`), all from Surat. "Commercial gas" must
include **transit inflows** — Surat takes gas from Moomba over the SWQP and from Silver
Springs, and LNG demand exceeds Surat's own deliverability on ~30 days a year, so
restricting exports to Surat's own production would strand the trains on those days and
change the no-reservation base case.

The reservation adds **no export revenue term**. That coupling is what produced negative
nodal prices at Perth in the removed WA DomGas build; here the reservation acts entirely
through supply cost and flow eligibility. Export value enters the objective only via LNG
netback pricing, and only on the contestable spot tail.

> This replaces an earlier **pure export-cap** formulation (piece 1 alone). Under that
> version the reserved gas was never produced at all: domestic demand was already met, so
> cost minimisation left it in the ground, and the reservation was an export cap by another
> name. **Pricing the gas at zero is what makes it move.**

### What it does

Stressed 2030 (Step Change, Winter High, LNG Medium):

| reservation | offered | taken up | production | mean price | QLD price | Melbourne |
| --- | --- | --- | --- | --- | --- | --- |
| 0% | — | — | 1,711,690 TJ | $13.82 | $8.57 | $53.45 |
| 5% | 65,971 TJ | 99.7% | −65,755 TJ | −$1.73 | −$2.57 | −$0.71 |
| 10% | 131,942 TJ | 99.7% | −131,509 TJ | −$2.61 | −$3.78 | −$1.28 |
| 20% | 263,883 TJ | 99.7% | −263,018 TJ | −$3.13 | −$4.59 | −$1.42 |
| 30% | 395,825 TJ | **80.3%** | −377,028 TJ | −$4.03 | −$8.41 | −$1.43 |

**The gas moves.** Take-up is 99.7% up to a 20% reservation — priced at zero it is
dispatched ahead of everything else. Beyond that the domestic market cannot absorb it: at
30% nearly a fifth of the offered volume finds no buyer it can reach.

**But it displaces rather than adds.** Domestic consumption is fixed, so the free gas
substitutes one-for-one for commercial gas that would have been produced anyway. Total
production falls by exactly the export cut. What changes is the *price*, because the
marginal molecule at Surat is now free.

**And the relief does not travel.** Price falls decay with distance from Surat:

| Brisbane / Gladstone / Surat | LNG nodes | Moomba / Darwin | Adelaide | Sydney | **Melbourne / Gippsland** | Iona |
| --- | --- | --- | --- | --- | --- | --- |
| −$4.59 | −$4.41 | −$3.62 | −$2.59 | −$1.93 | **−$1.42** | −$0.97 |

The smallest relief lands exactly where prices are highest. Melbourne and Gippsland sit at
~$53/GJ and move $1.42. The corridor is doing far more work than under the export-cap
version — `SWQP_Rev` goes from 257 TJ/d mean and 2 days at capacity to **505 TJ/d and 280
days at capacity**, and the `VGP` from 1.9 to 168.9 TJ/d — but it fills, and then nothing
more gets through.

**Curtailment does not move at all.** GPG shedding (11,877 TJ), industrial shedding (1,448
TJ) and winter shortage (4,302 TJ) are *identical at every reservation level*, 0% through
30%. Crashing the Queensland gas price to $0.17/GJ relieves not one TJ of southern
curtailment.

> **The conclusion, in sharper form than the export-cap version gave: a reservation is a
> Queensland price policy, not a southern supply policy.** Forcing the gas into the market
> at zero cost gets it produced and consumed, which the export cap never did, but it
> cannot put it where the shortage is.

### Take-or-pay contracts

The **Respect LNG foundation contracts** switch, on by default, decides whether the
reservation may break existing export contracts.

**On**: it may only take **uncontracted** export gas, so it is capped at
`1 − lng_foundation_share` = **7%** however high the slider goes. That is how the Heads of
Agreement with the east coast LNG exporters actually works, and how the ACCC frames the
quarterly balance — what matters is what producers do with their *uncontracted* gas.

**Off** (`--break-lng-contracts`): the reservation takes its share of all export volume,
foundation SPAs included. Drastic, but a real policy option, so the model represents it
rather than quietly refusing to.

Stressed **2030, Winter High / LNG Medium**:

| Reservation | Applied | LNG exported | Foundation | Spot | Domestic mean |
|---|---|---|---|---|---|
| none | — | 1,315 PJ | 1,223 | 92 | $14.31/GJ |
| 5%, respects SPAs | 5.0% | 1,270 PJ | 1,223 | 47 | $13.67/GJ |
| **20%, respects SPAs** | **7.0% (capped)** | 1,257 PJ | 1,223 | 34 | $13.63/GJ |
| 20%, breaks SPAs | 20.0% | 1,090 PJ | 1,052 | 38 | $12.06/GJ |
| 30%, breaks SPAs | 30.0% | 958 PJ | 921 | 38 | $10.98/GJ |

> **The headline: every reservation level above 7% is capped once contracts are
> respected.** 5% / 10% / 20% / 30% collapse toward the same outcome, because there simply
> is not more uncontracted gas to reserve. A reservation that actually delivers 20% or 30%
> is a policy that **breaks take-or-pay contracts** — which the model will now show you,
> but as a separate, explicitly labelled scenario (`_Reserve20incl`). The KPI card reports
> *asked* versus *allowed* so the gap is never silent.

> **Trap worth knowing.** The reservation deliberately does **not** scale the trains'
> demand rows under netback pricing. Doing so would shrink the foundation leg and thereby
> *enlarge* the spot headroom (nameplate less foundation) — the reservation would have
> converted contracted export into spot export and left total exports untouched. It is
> subtracted from the export ceiling instead.

Prices at Surat fall to $0.00/GJ under a large reservation — that is the zero-cost
reserved tranche being the marginal supply at the source node, the documented behaviour of
the mechanism. No strictly negative prices at any level.

### Why System Cost is not comparable

**System Cost is not comparable across reservation levels**, though how badly depends on
the netback switch. The reserved tranche is priced at $0/GJ in every mode, so reserving
more always lowers the figure. What changes is whether the export value given up is
counted at all:

| Mode | Foregone export revenue |
|---|---|
| Netback **off** | **Not counted.** `LNGNodes` is an empty set, so `lng_benefit` is identically zero and a reservation looks free because it removes demand nothing was paying for |
| Netback **on**, contracts respected (the default) | **Counted.** The reservation shrinks the spot ceiling by the full applied share, and a contract-respecting reservation is capped at the uncontracted tail — which is exactly the volume that carries revenue |
| Netback **on**, `--break-lng-contracts` | **Partly.** The spot portion is costed; volume taken from the foundation leg is not, because that is written back into `node_demand` as must-serve with no revenue attached |

Either way it is the cost of serving what was served, **not a welfare measure**. The KPI
carries the applicable caveat under the number, and **Gas Reserved** shows the percentage
actually taken up.

## Data centre gas demand

A "what if" lever for hyperscale data centre load, stated in either of two ways:

- a **flat volume** — an annual figure in PJ for **NSW** and for **VIC** plus the year it
  starts; from that year on it is added to large-industrial demand at Sydney and Melbourne
  and held for the rest of the horizon;
- a **linked spreadsheet** — a year-by-year series per state, read at solve time.

```bash
python src/solve.py --dc-nsw 50 --dc-vic 30 --dc-start 2030
```

Two states rather than one national figure because **where the load lands is the whole
point**: Sydney and Melbourne sit at opposite ends of the southbound corridor that binds
in every stressed GARY run.

**It enters as industrial demand**, which is what a data centre's gas call is — a firm,
round-the-clock load at a handful of large sites, not distribution-level household gas.
Two consequences worth knowing when reading a result:

- it curtails at the **industrial strike price** ($120/GJ), not at mass-market willingness
  to pay, so it outbids households and outranks GPG. In fact it is netted out of what the
  industrial tier may shed at all — see [`model.md`](model.md#constraints);
- it reaches the **capacity layer** through the same industrial series, so the investment
  model sizes pipe and storage for it rather than discovering it in dispatch.

**Daily shape follows GPG.** The annual volume is spread across the year in proportion to
that node's own gas-powered generation profile — day *d* gets
`PJ × 1000 × gpg[node, d] / Σ gpg[node, ·]`, which preserves the annual total exactly.

> **Caveat — the GPG shape is very peaky.** GPG runs intermittently, so 50 PJ/yr at Sydney
> arrives as anything from ~0.05 to ~580 TJ on a given day, against ~137 TJ/d if it were
> spread evenly. A real facility's own gas draw is far flatter than that. This shape is the
> right one if the load is understood as *gas generation firming a data centre*; it
> materially overstates day-to-day variation if it is meant to be the data centre's own
> boilers or fuel cells — and **the peak days are what the capacity layer sizes against**.

### Linking a demand series

The flat cell answers *"what would N PJ/yr of this do to the east coast market"*. A
build-out has a shape — a first site, a second, a plateau once the campus is full — so the
volume can instead be read year by year out of a spreadsheet you keep the pipeline in.
Everything above still applies: same node, same industrial tier, same GPG daily shape,
same firmness. The file only changes **how much, in which year**.

In the dashboard, put a path in **Or link a demand series**; the line beneath it reads the
file as you type and reports what it found. On the command line:

```bash
python src/solve.py --dc-file ~/work/data_centre_pipeline.xlsx
python src/solve.py --dc-file ~/work/pipeline.xlsx#Sydney --dc-vic 5   # sheet + VIC cell
```

The file needs a `Year` column and an `NSW` and/or `VIC` column in PJ/yr:

| Year | NSW | VIC |
|------|-----|-----|
| 2028 | 0   | 0   |
| 2030 | 3   | 1   |
| 2033 | 9   | 4   |
| 2040 | 18  | 9   |

`.csv`, `.xlsx` and `.xlsm` all work; add `#SheetName` to name a sheet, or GARY takes the
first sheet with a usable `Year` column. A financial-year label (`2032-33`, `FY2032`) is
read as its **leading** year, since GARY's horizon is calendar years. A column headed in
TJ (`NSW (TJ)`) is converted. A long-format extract with `Year, State, PJ` rows works too.
`src/data/datacentre_demand_example.csv` is a template.

**A state the file covers stops using its cell**, and a state it does not cover keeps
using it — so a file with only an NSW column leaves the VIC box doing exactly what it did
before, and nothing is counted twice. `--dc-start` likewise applies only to the states
still on a cell; a file says for itself when its load starts.

Rows may be sparse, and the three gaps are filled like this:

- **before the first row** — zero. The first row is when the load switches on;
- **between rows** — linear, i.e. a straight ramp between the two stated years. To mean
  *nothing until it opens*, put an explicit zero row the year before;
- **after the last row** — **held flat** at the last value. A series ending in 2040 is a
  data centre still running in 2050, not one that closes.

> **The file is read, never written.** GARY keeps no copy of it, which is the point of
> linking rather than importing — but it also means a series run is only reproducible while
> that spreadsheet still says what it said.

Set `datacentre_series_path` in `gary_parameters.xlsx` to have the sidebar box come up
already pointing at a file you maintain; `none` (the shipped value) leaves it empty.

## Scenario keys and the cache

Every setting that changes the answer is written into the scenario key, so runs with
different settings sit alongside each other in the cache instead of overwriting:

| Segment | Set by |
|---|---|
| `_Netback` | LNG netback pricing on |
| `_NoImports` | Import terminals disallowed |
| `_Reserve20` / `_Reserve20incl` | Reservation level; `incl` means foundation contracts broken |
| `_DC<nsw>N<vic>V<year>` | Flat data centre volumes |
| `_DCS<name>` | A linked data centre series, keyed on the **file's own name** |

Leaving a lever at its default produces the same key as before that lever existed, so
older cached scenarios stay valid.

**On the linked-series key:** it carries the file's name rather than a hash so a run reads
as *the NSW pipeline* rather than as something opaque — `datacentre_demand_NSW.csv` keys
as `_DCSNSW`. The shared `datacentre_demand_` prefix is stripped and the rest sanitised to
letters and digits, because the key is split on `_` and parsed by position. A sheet name
joins it, so `book.xlsx#Q3` keys as `_DCSbookQ3`.

> **The trade-off:** a name is stable across edits, so changing a volume in the file now
> **overwrites** that name's cached result instead of filing a new one. That is the point —
> the file names a scenario you re-run — but it means the cache is only as current as the
> last solve of that name. The hash of the numbers is still computed and saved with the
> result, as is the path, and the run header names both. Keys from before this change
> (`_DCS1a2b3c4d`) still decode, so older cached results stay readable.
