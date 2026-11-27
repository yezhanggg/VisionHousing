# Method: parking reduction (benchmark 4)

## The question

How many more homes would be permitted in 2027–2031 if residential parking
minimums were removed? The Handbook asks teams to state what developers still build.

## What developers still build

The benchmark parking rule: after the minimums go, projects still build about 60% of the spaces
once required in transit-rich tracts (range 45–75%) and 85–100% elsewhere (central
92.5%). Lenders, buyers and tenants still want parking, so most of the saving
appears only where transit makes cars optional. Both shares are ledger entries
(`parking.share_still_built_transit_rich`, `parking.share_still_built_elsewhere`) and
can be changed per run. If both are 1, nothing changes and the effect is exactly zero.

## The chain

1. Spaces required per unit before the reform: 1.5 for apartments (5+ units), 2.0
   for 2-unit and 3–4-unit buildings. These are **defaults**, used wherever no
   verified local schedule exists (draft judgement; a sample of Pennsylvania
   codes will replace them). 1-unit buildings are not reached: the permit data
   cannot separate townhouses from detached homes, so townhouse savings are left out.
2. Spaces avoided = required × (1 − share still built).
3. Cost per space: apartments use the tract class's mix of surface, above-ground
   structured and underground spaces; 2–4-unit buildings are mostly surface
   (`parking.mix_surface_attached`). Costs per space are sourced from published cost
   reviews (surface about $8,000 with land; structured about $52,000 and underground
   about $73,000 excluding land).
4. Saving per unit ΔC = spaces avoided × cost per space.
5. Cost channel: baseline × ε × (ΔC ÷ V) × k, with the same ε and V as the fee
   reform (see [fees.md](fees.md)) and the general pass-through share
   `supply.saving_passthrough_k`.
6. Site yield: baseline × `parking.site_yield_gain` × share of spaces avoided. The
   gain (central 5%, range 0–15%) is the extra units a site could hold if all its
   parking land were freed; it is scaled by the share actually freed, so it is small
   away from transit.

## Spaces before and after, in matched prototypes

Every run reports, for an apartment building (5+ homes) and a 2–4 unit
building, near frequent transit and elsewhere, the spaces per home provided
**before** the reform (the old minimum; it binds) and **after** it (records
`provided_<type>_<access>_before` / `_after`), at central values. Spaces avoided
are before minus after in the same building type and transit class, so the
saving never mixes a downtown apartment with a suburban duplex.

**Variant `parking_provided_spaces`** (`response: provided`) replaces the benchmark
shares with what builders would provide anyway, per home, by building type and
transit access: `parking.voluntary_spaces_apartment_transit_rich` (0.7, range
0.4–1.0), `..._apartment_elsewhere` (1.2), `..._small_transit_rich` (0.9),
`..._small_elsewhere` (1.5). After = min(minimum, provided anyway): where
builders would provide more than the minimum, the reform avoids nothing.

## Where a minimum actually binds

Every response above is one rule, applied to a matched design (the same
building type in the same transit class):

    spaces avoided = max(v, m_before) - max(v, m_after)

- `v` is the spaces a project would provide with no minimum (what lenders,
  buyers and tenants ask for);
- `m` is the legal minimum. Where no verified local schedule exists the minimum
  is the ledger **default** of 1.5 spaces per apartment and 2.0 per home in a
  2-4 unit building, and it is labelled as a default. In the benchmark,
  verified local rules enter only where they set the minimum to zero (the
  county reach factor, in counties whose verified rows cover at least half of
  their residents). Verified non-zero schedules enter through the variant
  `parking_verified_minimums` (next section).

A minimum **binds** a project when it asks for more than the project would
build anyway (`m > v`). Where the old minimum is zero, or the project would
build at least as many spaces anyway (`v >= m_before`), the reform avoids
nothing. Both cases are tested.

**The binding share** is the share of baseline homes (in the building types
the reform reaches) whose project the minimum binds. Every run reports it in
every draw at several levels of a partial cut: the share of today's minimum
left in force, 100%, 75%, 50%, 25% and 0% (levels in
`assumptions/cost_reforms.yaml`). Records `binding_share_kept_<pct>` hold the
binding share and `added_kept_<pct>` the added homes at each level, from the
same draws. `binding_share_before` is today's rule and `binding_share_after`
the rule the run leaves in force.

**Why halving the minimum adds about as many homes as removing it.** Under
the benchmark, `v` is a share of the old minimum: about 60% of it near
frequent transit (45-75%) and 85-100% elsewhere. So the old minimum binds
every project, but a minimum cut to half binds almost none: projects already
want more than half the old minimum. The binding share at a 50% minimum is
close to zero, and the added homes at "halved" and "removed" are nearly the
same. In Pennsylvania the benchmark's added homes are flat between half and
full removal for exactly this reason; the policy dial (`parking_minimum`) shows
the same flat step.

