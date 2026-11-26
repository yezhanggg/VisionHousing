# Module: Water and sewer readiness (`features/infrastructure.py`)

**What it does.** For every census tract and county it answers one question from official records only: *does a published record place these homes inside a public sewer service area?* It does the same for public water service, and it adds the age of each Pennsylvania municipality's sewage facilities plan (Act 537) and EPA's documented clean-water needs as context. The answers feed the place profile section "Water and sewer readiness" and the optional practical capacity screen ([capacity.md](capacity.md)). Without that option nothing here changes a model estimate.

**Why.** A reform can make a lot legally buildable, but an added home also needs somewhere for its wastewater to go. Whether a lot can connect to public sewer is decided by utilities and by the municipality's plan, not by how close a pipe is. So the tool never guesses sewer from distance, and it never claims spare capacity. Where no record was found it says "unknown".

## Terms

- **Public sewer service area**: the area a public sewer system serves, as mapped by a county, a state agency or the utility.
- **Act 537 plan**: Pennsylvania's Sewage Facilities Act (1966) requires each municipality to keep an "official plan" for sewage; DEP approves it. DEP publishes the date of each municipality's latest approval and an age class.
- **Chapter 94**: the Pennsylvania rule (25 Pa. Code Chapter 94) under which DEP can ban new sewer connections when a plant or sewer is overloaded.
- **Sewershed**: the area draining to one wastewater treatment plant. EPA's national sewersheds are mostly modelled by machine learning.
- **CWNS**: EPA's Clean Watersheds Needs Survey; the 2022 survey lists capital needs for clean-water projects, in January 2022 dollars.
- **Homes**: 2020 Census housing units by block. The Census Bureau does not place single-family homes separately, so shares are of all homes; they are the best public stand-in for 1-unit homes. A block counts as inside an area when its internal point is.

## The four answers

Every block with homes gets one answer. A tract's `sewer_status` is `verified` only when **more than half** of its homes are verified (`status.verified_min_share: 0.5` in `assumptions/infrastructure.yaml`); having more verified homes than any other single answer is not enough. Otherwise the status is whichever of the other answers holds the most homes, ties going to the more cautious one (constraint, then unknown, then outside). A tract without 2020 homes has a blank status.

| Answer | Meaning | Where it comes from |
|---|---|---|
| `verified` | verified available: more than half of the tract's homes inside a mapped public sewer service area | a county or state map whose terms allow sharing results, or a utility's statement that it serves the whole jurisdiction |
| `outside` | outside every service area of a complete county map (on-lot septic systems expected) | only counties whose map is a complete inventory |
| `constraint` | a documented moratorium, connection ban or capacity allocation | an official record listed by hand in `assumptions/infrastructure.yaml`, with its address, date and a quote |
| `unknown` | no official record speaks to these homes | everything else |

`sewer_share` is the share of a tract's homes inside a mapped sewer service area, including any there under a documented constraint (blank when no record speaks to any of them; verified = `sewer_share` − the constraint homes inside the area); `sewer_known` is the share any record speaks to; `constraint_share` the share under a documented constraint.

## Records read

The list lives in `assumptions/infrastructure.yaml`; every entry is a source in `sources.yaml` with its licence and limits.

| Record | State | Use | Edition | Terms |
|---|---|---|---|---|
| Montgomery County sewer service areas (`pa_montco_sewer_service_areas`) | PA | status (complete county map) | July 2022 | public county record on PASDA, no use constraint stated |
| Adams County sewer service areas, existing only (`pa_adams_sewer_service_areas`) | PA | status (complete county map) | September 2024 | county notice: information only, verify with the provider |
| Philadelphia Water Department statement (`phl_pwd_wastewater`) | PA | status (whole city) | read by script 2026-11-24 | public statement on water.phila.gov/about-pwd: PWD "supplies drinking water, wastewater, and stormwater services to customers within the City of Philadelphia"; its wastewater page adds "combined sewers in approximately half of the city's neighborhoods and a separate sewer system in the other half". A statement of the utility's service territory, not a parcel map: a few properties on private on-lot systems would not show |
| Known wastewater service areas (`vt_accd_wastewater_service_areas`) | VT | status (inside only: outside stays unknown) | August 2023 | public state record (ACCD, published by VCGI), no licence stated |
| Public water supplier service areas (`pa_dep_pws_service_areas`) | PA | water share (statewide map) | October 2026 | public DEP record on PASDA |
| Act 537 plan status (`pa_dep_act537_plan_status`) | PA | plan age per municipality | DEP tables dated 2025-05-29 | public DEP PDFs |
| EPA sewersheds and endpoint plants (`epa_sewersheds`) | both | context only, contacts | version 1.3, October 2026 | U.S. government work, MIT licence |
| CWNS 2022 needs by county (`epa_cwns_2022`) | both | context only | 2022 survey | public domain |

