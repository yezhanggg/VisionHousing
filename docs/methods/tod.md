# Method: transit-oriented density (Reform 3)

This note explains how the tool estimates added homes from more height and floor area near major transit stops, which numbers it uses, where they come from, and what the method cannot tell you. The code is `src/visionhousing/scenarios/tod.py`; the module note is [`docs/modules/scenario_tod.md`](../modules/scenario_tod.md).

**Major transit stop**: a fixed-guideway station (subway, light rail or trolley, commuter or regional rail), or a bus stop with at least 64 scheduled departures on a representative weekday (about every 15 minutes for 16 hours).
**FAR** (floor area ratio): a building's floor area divided by its lot area.

## The question

If a state allows mid-rise apartment buildings (FAR 2.5, five storeys) within a set distance of every major transit stop, with less parking, how many more homes would be permitted in 2027–2031 than under today's rules? The answer is a likely range by tract, rolled up to county and state.

## The distance

The distance is a scenario choice, not a ledger value:

| Choice | Meaning | Why offer it |
|---|---|---|
| `d07` (default) | ½ mile from a rail station, ¼ mile from a frequent bus stop | The benchmark rule. ½ mile is about a 10-minute walk and the threshold in several state transit-housing laws (California SB 79 uses ½ mile with a ¼-mile inner ring); ¼ mile is the common bus standard |
| `0.25`, `0.5`, `0.75` | That many miles from any major stop | Sensitivity. A wider ring always reaches at least as much land |

Distances are straight lines from 2020 census block internal points to stops (see [`docs/modules/transit.md`](../modules/transit.md)). The benchmark preset keeps `d07`.

### Network walksheds and the peak frequent-bus rule (variants)

Where the state pack has the table `transit_walk_tract` ([`docs/modules/transit_walk.md`](../modules/transit_walk.md)), three named variants measure the distance on foot instead:

| Variant | Distance on the street network | Major stops |
|---|---|---|
| `tod_network_half_mile` | ½ mile (the network default) | rail, and bus stops every 15 minutes or better in both weekday peaks |
| `tod_network_quarter_mile` | ¼ mile | same |
| `tod_network_three_quarter_mile` | ¾ mile | same |

For each tract (`scenarios.tod.network_columns`):

- eligible land = network walkshed acres × 4,046.86 m² per acre × (tract land area ÷ tract outline area, which includes inland water);
- homes on it = eligible land × homes per m² of the straight-line land at the same distance (2020 blocks), or the tract's own density where that is blank;
- fallback land = the part of the walkshed that is a straight-line circle because the mapped street network was missing or fragmentary near a stop.

A pack whose walkshed table has no rows counts as having none, so the run says it uses straight-line distance. A state whose stop table has no major stop (for example a state with no rail whose bus stops all have an unknown frequency because no schedule feed covers them) gets a run note and a county note naming that cause, and the zero decomposition says "No major transit stops in this state" instead of "almost no land near a major stop": the zero is a gap in the transit data or service, not a measured zero of land.

Every network run discloses the fallback: a state record `walk_fallback_share` (fallback land ÷ eligible land) and, for each county, a note with its own share and acres. Every straight-line run says per county that its land is straight-line from major stops, and, where the pack has no walkshed table, says that too; asking for a network variant on such a pack falls back to the straight-line distance of the same length from major stops and says so.

### What is known about transit service: the status ladder

Before any homes are counted, every TOD run reports, for the state and each county, five separate answers (`features/transit_status.py`; module note [`transit.md`](../modules/transit.md), "Feed discovery and the status ladder"): is a schedule feed **discovered** for the agencies there; is it **legally usable**; how much of the National Transit Map's bus-stop inventory has **measured service** (coverage); does any stop **qualify** under the benchmark rule (rail, or ≥ 64 scheduled weekday bus departures) and under the peak-headway variant; and is the mid-rise prototype **financially feasible** on the eligible land. "Unknown" (some stops not measured) is a different answer from "measured zero" (every stop measured, none qualifies). A feed that is only discovered adds no homes.

