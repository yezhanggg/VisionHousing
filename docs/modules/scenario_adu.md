# Module: ADU scenario (`scenarios/adu.py`)

**What it does.** Estimates how many accessory dwelling units (ADUs) a reform adds in each tract over 2027–2031, as a funnel with likely ranges: eligible lots → lots with room → feasible lots → ADUs built → net added units. The method, the numbers and their sources are in [`docs/methods/adu.md`](../methods/adu.md).

**Why.** This is benchmark reform 1 and the two Pennsylvania ADU bills. Showing every step of the funnel lets a reader see which step limits the answer (usually feasibility, then take-up).

## Inputs

| Input | Where from |
|---|---|
| Tract table | State pack in the Testland schema: `geoid`, `county`, `area_type`, `population`, `units_1_detached`, `units_1_attached`, `units_2`, `units_3_4`, `median_gross_rent`, `median_rent_b1`, the `rent_*` bracket columns; optional `cost_factor` |
| Ledger | `assumptions/parameters.yaml`, with the scenario's overrides applied (`scenario_ledger`) |
| Fit table | `assumptions/calibration/adu_fit.yaml` via `load_fit_table()` |
| Legal share and place scope | `assumptions/calibration/adu_legal_share.yaml` via `load_legal_share()` |
| Scope | `scope_from_scenario(id, scenario, legal)`, from `assumptions/scenarios.yaml` |

## Main functions

| Function | What it does |
|---|---|
| `adu_funnel(tracts, ledger, fit, scope, draws, seed)` | Pure. Samples the ledger, runs every step on draws × tracts arrays, returns an `AduResult` |
| `scope_from_scenario` | Benchmark, HB 2186 or SB 1346 → the kinds of lots reached and places left out |
| `legal_share_override(legal)` | The ledger override that sets `adu.already_legal_share` to the state's calibrated range |
| `ramp_sum` | Yearly ramp factors after a reform |
| `FitTable.shares` | Fit share by kind of home and area type at a drawn position in the range |

## Outputs (`AduResult`)

- `stages`: draws × tracts arrays for `eligible`, `fit`, `feasible`, `built`, `net`.
- `records`: one state record per stage, plus `per_100k` (net ADUs per 100,000 residents per year, next to the published 26 for permissive cities outside California). Each record has P10 / P50 / P90, a formula in words, the ledger ids and the source ids.
- `funnel()`: the five state records in order.
- `tract_table()`: P10 / P50 / P90 of every stage for every tract.
- `county_records(stage)`: one record per county.
- `feasible_share`, `breakeven_rent`, `net_by_year`: the working, for charts and explanations.

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

For a Pennsylvania run, also apply `legal_share_override(load_legal_share())` to the ledger.

## Flagged stub

Pittsburgh is a city inside Allegheny County. SB 1346 leaves it out by removing `adu.pittsburgh_share_of_allegheny_lots` of every Allegheny tract's eligible lots, until the tract-to-municipality split exists (P4-08).

## How it is tested

`tests/test_scenario_adu.py` (tables, hooks, working, sanity bands) and `tests/test_scenario_adu_invariants.py` (method invariants): the funnel narrows at every step; the rent share equals `share_above` by hand; the family-use share adds on top; the no-renter fallback; SB 1346 removes Philadelphia and part of Allegheny; formulas, ids and sources are present; counties sum to the state; Testland's five-year units are a small share of eligible lots; Testland scaled to Pennsylvania lands in the plan's band; more lots never reduce units; higher costs never raise them; higher rents never lower them; zero lots give zero; same seed, same result; P10 ≤ P50 ≤ P90; SB 1346 ≤ HB 2186; the benchmark ≤ HB 2186.
