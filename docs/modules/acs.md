# American Community Survey (`data/sources/acs.py`, `features/acs.py`)

Code: `src/visionhousing/data/sources/acs.py` (fetch and read),
`src/visionhousing/features/acs.py` (tract features).
Tests: `tests/test_acs.py` (offline, on recorded samples), `tests/test_acs_realdata.py`
(live files, `make test-realdata`).

## What it does, in plain words

The American Community Survey (ACS) is the Census Bureau's yearly household survey.
Its 5-year estimates (2020-2024) are the only source of housing detail for every
census tract: what kinds of buildings there are, who owns or rents, home values and
rents, and the age of residents and buildings. This module fetches 13 ACS tables for
one state's tracts, counties and the state itself, and builds the tract table the
model reads.

## Terms used

| Term | Meaning |
|---|---|
| Census tract | A small area of about 4,000 people. Pennsylvania has 3,446 (2020 boundaries). |
| Estimate (E) and margin of error (M) | The survey's number and its 90% margin: the true value is likely within estimate +/- margin. |
| Summary file | The Bureau's bulk download: one national file per table, separated by `|`. |
| `GEO_ID` | The place's code: `0400000US42` = Pennsylvania, `0500000US42001` = Adams County, `1400000US42001030101` = one tract. |
| Special code | A negative number that means "no estimate" or "margin not computed", for example `-666666666`. |
| Table shells | The Bureau's list of what every cell in every table means. |

## Inputs

| Source id | What | Streamed | Kept on disk |
|---|---|---|---|
| `acs5_summary_file` | one `.dat` per table (13 tables) | about 662 MB | about 4.4 MB (Pennsylvania rows only) |
| `acs5_geos` | the geography list `Geos20245YR.txt` | about 91 MB | 0.5 MB |
| `acs5_table_shells` | the table shells | about 5.6 MB | 12 KB (the 13 tables only) |
| `acs5_api` (optional) | Census Data API | — | cached answers |

Tables: B01001 (age), B01003 (population), B19013 (median income), B25001 (housing
units), B25002 (vacancy), B25003 (tenure), B25024 (units in structure), B25031
(median rent by bedrooms), B25034 (year built), B25063 (rent brackets), B25064
(median rent), B25075 (value brackets), B25077 (median value).

## How it fetches

- **Default: summary files, no key.** Each national file is read a piece at a time
  (streamed). Only the header and the rows whose `GEO_ID` is the state, one of its
  counties or one of its tracts are written to `data/raw/acs/2024/`. The national
  file is never stored. The filtered file is written as `.part` and renamed only
  after the whole file arrived with the right length; a dropped connection starts
  that file again after a wait.
- **Optional: the API** (`fetch_acs(..., method="api")`), only when
  `CENSUS_API_KEY` is set in the environment. Variables go in batches of 48 plus
  `GEO_ID` and `NAME` (50 per call, the API's limit), for the state, its counties and
  its tracts. Both paths return the same columns.
- **Labels are checked first.** The value (B25075) and rent (B25063) brackets go
  through `features/brackets.check_labels`; every other cell used is compared with
  `EXPECTED_LABELS` in the code. Any mismatch stops the run.
- **Special codes become missing.** Every negative value is turned into NaN.

## Tract features (`features/acs.py`)

Column names follow the Testland data dictionary (`tests/fixtures/testland/`),
which is the schema of every state pack.

| Feature | From |
|---|---|
| `population`, `housing_units` | B01003_001, B25001_001 |
| `units_1_detached` ... `units_10_19`, `units_mobile` | B25024_002-007, 010 |
| `units_20_plus` | B25024_008 + B25024_009 |
| `units_boat_rv` (extra) | B25024_011 |
| `households_owner`, `households_renter` | B25003_002, 003 |
| `vacant_units`, `vacancy_rate` | B25002_003; vacant / B25002_001 |
| `median_household_income`, `median_home_value`, `median_gross_rent` | B19013, B25077, B25064 |
| `share_age_65plus` | B01001 cells 020-025 and 044-049, over B01001_001 |
| `value_*`, `rent_*`, `rent_no_cash` | B25075, B25063 (names from `features/brackets.py`) |
| `built_2020_plus` ... `built_1939_or_earlier` (extra) | B25034_002-011 |
| `median_rent_b0` ... `median_rent_b3` | B25031 with the county-ratio fallback below |

Every count and median also has `<name>_moe`. For a sum of cells the margin is the
square root of the sum of squared margins; for `share_age_65plus` it is the Bureau's
formula for a proportion. These are the Bureau's standard approximations.

### Rent by bedrooms

B25031 is blank in many tracts (too few renters in the sample). For each tract and
bedroom count:

1. use the tract's own median when it is reported (`method = "tract"`);
2. otherwise, tract median gross rent x (county bedroom median / county median gross
   rent), rounded to the dollar (`method = "county_ratio"`);
3. otherwise missing (`method = "missing"`).

The method is kept in `median_rent_b{k}_method`. `median_rent_b3` uses the
3-bedroom cell: medians for 3, 4 and 5+ bedrooms cannot be combined.

In Pennsylvania (2020-2024) the tract's own median is used for 441 (studio),
2,146 (1 bedroom), 2,777 (2) and 2,371 (3) of 3,446 tracts; the county ratio fills
2,778, 1,124, 493 and 902; the rest have no rent at all.

## How to check it

`make test-realdata` fetches Pennsylvania (about 30 seconds) and checks:

- 3,446 tracts, 67 counties and the state row;
- tracts with housing units: 3,405. The other 41 have none: Lake Erie's water-only
  tract (42049990000), 36 special-use tracts such as parks and airports (codes
  98xxxx), and 4 ordinary tracts with no homes in the estimate;
- tract sums of population (13,018,639), housing units (5,806,452) and 1-unit
  detached units (3,287,467) equal the published state row.

## Limits

- Five-year averages, centred on 2022; they lag current conditions.
- Small tracts have wide margins; keep `_moe` in view.
- Medians are top-coded by the Bureau (for example rent "3,501" means $3,500 or
  more; income "250,001" means $250,000 or more).
- Only the 2020-2024 vintage is wired; another vintage needs new URLs and a fresh
  label check.
