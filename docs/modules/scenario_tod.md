# Module: transit-oriented density scenario (`scenarios/tod.py`)

**What it does.** Estimates how many homes a transit-oriented density reform adds in each tract over 2027–2031, as a funnel with likely ranges: eligible land → developable land → feasible land → added capacity → homes built → homes absorbed → net added homes. The method, numbers and sources are in [`docs/methods/tod.md`](../methods/tod.md).

**Why.** This is benchmark reform 3: more height and floor area near major transit stops, with reduced parking. Each step is shown, so a reader can see which one limits the answer. In Pennsylvania it is the feasibility step (mid-rise rents are low next to mid-rise costs) and then the regional absorption cap.

**Terms.** *Major transit stop*: a rail station, or a bus stop with at least 64 weekday departures. *FAR* (floor area ratio): building floor area divided by lot area. *Residual land value*: what a builder could pay for land and still make the required profit. *Absorption*: how many new apartments a region can rent out in a period.

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

- `stages`: draws × tracts arrays for each of `eligible`, `developable`, `feasible` (acres), `capacity`, `built`, `absorbed`, `authorized` (added units authorized: the headline, `HEADLINE_STAGE`), `net` (net added homes after demolition, `NET_STOCK_STAGE`) (units).
- `records`: one state record per stage (P10 / P50 / P90, formula in words, ledger ids, source ids), plus `eligible_homes`, `per_100k`, `removed` (homes demolished on redeveloped land), `baseline_on_sites` (the baseline's own permits there) and, when the pack has by-right districts, `already_zoned` (acres taken out of the developable land).
- `existing`, `capacity_by_right`: land already zoned by right and its modeled capacity (`ExistingCapacity`), and the additional legal capacity on it, draws × tracts (`None` when not used). `existing_capacity(tracts, table, suffix)` reads `mbta_3a_capacity_tract` (station-area part scaled by eligible ÷ half-mile land, the rest by the share of the tract beyond the half mile, capped at the eligible land); `tod_funnel(..., existing=...)` counts `max(0, proposed − existing)` homes per m² on `D3 × p` (`additional_legal_capacity`). Records `existing_3a_land`, `existing_3a_capacity`, `proposed_3a_capacity`, `existing_3a_on_developable`, `additional_3a_capacity`, `existing_3a_resolved_share`. See the method note, "Land already zoned by right".
- `zoned`: the older acre-share rule, now variant `tod_ma_acre_share` (option `existing_capacity: acre_share`): acres already zoned for multi-family by right taken out, draws × tracts (`None` without districts). `zoned_land_m2(tracts, districts)` sums the district acres inside station areas by centre tract (m²); `tod_funnel(..., zoned_m2=...)` takes `min(Z × tod.state_zoned_overlap_share, L)` off `L × d`. Used by the benchmark only for a pack with `mbta_3a_districts` but no `mbta_3a_capacity_tract`.
- `year_share` (each year's share of the total, per draw), `removed`, `baseline_on_sites` (draws × tracts).
- `funnel()`, `tract_table()`; working values `feasible_share`, `residual_per_unit` (median RLV per apartment by tract), `capped_share` (share of draws each county hit the cap), `in_area_units_share`, `net_by_year`.

## For the engine

- Adapter `tod` in `engine/adapters.py` (family `tod`). Options `distance`, `existing_3a_subtract` (`full`, the headline, or `q_scaled`: variant `tod_ma_3a_q_scaled`, `max(0, proposed − q × existing)` on 3A land; `tod_funnel(..., existing_subtract=...)`) and `existing_capacity` (`capacity`, the default where the pack has `mbta_3a_capacity_tract`, or `acre_share`; `engine.adapters.tod_by_right` picks the rule and is shared with transit zoning with funded infrastructure). The cap is `baseline_types["units_5plus"]` by county, times the run's baseline scale per draw. Where the pack has no transit columns (Testland), the adapter works them out from blocks and stops once per run context (`features.transit.tract_transit_features`).
- **Overlap with missing middle.** Lots inside transit areas count under TOD, the denser reform. `engine.packages.tod_package(result)` removes missing-middle units inside the TOD area (split by `in_area_units_share`), behind the flag `TOD_WINS_OVER_MIDDLE`. It is a separate function; the core waterfall is unchanged.
- **Parking.** The TOD pro forma already builds parking at the transit-rich still-built share. The parking reform acts only on baseline production. So no unit is counted twice, and no parking → TOD interaction is added.
- **Fees.** A fee cut in a package does not re-enter the TOD pro forma (no interaction). This understates the package a little.

## How it is tested

`tests/test_scenario_tod.py`: distance ¼ → ½ → ¾ mile gives more eligible land, every time (Testland and the nested pack columns); the funnel narrows at every step; zero upzoning (FAR 0, or FAR equal to today's) gives 0; P10 ≤ P50 ≤ P90; same seed, same result; the absorption cap binds, and a zero cap gives 0; the net-new share scales the headline exactly; less parking never lowers feasibility; the engine runs `tod` and the distance option orders eligible land; with all five reforms the package invariant holds, and the TOD-over-missing-middle rule only removes units, never more than missing middle keeps; the flag off changes nothing.

## Network walksheds, the low-rise prototype and the zero decomposition

- `TodScope(walk="network")` reads columns `transit_*_net_<d>` made by `network_columns(tracts, walk)` from the pack table `transit_walk_tract` (eligible land = network acres x land share of the tract outline; homes at the straight-line density; fallback land kept apart). The adapter option `walk` defaults to `straight` (the benchmark); variants `tod_network_half_mile`, `tod_network_quarter_mile`, `tod_network_three_quarter_mile` use the network. A network run adds `walk_fallback_share` and a per-county note with the fallback acres; a straight-line run notes per county that it is straight-line (and when the pack has no walkshed table).
- Variant `tod_lowrise`: a 3–4 storey walk-up with surface parking (`tod.lowrise_*`).
- Option `decompose`: `zero_decomposition` runs mid-rise and walk-up at the run's coverage and at 3/4 mile on the same draws (records `decomp_*`), and `decomposition_verdict(records, major_stops)` says whether coverage, the prototype or both bind; with no major stop in the stop table and zero eligible land it says there are no major transit stops in the state. The adapter adds a run and county note naming the cause (`_no_major_stops_note`: rail stations, and stops with unknown frequency because no schedule feed covers them).

The 3A comparison is tested in `tests/test_tod_existing_capacity.py` (identical envelope gives 0, a district straddling two tracts conserves capacity, 30 existing / 50 proposed gives up to 20, no 3A land is the benchmark exactly, distance scaling and the cap, the engine and the variant).

Tested in `tests/test_benchmark_variants.py`: unit conversion of the walk table; the benchmark stays straight-line with a walk table present; network 1/4 < 1/2 < 3/4 eligible land; the fallback share and county notes; a network variant on a pack without the table falls back and says so; the decomposition records and sentence.

## Transit status and partial footprints

The adapter adds `status` to the TOD result (`ReformResult.status`, written under `status` in the run's JSON): the five-stage ladder of `features/transit_status.py` for the state and every county (discovered, legal, coverage, headway with the benchmark rule and the peak-rule variant, feasible), each answer one of yes / partial / no / unknown / not_reached / measured_zero, and the completeness label. Feasibility per county is the funnel's eligible and feasible acres at P50. When the state's measured share of bus stops is below `tod.status_min_measured_bus_share`, `status.statewide.shown` is false, the state record's name says "measured contribution only (...); statewide range not shown", the percent-change record says "not shown", and a run note explains why. The numbers themselves are unchanged (packages still add them), so a reader sees the measured contribution, labelled. Testland's stop table has no frequency columns, so its runs carry no status. Tested in `tests/test_transit_status.py` (unknown vs measured zero, discovered-only feeds, legal records, partial footprint and the display share, the Transitland Atlas list, and the engine labels of a hidden statewide range).

## The factorial comparison hooks

For the opt-in comparisons part `tod_factorial` ([comparisons.md](comparisons.md); method in [methods/tod.md](../methods/tod.md), "Factorial comparison of the transit definition") this module provides the service-rule machinery: `SERVICES` (`daily`, `peak_average`, `peak_max_gap`) and `ACCESSES`; `max_scheduled_gap(times, window, dedupe_min=)` (the longest wait in a peak window, window edges included, a bus listed twice counted once); `hourly_gap_lower_bound(counts)` (the smallest that longest wait can be from departures per hour: it can rule a stop out, never confirm it); `gap_screen(peaks, rule)` (per stop, the best platform side's bound over both peaks); `service_flags(stops, feeds, peaks, rule)` (each stop's standing under the three rules, matched to the pack's stops as the walkshed build does); `stops_for_service` and `recoded_transit_columns` (the benchmark's block method redrawn around another rule's major stops, on the same stop table); `walk_straight_columns` (straight-line land on the walkshed grid, so network and straight line compare on one geometry). Tested in `tests/test_tod_factorial.py` (bunched departures fail the longest-gap rule but pass the average; window edges; repeats; a rule picking the benchmark's stops gives the benchmark exactly; paired draws).
