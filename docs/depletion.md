# Running out of gas: what GARY does, and what is still wrong with it

**Read this before quoting anything GARY says about the 2040s.**

GARY has to answer one question over and over: *should this gas be produced now, or left
in the ground for later?* Every price and every export volume in the model follows from
how that question is answered. This page explains the answer GARY currently gives, the
three things still wrong with it, what you can safely conclude anyway, and what fixing it
would take.

Plain language throughout. The technical detail and the measurements behind every number
here are in [`TODO.md`](../TODO.md) items 15, 16 and 17.

---

## 1. How GARY decides, today

**Moved.** The whole of "what the scarcity rent is, why it has to exist, what the right
answer looks like and how close GARY is to it" now lives in
[`scarcity-rent.md`](scarcity-rent.md) — plain language, nine short sections, and with
the numbers re-measured after the 12 September 2026 fixes. Read that first; this page
assumes it.

The two-line version: producing a GJ of Surat gas costs $3.65 to lift **and** uses up a
GJ you can never sell again. That second cost is the scarcity rent. Without it the model
burns the cheap gas first and hits a wall — measured at 758 PJ/yr of shortage over
2047–50 before the rent existed.

**The horizon.** GARY solves to 2051 and reports to 2050. The extra year absorbs
end-of-model artefacts so nobody reads them.

---

## 2. What is still wrong

### Issue 1 — the answer depends on where you stop the model

This is the big one. Run the same scenario three times, changing only the year the model
stops, and look at what each says about the years everyone reads:

| | stop at 2051 | stop at 2055 | stop at 2060 |
|---|---|---|---|
| Last year with LNG exports | **2044** | **2042** | **2040** |
| Surat scarcity rent in 2050 | $4.17 | $4.93 | $6.18 |
| Total exports to 2050 | 23,101 PJ | 21,245 PJ | 19,358 PJ |

Same gas, same demand, same everything — different answers, because of an arbitrary
choice about where to stop. And it does not settle down as the horizon lengthens; the gap
keeps growing.

**Why.** The reserve constraint is a *budget over the years solved*: total production
across the horizon ≤ reserves. Solve more years, the same gas has to cover more demand, so
it gets scarcer. The tightness of the budget is partly a fact about gas and partly a fact
about how many years you asked for.

### Issue 2 — the rent grows at exactly 7%/yr because of arithmetic, not gas

Measured across all three horizons: **7.000%/yr, to three decimal places**, identical to
the discount rate. That is not a result about Australian gas. The budget is a single
constraint, so it has a single shadow price, and that one number gets applied to every
year alike; converting it back to cash terms then *forces* a 7% path before any data is
read.

The consequence: the rent carries no information about *when* a field runs down.

### Issue 3 — AEMO's reserves and its decline rates contradict each other

Follow each field along AEMO's own decline rate, from its own stated capacity, and total
what it produces by 2050. Compare with the reserves AEMO states for the same field:

| field | would produce | AEMO says it holds | over by |
|---|---|---|---|
| Surat 2P | 33,574 PJ | 28,911 PJ | 1.2× |
| Gippsland 2P | 2,246 PJ | 1,108 PJ | 2.0× |
| Moomba 2P | 2,662 PJ | 850 PJ | 3.1× |
| Iona 2P | 3,322 PJ | 304 PJ | **10.9×** |

Both numbers are AEMO's, from the same workbook. GARY survives this — production is capped
at whatever the field has left — but it means **the reserve limit is what binds, and the
decline rates are close to decorative** for the southern fields.

> **Practical consequence: never read a GARY result as following from a decline rate.**
> Depletion in GARY is reserve-driven.

---

## 3. What you can trust anyway

| period | verdict |
|---|---|
| **2025–2039** | **Solid.** Well clear of the horizon, and the export path is set by real economics — the netback against the cost of getting gas to a train. |
| **2040s** | **Conditional.** Directionally informative, but the exact year exports stop is partly an artefact of stopping the model at 2051. Quote it with the caveat. |
| **Any single year's lump sums** | Volatile. Use the whole projection. |
| **Anything attributed to a decline rate** | Don't. See issue 3. |

The model is internally consistent and shorts zero gas in all 17 standard scenarios. The
problem is not that it is broken — it is that the late years answer a question that
includes "when did you stop asking?"

---

## 4. Options for fixing

### Option A — leave it, use the caveats

**Cost:** nothing. **Buys:** nothing.

Defensible. The 2030s carry most of the policy interest, and they are sound. This is the
current position.

### Option B — per-year stock, with a plateau-then-decline rule

