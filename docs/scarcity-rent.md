# The scarcity rent, in plain language

*Written 12 September 2026, after the LNG export investigation. This is the "what is
this thing and is it right?" document. [`depletion.md`](depletion.md) is the one about
what is still broken; [`model.md`](model.md) has the formulas.*

---

## 1. What it is

GARY adds a surcharge to gas from fields that are running out. It's called the scarcity
rent and it sits on top of the field's production cost:

```
what a buyer pays  =  cost to get the gas out  +  scarcity rent  +  pipeline tariff
```

For Surat in 2045 that's `$3.65 + $2.70 + $0.75`.

## 2. Why it has to be there

Without it the model burns the cheapest gas as fast as it physically can and then walks
into a wall. That isn't a theory — it was measured on 30 August 2026: **758 PJ/yr of
shortage over 2047–50** and a $168/GJ average price, because the investment layer (which
can see the whole horizon) had no reason to build replacement supply, and the dispatch
layer (which sees one year at a time) had no reason to save anything.

The rent is what makes the two layers agree. It tells the year-by-year dispatch what the
whole-horizon plan already knows: this gas is going to run out, so don't spend it like
it won't.

## 3. What it actually represents — and the thing that trips people up

**It is not a price on the gas. It is a price on the cheapness.**

Queensland is not short of gas. There is 28,900 PJ of *developed* gas at **$3.65/GJ**, and
23,300 PJ of *undeveloped* gas behind it at **$6.65/GJ** — different grades of the same
rock, the second dearer because you still have to drill it and build the plant.

So a GJ of the cheap gas burnt today is not a GJ you can't have later. It is **a $3.00
discount you can't have later**. That discount is a real asset. If you don't charge for
it, the model spends it immediately.

This answers the obvious objection — *"there's plenty more gas behind it, so why is it
scarce?"* The gas isn't scarce. The discount is.

## 4. What the right answer looks like

This is a solved problem in resource economics: a cheap finite grade with an unlimited
dearer grade behind it (Hotelling with a backstop; Nordhaus 1973, Heal 1976). The
efficient path has three properties:

1. **Use the cheap grade first.**
2. **The rent rises at the interest rate** — 7%/yr here, because that's GARY's discount
   rate. Gas in the ground is an investment; it has to earn the same return as anything
   else or you'd sell it and bank the money.
3. **At the moment the cheap grade runs out, the rent equals exactly the cost gap between
   the two grades — and after that it is zero.**

Point 3 is the one that pins everything down. Working backwards from it:

```
rent in year t   =   (cost of the dear grade − cost of the cheap grade)  ×  1.07^(t − T)

                 =   ($6.65 − $3.65) × 1.07^(t − 2047)        for Surat
```

where **T = 2047** is the year Surat's developed gas runs dry.

The ceiling makes intuitive sense on its own: **nobody pays more than $3.00 extra for the
cheap gas when the dear gas is sitting there at $6.65 and there's effectively unlimited
amounts of it.**

## 5. How GARY compares

Very well, as it turns out — after the 12 September fixes.

| year | what theory says | GARY now | GARY before the fixes |
|---|---|---|---|
| 2025 | 0.68 | **0.70** | 0.77 |
| 2035 | 1.33 | **1.37** | 1.51 |
| 2045 | 2.62 | **2.70** | 2.97 |
| 2047 *(runs dry)* | 3.00 | **3.09** | 3.40 |
| 2050 | — | 3.79 | 4.17 |

*$/GJ, Step Change · Winter Medium · LNG Medium.*

**GARY now sits 3% above the textbook path at every single point.** Before, it was 13%
above. The shape was always right — it grows at exactly 7.000%/yr, which it has to, for
reasons in section 7 — and what the terminal-value fix corrected was the level.

The 3% left over means GARY's implied run-dry date is about six months later than 2047,
which is fair: 2046 is a part-year (the field produces 680 PJ, down from 1,159).

## 6. What is still wrong with it

**The rent doesn't stop.**

After 2047 there is no cheap Surat gas left to ration, so the rent should vanish and
Surat should simply price at $6.65 — the cost of the dear grade, which is now the only
grade. GARY keeps charging: $3.09, $3.31, $3.54, $3.79 through to 2050. By then it is 26%
above a ceiling that by that point should not exist at all.

**But it costs nothing, and that is worth knowing before anyone spends a week on it.** A
field producing zero is never the marginal supplier, so a rent sitting on it prices
nothing. Measured: from 2047 to 2050 the Surat price pins at exactly **$6.65** — flat,
which is Surat 2C's cost and nothing else — while the 2P rent climbs to $3.79. Brisbane
holds at $7.35 and Gladstone at $7.90 on the same flat line.

