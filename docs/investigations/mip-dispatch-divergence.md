# Investigation — the capacity MIP and the dispatch layer disagree

[← back to README](../../README.md)

**Status: open.** Started 30 Aug 2026. Every test is recorded here, failures included,
so nothing gets retried by accident.

## The symptom

Reference scenario (Step Change · Winter Medium · LNG Medium · netback). Southern
production, what the capacity MIP planned against what the 365-day dispatch layer
actually drew:

| year | MIP plan | dispatch | gap |
|---|---|---|---|
| 2029 | 287 PJ | 331 PJ | +15% |
| 2030 | 234 PJ | 278 PJ | +19% |
| 2031 | 223 PJ | 266 PJ | +19% |
| 2032 | 172 PJ | 79 PJ | tranches exhausted |
| 2033 | 91 PJ | 0 PJ | nothing left |

Dispatch over-draws the southern tranches by ~44 PJ/yr for three years, exhausts
Gippsland, Otway and Cooper about a year early, and hits a hole in 2032–35 that the
MIP never saw and therefore scheduled no build for.

**This should not happen.** Both layers face the same demand, the same costs plus
scarcity rent, the same network and the same builds. And the representative-day
weights sum to **370**, not 365, so the MIP should if anything plan ~1.4% *more*
production than dispatch needs. It plans 15–19% *less*.

Consequences seen: 16,008 TJ of shortage in 2032 at 13 representative days; Melbourne
at $22–24 across 2033–35; **every southern 2P tranche drawn to exactly 100%** while
**27,354 PJ of 2C sits at 0%**; and no Geelong FSRU built despite a 750 TJ/d arc
straight into Melbourne and three years of $24 prices.

---

## Tests

### T1 — MIP optimality gap · RULED OUT

*Hypothesis.* The 0.5% gap hides a build decision, now that field developments carry
CapEx in the tens of billions.

*Method.* Reference solved at `mip_gap=0.0005` against `0.005`.

*Result.* **Byte-identical build lists and identical shortage.** Not the gap.

### T2 — representative-day resolution · PARTIAL, and not by the stated mechanism

*Hypothesis.* Twelve monthly MEAN days flatten the load-duration curve inside each
month, so the MIP mis-estimates how much southern gas dispatch will take.

*Method.* `build_representative_days` rewritten to sort each month's days by total
demand and split them into contiguous load bins. Tested at 1 bin (13 days/yr, the old
behaviour), 3 (37) and 5 (61).

*Result.* The **2032 shortage cleared**: 16,008 TJ → 0 at 37 days, still 0 at 61.
But the ~44 PJ/yr plan-versus-actual gap is **unchanged** at every resolution, and the
**build list is byte-identical at 13, 37 and 61 days**. So resolution never touched the
investment decision, and the shortage cleared by some indirect route (inferred: changed
duals → changed rents → dispatch rationed harder) rather than by the layers agreeing.

*Kept* — `rep_bins_per_month` on the Parameters sheet, default 3, with a dashboard
slider. It removes a real shortage. It does not fix the divergence.

### T3 — hold dispatch to the MIP's annual plan · FAILED, made it worse

*Hypothesis.* Cap each supply row's annual dispatch volume at what the MIP planned, so
the layers cannot disagree about drawdown.

*Method.* `annual_plan_cap` constraint in the dispatch model, fed from
`get_annual_production_pj()`.

*Result.* **Worse.** Prices pinned at **$22–24 for almost the entire horizon** rather
than a 2033–35 bump, and the 2036 contract-expiry relief disappeared. The cap binds in
*every* year.

*Conclusion.* That is the important finding: the MIP's plan is not merely mistimed, it
is **systematically ~15–19% below what dispatch needs**. Constraining dispatch to it
manufactures permanent scarcity. **Code removed entirely**, not just switched off.

### T4 — where the divergence actually lives · ANSWERED: it is the supply MIX

*Method.* Compare MIP against dispatch on three quantities per year, not just southern
production: **total production**, **LNG export volume**, and **southern production**.

*Readings committed in advance:*

* totals match, mix differs → a supply-**mix** effect; the rent is the wrong lever and
  the representative-day *shape* is the thing to change, not the count.
* MIP total lower, export explains the gap → the two layers settle `lng_export` at
  different volumes because nothing ties them together.
* MIP total lower, export matches → the representative days under-represent annual
  energy, and the capacity layer has been sizing the system against less demand than
  gets dispatched. This would be the serious one: it undercuts every build decision.

*Result.*

| year | TOTAL production PJ (MIP \| dispatch) | southern PJ (MIP \| dispatch) |
|---|---|---|
| 2029 | 1725 \| 1750 | 287 \| 331 |
| 2030 | 1658 \| 1683 | 234 \| 278 |
| 2031 | 1632 \| 1657 | 223 \| 266 |
| 2032 | 1622 \| 1648 | 172 \| 79 |

**Totals agree to within 1–2%.** The serious reading is ruled out: the capacity layer is
NOT sizing against less demand than gets dispatched. But southern production differs by
15–19%, so dispatch takes ~44 PJ/yr MORE from the south and ~44 PJ/yr LESS from
somewhere else. **This is a supply-mix effect, not a volume one.**

> *Measurement error, recorded so it is not repeated.* The LNG column in this test
> compared the MIP's `lng_export` variable — which is only the contestable **spot
> tail** — against dispatch's `lng_exported_tj`, which is **total** exports including
> the must-serve foundation volume. Those are different quantities and the comparison
> was meaningless. T5 compares `lng_spot_tj` instead.

### T5 — which source is substituted, and is transport the reason? · IN PROGRESS

*Hypothesis.* Averaging demand across a month under-states pipeline **congestion**. On
an averaged day the corridors are slack, so the MIP believes Melbourne can be served
from Surat down the MSP/EGP; on real peak days those corridors bind and dispatch is
forced onto local southern gas instead. If so the fix is in the representative days'
*shape* — they must preserve the days when transport binds — not in their count, which
would explain why T2's load bins changed nothing about the divergence.

*Method.* Per-node production MIP vs dispatch; LNG spot tail compared like for like;
and corridor utilisation (mean, max, days above 99%) on MSP, EGP_Rev, VNI_Rev, SWP,
Bulloo and SEA_Gas, MIP against dispatch.
