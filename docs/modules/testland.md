# Testland: the synthetic test state

## What it is

Testland (state code `TL`) is a small, made-up state. Nothing in it is real. It has
4 counties, 40 tracts, 400 blocks, 30 transit stops and permit counts for 1996 to 2025.
Its tables use the same column names and shapes as a real state pack. So every test, and
the interface, can run offline with no downloads.

The files live in `tests/fixtures/testland/` and total about 150 KB.

## Why

Tests must pass on a clean checkout with no network. Real data is fetched by script and
never stored in the repository. Testland fills that gap: it is tiny, fully synthetic, and
committed.

## Files

| File | One row per | Key columns |
|---|---|---|
| `counties.csv` | county | `geoid`, `name`, `population`, `housing_units` |
| `tracts.csv` | tract | `geoid`, `county`, `area_type`, `land_area_m2`, `housing_units`, units in structure, owner and renter households, vacancy, income, home value, rent and rent by bedrooms, share aged 65+, value and rent bracket counts |
| `blocks.csv` | block | `geoid`, `tract`, `housing_units`, `land_area_m2`, `lat`, `lon` |
| `permits_county_year.csv` | county and year | `units_1`, `units_2`, `units_3_4`, `units_5plus`, `imputed` |
| `transit_stops.csv` | stop | `stop_id`, `lat`, `lon`, `mode` (rail or bus), `weekday_departures` |
| `boundaries.geojson` | county or tract | rectangles; properties `level`, `geoid`, `name` |
| `acs_bands.csv` | state, county or tract | `geoid`, `level`, then each collapsed survey band (age, race and ethnicity, household size, income, education, commute mode, owners and renters, building type, year built, rent burden, vacancy reasons) and its `_moe`; tracts carry only the tract groups; county and state rows add the medians |
| `infra_tract.csv` | tract | water and sewer readiness, same columns as a real pack (`features/infrastructure.py`): `sewer_status`, `sewer_share`, `sewer_known`, `constraint_share`, `sewer_source`, `water_share`, `epa_share`, `act537_class` (blank) |
| `hazard_tract.csv` | tract | screening shares, same columns as a real pack (`features/hazards.py`): `sfha_share`, `sfha_dev_share`, `developed_share`, `impervious_share`, `steep_share` |
| `parcels_area.csv` | tract or county, two counties only | parcel land use, same columns as a real pack (`features/parcels.py`): `parcels`, `lu_<class>`, single-family counts, medians, year built and sales |
| `afford_income_limits.csv` | county | HUD-style area median family income and the 30%, 50% and 80% limits for 1 to 8 people (`features/affordability.py`) |
| `data_dictionary.json` | column | `table`, `name`, `unit`, `description`, `source` (always "synthetic"), `tier` |

Codes follow the Census pattern: county = 2-digit state + 3-digit county; tract =
county + 6 digits; block = tract + 4 digits. The state code `99` is not a real state.
The coordinate box sits in open ocean so it cannot be mistaken for a real place.

Home-value brackets use the edges of Census table B25075 (26 brackets, from "less than
$10,000" to "$2,000,000 or more"). Gross-rent brackets use the edges of B25063 (24 cash
brackets, from "less than $100" to "$3,500 or more", plus "no cash rent").

## How the numbers are made

- One fixed seed drives the standard-library Mersenne Twister. Only `random()` is used,
  whose output Python guarantees, so the files do not drift between Python versions.
- Each area type has a profile: typical housing units per tract, renter share, vacancy,
  income, rent, home value and share aged 65+. Each tract draws small random changes
  around its profile.
- The urban county is mostly attached houses and has many renters. Suburban and rural
  counties are mostly detached and owner-occupied. The rural county has many mobile
  homes.
- Values and rents follow log-normal curves, cut at the Census bracket edges.
- Whole-number splits use the largest-remainder rule, so parts always add up exactly:
  structure types to housing units, value brackets to owners, rent brackets to renters,
  blocks to their tract, tracts to their county.
- Permits are a county's typical yearly rate times a statewide cycle: a mid-2000s peak,
  a deep dip in 2008 to 2011, and a rise in recent years. Some early rural county-years
  are flagged `imputed`, as the real permit survey does.
- The survey bands in `acs_bands.csv` use a second generator seeded with the seed + 1,
  so adding them changed no other file. A tract's bands add up to its residents,
  households and homes; owners, renters and building types copy the tract table; county
  and state rows are the sums of their tracts, with margins combined by the
  square-root-of-squares rule.
- The four optional tables (the ones the extra scenarios and the practical capacity
  screen read when a pack has them) use a third generator seeded with the seed + 2, so
  adding them changed no earlier table. Readiness and hazards are made as made-up
  per-tract counts and then passed through the same table functions a real pack uses,
  so the columns and their meaning match. The rules, all constants in the script:
  - Readiness: Harbor County has one utility serving everyone (all verified); Maple and
    Prairie counties have a service-area map (homes inside are verified, the rest
    outside; few rural homes are inside); one Maple tract has a documented moratorium
    area; Brookside County has no record (every home unknown, no water share).
  - Hazards: developed land, impervious cover and steep land drawn in ranges by area
    type; about one tract in five sits on a river with a higher flood-zone share; half
    of Prairie County has no digital flood map, so its flood shares are blank, not zero.
  - Parcels: only Harbor and Maple counties are measured (as only some real counties
    publish parcels); the others stay "not measured". Parcels per home and land-use
    class weights by area type; county rows are the sums of their tracts (county
    "medians" are parcel-weighted means of tract medians).
  - Income limits: three HUD-style areas. Area median family income is the area's
    household-weighted median household income times a drawn ratio; the 4-person 50%
    limit is half of it, the 30% and 80% limits are 0.6 and 1.6 times the 50% limit,
    other sizes use HUD's family-size factors, and each limit is rounded up to $50.
- The data dictionary describes these columns with the real pack's descriptions,
  marked synthetic.

Because Testland has these tables, a run on Testland exercises each extra scenario's
"table present" path. A test that needs the "no table" path hides them
(`StateContext.derived[("pack_table", name)] = None`). The five benchmark reforms read
none of them (`tests/test_testland_extras.py` checks this).

## Inputs and outputs

- Input: none. All settings are constants at the top of `scripts/make_testland.py`.
- Output: the twelve files above.

## How to rebuild and check it

```
uv run python scripts/make_testland.py --out tests/fixtures/testland
uv run pytest tests/test_testland.py
```

The tests regenerate Testland into a temporary folder and check that every file is
byte-identical to the committed one. They also check the size budget (under 300 KB),
that every block's tract and every tract's county exist, that blocks sum to tracts and
tracts to counties, that permits cover every county and year, that the data
dictionary describes every column, and that the four optional tables have the real
pack's columns, cover every tract and county, keep shares between 0 and 1, add up, and
rise with household size and income level.

## Limits

Testland is for testing code, not for answers. Its numbers have plausible shapes but no
real meaning. Do not quote any Testland result as a finding.

At central costs three extra scenarios still add no permits on Testland, for economic
reasons the tables cannot change: Testland's rents are too low for the TOD benchmark (and
so for transit zoning with funded infrastructure, which never exceeds it) and for a
walk-up on commercial land, and its home values sit below a starter home's cost. With
the market-sensitivity inputs the tests use, each shows a range above zero.
Adaptive reuse adds no permits by design (conversions are reported outside the permit
count) and shows conversions; fee deferral shows a range.

## A second synthetic state

Testland stays byte-identical. The decision-demo checks run on a separate, smaller
synthetic state with its own generator and seed: see [the demo state](demo_state.md).
