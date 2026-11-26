# Safety and neighbourhood distress

Code:

- City records, no key: `src/visionhousing/data/sources/phl_safety.py` (fetch), `src/visionhousing/features/safety_local.py` (table), rules in `assumptions/safety_local.yaml`.
- FBI path, needs a key: `src/visionhousing/data/sources/safety.py`, `src/visionhousing/features/safety.py` (see [place.md](place.md)).
- Both reach the pack through `src/visionhousing/data/sources/place.py` and the profile through `src/visionhousing/profile/sections/safety.py`.

Tests: `tests/test_place_safety_local.py` (city records, offline: a fake Carto server answers with the hand-made CSV answers in `tests/fixtures/place/carto_safety_*.csv`, placed in synthetic Testland) and `tests/test_place_safety.py` (FBI path).

## What it does, in plain words

The profile's Safety section answers two questions for a place: how much crime is reported here, and are there signs that blocks are being neglected? It compares a tract with its county and says whether numbers went up or down over the last five years.

The FBI's crime API needs a key, so without one the section used to be empty. Philadelphia publishes its own police, inspection and 311 records with no key, so for Philadelphia the section now has real numbers. The FBI path is kept: when a key is set at build time, the county figures from the FBI appear as well, for every county.

## Terms used

- **Incident**: one police report, classed by its most serious offense with the FBI's Uniform Crime Reporting (UCR) code.
- **Violent incidents**: homicide (UCR 100), rape (200), robbery (300), aggravated assault (400).
- **Property incidents**: burglary (500), theft (600), motor-vehicle theft (700), arson (900).
- **Shooting victims**: people shot, one record each, including police-involved shootings; those who died are counted too.
- **L&I code-violation case**: a case opened by the Department of Licenses and Inspections after an inspection finds a code violation (notice of violation, site violation notice). Counted once per case number.
- **Neglect-related 311 requests**: requests to the City's 311 line about illegal dumping, abandoned vehicles, graffiti, dangerous buildings, property maintenance and sanitation violations (the list is in `assumptions/safety_local.yaml`).
- **Rate per 1,000 residents**: records / residents x 1,000. Residents are the ACS 2020-2024 population, used for every year.
- **Trend**: the latest year's count / the first year's count - 1, over the window.

## Sources and licences

| Source id | Carto table | Licence | Kept |
|---|---|---|---|
| `phl_shootings` | `shootings` | City of Philadelphia License | counts per year and ~10 m cell |
| `phl_crime_incidents` | `incidents_part1_part2` | City of Philadelphia License | counts per year and cell |
| `phl_li_violations` | `violations` | City of Philadelphia License | case counts per year and cell |
| `phl_311_requests` | `public_cases_fc` | City of Philadelphia License | counts per year and cell |

All four OpenDataPhilly dataset pages (checked 2026-11-23) name the same licence, "City of Philadelphia License", and the same terms of use: browsing or using the data means accepting them; the City keeps all rights in the database and gives no title to it; the data come as they are, with no warranty; the user holds the City harmless. The terms allow use; they ask for nothing that stops publishing totals. This is a city licence, not an open-content licence such as CC BY, so the tool keeps only tract and county totals, credits the City, and shows the terms link with every number. The same licence already covers the City's permit and property files used elsewhere.

**Nothing personal is downloaded.** Every query adds records up on the City's server by calendar year and by location cell (the point rounded to 4 decimals, about 10 metres) and returns only the counts. No record, name, address, case number or note is read. Records with no location come back as one cell with an empty location; they count in the city total only.

**Allegheny County was checked and left out.** The Pittsburgh police incident blotter on the Western Pennsylvania Regional Data Center (CC BY) now holds a single record; its archived, UCR-coded version (CC BY) stops in November 2023; the current "Monthly Criminal Activity" dataset says "License not specified", so its terms are unclear and it is not used. Pittsburgh's 311 file is CC BY but covers only the City of Pittsburgh, not the county, so it would need a city-part comparison that is not built yet.

## Pack table `safety_local` (tier 2)

One row per 2020 tract and year, and per county and year: `population`, the counts `shootings`, `shootings_fatal`, `violent`, `property`, `li_cases`, `requests_311`, each with `<count>_per_1000`, and on county rows `unplaced` (records with no location or outside every tract). Every tract of the county has every year (zeros where nothing was recorded). A tract with fewer than `min_tract_population` residents (500) gets counts but no rate.

How it is built (`safety_local_table`):

1. Each cell is put in the 2020 tract whose outline holds its point (the same point-in-polygon test as the parcel and permit tables).
2. Counts are added per tract and year, and per county and year (all cells, placed or not).
3. Rates = count / population x 1,000, rounded to 2 decimals.

## In the profile

For a Philadelphia county or tract, for each measure: the yearly average per 1,000 residents over the last `window_years` (5) full years, the count in the latest year, and the trend from the first to the last year of the window. A trend is shown only when the first year has at least `min_trend_count` (20) records. A tract is compared with Philadelphia as a whole in plain words ("higher than the county"). Philadelphia itself has no statewide comparison, and the section says so: the records cover only the city. A county outside Philadelphia shows the FBI figures when the pack has them, else "no data".

Sample (pack of 2026-11-23): "City records for Philadelphia County, yearly average per 1,000 residents in 2021-2025: 1.1 shooting victims, 8.9 violent incidents and 42.3 property incidents. Signs of neglect: 28.3 code-violation cases and 71.5 neglect-related 311 requests per 1,000 residents a year. From 2021 to 2025, violent incidents fell 20% and shooting victims fell 59%."

## Limits

- Incidents known to police are not all crime; many offenses are never reported.
- 311 requests and code cases also follow how often residents call and where inspectors go, not only the state of the blocks.
- Locations are mostly the hundred-block point, so a record near a tract edge can fall in the next tract.
- Rates divide by residents, so tracts with many visitors and few residents (downtown, the stadiums, the airport) look high.
- One resident count (ACS 2020-2024) is used for every year.
- City records are not the FBI's agency counts and are not comparable with other places.
- The City revises records after first publication; a rebuild can change recent counts a little.

## Refresh

`make data` fetches and builds everything. To add only the place-profile tables (jobs, incentive areas, safety) and the typology to a pack that is already built:

```
uv run python -c "from pathlib import Path; from visionhousing.data.pack import add_profile_tables; add_profile_tables(Path('data/packs/PA'), Path('data/raw'))"
```

The fetch sends one query per table and year (20 small queries for five years) and takes about half a minute.
