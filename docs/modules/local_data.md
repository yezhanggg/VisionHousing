# Local refinement data: zoning, plans, parcels, owners, local permit pipeline

These sources exist only for Pennsylvania, or only for Philadelphia and Allegheny County. They feed five place-profile sections. They are **tier 2** ("local refinement only") in the data dictionary: the engine does not need them, and a place without them shows "no data" instead of failing.

## What it does

| Section | Pack tables | Sources | Places |
|---|---|---|---|
| `zoning` | `municipal_planning`, `tract_municipalities` | PA DCED Municipal Statistics; Census subcounty population estimates; 2020 block assignment file | Every Pennsylvania county and tract |
| `plans` | `municipal_planning`, `tract_municipalities`; `assumptions/plans.yaml` | Same; county plan pages (cited) | Every Pennsylvania county and tract; county plans and commission links for all 67 counties (34 confirmed). The ADU, lot and parking rules and the new `state_law` section are described in [law.md](law.md) |
| `parcels` | `parcels_area` | Philadelphia OPA; Allegheny assessments, sales and WPRDC parcel centroids | Philadelphia and Allegheny |
| `owners` | `owners_area`, `owners_deeds_year`; `_owners` in `assumptions/evidence.yaml` | Philadelphia OPA and transfer records; Allegheny assessments | Philadelphia and Allegheny (deed flow: Philadelphia) |
| `pipeline_local` | `pipeline_tract_year`, `lihtc_awards` | Philadelphia L&I permits; Pittsburgh PLI permits; PHFA award lists; Census Geocoder | Philadelphia and Pittsburgh (permits); all Pennsylvania (awards) |

Massachusetts has its own local tables (MBTA Communities Section 3A districts and status, and the state ADU survey), described in [ma_intake.md](ma_intake.md). `visionhousing.data.local.fetch_local_tables` runs every source on its own: when one city service is down the others still go in and the failure is written in the build notes. `make data` calls it through `pack.load_inputs`. `local.add_to_pack(folder, cache)` adds the tables to a pack that is already built and rebuilds its dictionary and manifest.

## Terms

- **County subdivision**: the Census Bureau's name for a municipality (city, borough, township, town).
- **Zoning regime**: who sets zoning in a municipality. `own` (its own ordinance), `joint` (an ordinance shared with neighbours), `county` (no ordinance of its own; the county's applies), `none`.
- **Entity**: an owner or buyer whose name contains a word such as LLC, INC or TRUST (list in `assumptions/owners.yaml`). Not the same as an institutional investor.
- **Homestead exemption**: a tax break the owner claims for their main home. No exemption suggests, but does not prove, that the home is not owner-occupied.
- **Stock and flow**: who owns homes today (stock) and who is buying them each year (flow).
- **Server-side totals**: the query is added up on the publisher's server (`GROUP BY`, medians); only totals are downloaded. This keeps memory small and means owner names never leave the publisher.

## How it works

1. **Zoning and plans.** The DCED report sits behind an ASP.NET form; `munstats.fetch_munstats` posts it for all counties and downloads the CSV export. `munstats.crosswalk` matches DCED municipalities to Census county subdivisions by county and normalised name (type words spelled out, punctuation removed), then by name without the type word, then by hand-checked pairs in `assumptions/zoning.yaml`. On 2026-11-22 all 2,567 DCED municipalities matched; one Census subdivision (Wheatland borough, Mercer County) has no DCED row. County shares use 2024 population estimates; tract shares split a tract by the 2020 housing units of its blocks.
2. **Parcels and owners, Philadelphia.** One slim row per parcel (location rounded to 5 decimals, category, value, year built, lot, last sale and three 0/1 flags worked out on the City's server) is read in pages of 100,000. Parcels are placed in 2020 tracts by testing which tract outline holds each point (the City's own tract field uses older tracts). Deed flow is counted per year on the server.
3. **Parcels and owners, Allegheny.** Assessments are joined to WPRDC's parcel centroids (which carry the 2020 tract) and to the sales file on WPRDC's server; only totals and medians per tract come back. Border parcels whose centre falls in a neighbouring county count only in the county total.
4. **Permits.** New-home building permits (types in `assumptions/pipeline_local.yaml`) are placed in tracts by their point. The window ends at the last full calendar year.
5. **LIHTC awards.** Award-list links are read from PHFA's programme page; each PDF is saved to a temporary file and read with `pypdf`. The reader finds a city/ZIP line, a county name and the closing line of numbers; the homes are the last whole number before the first dollar amount and the credit is the last dollar amount. Scanned lists (2019, 2025 at the time of writing) have no text and are skipped and named in the notes. Addresses are geocoded to 2020 tracts with the Census Geocoder.

All parameters (land-use crosswalk, year-built groups, sales windows and price floor, entity words, deed rules, permit types) live in `assumptions/parcels.yaml`, `owners.yaml`, `pipeline_local.yaml` and `zoning.yaml`.

## Limits

- DCED's record is self-reported and says whether an ordinance or plan exists, not what it allows.
- Allegheny values are 2012 base-year assessments and cannot be compared with Philadelphia's current market values.
- Philadelphia keeps only each parcel's latest sale, so repeat sales in a window count once.
- An entity is not an institutional investor; trusts include estate planning. The cited benchmarks (Philadelphia Fed 2025, Pew 2024) describe Philadelphia only and use other definitions.
- The City of Philadelphia's licence is to be confirmed before release; only derived totals are shown.
- Chester County GIS and the National Zoning Atlas are not used (their terms forbid it). Zoning district maps are not built yet.

## Tests

- `tests/test_munstats.py`: form parsing, name normalisation, the crosswalk (including the unmatched list), regimes and the two tables.
- `tests/test_local_parcels.py`: land-use crosswalks, Philadelphia and Allegheny aggregates, owner shares, deed years, that no query returns a name, and that every column is described.
- `tests/test_local_pipeline.py`: the PHFA reader on two layouts, award links, permit aggregation.
- `tests/test_local_sections.py`: the five sections on a tiny hand-made pack, and "no data" without the tables.
- `tests/test_local_realdata.py` (`realdata` marker): the live services, including a crosswalk match rate of at least 99%.
