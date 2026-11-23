# Building Permits Survey (`data/sources/bps.py`)

Code: `src/visionhousing/data/sources/bps.py`.
Tests: `tests/test_bps.py` (offline, on recorded samples), `tests/test_bps_realdata.py`
(live files, `make test-realdata`).

## What it does, in plain words

The model's outcome is housing units authorized by building permits. The Census
Bureau's Building Permits Survey (BPS) publishes these counts every year for every
county and state. This module downloads the annual files for 1990 to 2025, reads
them, and returns one tidy table: one row per place, year and structure type.

## Terms used

| Term | Meaning |
|---|---|
| Units authorized | Homes that a local permit office allowed to be built that year. Not homes started or finished. |
| Structure type | How many units the building has: 1, 2, 3-4, or 5 or more. |
| Reported | The figures permit offices sent in. |
| Imputed | The Bureau's estimate for offices that did not report. The published total ("estimate") = reported + imputed. |
| FIPS code | The federal code of a place: 2 digits for a state (Pennsylvania = 42), 3 more for a county (Adams = 42001). |

## Inputs

| Source id | File | Size |
|---|---|---|
| `bps_county_annual` | `https://www2.census.gov/econ/bps/County/co{YYYY}a.txt` | 0.36-0.61 MB a year |
| `bps_state_annual` | `https://www2.census.gov/econ/bps/State/st{YYYY}a.txt` | about 15 KB a year |

All 72 files (36 years x 2) take about 10 MB in `data/raw/bps/` and are kept,
because they are small. Every download is recorded in `data/manifest.json`.

## How the files are read

- **By header name, never by position.** Each file has two header rows. The first
  names the group ("1-unit", "2-units", "3-4 units", "5+ units", then the same with
  "rep"); the second names the measure (Bldgs, Units, Value). The rows are joined
  into names such as `est_3-4_units` and `rep_5+_value`. A header the code does not
  know stops the run with a message, so a layout change can never shift columns
  silently.
- **Two layouts.** 1990-1998: cells padded with spaces, Windows line ends, short
  group names ("34unit", "5-unit"). 1999 onward: no padding, "3-4 units", "5+ units".
  The state files have no county column. One set of name rules covers all of them.
- **Year check.** The survey-date cell ("9099" in 1990, "199999" in 1999, "2024" in
  2024) must agree with the year in the file name.
- **Value** is dollars in county files and thousands of dollars in state files. Both
  are returned in dollars.
- **Blank cells** (a few territory rows, for example Puerto Rico in 2010) stay
  missing; any other non-number stops the run.
- **Non-state rows** in the state files (U.S. total, regions R1-R4, divisions D1-D9)
  are dropped.

## Output

`fetch_bps(state, years, cache)` returns these columns:

| Column | Meaning |
|---|---|
| `level` | `county` or `state` |
| `geoid` | 5-digit county code or 2-digit state code |
| `state_fips`, `name`, `year`, `structure` | as named; `structure` is `1`, `2`, `3-4` or `5+` |
| `buildings`, `units`, `value_dollars` | published estimates (reported + imputed) |
| `buildings_reported`, `units_reported`, `value_reported_dollars` | reported only |
| `units_imputed` | `units - units_reported` |
| `imputed` | true when buildings or units include any imputed part |

`permits_county_year(table)` turns it into the state-pack table with the Testland
column names: `county, year, units_1, units_2, units_3_4, units_5plus, imputed`.

`county_state_check(table)` adds up the county rows and compares them with the
state row, year by year and type by type.

## How to check it

`make test-realdata` downloads the real files (or reuses `data/raw/bps/`) and checks
that Pennsylvania's 67 county rows add up exactly to the published state totals in
2005, 2015 and 2024 (44,525, 22,854 and 24,810 units).

## Limits

- Permits are not construction: some permitted units are never built.
- In 1990-1993 (and by a handful of units in 1997, 2002 and 2003) the Pennsylvania
  county rows do not add up exactly to the state file (for example 1991 single-unit:
  30,691 county sum against 29,353 state). The state and county files were estimated
  separately in those years. The model uses the county rows; the gap is shown by
  `county_state_check`.
- Imputed figures are the Bureau's estimates, not counts. The `imputed` flag lets the
  model and the interface say where they are used.
