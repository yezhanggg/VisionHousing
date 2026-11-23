# Data provenance

Every dataset the tool uses, where it comes from, which edition, its licence, how it is cleaned, and what it cannot do.

All data is public. Every file is downloaded by script from its publisher (`make data`). None of it is stored in this repository. The code licence (Apache-2.0) does not cover the data. Each dataset keeps its publisher's terms.

## Where the tables come from

The tables below are **generated**. Do not edit them by hand. Run `uv run python scripts/gen_docs_tables.py` to rebuild them. Add `--check` to see whether they are out of date without changing anything.

- Each source is listed once in `src/visionhousing/data/sources.yaml` and loaded by `src/visionhousing/data/registry.py`.
- `provenance_rows()` in `src/visionhousing/data/manifest.py` turns the registry into one row per source. The interface's Method & data screen reads the same rows, so the screen and this file agree.
- Each download is recorded in `data/manifest.json` with its SHA-256 checksum, size, access time and edition. That file exists only on a machine that has run `make data`. So this document shows the day each address was last confirmed to answer. The access date of your own download is in your manifest and on the Method & data screen.
- The calibration tables come from the `provenance` part of each file in `assumptions/calibration/`.

## Columns

| Column | Meaning |
|---|---|
| Source | The dataset name, its publisher and its registry id |
| Version | The edition or vintage used |
| Address checked | The day the download address was last confirmed to answer |
| Licence | The licence or legal status of the data, with a link to the terms page |
| Cleaning | What the code does to it before use |
| Limits | What the data cannot tell us |

## What each source is for

| Source | Used for |
|---|---|
| Building Permits Survey, county and state annual files (Census Bureau) | The outcome (units authorized by building permits) and the baseline. The state files check that the county rows add up |
| American Community Survey 5-year summary files, the geography list and the table shells (Census Bureau) | Housing stock, home values, rents and households by tract. The geography list names each row. The shells give each cell its label |
| American Community Survey through the Census Data API | The same estimates by a faster path, used only when a free key is set |
| TIGER/Line 2020 blocks, attribute table only (Census Bureau) | Housing density and a lot-size proxy by tract. Block shapes are never downloaded |
| Cartographic boundary files for tracts and counties (Census Bureau) | The map outlines |
| FHFA House Price Index, county and tract | Price trends |
| Freddie Mac Primary Mortgage Market Survey | Mortgage rates |
| BLS Quarterly Census of Employment and Wages, construction | A county index of building costs, from construction pay |
| National Transit Map, stops (Bureau of Transportation Statistics) | Major transit stops |
| National Transit Map, agencies (Bureau of Transportation Statistics) | Which agency runs each stop, to match stops to schedule feeds |
| Mobility Database catalog (MobilityData) | Where each transit agency publishes its schedule feed |
| Transit agency schedule feeds (GTFS) | Weekday departures per stop, to find frequent service. Each feed keeps its agency's terms |
| Pennsylvania bill pages and text (HB 2186, SB 1346) | The state-specific ADU scenarios |
| HUD Fair Market Rents | A cross-check only. Skipped for now, because the site blocks scripted access |

## Source table

<!-- generated: provenance_table -->
*20 sources, in registry order. Generated from `provenance_rows()` and `src/visionhousing/data/sources.yaml`.*

