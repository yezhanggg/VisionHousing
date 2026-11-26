# Place profile data: jobs, incentive areas and safety

The keyless city safety records (Philadelphia) and how the Safety section uses both sources are in [safety.md](safety.md).

Code:

- Sources: `src/visionhousing/data/sources/jobs.py`, `incentives.py`, `safety.py`, `xlsb.py`, and `place.py` (the one door the pack builder uses).
- Tables: `src/visionhousing/features/jobs.py`, `incentives.py`, `safety.py`.
- Profile sections: `src/visionhousing/profile/sections/jobs.py`, `incentives.py`, `safety.py`.

Tests: `tests/test_place_jobs.py`, `tests/test_place_incentives.py`, `tests/test_place_safety.py`, `tests/test_place_pack.py` (offline, small files in `tests/fixtures/place/` and Testland), and `tests/test_place_realdata.py` (live files, `make test-realdata`).

## What it does, in plain words

The tool also helps with market research about one county or tract. Three profile sections answer three questions:

- **Jobs**: how many jobs are located here, how well do they pay, which industries, and do workers come from elsewhere?
- **Incentives**: which tax credits and incentive areas apply here?
- **Safety**: how many offenses do police report here, per 1,000 residents?

Each section gives one plain sentence, its facts (each with its source, edition and geography), a comparison (a county with the state, a tract with its county) and a limits text that says what the numbers cannot tell you.

None of these numbers feeds the reform model. They describe places.

## Terms used

| Term | Meaning |
|---|---|
| LODES | LEHD Origin-Destination Employment Statistics: jobs by the census block where people work, and by the pair of home and work blocks |
| CBP | County Business Patterns: establishments, employment and payroll by county and industry |
| NAICS | North American Industry Classification System; two-digit codes are sectors (62 = health care) |
| QCT | Qualified Census Tract: a low-income tract where a tax-credit project gets a 30% larger credit basis |
| DDA | Difficult Development Area: a high-cost area with the same 30% boost. In metro areas it is a ZIP Code Tabulation Area (ZCTA), elsewhere a whole county |
| LIHTC | Low-Income Housing Tax Credit, the main federal subsidy for affordable rental homes |
| Opportunity Zone | A tract where investments get capital-gains tax relief. The round shown starts on 2027-01-01 |
| NMTC | New Markets Tax Credit; a tract qualifies when its poverty rate is 20% or more or its family income is 80% of the benchmark or less |
| National Register | The National Register of Historic Places; rehabilitating an income-producing listed building can earn the federal historic tax credit |
| KOZ | Keystone Opportunity Zone, Pennsylvania's state and local tax abatement on listed sites |
| ORI | The FBI's code for a police agency |
| NIBRS | The FBI's incident-based reporting system; Pennsylvania moved to it around 2021 |

## Sources and what is kept

| Source id | Published file | Key | Kept in `data/raw/` |
|---|---|---|---|
| `lodes_wac` | `pa_wac_S000_JT00_2024.csv.gz` (2.4 MB) | No | `jobs/lodes_wac_2024_tracts_42.csv`: tract sums only |
| `lodes_od` | `pa_od_main_JT00_2024.csv.gz` (31 MB) and `pa_od_aux_…` (2.6 MB) | No | `jobs/lodes_od_2024_counties_42.csv`: county pairs only |
| `cbp_county` | `cbp23co.zip` (13 MB, national) | No | `jobs/cbp_2023_county_42.csv`: state, all-sector and sector rows |
| `hud_qct` | HUD map service, 2020 tracts | No | `incentives/hud_qct_42.csv` |
| `hud_dda` | HUD map service (ZCTAs and non-metro counties) | No | `incentives/hud_dda_42.csv` |
| `census_zcta_tract_rel` | ZCTA-to-tract relationship file (24 MB, national) | No | `incentives/zcta_tract_42.txt`: the state's tracts only |
| `treasury_oz2027` | Treasury `.xlsx` of 2027 designations | No | `incentives/oz2027_42.csv` |
| `cdfi_nmtc_lic` | CDFI Fund `.xlsb` (4.7 MB, national) | No | `incentives/nmtc_lic_42.csv` |
| `hud_lihtc` | HUD LIHTC map service | No | `incentives/hud_lihtc_42.csv` |
| `nps_nrhp` | NPS National Register map service (points; district polygons simplified to about 10 m) | No | `incentives/nrhp_points_42.csv`, `nrhp_districts_42.geojson.gz` |
| `dced_koz_sites` | Pennsylvania DCED KOZ map layer | No | `incentives/dced_koz_sites_42.csv` (contact fields dropped) |
| `fbi_cde_agency` | FBI Crime Data Explorer API | **Yes**, a free api.data.gov key | `safety/2024/*.json` (one answer per agency and offense) and `safety/fbi_agencies_2024_42.csv` |

