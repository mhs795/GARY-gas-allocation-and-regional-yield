# GARY documentation

Ten pages. This one says which to read and in what order, so you don't have to open
all of them to find out.

## Start here

| | |
|---|---|
| **[`../README.md`](../README.md)** | What GARY is, how to run it, and the dashboard. Start here if you've never run it. |
| **[`the-2040s.md`](the-2040s.md)** | Why exports stop, what the GSOO is doing differently, and which of the two to believe about what. **Read this before quoting any number after ~2040.** |

## How it works

| | |
|---|---|
| **[`model.md`](model.md)** | The formulation. Sets, variables, the objective term by term, constraints, the two-stage solve, the curtailment ladder, and where a dual means nothing. |
| **[`pricing.md`](pricing.md)** | Where a price comes from. The LNG netback, the Gas Market Code cap, foundation contracts, customer-segment prices — and what a GARY price is *not*. |
| **[`inputs.md`](inputs.md)** | The three input groups, the parameters workbook, the demand build pipeline, the GSOO sector split. |
| **[`expansions.md`](expansions.md)** | The candidate menu: every project, its source, its basis. GSOO-named vs GARY's own market scan, and what the `Cost` column in `arcs.csv` means. |

## Running it

| | |
|---|---|
| **[`running.md`](running.md)** | CLI reference, solver backends, parallel sweeps, the results cache, project layout. |
| **[`scenarios.md`](scenarios.md)** | Every lever with measured results: winter, LNG market, reservation, dunkelflaute, data centre load. Scenario keys and how the cache is addressed. |

## Depletion — the part most likely to mislead you

| | |
|---|---|
| **[`scarcity-rent.md`](scarcity-rent.md)** | What the surcharge on scarce gas is, why it has to be there, what theory says it should be, and how close GARY is. Plain language. |
| **[`depletion.md`](depletion.md)** | What is still wrong with it, what you can trust anyway, and what fixing it would cost. Assumes `scarcity-rent.md`. |

## When something looks wrong

| | |
|---|---|
| **[`../TODO.md`](../TODO.md)** | The engineering log: every known defect and limitation, what it is worth, and what closing it would take. Long on purpose — items are numbered and stay numbered, including after they're fixed, so a commit can point at one. |
| **[`investigations/`](investigations/)** | One-off write-ups that didn't belong in a numbered item. |
| **[`../src/data/README_DATA.md`](../src/data/README_DATA.md)** | File-by-file account of `src/data/`. |
| **`tests/`** | Not a pytest suite — plain scripts, run them directly. `audit_inputs.py` checks every input against its source or against another input; `check_realisable_backstop.py` and `check_asset_salvage.py` check the terminal-value formulas; `check_salvage_objective.py` proves the credit is wired into the objective. |

---

## Two conventions worth knowing before you read anything else

**Every number in these pages is measured, not asserted.** If a page says a change was
worth 5,968 PJ, that figure came out of a run and the run is named. Where something is a
judgement rather than a measurement, it says so in those words.

**A number with no source is flagged as GARY's own, everywhere it appears.** The model is
built entirely from public data — AEMO, the ACCC, ACIL Allen, the Gas Bulletin Board. There
are currently three places where a number had to be chosen rather than sourced, and all
three are labelled at every mention: the export netback deduction, the LNG foundation
contract share, and the two LNG feed-pipe tariffs AEMO doesn't publish.