| Source | Version | Address checked | Licence | Cleaning | Limits |
|---|---|---|---|---|---|
| **Building Permits Survey, county annual files (U.S. Census Bureau)** (`bps_county_annual`) | 1990-2025 annual files (the 2025 file is the latest published) | 2026-11-22 | Public domain (U.S. government work) ([terms](https://www.census.gov/about/policies/open-gov/open-data.html)) | Two header rows are joined and columns are read by name, not position. Reported and imputed figures are kept apart. County FIPS codes are zero-padded to five digits. | Counts units authorized by permit, not units started or finished. Some counties are imputed rather than reported; the file flags which. Places without permit offices are not covered. |
| **American Community Survey 5-year table-based summary files (2020-2024) (U.S. Census Bureau)** (`acs5_summary_file`) | 2020-2024 5-year estimates | 2026-11-22 | Public domain (U.S. government work) ([terms](https://www.census.gov/programs-surveys/acs/data/summary-file.html)) | Rows are filtered to the state's tracts by GEO_ID prefix. Negative missing-value codes become blanks. Estimates (E) and margins of error (M) are both kept. | Estimates are survey averages over five years with margins of error; small tracts often have wide margins. One national pipe-delimited file per table (about 48 MB for B25024). Negative values are missing-value codes, not data. |
| **American Community Survey 5-year estimates via the Census Data API (2020-2024) (U.S. Census Bureau)** (`acs5_api`) | 2020-2024 5-year estimates | 2026-11-22 | Public domain (U.S. government work); API terms of service apply ([terms](https://www.census.gov/data/developers/about/terms-of-service.html)) | Same as the summary files. Variable labels are checked against the vintage's variable list before use. | At most 50 variables per call. On 2026-11-22 a call without a key was answered with a "Missing Key" page, so this path needs CENSUS_API_KEY; the summary-file path above needs no key and gives the same estimates. |
| **Cartographic boundary file, census tracts, 2020, 1:500,000 (U.S. Census Bureau)** (`cb_2020_tract`) | 2020 | 2026-11-22 | Public domain (U.S. government work) ([terms](https://www.census.gov/geographies/mapping-files/time-series/geo/cartographic-boundary.html)) | Shapes are simplified further and written as small GeoJSON files per county. | Generalized (simplified) shapes for maps, not for measuring land area or for parcel work. 2020 tracts match the 2020-2024 ACS. |
| **Cartographic boundary file, counties, 2020, 1:500,000 (U.S. Census Bureau)** (`cb_2020_county`) | 2020 | 2026-11-22 | Public domain (U.S. government work) ([terms](https://www.census.gov/geographies/mapping-files/time-series/geo/cartographic-boundary.html)) | Filtered to the state, simplified, written as GeoJSON. | One national file; generalized shapes for maps only. |
| **TIGER/Line 2020 tabulation blocks (attribute table only) (U.S. Census Bureau)** (`tiger_2020_tabblock`) | 2020 Census | 2026-11-22 | Public domain (U.S. government work) ([terms](https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html)) | Keeps GEOID20, HOUSING20, POP20, ALAND20, AWATER20 and the internal point. | Housing units and population are 2020 counts, not current. Internal points are a point inside each block, not its centre of population. Only the attribute table (the .dbf, about 46 MB of the 409 MB Pennsylvania zip) is read, by HTTP range requests; block shapes are never downloaded. |
| **FHFA House Price Index, annual, counties (Federal Housing Finance Agency)** (`fhfa_hpi_county`) | Annual developmental index, latest release | 2026-11-22 | Public domain (U.S. government work) ([terms](https://www.fhfa.gov/data/hpi/datasets)) | Converted to long form (county, year, index, annual change). Missing years stay missing. | Repeat-sales index built from mortgages bought or guaranteed by Fannie Mae and Freddie Mac; thin in rural counties, where years can be missing. Published as a spreadsheet (read with the standard library's zip and XML tools). |
| **FHFA House Price Index, annual, census tracts (Federal Housing Finance Agency)** (`fhfa_hpi_tract`) | Annual developmental index, latest release | 2026-11-22 | Public domain (U.S. government work) ([terms](https://www.fhfa.gov/data/hpi/datasets)) | Filtered to the state's tracts. Missing values stay missing. | Experimental tract-level index; many tracts have too few repeat sales and are blank. Check which tract vintage (2010 or 2020) each release uses before joining. |
| **Primary Mortgage Market Survey, weekly rate history (Freddie Mac)** (`freddie_pmms`) | Weekly, 1971 to the latest week | 2026-11-22 | Published freely by Freddie Mac; use under its website terms, with attribution ([terms](https://www.freddiemac.com/terms)) | Weekly 30-year and 15-year fixed rates; an annual average is computed. | National average offered rates for prime borrowers, not the rate a given household or builder pays. The value used in the model is a ledger parameter with a range. |
| **HUD Fair Market Rents, county and metro area file (U.S. Department of Housing and Urban Development)** (`hud_fmr_county`) | Fiscal year 2026 | not yet confirmed | Public domain (U.S. government work) ([terms](https://www.huduser.gov/portal/datasets/fmr.html)) | Rents by bedroom count per county; metro-wide values are applied to member counties. | Fair Market Rents are the 40th percentile of recent movers' gross rents, set for voucher payments; they are a cross-check, not a market rent. The direct file is used, never the token-gated API. |
| **National Transit Map, stops (U.S. Department of Transportation, Bureau of Transportation Statistics)** (`ntm_stops`) | National Transportation Atlas Database release compiled 2026-09-23 | 2026-11-22 | Public domain (U.S. government work) ([terms](https://geodata.bts.gov/datasets/usdot::national-transit-map-stops)) | Keeps stop id, name, latitude, longitude, stop type and feed id. Stops with no pickup are excluded later, using the agency feeds. | Stops as reported by agencies in their schedule feeds; some agencies are missing or out of date. Gives stop locations, not service frequency (frequency comes from the agency schedule feeds). About 673,000 stops nationally; queried by state bounding box in pages of at most 2,000. |
| **Pennsylvania House Bill 2186 (accessory dwelling units), bill page and text (Pennsylvania General Assembly)** (`pa_hb2186`) | 2025-2026 regular session; text Printer's Number 3373 | 2026-11-22 | Public legislative record ([terms](https://www.palegis.us/legislation/bills/2025/hb2186)) | Only status, dates and cited sections are recorded, in a hand-checked rule file. | A proposed bill, not law, unless enacted; it lapses at the end of the session. Status is re-checked after November 30. |
| **Pennsylvania Senate Bill 1346 (accessory dwelling units), bill page and text (Pennsylvania General Assembly)** (`pa_sb1346`) | 2025-2026 regular session; text Printer's Number 1747 | 2026-11-22 | Public legislative record ([terms](https://www.palegis.us/legislation/bills/2025/sb1346)) | Only status, dates and cited sections are recorded, in a hand-checked rule file. | A proposed bill, not law, unless enacted. Applies only to municipalities under the Municipalities Planning Code, so not to Philadelphia or Pittsburgh. |
| **Building Permits Survey, state annual files (U.S. Census Bureau)** (`bps_state_annual`) | 1990-2025 annual files (the 2025 file is the latest published) | 2026-11-22 | Public domain (U.S. government work) ([terms](https://www.census.gov/about/policies/open-gov/open-data.html)) | Same header rules as the county files. Region, division and U.S. total rows are dropped. Value is converted to dollars. | State totals of units authorized by permit, including the Bureau's imputation for offices that did not report. Construction value is in thousands of dollars. |
| **American Community Survey 5-year summary file geography list (2020-2024) (U.S. Census Bureau)** (`acs5_geos`) | 2020-2024 5-year estimates | 2026-11-22 | Public domain (U.S. government work) ([terms](https://www.census.gov/programs-surveys/acs/data/summary-file.html)) | Streamed; only the state's state, county and tract rows are written to disk. The national file is never stored. | One national pipe-delimited file (about 91 MB) that names every geography in the summary files. Tracts are 2020 census tracts. |
| **American Community Survey 5-year table shells (2020-2024) (U.S. Census Bureau)** (`acs5_table_shells`) | 2020-2024 5-year estimates | 2026-11-22 | Public domain (U.S. government work) ([terms](https://www.census.gov/programs-surveys/acs/data/summary-file.html)) | Streamed; only the rows of the tables used are written to disk. | The label of every table cell for this vintage; it holds no estimates. |
| **Quarterly Census of Employment and Wages, annual averages, construction (NAICS 23) (U.S. Bureau of Labor Statistics)** (`bls_qcew_construction`) | 2025 annual averages | 2026-11-22 | Public domain (U.S. government work) ([terms](https://www.bls.gov/cew/additional-resources/open-data/)) | Streamed; only the state's rows and the national row are written to disk. Private ownership (code 5) at nation, state and county level is kept. County pay is divided by state pay to give a county wage index. | Average pay of construction jobs at private employers, by where the employer reports, not where it builds. A wage index is a proxy for local building costs; materials, land and fees are not in it. BLS withholds county figures that would reveal one employer; those counties take the state value. |
| **National Transit Map, agencies (U.S. Department of Transportation, Bureau of Transportation Statistics)** (`ntm_agencies`) | National Transportation Atlas Database release compiled 2026-09-23 | 2026-11-22 | Public domain (U.S. government work) ([terms](https://geodata.bts.gov/datasets/usdot::national-transit-map-agencies)) | Keeps the National Transit Database id and agency name of the agencies with stops in the state's bounding box. | Agency names and addresses only; used to label the transit coverage table. |
| **Mobility Database catalog of transit schedule feeds (MobilityData)** (`mdb_catalog`) | current | 2026-11-22 | Catalog published openly by MobilityData; each listed feed keeps its agency's own terms ([terms](https://mobilitydatabase.org/)) | Keeps the state's schedule (GTFS) feeds. A deprecated feed is used only when the same provider has no active one. | A volunteer-maintained list; some agencies are missing, listed under another state, or listed only with an old (deprecated) feed. Feeds that need a key or log-in are not used. |
| **Transit agency schedule feeds (GTFS), via their published addresses or the catalog mirror (Each transit agency (copies mirrored by MobilityData))** (`gtfs_agency_feeds`) | The schedule current when fetched; the representative date is recorded per feed | 2026-11-22 | Each agency's own open-data terms (for example SEPTA and Pittsburgh Regional Transit developer terms) ([terms](https://mobilitydatabase.org/)) | Reads stops, routes, trips, stop times, calendars and frequencies (never shapes). Counts boarding calls per stop on the busiest Tuesday to Thursday; a trip's last stop and no-pickup calls are left out. Only the per-stop counts are stored. | Scheduled, not actual, service. One representative weekday per feed. Only feeds that download without a key are used; bus stops of other agencies are marked frequency unknown. Agencies change stop ids between schedules, so a few stops may not match the National Transit Map. |
<!-- /generated: provenance_table -->

## Calibration tables

Two small tables in `assumptions/calibration/` set the range of an assumption from measured evidence. They hold **aggregated shares only**. No imagery, footprint, parcel or lot-level record is in the repository or in the product, and `make data` downloads none of them (decision D-19).

### The ADU fit calibration and its terms

The fit table says what share of lots has room for one 600 sq ft detached ADU. It was measured once, outside the product, from three inputs. Each keeps its own terms.

| Input | Terms | What this project does |
|---|---|---|
| 2022 USDA NAIP aerial imagery | Public domain (U.S. government work) | Used only to review results. No image is kept or shared |
| Microsoft US Building Footprints | Open Database License (ODbL 1.0) | The shares are a produced work and carry the attribution below. The footprint data itself is not redistributed |
| County parcel layers (the City of Philadelphia, Allegheny County and four more Pennsylvania counties) | As each county states. Several state no licence | No parcel record is redistributed. Only shares by building type and area type are kept |

Attribution: the ADU fit shares contain information from Microsoft US Building Footprints, which is made available under the Open Database License (ODbL 1.0).

### Calibration provenance

<!-- generated: calibration_provenance -->
#### `adu_fit.yaml`: Share of lots with room for a 600 sq ft detached ADU

- File: `assumptions/calibration/adu_fit.yaml` (id `adu_fit_pa_2026_11`)
- Measured or read: 2026-11-18
- What it is: Measured on samples of single-family lots in two Pennsylvania counties from 2022 USDA NAIP aerial imagery, Microsoft building footprints, and county parcel layers; method: open-space test for a 20 x 30 ft footprint after setbacks.
- Method: For each lot, the lot outline (from the parcel layer) and the building outlines (from the footprint layer) are combined. The side and rear setbacks and the distances from the house and garages are removed. The lot counts as a fit when the remaining open ground holds a 20 x 30 ft rectangle. The fit test itself uses lot lines and building outlines; the 2022 aerial imagery was used only to review results. Only the shares by building type and place are kept.

| Source id | Source | Licence or terms |
|---|---|---|
| usda_naip_2022 | [U.S. Department of Agriculture, National Agriculture Imagery Program (NAIP), Pennsylvania, 2022](https://naip-usdaonline.hub.arcgis.com/) | Public domain (U.S. government work) |
| microsoft_building_footprints | [Microsoft, US Building Footprints](https://github.com/microsoft/USBuildingFootprints) | ODbL 1.0; the shares here are a produced work and are attributed |
| county_parcel_layers | Parcel layers published by the City of Philadelphia, Allegheny County and four further Pennsylvania counties | Varies by county; several state no licence. Only aggregate shares are used. |

| Sample | Lots | Note |
|---|---|---|
| Philadelphia (Philadelphia County) | 459,998 | Every 1-4 unit lot in the city; mostly rowhouses. |
| Pittsburgh (Allegheny County) | 92,081 | Every 1-4 unit lot in the city; mostly detached homes. |
| Towns in Lehigh, Montgomery, Chester and Centre counties | 73,608 | Allentown and nine smaller boroughs and townships. Results are for all 1-4 unit lots together (mostly detached), not split by building type. |

Limits:

- No hand-checked accuracy yet: a 250-lot check was drawn and not yet labelled.
- About 1 lot in 10 has low-confidence lot lines.
- Footprints miss small sheds (under about 400 sq ft), which slightly overstates fit.
- Suburban and rural cells are mostly extrapolation from city neighbourhoods and a few towns.
- Slope, flood zones, septic fields and trees do not change the fit answer.

#### `adu_legal_share.yaml`: Sets `adu.already_legal_share`

- File: `assumptions/calibration/adu_legal_share.yaml` (id `adu_legal_share_pa_2026_11`)
- Sets: ledger parameter `adu.already_legal_share`
- Measured or read: 2026-11-10 to 2026-11-22
- What it is: ADU status read from the zoning ordinances of 221 Pennsylvania towns and the two large cities (each with its code section and the date it was read), weighted by 2020 Census population; places without zoning from the Pennsylvania DCED Municipal Statistics filing.

| Source id | Source | Licence or terms |
|---|---|---|
| pa_town_zoning_codes | Zoning ordinances of 221 Pennsylvania municipalities, accessory-dwelling sections, read from each municipality's published code | Cited only. No data from it is kept. |
| pittsburgh_ord_31_2026 | City of Pittsburgh, Ordinance 31 of 2026 (Bill 2025-1545), signed Oct 29, 2026: accessory dwelling units by right citywide | Cited only. No data from it is kept. |
| philadelphia_code_14_604 | Philadelphia Code section 14-604(11), accessory dwelling units | Cited only. No data from it is kept. |
| pa_dced_munstats | [Pennsylvania Department of Community and Economic Development, Municipal Statistics, land-use ordinance fields (municipalities with no zoning ordinance)](https://apps.dced.pa.gov/Munstats-public/) | Cited only. No data from it is kept. |
| census_2020_pl | [U.S. Census Bureau, 2020 Census Redistricting Data (P.L. 94-171), population by municipality](https://www.census.gov/programs-surveys/decennial-census/about/rdo/summary-files.html) | Cited only. No data from it is kept. |

Limits:

- 77 of the 221 town readings are drafts not yet checked by a second reading.
- A share of residents is used as a share of lots; towns with larger lots are under-weighted.
- One statewide share is applied to every tract. A place-by-place mask needs the tract-to-municipality split (task P4-08).
- Philadelphia and Pittsburgh are inside the share; the SB 1346 exclusion of both is applied separately (place_scope below).
<!-- /generated: calibration_provenance -->

## How to check a download

Run `make data` again and compare the SHA-256 checksums in `data/manifest.json`. Identical checksums mean identical data.