Each fetch keeps going when one source fails; the failure becomes a note in the build report.

**Reading the `.xlsb` file.** The CDFI Fund publishes its NMTC list only as an Excel binary workbook. `xlsb.py` reads it with the standard library: the file is a zip; each sheet is a stream of records, each starting with its type and length; the few record types that hold cell values are decoded (see the module text). No spreadsheet library is added.

**The FBI key.** It is read only from `API_DATA_GOV_KEY` (or `FBI_API_KEY`). It is never written to disk or into a cache name, and it is removed from every error. With no key, nothing is fetched and the safety table is left out. api.data.gov allows about 1,000 requests an hour, so the first Pennsylvania run (two requests for each of about 1,200 agencies) paces itself and takes a few hours; later runs read the cache. This path has not yet been run against the live API (no key was available when it was written); the real-data test runs it when a key is set.

## Pack tables

| Table | One row per | Key columns |
|---|---|---|
| `jobs_tract` | tract | jobs, three earnings bands, 20 sectors |
| `jobs_county` | county | jobs, `live_and_work`, `inflow`, `inflow_out_of_state`, `outflow_in_state`, `employed_residents_in_state`, CBP establishments, employment, payroll |
| `jobs_county_sector` | county and sector | CBP establishments, employment (with noise flag), payroll |
| `incentives_tract` | tract | `qct`, `dda_land_share`, `dda`, `oz2027`, `oz2027_rural`, `nmtc_lic`, NMTC poverty and income, LIHTC projects and units, National Register listings and homes in districts, KOZ sites and acres |
| `incentives_county` | county | counts of designated tracts, `dda_nonmetro`, LIHTC totals by HUD's county, district counts, KOZ sites |
| `safety_county` | county | agencies, full-year agencies, population covered, violent and property offenses and rates per 1,000, coverage share |

Every column is in the pack's `data_dictionary.json` with its unit, description and source id. The Keystone columns are tier 2 (one state's own source); the rest are tier 0.

`make data` builds these tables with the rest of the pack. To add them (with `safety_local` and the typology tables) to a pack that is already built, from the cached files and without a full rebuild, use the same retrofit as the safety tables (`add_profile_tables` in `visionhousing.data.pack`; command in `docs/modules/safety.md`). It reads the cached job and incentive files, fetches only a file that is missing, builds the tables with the same function as the full build, and rewrites the data dictionary and the pack manifest. Pennsylvania (pack of 2026-11-23): the five tables add about 163 KB (`jobs_tract` 108 KB, `jobs_county_sector` 13 KB, `incentives_tract` 38 KB, the two county tables 4 KB); the pack was 9.43 MB of its budget, then 10 MB (now 12 MB).

## How the numbers are worked out

- **Jobs per resident** = LODES jobs / ACS residents. Above 1 means the place draws workers from elsewhere.
- **High-earning share** = jobs paying more than $3,333 a month / all jobs.
- **Inflow share** (county) = jobs held by people who live in another county or state / jobs located in the county. `jobs = live_and_work + inflow` always holds.
- **Work in home county** = `live_and_work / employed_residents_in_state`. Residents working in another state are not in Pennsylvania's files, so this share is a ceiling.
- **Average pay** = CBP annual payroll / mid-March employment. The state value adds up its counties.
- **DDA land share** = tract land shared with DDA ZCTAs / tract land shared with any ZCTA; a non-metro DDA county gives its tracts 1. A tract is "in a DDA" at one half or more (a definition, because HUD designates ZCTAs, not tracts).
- **Points to tracts**: LIHTC projects, KOZ sites and National Register points are placed in tracts with an even-odd point-in-polygon test. A district with no point uses the centre of its outline's vertices.
- **Homes in historic districts** = 2020 Census block homes whose block centre is inside a listed district polygon.
- **Offense rates** = offenses of agencies that reported all 12 months / the population those agencies serve x 1,000. An agency counts in the first county the FBI lists for it. No tract figures are made.

## What was left out, and why

- **Named largest employers**: Pennsylvania's Center for Workforce Information publishes its county "Top 50 employers" lists only as PDF files. They were not parsed.
- **Pennsylvania Uniform Crime Reporting**: the public site offers PDF annual reports and incremental submission extracts behind a session token; neither gives a clean table by agency. The FBI API is used instead.
- **First-round Opportunity Zones** (2018): they end on 2026-12-31 and use 2010 tracts.
- **Recent LIHTC projects**: HUD's full database site blocks scripted access; the map service used stops at 2020 for Pennsylvania.
