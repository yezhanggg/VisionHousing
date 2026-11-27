# Massachusetts intake: MBTA Communities (Section 3A) zoning and the state ADU survey

Massachusetts publishes data no other state in the tool has: a map of the multi-family zoning districts its MBTA communities adopted under Section 3A of the Zoning Act, the state's list of each community's compliance status, and a survey of ADU permits by town since the 2024 law made ADUs allowed by right. This module turns them into four small **tier 2** pack tables for Massachusetts. The engine does not need them: they are evidence and context, and a Massachusetts pack without them still runs every reform.

## What it does

| Pack table | One row per | Sources (registry ids) |
|---|---|---|
| `mbta_3a_districts` | 3A zoning district HLC found compliant | `massgis_mbta3a_atlas`, `census_subcounty_pop`, `cb_2020_tract` |
| `mbta_3a_district_tracts` | 3A district and 2020 tract its outline reaches | `massgis_mbta3a_atlas`, `cb_2020_tract` |
| `mbta_3a_status` | MBTA community (177) | `ma_3a_compliance_status`, `ma_3a_categories`, `ma_3a_development_tracker`, `massgis_mbta3a_atlas`, `census_subcounty_pop` |
| `ma_adu_survey` | town and survey period | `ma_adu_survey_file`, `census_subcounty_pop` |

Code: `src/visionhousing/data/sources/ma_intake.py` (fetch and parse), `src/visionhousing/features/ma_intake.py` (tables), wired into the pack build by `visionhousing.data.local.fetch_local_tables` (`make data STATE=MA`). Settings and the meaning of every count: `assumptions/ma_intake.yaml`.

Two more Massachusetts tables, the existing 3A modeled capacity by tract (`mbta_3a_capacity_tract`, `mbta_3a_capacity_district`) and MassDEP estimated sewer service (`ma_sewer_estimated_tract`), are made by their own add-on step: see [ma_capacity.md](ma_capacity.md).

## Terms

- **MBTA community**: one of the 177 cities and towns served by, or next to a town served by, the Massachusetts Bay Transportation Authority. Boston is exempt from the Zoning Act and so from Section 3A.
- **Section 3A district**: a zoning district where multi-family housing is allowed **as of right** (no special permit), at a gross density of at least 15 homes per acre, within half a mile of a station where the town has one.
- **Unit capacity**: how many homes the state's compliance model estimates the district's rules would allow. It is a zoning capacity, not a forecast of homes built.
- **HLC**: the Executive Office of Housing and Livable Communities, which sets the 3A rules and runs the ADU survey.
- **Cohort**: the period a survey row covers. Towns answered different survey waves, so "2025" means the full year for some towns and the first half for others.
- **Approval** (ADU survey): a building permit issued for an ADU. It is not a Building Permits Survey authorization: an attached or interior ADU in an existing house is usually filed as an alteration, which that survey leaves out.

## How it works

1. **3A atlas.** `fetch_3a_districts` asks the MassGIS map service for the districts layer in pages, with the outlines generalised on the server (vertices within about 20 m of the line dropped, coordinates to 5 decimals; `atlas` in the settings). It checks that the pages add up to the service's own count and caches one gzipped GeoJSON file. `parse_3a_districts` renames the attributes (for example `MFUC_DIST` becomes `capacity_district`) and turns the submission date from epoch milliseconds into a date. `districts_table` adds each district's county (from its town, through the Census list of county subdivisions) and the 2020 tract holding the centre of its largest part. The outline stays in the table as GeoJSON text.
2. **Tracts each district reaches.** A district can span several tracts, so its centre tract alone undercounts where it applies. `district_tract_links` (run when a pack is retrofitted: `visionhousing ma-3a-tracts --state MA`, and the last add-on step of `make data`) draws each district's outline and the tracts whose bounding box meets it on a flat grid in metres around the district (`district_tracts.cell_m`, 10 m), using the project's own NumPy helpers (`features/planar.py`: a cell belongs to a shape when its centre is inside it). The overlap of a district and a tract is the cells inside both. A tract is kept when it holds at least `district_tracts.min_share` (2%) of the district: the outlines are simplified by about 20 m, so thinner slivers are most likely the outline crossing a tract line it follows in law. The centre tract is always kept. The table `mbta_3a_district_tracts` holds `district_id`, `tract`, `overlap_acres`, `district_share` and `centre`. On the 2026-11-24 atlas: 494 districts, 832 links, 158 districts reach more than one tract. The place profile names every district reaching a tract; the engine still places district land by centre tract.
3. **3A lists.** HLC's compliance status sheet, community categories file and development tracker are CSV files on www.mass.gov. `parse_compliance_status`, `parse_categories` and `parse_development_tracker` read them by column name, strip thousands commas and percent signs, and keep "-" as empty. The tracker is summed per town; developer names and addresses are not kept. `status_table` joins the lists, the atlas (districts, reported capacity, acres, first submission date per town) and the Census towns.
4. **ADU survey.** `parse_adu_survey` reshapes the composite file into one row per town and period. The settings map each town's answer ("End-of-year", "Mid-year") to a cohort and dates. "No response" gives no row: unknown is never written as zero. `survey_totals_check` compares the published totals with the sum of the periods (the period suffixes come from `adu_survey.periods` in the settings, so a new wave needs only a settings line) and names any town where they differ; a town with every cell blank is unknown, not a disagreement.
5. **Observed context.** `survey_summary` gives, per survey period (every wave kept: the 2025 full-year and first-half cohorts and the 2026 first half), the towns that answered (of the 351 municipalities, `adu_survey.municipalities`), applications, approvals (detached, attached or interior) and certificates of occupancy; `survey_context` writes it as one sentence, statewide or for one county's towns. The place profile (State law section, by county), the state data report card and the ADU plausibility card show it as observed context. It is never divided into a rate and never an input to a run. Without the hand-saved file it says "not supplied".
6. **Town names.** The lists use common names ("Manchester"), the Census Bureau legal names with a type word ("Manchester-by-the-Sea town", "Agawam Town city"). `muni_key` drops the type word, spaces and punctuation; `name_overrides` in the settings holds the pairs that still differ. A name that does not match is kept with an empty county and listed in the build notes.