When some agencies' service is not measured, the footprint is **partial** and every output says so ("partial footprint: M of N bus stops (x%) from a of b agencies have measured service"). If the measured share of bus stops is below `tod.status_min_measured_bus_share` (0.5), nothing bounds what the unmeasured stops would add, so the statewide range and percent change are marked "not shown" and the run reports the **measured contribution** only, with its label (JSON result: `status.statewide.shown` = false). Wyoming is the case today: two operators' feeds (START in Jackson, Cheyenne Transit) are read, five agencies' are not.

### Factorial comparison of the transit definition (comparisons part `tod_factorial`)

The benchmark makes three choices at once: which stops count (**service rule**), how distance is measured (**access**) and how far (**distance**). The opt-in comparisons part `tod_factorial` (`engine/comparisons/tod_factorial.py`; settings `assumptions/comparisons.yaml`, block `tod_factorial`) changes them one at a time and all together, so a reader can see which choice moves the estimate. The benchmark itself is not changed: it stays the daily-count rule, straight line, rail ½ mile and bus ¼ mile, under its own id, and every other cell is a separate run.

| Factor | Levels (benchmark first) |
|---|---|
| Service rule | `daily`: rail, or a bus stop with at least `parking.transit_bus_min_departures` weekday departures. `peak_average`: rail, or a bus stop whose busiest platform side averages a bus every `tod.frequent_bus_max_headway_min` minutes or better in both peak windows (`tod.frequent_bus_am_*`, `tod.frequent_bus_pm_*`; the network variants' rule). `peak_max_gap`: the longest scheduled gap in each window is at most that headway, on one platform side |
| Access | `straight` line from 2020 block internal points; `network` walk (`transit_walk_tract`) |
| Distance | `d07` (rail ½, bus ¼ mile); ¼, ½, ¾ mile from any major stop |

**Same stops, same draws.** Every cell reads the pack's one stop table; only the rule that picks the major stops changes. A straight-line cell for a peak rule redraws the tract columns with the benchmark's own block method (`scenarios.tod.recoded_transit_columns`), so a peak rule that picked exactly the benchmark's stops would give the benchmark exactly (tested). Each cell runs the full funnel with the benchmark's ledger, seed, absorption cap and Section 3A rule. The **paired difference** is worked out draw by draw (cell − benchmark) before any percentile is taken; percentiles are never added or subtracted. Each cell also reports the share of draws in which it is below or above the benchmark ("in X% of modeled draws") with its Monte Carlo standard error, √(p(1 − p)/n).

**The longest-gap rule.** "Every 15 minutes or better" is a claim about the longest wait, not the average: eight buses in two hours can all leave in the first fifteen minutes. The rule (`scenarios.tod.max_scheduled_gap`) measures, on one platform side and in each window `[start, end)`, the gaps from the window start to the first departure, between departures, and from the last departure to the window end (convention `window_edges`). A bus listed twice (shared or interlined trips) counts once (`max_gap.dedupe_minutes`). The cached schedules hold departures per platform side and hour, already counted on a representative non-holiday weekday after calendar exceptions, with no-pickup calls and each trip's last stop left out and frequency-based trips expanded ([transit_walk.md](../modules/transit_walk.md)). Hourly counts give a lower bound on the longest gap (`hourly_gap_lower_bound`): in an hour with `c` departures some gap is at least 60/(`c` + 1) minutes, and over a window of `m` minutes with `n` departures at least `m`/(`n` + 1). A stop side above the headway fails for sure; any other is only **not ruled out**, because bunching inside an hour cannot be seen. Duplicate listings can hide a failure but never invent one. The `peak_max_gap` cells keep the stops not ruled out and are labelled an **upper reading**; they are not ranked. Exact gaps need departure times, which no cached file holds; the comparison fetches nothing.

**Cells that cannot be measured, and other outcomes.** Each cell has one outcome:

| Outcome | Meaning |
|---|---|
| `estimate` | An ordinary likely range |
| `small_estimate` | P50 above 0 but below `small_estimate_homes`: shown rounded, not as zero |
| `infeasible` | Land is near qualifying stops, but the prototype pencils nowhere (P50) |
| `no_qualifying_service` | Stops were measured, but no land is near a qualifying one |
| `schedules_missing` | No bus stop has the schedule counts the rule needs |
| `not_measurable` | The rule needs data the cache cannot give (longest gap on the network) |
| `not_measured` | The pack lacks the input (no walkshed around the daily-rule stops; no walkshed table) |
| `not_defined` | The walkshed table holds single distances, so the mixed `d07` distance has no network version |

**Access is measured off the benchmark.** The pack's walksheds are drawn around peak-rule stops at single distances, so the access change has no cell next to the benchmark. It is measured at the anchor `access_anchor` (peak average, ½ mile): the network walk against the straight-line circle **on the same walkshed grid** around the same stops (`walk_straight_columns`), so the difference is the access measure alone. A bridge record gives the gap between the two straight-line measures (walkshed grid against 2020 blocks), a method difference rather than a policy effect.

**Which factor moves TOD most.** For each factor, the largest |P50 of the paired difference| among its measured one-factor changes (access at the anchor); the factor with the largest is named, with the rule written beside it.

**Footprints.** Every cell carries the footprint label: the benchmark's status ladder for the daily rule, and for the peak rules the share of bus stops with peak counts. Where that share is below `tod.status_min_measured_bus_share`, the cell's statewide range is marked not shown (`statewide_shown` false), as for the benchmark.

## The funnel

```
net added (tract) = L × d × p × capacity per m² × min(1, h × Σ ramp(t)) × absorption factor × net-new share
```

| Step | Plain meaning | Where it comes from |
|---|---|---|
| 1 · Eligible land `L` | Block land within the distance | Tract columns `transit_land_m2_*` (2020 blocks; stops from the National Transit Map and agency schedules) |
| 2 · Developable share `d` | Share of that land that could become a mid-rise site (not parks, schools, recent buildings, small unassemblable lots) | `tod.developable_share_urban` / `_suburban` / `_rural`, by tract area type |
| 2b · Land already zoned by right | Where the state already allows multi-family homes by right near transit (Massachusetts: Section 3A), the same developable share `d` of that land, `D3`, is its developable part; step 4 counts only the capacity above what it already allows (below) | Pack table `mbta_3a_capacity_tract` (MassGIS 3A atlas parcels, split across tracts by area); records `existing_3a_*`, `proposed_3a_capacity`, `additional_3a_capacity` and a county note. States without the table are unchanged |
| 3 · Feasible share `p` | Share of developable land where a mid-rise building pencils | Mid-rise pro forma vs. the price of the existing homes (below) |
| 4 · Added capacity | Extra homes the upzoning allows on feasible land, beyond what the baseline would permit on those sites | `(min(tod.upzoned_far, tod.upzoned_storeys × tod.lot_coverage) − q × tod.current_far_allowed) × tod.net_floor_share / apartment size`, never below 0; `q` = `tod.baseline_replacement_probability`, the chance the site would have been redeveloped at today's floor area anyway. On feasible land already zoned by right (`D3 × p`): `max(0, proposed − existing)` homes per m² (below) |
| 5 · Built | Share of capacity permitted in five years | `tod.redevelopment_hazard_annual` with the ramp `tod.ramp_first_year_share`, `tod.ramp_years_to_full` |
| 6 · Absorbed | The county's total is capped by what its apartment market can rent | `tod.absorption_cap_share` × the county's baseline 5+ unit permits 2027–2031 (Building Permits Survey), per draw |
| 7 · Added units authorized (headline) | Permits that are new to the region, not moved within it | absorbed × `tod.net_new_share` |
| 8 · Net added homes | The change in the housing stock | added units authorized − (1 − q) × homes on the redeveloped land today (2020 blocks) |

### Step 3 in detail: does mid-rise pencil?

The shared mid-rise pro forma (`proforma.midrise_proforma`) gives the residual land value per apartment, `RLV`: stabilised value (rent × 12 × (1 − operating share) / cap rate) minus hard and soft cost, the $50,000 benchmark fees, structured parking, and the developer's margin.

- Rent = tract two-bedroom median (ACS B25031) × `tod.new_unit_rent_premium`; blank medians use median gross rent × `tod.rent_ratio_2br`.
- **Parking** = `parking.required_spaces_per_unit_multifamily × parking.share_still_built_transit_rich` (about 60% of the old requirement still built near frequent transit), at the structured cost per space. This is the reform's "parking may be assumed reduced", and is channel B inside the TOD pro forma.

At the upzoned intensity a square metre of site holds `n = FAR used × net floor share / apartment floor area` apartments, so it is worth `RLV × n` to a builder. Today it costs its existing homes: `V × h × (1 + tod.acquisition_premium)`, where `h` is homes per square metre of the eligible land (2020 blocks; never below `tod.min_existing_homes_per_acre`, so shops and parking lots are not free) and `V` a home value. `p` is the share of the tract's owner home values (ACS B25075 brackets) at or below the break-even value `RLV × n / (h × (1 + premium))`. The open top bracket is never counted as cheap enough.

### Land already zoned by right: Massachusetts Section 3A

Section 3A of the state's zoning act requires each MBTA community to zone at least one district where multi-family homes are allowed by right. Many such districts are near the same stations the TOD reform reaches, so part of the reform's "new" capacity is already legal. The tool compares the two on the same land, in the same unit (gross homes):

```
additional legal capacity = max(0, proposed capacity − existing legal capacity)
proposed = feasible 3A land × homes per m² of the FAR 2.5, five-storey prototype
existing = feasible 3A land × homes per m² the 3A district already allows
```

- **Existing legal capacity** is the state's own compliance model: each parcel's final unit capacity (atlas field `F_L_M__`, after every lot limit), split across tracts by the share of the parcel's area in each, never by its centre ([ma_capacity.md](../modules/ma_capacity.md)). Its density on the tract's 3A land is `e` (homes per m²).
- **Same land.** The pack holds walkshed acres, not shapes, so the 3A land inside the run's distance is worked out per tract from the table: the station-area part (parcels the compliance model places in a half-mile station area) × min(1, eligible land ÷ half-mile land), plus the rest × the share of the tract's land beyond the half mile that the distance reaches. The station-area part of the land is taken as its part of the capacity (3A density even within a tract): an approximation. The 3A land is capped at the eligible land, keeping its density.
- **Only the affected fraction.** The same developable share `d` and the same feasibility `p` apply to 3A land as to other land, so only `D3 × p` of it is compared, and the maximum is taken per tract before any sum (a tract where the district already allows more never offsets another).
- **Then the usual funnel.** Take-up (owners' redevelopment timing), the absorption cap, the net-new share and the homes removed apply exactly as on other land.
- **15 per acre versus about 105 per acre.** Section 3A sets a minimum gross density of 15 homes per acre for a district as a whole (with conditions). The prototype, FAR 2.5 with 850 sq ft apartments, is about 105 homes per acre on one lot, before other limits. These are different measures with different land denominators, so the tool never compares the prototype with the 15-per-acre floor; it compares it with each parcel's own modeled capacity.
- **Tracts not in the table.** The atlas holds only towns found fully compliant. A tract with no row holds no compliant 3A district in the atlas: its existing 3A capacity is 0 and the base-zoning rule (`q × tod.current_far_allowed`) applies there. The pack has no tract-to-town table, so the town's `mbta_3a_status` cannot be attached to a tract; the share of eligible land in tracts the table speaks to is reported (`existing_3a_resolved_share`), and the rest is named as not resolved.
- **Accounting.** On 3A land the full existing capacity is subtracted (it is legal already, whatever the chance of redevelopment), not `q ×` it as on base-zoning land; this is the legal-capacity increment the plan asks for and is the more cautious reading. The baseline authorizations on redeveloped 3A sites are recorded as `q × min(e, proposed)`.
- **The older rule** (district acres by centre tract × `tod.state_zoned_overlap_share` taken out of `L × d`) is variant `tod_ma_acre_share`, so the change has a same-input bridge. It took whole acres out; the new rule keeps the land and counts only the capacity above the district's.

Tests (`tests/test_tod_existing_capacity.py`): an identical pre/post envelope adds 0 at every stage; a district straddling two tracts conserves its capacity and its increment; a district allowing 30 homes raised to 50 adds 20 of capacity (and no more after take-up); no 3A land reproduces the benchmark exactly; the engine reads the table and the variant reproduces the older rule.

### Zero upzoning gives zero

Today's floor area (`tod.current_far_allowed`) counts against the reform only to the extent the baseline would have used it on the same sites: `q × today's FAR`. With `q = 1` and an upzoned FAR at or below today's, capacity, and every later step, is 0. This is tested. See `docs/methods/outcome.md` for gross and net.

## Variant: a 3–4 storey walk-up, and why a state's TOD can be zero

The benchmark prototype is a five-storey mid-rise (FAR 2.5) with structured parking. In small cities and rural station areas that building rarely pencils. **Variant `tod_lowrise`** swaps in a three- to four-storey wood-frame walk-up with surface parking: `tod.upzoned_storeys` → `tod.lowrise_storeys` (4, range 3–4), `tod.upzoned_far` → `tod.lowrise_far` (1.8, 1.4–2.2), `multifamily.hard_cost_per_sqft` → `tod.lowrise_hard_cost_per_sqft` ($230, $180–280), and the structured cost per space → the surface cost per space.

**Zero decomposition.** The option `decompose` runs four cases on the same draws and reports, for each, eligible land, feasible land and added units (records `decomp_<case>_*`):

| Case | Prototype | Coverage |
|---|---|---|
| `benchmark` | mid-rise | the run's distance |
| `lowrise` | 3–4 storey walk-up | the run's distance |
| `wider` | mid-rise | ¾ mile from any major stop |
| `lowrise_wider` | walk-up | ¾ mile |

A rule turns them into one sentence (`scenarios.tod.decomposition_verdict`, P50 values): almost no eligible land in any case → transit coverage binds; the walk-up adds homes where the mid-rise does not → the prototype binds (and if the wider coverage adds much more, coverage binds too); neither adds homes at either coverage → rents and prices bind under both; only the walk-up at the wider coverage adds homes → both bind. For Vermont this answers whether the zero is the prototype, the transit coverage, or both.

## Calibration and evidence

- **Slow take-up.** Freemark (2020) found no detectable rise in permits within about five years of Chicago's station-area upzonings, while land prices rose (`ev.tod.chicago_freemark`, abstract only). This supports a low yearly share (`tod.redevelopment_hazard_annual` 0.5–5%, central 2%), a slow ramp, an acquisition premium, and a cap tied to the regional market.
- **Intensity.** FAR 2.5 with 850 sq ft apartments is about 105 homes per acre, inside California SB 79's 80–120 per acre floors (`ev.tod.california_sb79_2025`).
- Every other `tod.*` value is team judgement (status draft) with a wide range and a "revisit" note.

## Packages: no double counting

- **With missing middle.** A single-family lot inside a transit area counts under the denser reform, TOD. `engine.packages.tod_package` removes missing-middle units inside the TOD area: each tract's missing-middle net units × the share of its homes within the distance, applied to what missing middle keeps after ADU lots. It is behind the flag `TOD_WINS_OVER_MIDDLE` and is a separate function; the engine's core waterfall is unchanged until it is wired in.
- **With the parking reform.** The TOD pro forma already has the reduced parking. The parking reform's cost saving and site yield act only on baseline production; TOD units are added on top of the baseline. So a unit is never counted by both, and no "parking → TOD" interaction is added.
- **With the fee cut.** The fee cut does not re-enter the TOD pro forma. The package slightly understates the combined effect.

## What the method cannot tell you

- **Net-new share is the headline caveat.** Some apartments near stations would have been built elsewhere in the same region anyway. The tool counts only `tod.net_new_share` of them, and that share is very uncertain (0.2–0.9).
- Section 3A capacity is the state's compliance model, not what owners will build. Which part of a tract's 3A land lies inside the run's distance is worked out from areas, not shapes (no walkshed outlines in the pack), and the station-area land share is taken as the capacity share. MBTA communities not yet in the atlas (not fully compliant) are not resolved by tract and get no 3A capacity.
- No parcel data: the developable share is a ledger value by area type, and site prices use tract home values, not assessed land values.
- Rental apartments only; condominiums and local tax abatements (for example Philadelphia's) are not priced, so feasibility in Philadelphia is likely understated.
- The cap uses each county's own baseline 5+ unit permits; a county with none gets no TOD units.
- Where some agencies publish no readable schedule, their stops never count as major; the result is the measured contribution, labelled partial (Wyoming: 135 of 349 bus stops measured). Scheduled service is for one representative weekday of the feed read; a seasonal operator's off-season schedule can understate its peak-season service.
- The benchmark's station distances are straight lines; the network variants use mapped streets and paths, which miss unmapped sidewalks, fences and unsafe crossings.
