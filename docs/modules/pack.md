# State data pack (`data/pack.py`)

Code: `src/visionhousing/data/pack.py`; command line `visionhousing fetch` and
`visionhousing build` in `src/visionhousing/cli.py`.
Tests: `tests/test_pack.py` (offline, Testland-shaped inputs), `tests/test_pack_realdata.py`
(Pennsylvania, `make test-realdata`).

## What it does, in plain words

It turns the downloaded sources for one state into one small folder, `data/packs/{STATE}/`,
that the engine and the interface read. Every table uses the same names as the synthetic
state Testland, so code tested on Testland runs on a real state unchanged.

## How to run it

```
make data STATE=PA        # = visionhousing fetch --state PA, then visionhousing build --state PA
```

- `fetch` downloads only what is missing into `data/raw/` and lists the cached files.
- `build` reads the cache (no network when it is complete), writes the pack, then runs the
  add-on steps in order (`RETROFITS` in `data/pack.py`: districts, affordability and stock
  ledger, infrastructure, hazards, transit walksheds, Massachusetts 3A district tracts,
  Massachusetts 3A capacity and estimated sewer, law provision versions) and
  prints a size report. Each add-on is also its own command (`visionhousing districts`,
  `affordability`, `infrastructure`, `hazards`, `transit-walk`, `ma-3a-tracts`, `ma-capacity`, `law-tables`) for refreshing
  one part; the two 3A steps do nothing in a pack without 3A district outlines. The law step writes
  `law_provision_version` in every state (one row per dated version of a provision, from
  `assumptions/law/timeline.yaml`; no download): on 2026-11-25 Pennsylvania 16 rows, Vermont 6,
  Massachusetts and Wyoming none verified yet (an empty table with its columns). Packs after the
  step: PA 10,601,328 bytes (88% of the 12 MB budget), VT 1,114,706 (9%), MA 4,271,640 (36%),
  WY 1,954,186 (16%). A step that fails is noted in the report
  and skipped; run its command to add it later. Both refuse to start with less than 15 GB of
  free disk.
  `visionhousing transit-stops --state WY` (`add_transit_stops`) rebuilds a built pack's
  `transit_stops`, `transit_feeds` and the tracts' `transit_*` columns from the cache, for a pack
  built before a schedule feed was added (not a full-build step: the build already makes them);
  run `transit-walk` after it.

## What is in the pack

| File | Rows (PA) | Bytes (PA) | Notes |
|---|---|---|---|
| `counties.csv.gz` | 67 | 2,287 | Testland columns + land area, 2020 units and people, density |
| `tracts.csv.gz` | 3,445 | 743,543 | Testland columns + ACS extras, margins of error, density, lot-size proxy |
| `blocks.csv.gz` | 329,905 | 4,595,047 | Testland columns; open-water blocks left out |
| `permits_county_year.csv.gz` | 2,412 | 15,976 | 67 counties × 1990-2025 |
| `transit_stops.csv.gz` | 0 | 60 | header only; filled by the transit step |
| `prices_county.csv.gz`, `prices_tract.csv.gz` | 65, 2,497 | 814, 24,298 | house price changes |
| `hpi_county_year.csv.gz` | 2,605 | 33,937 | county index series |
| `mortgage_rates_year.csv.gz` | 56 | 481 | annual averages |
| `costs_county.csv.gz` | 67 | 919 | construction wage index |
| `boundaries/` | 68 files | 2,703,066 | see [Boundaries](boundaries.md) |
| `data_dictionary.json` | | 58,147 | every table and column |
| `pack_manifest.json` | | 41,830 | hashes, inputs, provenance |
| **total** | | **8,220,405** | budget 12,000,000 (10,000,000 at the time) |

The place-profile chart table `acs_bands` (one row for the state, each county and each
tract; ACS tables collapsed into bands with margins, see [acs.md](acs.md)) adds 3,513 rows
and 415,827 bytes to the Pennsylvania pack. A pack already built gets it without a full
rebuild:

