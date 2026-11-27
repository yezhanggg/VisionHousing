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

- `stages`: draws × tracts arrays for `eligible`, `fit`, `feasible`, `redeveloped`, `units` (new units authorized), `authorized` (added units authorized: the headline, `HEADLINE_STAGE`) and `net` (net added homes after demolition, `NET_STOCK_STAGE`).
- `authorized` and `net` come from four event fields on the same lots in both arms (policy units and demolitions, counterfactual units and demolitions) through `engine.outcome.site_account`; records `removed`, `baseline_on_sites`, `counterfactual_removed`, `demolitions_caused` keep them. `work_types`: added units by work type per draw (stand-alone or attached new building).
- `records`: one state record per stage, plus `per_100k` (added units authorized per 100,000 residents per year), `removed` (homes demolished) and `baseline_on_sites` (the baseline's own permits on the redeveloped lots). Each has P10 / P50 / P90, a formula in words, the ledger ids and the source ids.
- `year_share` (each year's share of the total, per draw), `removed` and `baseline_on_sites` (draws × tracts).
- `funnel()`: the seven state records in order. `tract_table()`: P10 / P50 / P90 per tract. `county_records(stage)`: one record per county.
- `feasible_share` (mix-weighted `p`), `feasible_share_by_type` (`p_k`), `residual_land_value` (median over draws of each building type's residual land value per tract), `mix_weights`, `fit_source`, `net_by_year`: the working, for charts and explanations.

## For the engine

Register it like the ADU reform:

```python
scope = scope_from_scenario("missing_middle", scenario)
res = missing_middle_funnel(state.tracts, ledger, None, scope, draws=draws.n, seed=draws.seed)
# res.records["authorized"] is the headline, res.funnel() the steps,
# res.stages["authorized"] the tract draws, res.stages["net"] the net added homes.
```

- **Fees and parking in a package.** The pro forma reads `fees.fee_total_per_unit`, `parking.required_spaces_per_unit_attached` and `parking.cost_per_space_surface` from the ledger, so a fee cut or parking reform in the same package reaches this funnel through ledger overrides alone.
- **Overlap with ADUs (plan §4.5).** Pass `adu_lots`, for example the ADU result's `stages["built"]` from the same seed and draws. Eligible lots become `max(E − s × adu_lots, 0)` with `s = missing_middle.adu_lot_overlap_share`. Which lots go to which reform is the engine's rule; this function removes only what it is given.
- **Project choice (package option `market`).** Pass `rivals=MiddleRivals(adu_share, adu_uplift, adu_cost)` with `method="prototypes"` (not together with `adu_lots`): on each lot the best prototype must also outbid a conventional single-family rebuild (`prototypes.conventional_bid`, ledger `market.conventional_home_size_sqft`) and, where an ADU pays, the ADU's value to the owner (value-uplift share x home value - ADU cost). Feasible lots become `E x ((1 - a) f_conv + a f_win)`; the result's `choice` (`MiddleChoice`) keeps the lot shares and units with and without each rival. See [market.md](market.md).

## How it is tested

`tests/test_scenario_missing_middle.py`: hand-worked golden cases (a fourplex tract with every stage checked; the turnover cap; the duplex with an acquisition premium; mix weights divided by their sum; `max_units` switching off larger buildings; negative land value; the two-bedroom rent fallback; the no-owner-homes fallback; the open top value bracket); the lot-size proxy and measured fit; the overlap hook, alone and fed from the ADU funnel; records, tract and county tables; Testland and Testland scaled to Pennsylvania against the plan's sanity anchors.

`tests/test_scenario_missing_middle_invariants.py`: higher home values never raise units; higher rents never lower them; higher costs never raise them and a fee cut never lowers them; a zero single-family-only zoned share gives zero; zero lots or zero hazard give zero; net units = units − one per project; the funnel narrows at every step; shares stay in 0–1; P10 ≤ P50 ≤ P90; same seed, same result.

## Prototypes (`scenarios/prototypes.py`)

`missing_middle_funnel(..., floor=MiddleFloor(...))` applies a dated state floor (Vermont's 24 V.S.A. § 4412(1)(D)) in the mix method: a `k`-unit building counts only where `k` is above the units already allowed (the larger of the floor in force that year and the rights the baseline history reflects), split by the share of lots with municipal water and sewer; where the already-allowed design also pencils only the extra homes count, and the counterfactual on those lots is that design (its units and its demolition, no `q x r` rebuild on top). The engine builds the floor (`engine/law_floor.py`, `middle_floor_for`) from `law/rights.py` and the pack's `infra_tract` sewer share; the funnel adds records `floor_served_share`, `floor_newly_legal_share`, `floor_existing_alternative` and `floor_freeze_difference` ("freeze today's rules" minus current law, a separate comparison). Method: [missing middle](../methods/missing_middle.md#a-state-floor-by-date-vermont). `MiddleFloor.local` (variant `vt_missing_middle_town_bylaws`) holds `LocalLevel` entries, one per verified, in-force town bylaw: on the town's share of a tract's lots the comparator is `max(state floor comparator, town level)`; with none the funnel is the benchmark draw for draw; record `floor_town_bylaw_share`. The engine builds them (`engine.law_floor.town_levels_for`). Tested in `tests/test_law_local_variants.py`.

`prototype_choice(tracts, col, proforma_ids, draws)` compares a duplex, a fourplex and a six-unit building on every tract and draw: the share of lots big enough for each (lot-size proxy, made monotone), each one's land bid (residual land value with its own floor area, rent, building-code cost above two homes, accessible-design cost from four, commercial financing from five, minus demolition), the share where each pencils alone, and the best feasible choice by lot-size band. `missing_middle_funnel` reports it as records `proto_*` whenever the ledger has the prototype entries (the benchmark's numbers do not change), and uses it as the feasible step with `method="prototypes"` (variant `missing_middle_prototype_choice`). Method: [missing middle](../methods/missing_middle.md#prototypes-duplex-fourplex-six-unit-building).

Tested in `tests/test_benchmark_variants.py`: the benchmark stages are identical with and without the prototype entries; bigger buildings fit fewer lots; each prototype's "pencils alone" never exceeds its fit; the variant differs from the mix and averages 2–6 homes per redeveloped lot; on small lots a duplex pencils on more lots than a six-unit building.