**Cost:** large. Changes every year of every result. **Buys:** depletion becomes physical
and the rent finally carries timing information.

Give the model a running balance — `stock(y+1) = stock(y) − production(y)` — and tie how
fast a field can flow to how much is left in it, so a field declines because gas is
leaving rather than because the calendar advanced.

**Two-thirds of this was built and measured on 3 Sep 2026** (then deleted to keep the model
simple). What was learned:

* The architecture works — it builds and solves in about the same time.
* The rent has to be split, and the split is known: pass only the "this is the last gas"
  part, not the part that reflects how fast the field can flow, or the dispatch layer pays
  twice and prices $3.65 gas at $21.36.
* **What killed it:** tying the rate to remaining stock, using the field's starting
  reserves-to-production ratio, forces decline from year one at up to 5× AEMO's own rates
  — so no field can hold a plateau. Southern supply collapsed and LNG contracts went
  134,496 TJ short.

Real fields hold a plateau, *then* decline. Implementing that needs one number — the point
at which plateau ends — and **neither AEMO input supplies it.** See section 5.

### Option C — rolling horizon

**Cost:** roughly 26× the solve time. **Buys:** removes issue 1 completely.

Solve 2025–2050, keep only 2025, then solve 2026–2051 and keep only 2026, and so on. No
reported year is ever near the horizon. Textbook, entirely correct, and expensive.

### Option D — just extend the horizon ❌

**Tried 2 Sep 2026. Rejected, with measurements.**

Solving to 2065 bought exactly one year of exports. Fourteen extra years of held-flat
demand against the same fixed reserves manufactured scarcity faster than the longer horizon
relieved it — the reserve dual went from $0.00 to $14.42/GJ. It also makes the 2050 answer
depend on demand data AEMO never published.

### Option E — retune the terminal value ❌ (for the 2P row)

**Tried 3 Sep 2026. Cannot work, and this was measured rather than argued.**

The terminal value and the reserve dual are **perfectly substitutable** — lower one and the
other rises to match:

| terminal value | reserve dual | total rent |
|---|---|---|
| 8.64 | 0.00 | 8.07 |
| 2.57 | 1.77 | 4.17 |
| 0.00 | 4.17 | 4.17 |

Below a floor, changing the terminal value changes nothing at all. There is no setting of
it that fixes issue 1.

> **The substitution is a property of a BINDING row, and that is the loophole — closed
> 12 Sep 2026.** It holds for Surat 2P, whose reserve constraint binds, so there is a dual
> standing by to absorb whatever the credit gives up. It does **not** hold for Surat **2C**,
> whose dual is exactly zero because 91% of the tranche is still in the ground at the
> horizon. Nothing absorbs a change there.
>
> That matters because 2C is the **backfill** — the tranche that is supposed to take over
> when 2P runs down. Carrying $1.32/GJ of terminal credit, it priced at $6.65 + $1.32 =
> $7.97 against a 2P tranche at $3.65 + $4.17 = $7.82, so the replacement stayed dearer
> than the thing it replaces right through the depletion. Removing the credit (TODO item
> 21) put 2C back underneath, and the model's own arbitrage then capped the 2P rent:
>
> | | 2P dual | 2P salvage | **2P rent** | 2C rent |
> |---|---|---|---|---|
> | shipped | 1.76 | 2.40 | **4.17** | 1.32 |
> | route-capacity credit | 2.72 | 1.07 | **3.79** | **0.00** |
>
> The 2P total still moved only $0.38 — Option E's floor is real. The **2C** row moved all
> the way to zero, and that is what changed the answer.
>
> **It buys nothing on issue 1.** Re-running the three-horizon test both ways on 12 Sep
> 2026, 2051 → 2055: the reported export total moves **+1.8%** on the old credit and
> **−2.0%** on the new one. Same size, opposite sign. The horizon's grip is the budget's
> shape, exactly as item 16 says — removing a terminal-value artefact does not loosen it.
> The table at the top of issue 1 was measured on an earlier code vintage and should not be
> read against post-12-Sep runs.

---

## 5. The question that has to be answered first

Option B is the only one that addresses the underlying problem at reasonable cost, and it
cannot start until someone decides:

> **When AEMO's reserves and AEMO's decline rates disagree, which does GARY believe?**

Today GARY takes the *total* from reserves and the *shape* from decline rates, which is why
fields run flat and then stop dead rather than tapering. Any plateau-then-decline rule needs
one of those two inputs to give way.

This is a judgement about the source data, not a modelling problem, and picking the answer
that makes the export path look reasonable would be fitting the model to a conclusion. It
needs a person.