**Variant `parking_voluntary_floor`** (`response: voluntary`) makes `v` differ
between projects. Within a building type and transit class, projects spread
evenly between the `parking.voluntary_spaces_*` average x (1 - spread) and x
(1 + spread), with the spread `parking.voluntary_project_spread` (central 0.4,
range 0.2-0.6). Some projects then provide more than the old minimum and save
nothing, and the binding share before the reform is below 100%. The average of
`max(v, m)` over projects has a closed form (no extra random numbers). With a
spread of zero the variant equals `parking_provided_spaces` exactly (tested).

## Verified local minimums (variant `parking_verified_minimums`)

The benchmark's old minimum is the ledger default everywhere. The variant
`parking_verified_minimums` (option `minimums: verified`) uses each
municipality's own minimum wherever a reading of its ordinance is verified
and in force on the run's as-of date, and the labelled default everywhere
else. Blank is never zero: a municipality not read, or read without a parking
number, keeps the default.

- **Which rows.** `visionhousing.law.parking_rules.verified_parking_minimums`
  reads the pack's `muni_zoning_rules` table (read-only) with the same
  in-force test as the law hooks. A rule whose effective date is after the
  as-of date is not law yet. Pittsburgh's Ordinance 31 (Chapter 914 replaced:
  no automobile minimum, only maximums) is effective October 29, 2026, so it
  counts as a minimum of 0 from that date and not before (tested).
- **Which number.** Apartments (5+ homes) use the per-apartment figure
  (`parking_min_per_unit_mf`); a home in a 2-4 unit building uses that same
  per-dwelling figure, or the single-family figure where only that is read.
  The field order is a setting (`verified_minimums` in
  `assumptions/cost_reforms.yaml`).
- **Weights.** A tract's existing homes of the building type (ACS units in
  structure) are split among the municipalities it touches by their 2020
  housing units in it. Shares are taken within each transit class, so a
  city's minimum lands on its own transit-rich tracts.
- **The rule.** Spaces avoided = max(v, m) − max(v, m left), averaged over the
  county's shares, with `m` the verified or default minimum and `v` set from
  the **default** minimum as in the run's response (a local minimum changes
  the law, not what projects want). So a verified minimum at or below `v`, or
  a verified zero, avoids nothing; one above the default avoids more. The
  site-yield term uses spaces avoided ÷ the default minimum (the parking land
  a default site would free). The county reach factor is not applied in this
  variant: verified zeros are already in the mix.
- **Coverage.** Every run of the variant reports, in every draw, the share of
  baseline homes (2-, 3-4- and 5+-unit buildings) under a verified minimum
  (`parking_verified_share`), the share still on the default
  (`parking_default_share`), the share under a verified zero
  (`parking_verified_zero_share`), the old legal minimum
  (`parking_legal_minimum`) and the spaces provided before the reform
  (`parking_provided_before`), all baseline-weighted, beside the binding-share
  curve. With no law tables (Testland) the variant equals the benchmark
  exactly (tested).

The benchmark headline does not change: verified non-zero minimums enter
only through this named variant.

## The documented 50% bar (variant `parking_verified_minimums_bar`)

The law hooks let a county use its own read values only when its verified
rows cover at least half of its residents (`min_coverage` in
`assumptions/law/hooks.yaml`). The variant `parking_verified_minimums_bar`
(option `minimums: verified_bar`) applies that same bar to non-zero
minimums:

- **Where.** Only the counties the parking hook qualifies (the run's notes
  name them; on 2026-11-26 Fayette, Pike and Warren). Every other county
  keeps the labelled default, even where some municipalities are read.
- **Which numbers.** The verified, in-force minimums above zero of those
  counties' municipalities (`scenarios.parking.bar_minimums`), mixed in as in
  `parking_verified_minimums` (same weights, same rule, `v` from the default).
- **Zeros as in the benchmark.** A verified zero is not put in the mix; it
  keeps working through the county reach factor, exactly as in the
  benchmark, so nothing is counted twice.
- **Checks (tested).** With no county over the bar the variant equals the
  benchmark draw for draw (up to floating-point rounding); only counties over
  the bar change; zero minimums give the benchmark's reach-factor result.

It is shown beside the benchmark in "Alternatives at a glance"
(`assumptions/alternatives.yaml`, Pennsylvania only). The benchmark headline
does not change.

## Vermont and Wyoming local readings (variants `vt_parking_town_bylaws`, `wy_parking_local`)

Vermont and Wyoming have no municipal rule table. Their town and city
readings (`assumptions/law/local_readings.yaml`) feed the same mix through
option `minimums: local_readings` and `readings_state` (`VT` or `WY`):

