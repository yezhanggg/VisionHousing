# Places like this one (typology)

Code: `src/visionhousing/features/typology.py` (features, k-means, naming, neighbours), `src/visionhousing/profile/sections/typology.py` (the profile section). Rules: `assumptions/typology.yaml`.

Tests: `tests/test_typology.py` (offline: synthetic points for k-means and the elbow rule, synthetic group centres for the names, Testland for the tables and the section).

## What it does, in plain words

It sorts the state's counties into a few groups of similar places, and does the same for tracts, then names each group in plain words ("Suburban, growing, higher-cost") and, for every place, lists the few places most like it. The profile shows this as "Places like this one". Later, the back-test will use the groups to show whether the baseline errs more for some kinds of place than others.

Nothing here changes a reform estimate. It only describes places.

## Terms used

- **Feature**: one number describing a place (for example its median rent).
- **z-score**: (value - state average) / state standard deviation. It puts every feature on the same scale, so no feature counts more because of its unit.
- **k-means**: a way to split places into k groups so places in a group are close to each other. Lloyd's algorithm: put each place in the group whose centre is nearest; move each centre to the average of its places; repeat until nothing moves.
- **k-means++ start**: the first centre is a random place; each next one is picked with a chance that grows with its squared distance from the centres already chosen, so starts are spread out.
- **Within-group sum of squares W(k)**: the sum, over all places, of the squared distance to their group's centre. Smaller means tighter groups.
- **Elbow rule**: the number of groups after which one more group explains little more.

## Features

Counties (from `counties`, `tracts`, `permits_county_year`, `jobs_county` and `incentives_tract` in the pack):

| Feature | Formula | Transform |
|---|---|---|
| population | ACS 2020-2024 residents | log |
| population_change | ACS 2020-2024 residents / 2020 Census residents - 1 | none |
| housing_density | 2020 homes per km2 of land | log |
| median_rent | mean of tract median gross rents, weighted by renter households | none |
| median_value | mean of tract median home values, weighted by owner households | log |
| permits_per_1000 | average yearly homes permitted over the last 5 years / homes x 1,000 | log |
| transit_share | 2020 homes within the major-transit-stop distance / 2020 homes | none |
| renter_share | renter households / occupied homes | none |
| vacancy_rate | vacant homes / homes | none |
| share_65plus | residents 65 or older / residents (population-weighted) | none |
| jobs_per_resident | jobs located in the county (LODES workplace file) / ACS residents | none |
| qct_share | homes in HUD Qualified Census Tracts / all homes (tract homes summed) | none |

Tracts (from `tracts` and `jobs_tract`; tracts with fewer than 100 residents are left out): housing_density, median_rent, median_value, median_income (log), new_homes_share (homes built 2010 or later / homes), transit_share, renter_share, vacancy_rate, share_65plus, jobs_per_resident (jobs located in the tract / residents, log: a downtown tract can have hundreds of jobs per resident). The tract Qualified Census Tract flag is 0 or 1, so it is not used as a tract feature: a yes/no number would split tracts on that alone.

"log" means log(value + 1), for numbers with a long tail (a few very large counties). A pack without the jobs or incentive tables (for example a state whose job or incentive files could not be fetched) simply leaves `jobs_per_resident` and `qct_share` out; the grouping then runs on the other features.

A missing value is set to the state median of that feature (after the log), so it pulls the place neither way; `imputed_features` counts them. A feature with no spread is dropped.

## Method

1. Work out the features; log the ones marked "log"; fill missing values; z-score.
2. For every k from 3 to 8 (`k_range`), run k-means 10 times (`n_init`) from k-means++ starts and keep the run with the smallest W(k). The random generator is seeded with `seed` (20261123) and k, so the same pack always gives the same groups.
3. Choose k: the smallest k for which going to k + 1 groups lowers W by less than 5% (`min_gain`) of the total sum of squares W(1). If every step gains more, k = 8.
4. Number the groups from the densest (1) to the least dense.
5. Name each group by rule from its centre:
   - a settlement word from the group's typical density (geometric mean of homes per km2; thresholds per level in the rules file; a county's are lower because it averages over farmland);
   - a growth word from the mean z-score of its growth features (counties: permits per 1,000 and population change; tracts: share of new homes): +0.5 or more "growing", -0.5 or less "slow-growth", else "steady";
   - a cost word from the mean z-score of rent and value: "higher-cost" or "lower-cost" beyond ±0.5, else none;
   - if two groups get the same name, each adds the phrase of its most unusual other feature ("more renters", "more empty homes" ...); if that still ties, "(group n)".
6. Similar places: the 3 (`n_similar`) places nearest in the same z-score space, whatever their group.

The profile also lists what sets the group apart: the phrases of its up to three most unusual features, if at least half a standard deviation from the state's average place.

## Pack tables

`typology_county` and `typology_tract` (tier 0, built from tier-0 tables): `geoid`, (`county` for tracts), `type_id`, `type_name`, `k`, `places_in_type`, `distance` (from the group centre, in z-score units), `similar` (nearest first, `;`-separated), `imputed_features`, `features` (used, `;`-separated) and the raw feature values. A pack built before these tables existed still gets the section: the profile works them out from the pack's own tables with the same function.

Pennsylvania (pack of 2026-11-23, with the jobs and incentive features): k = 5 county groups, "Big-city, growing, higher-cost" (1: Philadelphia, set apart by its share of homes in Qualified Census Tracts), "Suburban, growing, higher-cost" (19, Allegheny among them), "Small-town, steady" (18), "Rural, steady, lower-cost" (27) and "Rural, slow-growth, lower-cost" (2: Cameron, Forest); k = 5 tract groups. Philadelphia's nearest counties: Delaware, Allegheny, Lehigh.

## Limits

- Groups say how alike places are on these numbers, not why.
- Survey margins of error are not carried into the grouping.
- The elbow rule is a rule of thumb, not a test that the groups are real. Another threshold gives more or fewer groups; places near a group's edge may be as close to the next group.
- The features weigh equally; a different list would group places differently.
- Population change compares a 5-year survey average with the 2020 count, so it is a rough growth signal.
