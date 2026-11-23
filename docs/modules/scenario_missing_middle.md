# Module: missing-middle scenario (`scenarios/missing_middle.py`)

**What it does.** Estimates how many homes a missing-middle reform adds in each tract over 2027–2031, as a funnel with likely ranges: eligible lots → lots large enough → feasible lots → lots redeveloped → new units → net units after demolition → net added units. The method, the numbers and their sources are in [`docs/methods/missing_middle.md`](../methods/missing_middle.md).

**Why.** This is benchmark reform 2: 2–6 homes, up to three storeys, on lots now zoned for one home. Every step of the funnel is shown, so a reader can see which step limits the answer. At benchmark fees and costs it is usually the feasibility step.

## Inputs

| Input | Where from |
|---|---|
| Tract table | State pack in the Testland schema: `geoid`, `county`, `population`, `units_1_detached` (and `units_1_attached` if the scope reaches attached homes), `median_rent_b2`, `median_gross_rent`, `median_home_value`, the `value_*` bracket columns. Optional: `cost_factor`, and the block lot-size proxy `lot_m2_per_unit_p25/p50/p75` (from `features.density.tract_density`) |
| Ledger | `assumptions/parameters.yaml`, with the scenario's overrides applied (`scenario_ledger`) |
| `fit` | Optional measured share of eligible lots large enough, by tract code (Tier 2). `None` uses the lot-size proxy if present, else `missing_middle.fit_share` |
| Scope | `scope_from_scenario(id, scenario)`, from `assumptions/scenarios.yaml` |
| `adu_lots` | Optional overlap hook: lots already used by ADUs, draws × tracts or one number per tract |

## Main functions

| Function | What it does |
|---|---|
| `missing_middle_funnel(tracts, ledger, fit, scope, *, draws, seed, adu_lots=None)` | Pure. Samples the ledger, runs every step on draws × tracts arrays, returns a `MiddleResult`. Same positional order and result shape as `adu_funnel` |
| `scope_from_scenario` | Scenario → the kinds of single-family lots reached |
| `share_at_least(p25, p50, p75, threshold)` | Share of homes whose land per home is at least a threshold, from three percentiles (straight lines on a log scale) |

## Outputs (`MiddleResult`)

- `stages`: draws × tracts arrays for `eligible`, `fit`, `feasible`, `redeveloped`, `units`, `net_units`, `net`.
- `records`: one state record per stage, plus `per_100k` (net added units per 100,000 residents per year). Each has P10 / P50 / P90, a formula in words, the ledger ids and the source ids.
- `funnel()`: the seven state records in order. `tract_table()`: P10 / P50 / P90 per tract. `county_records(stage)`: one record per county.
- `feasible_share` (mix-weighted `p`), `feasible_share_by_type` (`p_k`), `residual_land_value` (median over draws of each building type's residual land value per tract), `mix_weights`, `fit_source`, `net_by_year`: the working, for charts and explanations.

## For the engine

Register it like the ADU reform:

```python
scope = scope_from_scenario("missing_middle", scenario)
res = missing_middle_funnel(state.tracts, ledger, None, scope, draws=draws.n, seed=draws.seed)
# res.records["net"] is the headline, res.funnel() the steps, res.stages["net"] the tract draws.
```

- **Fees and parking in a package.** The pro forma reads `fees.fee_total_per_unit`, `parking.required_spaces_per_unit_attached` and `parking.cost_per_space_surface` from the ledger, so a fee cut or parking reform in the same package reaches this funnel through ledger overrides alone.
- **Overlap with ADUs (plan §4.5).** Pass `adu_lots`, for example the ADU result's `stages["built"]` from the same seed and draws. Eligible lots become `max(E − s × adu_lots, 0)` with `s = missing_middle.adu_lot_overlap_share`. Which lots go to which reform is the engine's rule; this function removes only what it is given.

## How it is tested

`tests/test_scenario_missing_middle.py`: hand-worked golden cases (a fourplex tract with every stage checked; the turnover cap; the duplex with an acquisition premium; mix weights divided by their sum; `max_units` switching off larger buildings; negative land value; the two-bedroom rent fallback; the no-owner-homes fallback; the open top value bracket); the lot-size proxy and measured fit; the overlap hook, alone and fed from the ADU funnel; records, tract and county tables; Testland and Testland scaled to Pennsylvania against the plan's sanity anchors.

`tests/test_scenario_missing_middle_invariants.py`: higher home values never raise units; higher rents never lower them; higher costs never raise them and a fee cut never lowers them; a zero single-family-only zoned share gives zero; zero lots or zero hazard give zero; net units = units − one per project; the funnel narrows at every step; shares stay in 0–1; P10 ≤ P50 ≤ P90; same seed, same result.
