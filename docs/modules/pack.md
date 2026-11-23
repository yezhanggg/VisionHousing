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
- `build` reads the cache (no network when it is complete), writes the pack and prints a size
  report. Both refuse to start with less than 15 GB of free disk.

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
| **total** | | **8,220,405** | budget 10,000,000 |

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

## Checks the build makes

- Every county and tract in the tables has a shape, or the build stops.
- ACS tracts and block tracts are the same set, or the build stops.
- `counties.geojson` ≤ 300 KB and the whole pack < 10 MB, or the build stops. The pack is
  written to a temporary folder and swapped in only when complete, so a failed build keeps the
  old pack.

## Disk

Large national files are never kept: the block `.dbf` is read in memory, the national tract
price file and wage file are streamed and filtered, and the national county zip and price
workbook are deleted after the state's rows are saved. Pennsylvania's whole cache in
`data/raw/` is about 32 MB, the 72 permit files (15 MB) included.