Left out on purpose, because their terms forbid sharing anything derived from them: Chester County's sewer service areas (the County GIS Access Policy bars disseminating "any product or service derived therefrom") and ALCOSAN's service area in Allegheny County (its GIS disclaimer bars redistribution "in any form"). Other Pennsylvania counties publish no sewer service-area map that could be found and read without a key. No machine-readable official list of Chapter 94 connection bans or moratoria was found for either state on 2026-11-24, so `constraint_records` is empty; a record is added only after reading it.

## How each number is worked out

1. **Read the layers.** Each map service is asked for its object ids, then for the features in chunks of 100 (works with or without paging), in longitude/latitude (NAD83), thinned by the server to about 5 m. The polygons are cached in `data/raw/infrastructure/` and recorded in the download manifest; they never enter the pack.
2. **Place the blocks.** For each layer, every block point inside a polygon's bounding box is tested against the polygon by ray casting (`features/planar.py`, plain NumPy), combining a polygon's rings by the even-odd rule so holes come out right. A block takes the first official layer that holds it.
3. **Add up.** Per tract and county: homes verified, outside, under constraint and unknown; homes inside a public water area; homes inside an EPA sewershed. County figures add up blocks, so they are exact sums, not averages of tract shares.
4. **Act 537.** DEP's six regional tables are read with `pypdf`; each row (municipality, county, approval date, age words) is matched to a municipality (county subdivision) by name within the county, ignoring the words township, borough, city, town and "of", and using the type when two names would collide. 2,534 of 2,569 rows matched on 2026-11-24. Per tract, `act537_class` is the class of the municipality holding most of its homes (`lt10`, `10to20`, `20plus`, `prereg`); per county, the share of homes in each class, weighted by the 2020 homes of each tract piece in each municipality.
5. **Contacts.** For each county and kind (mapped sewer provider, public water system, EPA sewershed plant), the three providers serving the most homes, with an official listing page where one exists: EPA SDWIS for a water system (by its PWS id) and EPA ECHO for a treatment plant (by its NPDES permit).

## Tables in the pack

| Table | One row per | Columns |
|---|---|---|
| `infra_tract` | tract | `geoid`, `sewer_status`, `sewer_share`, `sewer_known`, `constraint_share`, `sewer_source`, `water_share`, `epa_share`, `act537_class` |
| `infra_county` | county | homes; tracts and homes by status; `water_share`, `epa_share`; `sewer_sources`, `sewer_as_of`; `act537_lt10`, `act537_10to20`, `act537_20plus`, `act537_prereg`, `act537_unmatched`; CWNS needs (`cwns_total`, `cwns_treatment`, `cwns_new_sewers`, `cwns_sewer_repair`, `cwns_cso`, `cwns_stormwater`, `cwns_decentralized`, `cwns_nonpoint`); `epa_plants` |
| `infra_contacts` | county, kind and rank | `name`, `homes`, `contact_url`, `source` |

Shares have 3 decimals. Every column is described in the pack's data dictionary.

## Coverage (built 2026-11-24)

| State | Tracts | Verified | Outside | Constraint | Unknown | Blank (no homes) |
|---|---|---|---|---|---|---|
| Pennsylvania | 3,445 | 621 | 25 | 0 | 2,782 | 17 |
| Vermont | 193 | 49 | 0 | 0 | 143 | 1 |