## Where the files come from, and what happens when they cannot be read

The atlas is read by script. www.mass.gov answers scripted requests with HTTP 403 "Not allowed" (checked 2026-11-24), and the build respects that: `fetch_copy` asks the publisher's address once and, when refused, reads a copy a person downloaded in a browser and saved under the name in the settings (for example `data/raw/local/ma/mbta3a_compliance_status.csv`). Each table records which edition it holds (`status_as_of`, `categories_as_of`, `tracker_as_of`, `survey_as_of`) and how it was read (`status_route`, `categories_route`, `tracker_route`, `survey_route`: `publisher` or `hand_saved`).

A hand-saved copy records its own edition, never the edition the settings list for the publisher's address (the person may have saved a newer one). In order: a small note the person writes next to the file, `<file>.edition.yaml` with `as_of: YYYY-MM-DD` (and, if they like, `url:`); else an "as of" date in the file's title or header row ("Compliance Status Sheet as of 11-24-26"); else the edition is unknown, the `*_as_of` column stays empty and the build notes ask for the note. A copy replaced by hand, or a note added later, is read afresh (the cache records the file's checksum). The state data report card marks such a table "hand-saved" and names each file's edition.

Counts summed per town (atlas capacity and acres, development-tracker units) stay unknown when every value for the town is blank: they are never written as 0. A town the tracker does not list has 0 linked developments.

| Missing file | What the pack does |
|---|---|
| Compliance status sheet | `mbta_3a_status` is built from the atlas alone (`status_source = atlas`): only the towns HLC found compliant, with no category, deadline or required capacity. The other MBTA communities are missing, not "noncompliant" |
| Categories or development tracker | Their columns stay empty |
| ADU survey | `ma_adu_survey` is left out; the statewide totals stay in the evidence library (`ev.adu.massachusetts_2025`); the profile, report card and plausibility card say "not supplied" |
| Atlas (service down) | `mbta_3a_districts` is left out; `mbta_3a_status` is built only if the status sheet was read |

The build notes say which of these happened, and the state data report card marks each absent file "not supplied" ([report_card.md](report_card.md)). A clean build with all four files absent (checked 2026-11-25 on a copy of the cache, every request refused) gives the same engine results for all five reforms as the current pack: none of these tables feeds the engine.

## Terms of use

- **Atlas**: "Data are public" (MassGIS item licence), meant for policy analysis, research and public understanding of zoning. HLC cautions that it is not a legal record of permitted uses; the compliance determination letter is the dispositive record.
- **HLC lists and survey**: public records posted for download by the Commonwealth; the mass.gov terms of use apply.
- **Transit schedules** used for the Massachusetts pack (MBTA and the regional transit authorities) are under the MassDOT Developers License Agreement: use and redistribution allowed, MassDOT acknowledged as the provider, no endorsement implied. Only per-stop departure counts are kept.

## Limits

- The atlas holds only compliant districts. Its capacity fields are the compliance model's estimates and are filled unevenly (`capacity_cleaned` and `capacity_parcels` are often 0 where `capacity_district` is filled).
- A district's submission date is not its adoption or compliance date.
- Outlines are simplified and are not legal boundaries, so tract overlaps are approximate and slivers under 2% of a district are dropped.
- The ADU survey is self-reported by building officials and not every town answered every wave; totals are a floor.
- Status lists change often and HLC gives each update a new file name: the address in `assumptions/ma_intake.yaml` must be updated by hand.

## Tests

`tests/test_ma_intake.py` and `tests/test_ma_adu_rights.py` (survey context; format fixtures in `tests/fixtures/ma/`: the published headers with a few made-up rows) and `tests/test_transit_pairing.py`. No network.
