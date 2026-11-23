# Testland: the synthetic test state

## What it is

Testland (state code `TL`) is a small, made-up state. Nothing in it is real. It has
4 counties, 40 tracts, 400 blocks, 30 transit stops and permit counts for 1996 to 2025.
Its tables use the same column names and shapes as a real state pack. So every test, and
the interface, can run offline with no downloads.

The files live in `tests/fixtures/testland/` and total about 70 KB.

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

## Inputs and outputs

- Input: none. All settings are constants at the top of `scripts/make_testland.py`.
- Output: the seven files above.

## How to rebuild and check it

```
uv run python scripts/make_testland.py --out tests/fixtures/testland
uv run pytest tests/test_testland.py
```

The tests regenerate Testland into a temporary folder and check that every file is
byte-identical to the committed one. They also check the size budget (under 300 KB),
that every block's tract and every tract's county exist, that blocks sum to tracts and
tracts to counties, that permits cover every county and year, and that the data
dictionary describes every column.

## Limits

Testland is for testing code, not for answers. Its numbers have plausible shapes but no
real meaning. Do not quote any Testland result as a finding.
