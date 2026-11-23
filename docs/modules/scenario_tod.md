# Module: transit-oriented density scenario (`scenarios/tod.py`)

**What it does.** Estimates how many homes a transit-oriented density reform adds in each tract over 2027–2031, as a funnel with likely ranges: eligible land → developable land → feasible land → added capacity → homes built → homes absorbed → net added homes. The method, numbers and sources are in [`docs/methods/tod.md`](../methods/tod.md).

**Why.** This is benchmark reform 3: more height and floor area near major transit stops, with reduced parking. Each step is shown, so a reader can see which one limits the answer. In Pennsylvania it is the feasibility step (mid-rise rents are low next to mid-rise costs) and then the regional absorption cap.

**Terms.** *Major transit stop*: decision D-07 (rail station, or bus stop with at least 64 weekday departures). *FAR* (floor area ratio): building floor area divided by lot area. *Residual land value*: what a builder could pay for land and still make the required profit. *Absorption*: how many new apartments a region can rent out in a period.

## Inputs

| Input | Where from |
|---|---|
| Tract table | State pack: `geoid`, `county`, `area_type`, `population`, `median_rent_b2`, `median_gross_rent`, `median_home_value`, the `value_*` brackets, and for the chosen distance `transit_land_m2_<s>`, `transit_units_<s>`, `transit_units_share_<s>` (`s` = `0_25mi`, `0_50mi`, `0_75mi` or `d07`; see [`transit.md`](transit.md)). Optional `cost_factor` |
| Ledger | `assumptions/parameters.yaml` (`tod.*`, `multifamily.*`, `parking.*`, `fees.fee_total_per_unit`), with the scenario's overrides |
| Scope | `scope_from_scenario("tod", scenario, distance)`; distance is `0.25`, `0.5`, `0.75` or `"d07"` (default) |
| `mf_baseline` | Baseline 5+ unit permits over the five years by county (absorption-cap scale). `None` means no cap |
| `mf_scale` | Optional baseline multiplier per county and draw (the engine's baseline uncertainty) |

## Main functions

| Function | What it does |
|---|---|
| `tod_funnel(tracts, ledger, scope, *, mf_baseline, mf_scale, draws, seed)` | Pure. Samples the ledger, runs every step on draws × tracts arrays, returns a `TodResult` |
| `scope_from_scenario` | Scenario + distance → `TodScope` |
| `distance_suffix(d)` | Distance choice → tract-column suffix (`0.5` → `0_50mi`) |

## Outputs (`TodResult`)

- `stages`: draws × tracts arrays for each of `eligible`, `developable`, `feasible` (acres), `capacity`, `built`, `absorbed`, `net` (units).
- `records`: one state record per stage (P10 / P50 / P90, formula in words, ledger ids, source ids), plus `eligible_homes` and `per_100k`.
- `funnel()`, `tract_table()`; working values `feasible_share`, `residual_per_unit` (median RLV per apartment by tract), `capped_share` (share of draws each county hit the cap), `in_area_units_share`, `net_by_year`.

## For the engine

- Adapter `tod` in `engine/adapters.py` (family `tod`). Option `distance`. The cap is `baseline_types["units_5plus"]` by county, times the run's baseline scale per draw. Where the pack has no transit columns (Testland), the adapter works them out from blocks and stops once per run context (`features.transit.tract_transit_features`).
- **Overlap with missing middle.** Lots inside transit areas count under TOD, the denser reform. `engine.packages.tod_package(result)` removes missing-middle units inside the TOD area (split by `in_area_units_share`), behind the flag `TOD_WINS_OVER_MIDDLE`. It is a separate function; the core waterfall is unchanged.
- **Parking.** The TOD pro forma already builds parking at the D-08 transit-rich share. The parking reform acts only on baseline production. So no unit is counted twice, and no parking → TOD interaction is added.
- **Fees.** A fee cut in a package does not re-enter the TOD pro forma (no interaction). This understates the package a little.

## How it is tested

`tests/test_scenario_tod.py`: distance ¼ → ½ → ¾ mile gives more eligible land, every time (Testland and the nested pack columns); the funnel narrows at every step; zero upzoning (FAR 0, or FAR equal to today's) gives 0; P10 ≤ P50 ≤ P90; same seed, same result; the absorption cap binds, and a zero cap gives 0; the net-new share scales the headline exactly; less parking never lowers feasibility; the engine runs `tod` and the distance option orders eligible land; with all five reforms the package invariant holds, and the TOD-over-missing-middle rule only removes units, never more than missing middle keeps; the flag off changes nothing.