Massachusetts has no verified answer yet (every tract `unknown` here). MassDEP's *estimated* sewer service areas are read into a separate table, `ma_sewer_estimated_tract`, with its own evidence class ("estimated service") and a flag for placeholder circles; it is not mixed into these answers and the capacity screen does not read it ([ma_capacity.md](ma_capacity.md)).

By homes, Pennsylvania is 19% verified, 0.7% outside and 81% unknown; the verified and outside tracts are in Philadelphia, Montgomery and Adams counties; every Pennsylvania home also has a public water answer (the DEP map is statewide). Vermont has no statewide public water map that could be read, so its water shares are blank.

## Engine use: the practical capacity screen

The practical capacity screen (run option `capacity_screen`, [capacity.md](capacity.md), method [../methods/capacity.md](../methods/capacity.md)) reads these columns; without that option no estimate uses them. The columns are `infra_tract.sewer_status`, `sewer_share` and `sewer_known` (share of homes inside a public sewer service area, constraint homes included, and share with any official answer), and `constraint_share`. A natural use: count an added ADU or small multi-unit project as sewer-ready only for `sewer_share`, treat `outside` homes as needing on-lot approval, and carry `unknown` as a stated range (a sensitivity from 0 to 1), never as zero. Nothing should read `epa_share` or the CWNS needs as capacity.

## Use

```
visionhousing infrastructure --state PA     # adds infra_tract, infra_county, infra_contacts
```

The command reuses cached layers in `data/raw/infrastructure/`, rewrites the pack's data dictionary and manifest, and prints the tract counts by status and the pack's size. A full `make data` runs it as one of the add-on steps.

Profile section `infrastructure` ("Water and sewer readiness"): facts for homes verified, outside, under constraint and unknown; homes in a public water area; homes in an EPA sewershed (context); for counties the Act 537 old-plan share and CWNS needs; the largest providers with their listing pages. Charts: `infrastructure.sewer_evidence` (stack), `infrastructure.water` (stack), `infrastructure.act537` (bar, counties and the state) and `infrastructure.cwns_needs` (rank, by county).

## Limits

- "Unknown" means no shareable official record was found, not that sewer is absent; most Pennsylvania tracts are unknown.
- A service-area map shows where a system serves, not spare treatment or pipe capacity; a home inside may still need an extension, a connection fee or an allocation.
- Maps age: Montgomery's is from 2022, Vermont's polygons date from 2003 to 2023.
- Philadelphia is "verified" from the utility's own statement of its service territory (the whole city), not from a parcel map; a few properties on private on-lot systems would not show, and the statement says nothing about capacity or combined-sewer overflow limits.
- A tract where verified homes are the largest group but not a majority shows the next answer (usually "unknown"), so `sewer_share` can be up to 0.5 in a tract that is not "verified".
- Homes are placed by block centre points, so a large rural block counts wholly in or out.
- Act 537 plan age is the date of DEP's last approval; an old plan is not a ban, and some names did not match (shown as `act537_unmatched`).
- EPA sewersheds are mostly modelled (Pennsylvania 799 of 838); EPA says they are not authoritative for property-level connections.
- CWNS needs are what states documented for the 2022 survey; a zero can mean nothing was submitted.

## Files

| File | What it does |
|---|---|
| `data/sources/infrastructure.py` | Reads map-service layers by id chunks, the EPA endpoints and CWNS county needs, and DEP's Act 537 PDFs |
| `features/infrastructure.py` | Points in polygons (even-odd rule), the four answers per block, statuses, Act 537 matching, the three tables |
| `features/infrastructure_build.py` | `add_infrastructure` (the pack add-on) and the `visionhousing infrastructure` command |
| `profile/sections/infrastructure.py` | The profile section and its charts |
| `assumptions/infrastructure.yaml` | The records read, the status words and tie order, constraint records |

## Tests

`tests/test_infrastructure.py` (points in polygons with holes, the four answers, tie order, tables, Act 537 parsing and matching, contacts, the id-chunk reader against a fake server) and `tests/test_profile_readiness.py` (the profile section on a Testland copy). No network:

```
.venv/bin/python -m pytest -q tests/test_infrastructure.py tests/test_profile_readiness.py -p no:cacheprovider
```