That is theory arriving unprompted: once the cheap grade is gone, the price *is* the cost
of the dear grade with no rent on top. So this is a blemish in what GARY *reports* as a
rent, not an error in any price it publishes.

Why it happens: the reserve limit is a **single** constraint covering the whole horizon —

```
total production 2025-2051  ≤  28,911 PJ
```

One constraint gives one shadow price, and `get_scarcity_rents` spreads that one number
across every year by dividing by the discount factor. It has no way of knowing the field
ran out in 2047, so it keeps charging for a discount that has already expired.

## 7. Two things this means you can't read into a GARY number

**The 7%/yr growth is arithmetic, not geology.** It is measured at 7.000%/yr to three
decimal places, identical to the discount rate, in every run at every horizon. That is
the Hotelling rule and it is the correct *shape* — but it would come out at exactly the
discount rate whatever the gas data said, so never quote it as a finding about Australian
gas.

**A rent above the cost gap is not automatically wrong.** The ceiling only applies when
the replacement grade is genuinely unlimited. Surat's is (3,220 TJ/day of deliverability
and 21,106 PJ untouched at the horizon), so its ceiling should hold. Gippsland's is not —
its replacement is 500 TJ/day gated on two projects, against a field declining at 12%/yr
— so Gippsland's rent of $16.62 against a $10.60 "ceiling" is telling you the southern
backfill is genuinely too small, which is a real result and not an error.

| row | cost | rent 2050 | cost gap | over? |
|---|---|---|---|---|
| Surat 2P | 3.65 | 3.79 | 3.00 | 1.26× — **and its backstop IS unlimited, so this is the real violation** |
| Gippsland 2P | 5.16 | 16.62 | 10.60 | 1.57× — backstop is capacity-short, so legitimate |
| Iona 2P | 7.27 | 8.89 | 8.35 | 1.06× — backstop is capacity-short, so legitimate |
| Moomba 2P | 8.45 | 1.90 | 3.18 | under |
| Amadeus 2P | 6.50 | 6.93 | 10.44 | under |

## 8. What I decided not to build, and why

The obvious fix — and the one the TODO has had queued as item 16 since 3 September — is to
give the investment model a **running stock balance**: `stock next year = stock this year
− what you produced`, instead of one budget for the whole horizon.

**On its own, that changes absolutely nothing, and this is provable rather than a guess.**
Because production can't be negative, saying "the stock never goes below zero in any year"
is *exactly the same statement* as "total production over the horizon doesn't exceed
reserves" — the tightest year is always the last one. Same feasible set, same solution,
same shadow price. You'd spend a week rebuilding the core of the model and get identical
numbers.

What *would* change something is the second half of item 16: also tying how fast a field
can flow to how much is left in it. That is where the 3 September attempt died — it used
the field's *starting* reserves-to-production ratio, which forces decline from year one at
up to 5× AEMO's own decline rates, so no field can hold a plateau. Southern supply
collapsed and QCLNG contracts went 134,496 TJ short.

There is a version that doesn't have that problem: use the rule the **dispatch layer
already uses** — "you may not produce more in a year than is left in the ground" — which
is inert until a field is nearly empty and needs no invented parameter. But it makes the
existing reserve limit redundant, and when two constraints say the same thing a solver can
put the shadow price on either one. The rent is extracted from that shadow price. So the
risk is that the rebuild silently zeroes the rent, and the rent is the thing holding the
whole depletion story together.

Set against that risk: **shortage is zero in every year of every run** in the current
model, and the rent is within 3% of theory while the field is producing. The inconsistency
the rebuild would fix is real but is not currently producing a wrong number.

**So: not built. The judgement is recorded here rather than buried, and it is a judgement
— someone may reasonably decide the architecture is worth having for its own sake.**

## 9. How to tell if a future change to this actually worked

Don't argue about it — measure it. A correct depletion model should pass all three without
being told to:

1. **The backstop test.** `rent(cheap grade) ≤ cost(dear grade) − cost(cheap grade)` in
   every year the dear grade is developable and has spare deliverability. Surat: $3.00
   from 2030, when the Bowen Gas Project gets built.
2. **The stopping test.** Once a tranche is exhausted, its rent is zero.
3. **The horizon test.** Move the stop year from 2051 to 2055 and report the same window;
   the answer shouldn't move. Currently it moves ~2% either way — small, and **unchanged**
   by the 12 September fixes, because the horizon's grip is the budget's shape, not the
   terminal value's level.
