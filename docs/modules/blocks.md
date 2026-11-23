# Census blocks and density (`data/sources/blocks.py`, `features/density.py`)

Code: `src/visionhousing/data/sources/blocks.py`, `src/visionhousing/features/density.py`.
Tests: `tests/test_blocks.py`, `tests/test_density.py` (offline), `tests/test_pack_realdata.py`
(live files, `make test-realdata`).

## What it does, in plain words

A census block is the smallest area the Census Bureau counts. The 2020 Census gives, for every
block, its housing units, people, land and water area, whether it is urban or rural, and a
point inside it. This module reads those numbers for one state and turns them into tract
measures: how dense the housing is, and roughly how much land each home has.

## Terms used

| Term | Meaning |
|---|---|
| Block | The smallest census area. Pennsylvania has 336,985. |
| Attribute table | The `.dbf` file inside a shapefile zip: one row of numbers and codes per shape. |
| Range request | Asking a web server for bytes *x* to *y* of a file instead of the whole file. |
| Internal point | A point the Bureau places inside each block (`INTPTLAT20`, `INTPTLON20`). Not the centre of population. |
| Census-urban block | A block inside a 2020 urban area (`UR20 = U`). |
| Lot-size proxy | Block land area divided by the block's housing units. |

## Input

| Source id | File | Read |
|---|---|---|
| `tiger_2020_tabblock` | `tl_2020_42_tabblock20.zip` (409.4 MB) | only `tl_2020_42_tabblock20.dbf` (46.2 MB, 11.3%) |

The zip's index is read first, then only the `.dbf` bytes. The 360 MB of block shapes are
never transferred. The `.dbf` is read in memory and never saved. The cache keeps one small
extract, `data/raw/blocks/tl_2020_42_tabblock20_attributes.csv.gz` (5.5 MB), with these
columns: `geoid, tract, county, housing_units, population, land_area_m2, water_area_m2,
urban, lat, lon`. A rebuild reads the extract and needs no network.

## Checks when reading

- Block ids are 15 digits, unique, and belong to the state.
- Units, people and areas are whole numbers of zero or more.
- Latitude is within ±90 and longitude within ±180; both are kept to 5 decimals (about 1 m).
- Real data: Pennsylvania's blocks add up to **5,742,828 housing units**, the 2020 Census count.

## Tract features (`features/density.py`)

All pure functions: a blocks table in, a tract table out.

| Column | Formula |
|---|---|
| `housing_units_2020`, `population_2020` | Sum over the tract's blocks |
| `blocks`, `residential_blocks` | Number of blocks; blocks with at least one unit |
| `land_area_m2` (pack) | Sum of the blocks' land area |
| `housing_density_per_km2` | `housing_units_2020 / (land_area_m2 / 1,000,000)`; blank with no land |
| `lot_m2_per_unit_p25`, `_p50`, `_p75` | Percentiles of block land / block units over blocks with at least one unit, **weighted by units** |
| `urban_unit_share` | Units in Census-urban blocks / all units (urban share of land when there are no units) |
| `area_type` | See the rule below |

**Weighted percentile.** Sort the values; the *q* percentile is the first value at which the
running total of weights reaches *q* × total weight. No interpolation, so the answer is a value
that occurs in the data. Weighting by units means the median describes the block the middle
home of the tract sits in.

**Area type rule.**

1. `rural` if less than half of the tract's units are in Census-urban blocks.
2. Otherwise `urban` if density is at least 2,000 units per square mile of land (about 772
   per km²; row-house and small-lot city neighbourhoods).
3. Otherwise `suburban`.

Step 1 is the Census Bureau's own classification. The 2,000 threshold is a judgement. It is a
constant in `features/density.py` (`URBAN_UNITS_PER_SQ_MILE`) and only labels tracts; no count
depends on it. Pennsylvania 2020 result: 1,058 urban, 1,588 suburban, 799 rural tracts.

## Limits

- Counts are from April 2020, not today.
- Block land includes parks, shops, farms and half of the surrounding streets. The lot-size
  proxy is therefore larger than the real lot, most of all in rural blocks where one house
  can sit in a block of farmland.
- The internal point is not where people live inside a large rural block. Distances from it
  are approximate.
- The pack's `blocks` table drops blocks with no land and no units (open water): 7,080 of
  336,985 in Pennsylvania.

## How to check it

`make test` runs the offline tests (a hand-made `.dbf` served by a fake range server, and
hand-calculated densities and percentiles). `make test-realdata` checks the Pennsylvania
total and that the `.dbf` is under 15% of the zip.