```
uv run python -c "from pathlib import Path; from visionhousing.data import pack; pack.add_acs_bands(Path('data/packs/PA'), Path('data/raw'))"
```

`add_acs_bands` reads the cached ACS cells (fetching only what is missing: the API when
`CENSUS_API_KEY` is set, else the keyless summary files), writes `acs_bands.csv.gz`, and
rewrites the data dictionary and the pack manifest exactly as a full build would.

The cost and affordability tables (`afford_chas`, `afford_chas_tract`, `afford_income_limits`,
`afford_assisted`, `stock_ledger_county`; method in [affordability.md](affordability.md)) are
added to a built pack with `visionhousing affordability --state PA`, which rewrites the data
dictionary and manifest the same way and prints the pack total against the budget. They add
about 93 KB of tables to the Pennsylvania pack (tract cost burden is kept as a compact renter
table to stay inside the budget).

State legislative districts (see [districts.md](districts.md)) are added the same way, with
`visionhousing districts --state PA`: the tables `districts`, `district_tract_weights` and
`district_county_weights`, the outlines `boundaries/districts_upper.geojson` and
`boundaries/districts_lower.geojson`, and `district_vintage.json`. For Pennsylvania they add
357,201 bytes (outlines 306,640; tables 50,111; vintage note 450).

The water and sewer readiness tables (`infra_tract`, `infra_county`, `infra_contacts`; method in
[infrastructure.md](infrastructure.md)) and the hazards and land tables (`hazard_tract`,
`hazard_county`; method in [hazards.md](hazards.md)) are added with
`visionhousing infrastructure --state PA` and `visionhousing hazards --state PA`. All
spatial steps (walksheds, flood zones, tract outlines, service areas) use plain NumPy
geometry (`features/planar.py`: ray-casting point-in-polygon, shoelace areas, shapes drawn
on grids by cell centre); no compiled geometry library is installed (the project's licence rule: GEOS and
GDAL are LGPL). For
Pennsylvania they add 31,608 and 38,225 bytes (shares kept to 3 decimals); the pack was
10,536,522 bytes after both on 2026-11-24.

The Massachusetts pack carries the same add-ons. On 2026-11-25 it held 4,262,765 bytes (35.5% of
the 12 MB budget), with `hazard_tract` 16,849 and `hazard_county` 718 bytes, and
`transit_walk_tract` 20,611 and `transit_walk_county` 452 bytes. The hazard tables were added with
`visionhousing hazards --state MA` and the walk tables with `visionhousing transit-walk --state MA`
on the built pack (no full rebuild). In Massachusetts the benchmark estimates do not change with
either table: the benchmark TOD preset measures straight-line distance (the walk tables feed only
the network TOD variants), and the hazard shares are read only by the optional practical capacity
screen.

Tables keep the Testland columns first, in Testland order, then extra columns. Codes (`geoid`,
`county`, `tract`) are text with leading zeros. Whole-number columns are integers; other
numbers are rounded to 4 decimals (latitude and longitude to 5).

Tracts with no land and no housing units are left out (Pennsylvania: `42049990000`, open water
in Lake Erie, which has no cartographic shape). County population and housing units are the
sums of the county's tracts.

## Data dictionary (`data_dictionary.json`)

The Testland layout (`state`, `synthetic`, `columns` with `table, name, unit, description,
source, tier`), plus:

- `tables`: each table's file, key columns, description, source ids, tier and its Testland
  columns.
- `tiers`: what tier 0, 1 and 2 mean. Every Pennsylvania column today is tier 0 (available for
  every state from national files).

A column with no description stops the build, so the dictionary can never fall behind.

## Pack manifest (`pack_manifest.json`)

- `files`: every file's path, bytes, SHA-256 and row count.
- `pack_hash`: one SHA-256 over every file's path and checksum.
- `inputs`: every cached input file with its source id, SHA-256, bytes and access date.
- `provenance`: one row per source used (name, version, access date, licence, terms, cleaning,
  limits, tier), from the source registry.

