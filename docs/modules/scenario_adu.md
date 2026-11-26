# Module: ADU scenario (`scenarios/adu.py`)

**What it does.** Estimates how many accessory dwelling units (ADUs) a reform adds in each tract over 2027–2031, as a funnel with likely ranges: eligible lots → lots with room → feasible lots → ADUs built → net added units. The method, the numbers and their sources are in [`docs/methods/adu.md`](../methods/adu.md).

**Why.** This is benchmark reform 1 and the two Pennsylvania ADU bills. Showing every step of the funnel lets a reader see which step limits the answer (usually feasibility, then take-up).

## Inputs

| Input | Where from |
|---|---|
| Tract table | State pack in the Testland schema: `geoid`, `county`, `area_type`, `population`, `units_1_detached`, `units_1_attached`, `units_2`, `units_3_4`, `median_gross_rent`, `median_rent_b1`, the `rent_*` bracket columns; optional `cost_factor` |
| Ledger | `assumptions/parameters.yaml`, with the scenario's overrides applied (`scenario_ledger`) |
| Fit table | `assumptions/calibration/adu_fit.yaml` via `load_fit_table()` |
| Legal share and place scope | `assumptions/calibration/adu_legal_share.yaml` via `load_legal_share()`; every state's table (`adu_legal_share*.yaml`, `already_legal_*.yaml`) via `load_legal_shares()` |
| County shares from read local law | `local_legal={county: (a, b)}` from `visionhousing.law.hooks` (the engine passes `StateContext.local_law`) |
| Scope | `scope_from_scenario(id, scenario, legal)`, from `assumptions/scenarios.yaml` |

## Main functions

