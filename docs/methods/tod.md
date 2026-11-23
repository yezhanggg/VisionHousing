# Method: transit-oriented density (Reform 3)

This note explains how the tool estimates added homes from more height and floor area near major transit stops, which numbers it uses, where they come from, and what the method cannot tell you. The code is `src/visionhousing/scenarios/tod.py`; the module note is [`docs/modules/scenario_tod.md`](../modules/scenario_tod.md).

**Major transit stop** (decision D-07): a fixed-guideway station (subway, light rail or trolley, commuter or regional rail), or a bus stop with at least 64 scheduled departures on a representative weekday (about every 15 minutes for 16 hours).
**FAR** (floor area ratio): a building's floor area divided by its lot area.

## The question

If a state allows mid-rise apartment buildings (FAR 2.5, five storeys) within a set distance of every major transit stop, with less parking, how many more homes would be permitted in 2027–2031 than under today's rules? The answer is a likely range by tract, rolled up to county and state.

## The distance

The distance is a scenario choice, not a ledger value:

| Choice | Meaning | Why offer it |
|---|---|---|
| `d07` (default) | ½ mile from a rail station, ¼ mile from a frequent bus stop | Decision D-07. ½ mile is about a 10-minute walk and the threshold in several state transit-housing laws (California SB 79 uses ½ mile with a ¼-mile inner ring); ¼ mile is the common bus standard |
| `0.25`, `0.5`, `0.75` | That many miles from any major stop | Sensitivity. A wider ring always reaches at least as much land |

Distances are straight lines from 2020 census block internal points to stops (see [`docs/modules/transit.md`](../modules/transit.md)).

## The funnel

```
net added (tract) = L × d × p × capacity per m² × min(1, h × Σ ramp(t)) × absorption factor × net-new share
```

| Step | Plain meaning | Where it comes from |
|---|---|---|
| 1 · Eligible land `L` | Block land within the distance | Tract columns `transit_land_m2_*` (2020 blocks; stops from the National Transit Map and agency schedules) |
| 2 · Developable share `d` | Share of that land that could become a mid-rise site (not parks, schools, recent buildings, small unassemblable lots) | `tod.developable_share_urban` / `_suburban` / `_rural`, by tract area type |
| 3 · Feasible share `p` | Share of developable land where a mid-rise building pencils | Mid-rise pro forma vs. the price of the existing homes (below) |
| 4 · Added capacity | Extra homes the upzoning allows on feasible land | `(min(tod.upzoned_far, tod.upzoned_storeys × tod.lot_coverage) − tod.current_far_allowed) × tod.net_floor_share / apartment size`, never below 0 |
| 5 · Built | Share of capacity permitted in five years | `tod.redevelopment_hazard_annual` with the ramp `tod.ramp_first_year_share`, `tod.ramp_years_to_full` |
| 6 · Absorbed | The county's total is capped by what its apartment market can rent | `tod.absorption_cap_share` × the county's baseline 5+ unit permits 2027–2031 (Building Permits Survey), per draw |
| 7 · Net added | Units that are new to the region, not moved within it | `tod.net_new_share` (0.2–0.9, central 0.5) |

### Step 3 in detail: does mid-rise pencil?

The shared mid-rise pro forma (`proforma.midrise_proforma`) gives the residual land value per apartment, `RLV`: stabilised value (rent × 12 × (1 − operating share) / cap rate) minus hard and soft cost, the $50,000 benchmark fees, structured parking, and the developer's margin.

- Rent = tract two-bedroom median (ACS B25031) × `tod.new_unit_rent_premium`; blank medians use median gross rent × `tod.rent_ratio_2br`.
- **Parking** = `parking.required_spaces_per_unit_multifamily × parking.share_still_built_transit_rich` (decision D-08: about 60% of the old requirement still built near frequent transit), at the structured cost per space. This is the reform's "parking may be assumed reduced", and is channel B inside the TOD pro forma.

At the upzoned intensity a square metre of site holds `n = FAR used × net floor share / apartment floor area` apartments, so it is worth `RLV × n` to a builder. Today it costs its existing homes: `V × h × (1 + tod.acquisition_premium)`, where `h` is homes per square metre of the eligible land (2020 blocks; never below `tod.min_existing_homes_per_acre`, so shops and parking lots are not free) and `V` a home value. `p` is the share of the tract's owner home values (ACS B25075 brackets) at or below the break-even value `RLV × n / (h × (1 + premium))`. The open top bracket is never counted as cheap enough.

### Zero upzoning gives zero

Only floor area above what today's rules allow counts (`tod.current_far_allowed`, 0.4–1.4). Anything allowed today is already in the baseline. If the upzoned FAR is at or below today's, capacity, and every later step, is 0. This is tested.

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
- No parcel data: the developable share is a ledger value by area type, and site prices use tract home values, not assessed land values.
- Rental apartments only; condominiums and local tax abatements (for example Philadelphia's) are not priced, so feasibility in Philadelphia is likely understated.
- The cap uses each county's own baseline 5+ unit permits; a county with none gets no TOD units.
- Station distances are straight lines, not walking routes.