No build time is stored.

## Determinism

The same cache gives byte-identical files: rows are sorted by key, numbers are rounded the same
way, gzip headers carry no time stamp or file name, JSON key order is fixed. Two Pennsylvania
builds give the same `pack_hash`
(`88bef4f9c5ffe64c97172f219d87a2f08bc01dc83adb0de3b8ca683098abca37` on 2026-11-22).

## Size budget

The whole pack must stay under 12 MB (`PACK_BUDGET_BYTES` in `data/pack.py`). It was
10 MB until the Pennsylvania pack reached 10,420,616 bytes on 2026-11-24, after the district
outlines, census bands, affordability and law columns were added (the transit walk tables were
still to come). The largest parts
then were `blocks.csv.gz` (4.60 MB), the tract outlines (2.61 MB), `tracts.csv.gz`
(0.78 MB), `transit_stops.csv.gz` (0.47 MB), `acs_bands.csv.gz` (0.42 MB), the district
outlines (0.31 MB) and `data_dictionary.json` (0.23 MB). These were checked for lossless
savings first: block and stop coordinates are already at 5 decimals (about 1 m), outlines
are already simplified (districts at 0.002–0.003 degrees, 4 decimals, see
[districts.md](districts.md)), and the one duplicated column (`blocks.tract`, the first 11
digits of `blocks.geoid`, about 260 KB) is read by the engine and a dozen builders, so
dropping it would not be worth the risk. Rounding block coordinates to 4 decimals (about
11 m) would save about 450 KB, but it is not lossless: block internal points are used to place
blocks in districts, walksheds and incentive areas, and a small city block can sit within
11 m of a line, so it was not done. Packs are git-ignored and built locally by
`make data`; the static site has its own limits (12 MB compressed, `export_static.GZIP_LIMIT_BYTES`, and 60 MB uncompressed, `export_static.SIZE_LIMIT_BYTES`). With all four states, tract profiles included (compact), and the shared pool per state (see [web.md](web.md), "The exporter"), the static folder held 52.4 MB uncompressed and 11.4 MB compressed on 2026-11-25 (Pennsylvania 29.5 / 6.5, Massachusetts 11.1 / 2.7, Wyoming 6.2 / 1.1, Vermont 5.0 / 1.0, shared files 0.7 / 0.2; MB = 2^20 bytes). Massachusetts was exported again on 2026-11-25 after its walk tables were refreshed; the folder's size did not change at this precision. The
12 MB cap leaves about 1.5 MB for the tables still being added and keeps a pack quick to
load.

## Law lot weights

A full build makes the law tables with the other local tables, before `tracts` and `blocks`
exist. Once they do, the build fills the single-family lot weights in `muni_zoning_rules`
and rebuilds `county_land_use` (coverage by residents, lots and land) from the pack's own
tables (`law.tables.weigh_law_tables`). So `make data` gives the same weights as
`visionhousing law-tables` run on a built pack.

## Checks the build makes

- Every county and tract in the tables has a shape, or the build stops.
- ACS tracts and block tracts are the same set, or the build stops.
- `counties.geojson` ≤ 300 KB and the base pack < 12 MB (`PACK_BUDGET_BYTES`), or the build
  stops. The pack is written to a temporary folder and swapped in only when complete, so a
  failed build keeps the old pack. After the add-on steps, `visionhousing build` checks the
  whole pack against the same budget again and exits with code 1 when it is over. The add-on
  commands print the pack's total against the budget but do not stop.

## Disk

Large national files are never kept: the block `.dbf` is read in memory, the national tract
price file and wage file are streamed and filtered, and the national county zip and price
workbook are deleted after the state's rows are saved. Pennsylvania's whole cache in
`data/raw/` is about 32 MB, the 72 permit files (15 MB) included.