| Function | What it does |
|---|---|
| `adu_funnel(tracts, ledger, fit, scope, draws, seed, local_legal=None)` | Pure. Samples the ledger, runs every step on draws × tracts arrays, returns an `AduResult`. With `local_legal`, a listed county's tracts use `a + b x s` as the already-legal share |
| `scope_from_scenario` | Benchmark, HB 2186 or SB 1346 → the kinds of lots reached and places left out |
| `legal_share_override(legal)` | The ledger override that sets `adu.already_legal_share` (and any entry in the table's `also` block) to the state's range |
| `ramp_sum` | Yearly ramp factors after a reform |
| `FitTable.shares` | Fit share by kind of home and area type at a drawn position in the range |

## Outputs (`AduResult`)

- `stages`: draws × tracts arrays for `eligible`, `fit`, `feasible`, `built` (ADUs authorized), `net` (added units authorized: the headline, `HEADLINE_STAGE`; an ADU removes no home, so it is also the net added homes).
- `records`: one state record per stage, plus `per_100k` (added ADUs authorized per 100,000 residents per year, next to the published rate for permissive cities outside California).
- `year_share`: each year's share of the total, per draw (the ramp, with `adu.start_delay_years`); `net_by_year` = the total × `year_share`.
- `ramp_sum(r1, Y, horizon, delay)` and `year_shares(ramp)` are shared with missing middle, TOD and the engine's yearly split. Each record has P10 / P50 / P90, a formula in words, the ledger ids and the source ids.
- `funnel()`: the five state records in order.
- `tract_table()`: P10 / P50 / P90 of every stage for every tract.
- `county_records(stage)`: one record per county.
- `feasible_share`, `breakeven_rent`, `net_by_year`: the working, for charts and explanations.
- `lot_detail` (only with `adu_funnel(..., lot_detail=True)`, used by project choice, [market.md](market.md)): an `AduLotDetail` with the share of lots the rule reaches, the share where an ADU pays by lot type, each type's share of ADUs, the value-uplift share and the ADU's total cost. No stage changes.

## Example

```python
from visionhousing.ledger import load_ledger, load_scenarios, scenario_ledger
from visionhousing.scenarios.adu import (
    adu_funnel,
    load_fit_table,
    load_legal_share,
    scope_from_scenario,
)

ledger = load_ledger()
scenario = load_scenarios(ledger)["adu"]
result = adu_funnel(
    tracts,
    scenario_ledger(ledger, scenario),
    load_fit_table(),
    scope_from_scenario("adu", scenario, load_legal_share()),
    seed=0,
)
for record in result.funnel():
    print(record.name, record.p10, record.p50, record.p90)
```

For a Pennsylvania run, also apply `legal_share_override(load_legal_share())` to the ledger (the engine does this for every state with a table: `engine.context.state_legal_share`).

## Flagged stub

Pittsburgh is a city inside Allegheny County. SB 1346 leaves it out **tract by tract**. The place is named by its Census county subdivision code (`cousub: "61000"` in the `place_scope` block of `assumptions/calibration/adu_legal_share.yaml`). The engine (`engine/place_scope.py`, `measured_scope`) reads the pack's `tract_municipalities` table (2020 Census block assignment file: housing units by tract and county subdivision), measures each Allegheny tract's share of 2020 housing units inside the city (`law.places.place_tract_shares`) and passes the shares in the scope (`PlaceScope.by_tract`). Scenario modules never import the law layer themselves. In step 1 the funnel then uses `inside = 1 - share[tract]` for Allegheny tracts: tracts wholly inside the city keep no lots, suburban tracts keep all of them, a split tract keeps the part outside (homes of every type are assumed to split like its 2020 housing units). The record `place_pittsburgh_share` gives the resulting county-wide share of the scenario's homes inside the city, with its formula and sources (`census_baf_2020_mcd`, `tiger_2020_tabblock`); the eligible-lots record cites the same sources and no longer lists the drawn share as an input.

**Fallback.** A pack without `tract_municipalities` keeps the old rule: `adu.pittsburgh_share_of_allegheny_lots` (judgement, 0.15–0.25) of every Allegheny tract's eligible lots is removed, drawn per run, and a run note says so. Philadelphia is a whole county (`tract_share: 1.0`) and needs no table. Tests: `tests/test_place_scope.py` (exact tract exclusion, the split tract, the county total equal to (1 - measured share) x county lots, sources and ids, the fallback and its range).

## Measurement note

The outcome is homes authorized by building permits, but permit statistics do not count ADUs consistently. Many towns permit an ADU as an addition to a house or a conversion of a garage, not as a new home, and the national Building Permits Survey counts only new housing units. So some of the ADUs this reform adds may never appear in published permit counts. Every place that names the homes an ADU reform adds says "homes added, of which some may not appear in permit statistics", and the ADU limits list (`assumptions/limits.yaml`) says why.

## How it is tested

`tests/test_scenario_adu.py` (tables, hooks, working, sanity bands) and `tests/test_scenario_adu_invariants.py` (method invariants): the funnel narrows at every step; the rent share equals `share_above` by hand; the family-use share adds on top; the no-renter fallback; SB 1346 removes Philadelphia and part of Allegheny; formulas, ids and sources are present; counties sum to the state; Testland's five-year units are a small share of eligible lots; Testland scaled to Pennsylvania lands in the plan's band; more lots never reduce units; higher costs never raise them; higher rents never lower them; zero lots give zero; same seed, same result; P10 ≤ P50 ≤ P90; SB 1346 ≤ HB 2186; the benchmark ≤ HB 2186.

## Legal-status categories, forms, installed cost (refinements) and variants

- `adu_funnel(..., local_categories=...)`: county to its lot-weighted shares of the five ADU legal-status categories. In those counties the already-legal share of a draw is `already permitted + (1 - adu.conditions_exposure_share) x by right with conditions + unknown x statewide share`, so only lots where the benchmark changes the status quo stay exposed. The engine passes it only when the law hooks weight the categories by single-family lots and the county passes the coverage rule (`engine.adapters.adu_lot_categories`); otherwise the resident-weighted `local_legal` rule stands.
- Records `work_<type>_<stage>` (types `new_standalone`, `uncertain`, `conversion`; stages `fit`, `feasible`, `built`, `net`): the funnel split by work type from the lots-with-room step on; the types add up to each stage. `AduResult.work_types` holds the added units by work type per draw (the engine's five record types read it). `form_detached` and `form_semi_detached` sum them into the two benchmark forms; they add up to `built`.
- Records `legal_size_condition` (`adu.min_size_sqft`, the law) and `prototype_floor_area` (`adu.unit_size_sqft`, the priced prototype), kept apart.
- Records `installed_cost`, `installed_utility_cost`, `owner_cash`, `owner_monthly_cost`: the pro forma at average regional cost.
- Named variants `adu_financing_tight`, `adu_financing_easy`, `adu_full_installed_cost` (`assumptions/variants.yaml`, method in [the ADU note](../methods/adu.md)).

Tested in `tests/test_benchmark_variants.py`: unknown-only categories change nothing; already-permitted empties the county's eligible lots and prohibited fills them; the conditions share sets the exposed part; the forms add up; the feature detection; financing variants ordered tight < benchmark < easy.

## The two-lever case (`scenarios/adu_levers.py`)

**What it does.** Adds a timing lever (predictable approval) and a financing lever (homeowner financing access) to an ADU run, on the same draws. `adu_funnel` calls `adu_levers.apply_levers(...)` once after the take-up step; it returns `None` when every lever setting in the ledger is at its benchmark value (0), so a benchmark run is unchanged. Otherwise it replaces the feasible, built and net stages and the year shares, and adds `lever_*` records. The result is kept on `AduResult.levers`.

| Function | What it does |
|---|---|
| `lag_cdf(x, m, s, pi)` | Lognormal lag curve; with `pi > 0` every lag above the median moves `pi` of the way to it |
| `issuance_by_year(filings, m, s, pi)` | Permits per calendar year from applications per year (12 filing dates a year); what is left is pending |
| `lag_anchors()` | Reads the Philadelphia stage lag and the California ADU donor ranges behind the lag ledger ranges |
| `cash_access(C, C50, k)` | Share of would-be builders whose cash covers `C`: `1 / (1 + (C / C50)^k)` |
| `apply_levers(...)` | Pure. Financing (program cash, counted rent, `A(C1)/A(C0)`) then timing (`W'/W`); paired records, county splits, binding factor |
| `outcome_timing(result, label, years)` | The "shifted into the window" and "newly feasible" records for the yearly series (read by `engine/core.py`) |
| `card_fields(outcome, county)` | Moved, newly feasible, yearly changes, county splits and binding text for the lever card (read by `api/cards.py`) |

Method and numbers: [`docs/methods/adu_levers.md`](../methods/adu_levers.md). Tests: `tests/test_adu_levers.py` (the lag curve only shortens the slow tail; issuance conserves applications; the lag ranges bracket the calibration files; timing-only keeps the five-year-plus-beyond total and moves only the in-window share; financing access is monotone; the variants are distinct; paired effects on common draws add up by year and by county; binding text and card fields).

## Budget-capped program, conserved timing and the hazard on surviving sites (`scenarios/adu_budget.py`)

**What it does.** When a run's ledger turns on `adu.lever_hazard_method` (the variants `adu_hazard_reference`, `adu_timing_conserved`, `adu_finance_budget`, `adu_finance_budget_loan`), `apply_levers` hands the run to this module instead of the linear levers. It counts take-up on a finite stock of sites (`applications = remaining sites x hazard`, then the sites left fall), convolves applications with the application-to-permit lag over an extended horizon, and, with a budget, pays capped awards first come, first served to every eligible owner who files, including owners who would have built anyway. The funnel passes two extra inputs for it: the lots with room where an ADU is already legal (worked out only when asked) and each tract's detached share. The benchmark run never enters this module.

| Function | What it does |
|---|---|
| `simulate(inputs, law=, program=, pull=)` | One arm, year by year over the extended span: applications, awards, spending, awards to owners building anyway; in-window permits per tract when asked |
| `arm_against(inputs, arm, ref, program)` | One arm against another on the same draws: added and comparable authorizations, units moved from later years (same site groups paired, never negative), new projects after the window (site groups the reference lacks), lifetime projects, awards, net public cost, unused money |
| `hazard_variant(...)` | The lever result of a hazard variant: stages, year shares, county splits and the `hazard_*`, `timing_*`, `budget_*` and `lever_*` records |
| `Program` | One program setting on the run's draws: award, loan share, feasible share and hazard multiplier with the award, administration, loan loss and interest subsidy |
| `budget_grid(state)` / `grid_from_inputs(...)` | The budget-and-target grid (`assumptions/adu_budget_grid.yaml`) on one run's draws |
| `grid_lookup(grid, target=, budget=, award=, accept=)` | Answers a query against the grid without interpolating, with out-of-range flags |

Method: [`docs/methods/adu_levers.md`](../methods/adu_levers.md), "A program with a budget". Tests: `tests/test_adu_budget.py` (applications equal remaining sites x hazard; no depletion reproduces the linear benchmark; $2 million at $20,000 caps awards at 100 before administration and does not give 100 induced ADUs; a used-up budget funds no further award; unused money is returned; zero uptake, delayed enactment and prolonged lag; a fully legal state gives zero pure permission gain; timing only conserves projects and reconciles the window; all-conversion gives zero comparable units; the grid pairs on the same draws and never interpolates; the interface address).