- **Which numbers.** Only a place whose reading is verified and counts from a
  date on or before the run's as-of date (effective date, else `in_force_by`,
  else review date), and only its `parking` fact marked verified. A draft fact
  (Rutland), a fact with an open flag (Essex and Hartford: the state cap on
  minimums was not checked; `skip_flagged`), a superseded text (Teton County)
  and a place not read keep the default. Numbers are tried in the order of
  `apartment_fields` / `small_fields` (smallest stated minimum first);
  Laramie's apartments keep the default, because its per-home figure is the
  one- and two-family one (`generic_not_for_apartments`). Settings:
  `local_readings` in `assumptions/variants.yaml`.
- **Where.** As in `parking_verified_minimums`: each tract's homes split among
  its county subdivisions by 2020 housing units (`tract_municipalities`). A
  reading of a Census place that is not a county subdivision (Laramie, 7-digit
  `geoid`) is split the same way over the place pieces of `tract_places`
  (`law/places.py`: `place_pieces`, `readings_on_places`); the two tables are
  never mixed in one run (a state whose readings in force mix both keeps the
  places on the default, noted). The county reach factor stays as in the
  benchmark.
- **Checks (tested).** In another state, with nothing in force, or with no way
  to place the places on tracts, the variant equals the benchmark draw for
  draw (up to floating-point rounding).

Since D6 (2026-11-26) the Vermont pack has `tract_municipalities` and the
Wyoming pack `tract_places` (see [pack](../modules/pack.md)), so every reading
in force is placed on tracts. Runs, seed 0, 2,000 draws, P50 (P10-P90), as-of
2026-11-24:

- `vt_parking_town_bylaws`: benchmark 82.5 (33.6-166.4), variant 82.5
  (33.7-166.0), paired difference -0.7 (-3.3 to +1.7). Barre and Montpelier (1
  space per home) sit below the 1.5 / 2.0 default, so their homes avoid fewer
  spaces; Colchester (1.5) changes little.
- `wy_parking_local`: 21.2 (8.4-48.0), equal to the benchmark (paired 0):
  Laramie counts from its review date, 2026-11-26, after the as-of date.

The same runs with the readings read on 2026-11-26 (a check, not shipped):
Vermont parking 29.3 (11.4-63.1), paired -51.5 (-105.2 to -21.1), mostly
Burlington's zero minimum (nothing left to remove there); Wyoming 21.2
(8.3-48.0), paired -0.03 (-0.07 to -0.01): Laramie's 1 space per home in 2-4
unit buildings is below the default, and its apartments keep the default.

## Baseline-window sensitivity

**Variant `parking_baseline_window_pa_2022_2025`** (Pennsylvania only) re-runs
the reform with the baseline under the 2022-2025 permit window, the diagnostic
`baseline_pa_window_2022_2025` from the baseline card. Each county's baseline is
scaled by that diagnostic's five-year total / the shipped total (record
`baseline_window_factor`). The parking estimate is linear in the baseline, so
this is the same as running it on that baseline. It is shown beside the
headline as a sensitivity and never subtracted from anything.

## Surface or structured

Each space avoided is costed by the mix of surface, structured and underground
spaces of its tract class. Two records bound the saving per home at central
values: `saving_all_surface` and `saving_all_structured` (every space avoided
costed as surface, or as structured). Two variants run the whole estimate at
those bounds: `parking_all_surface` and `parking_all_structured`.

## The no-response case

**Variant `parking_no_response`** sets both shares still built to 1
(`parking.no_response_share_still_built`, fixed): builders keep every space
the old minimum required. Spaces avoided, the saving and the site-yield share
are then zero, so the reform adds exactly zero homes (tested).

## Transit-rich tracts

A tract is transit-rich when at least half of its homes are in census blocks whose
centre is within ½ mile of a rail station or ¼ mile of a bus stop with 64 or more
weekday departures, or when a major stop lies inside the tract. The
second test matters where blocks are large; in cities, small tracts that contain a
station are mostly within walking distance of it. Distances are straight-line.

Baseline permits are known by county, not tract. Each county's saving for a building
type is therefore the two classes' savings weighted by the share of the county's
existing homes of that type in transit-rich tracts. Everything after the saving is
linear, so this weighting is exact for the cost channel.

## Pro-forma cross-check

As for fees: the saving (times k) lowers each pro forma's break-even, and the rise in
the share of sites that pencil gives a second estimate, reported side by side.

## What this cannot tell you

- Whether lenders in a given market would finance projects with fewer spaces.
- Effects on street parking or on nearby residents.
- Whether freed land is used for homes or for other uses.
- Savings on townhouses counted as 1-unit buildings in the permit data. The
  permit data cannot separate townhouses from detached homes, so this
  omission is marked, not bounded.
- Shared parking in mixed-use projects and required accessible spaces are not
  modelled separately.
- Census vehicle-availability counts could stratify `v` by area, but they say
  what households own, not what a developer builds; they are not used.
