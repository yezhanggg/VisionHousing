# Module: Place profiles (`src/visionhousing/profile/`)

**What it does.** Describes one place, a county or a census tract, in short plain sections: who lives there, what the homes are like, how many homes are being permitted, how close homes are to frequent transit, and how good the data are. It is built for market research: easy to read, share, print and reuse.

**Why.** The reform estimates answer "what would a state law change?". People also ask "what is this place like?". A profile answers that from the same state data pack, with the same rule as the rest of the tool: every number carries its source, its edition and its year, and nothing is typed by hand.

## Terms

- **Fact**: one number (or one short word) about the place, with its label, unit, the text shown to people, its source id (an entry in `src/visionhousing/data/sources.yaml`), the source's edition ("vintage"), the year it describes, the geography it is measured at and, where the source gives one, its 90% margin of error and range.
- **Comparison**: the same measure for a bigger area, with plain words: "higher than the state", "lower than the county", "about the same as the state". A county is compared with its state; a tract with its county.
- **Section**: one topic. It opens with one plain sentence, then lists its facts, then says what it cannot tell you.
- **Margin of error**: the survey's 90% margin. The range shown is the value minus and plus the margin.
- **Headline**: a section's lead fact, shown large at the top (`headline`: the fact id, label, text and comparison words).
- **Summary points**: up to three short plain facts under the headline (`summary_points`), for a quick read. The one-sentence `summary` stays as it was.
- **Chart**: a breakdown ready to draw (see "Charts" below). Every share in it is worked out in Python; the browser only draws.
- **Rank**: for a county, where it stands among the state's counties on one measure ("4th highest of 67 counties in Pennsylvania").

## Sections

| Key | Title | Built from | Status |
|---|---|---|---|
| `people` | People | ACS 2020-2024 (`acs5_summary_file`, `acs5_profile_tables`) | built |
| `housing` | Homes | ACS 2020-2024 (`acs5_summary_file`, `acs5_profile_tables`); FHFA price index (`fhfa_hpi_county`, `fhfa_hpi_tract`) | built |
| `jobs` | Jobs and employers | LODES (`lodes_wac`, `lodes_od`), County Business Patterns (`cbp_county`); commute mode from ACS (`acs5_profile_tables`) | built |
| `permits` | Permits | Building Permits Survey (`bps_county_annual`) | built |
| `affordability` | Cost and affordability | HUD CHAS 2018-2022 (`hud_chas`), income limits FY2026 (`hud_income_limits`), assisted housing lists (`hud_mf_assisted`, `hud_public_housing`, `hud_lihtc`, `hud_hcv_tract`); NLIHC state context (`nlihc_gap_2026`) | built where the pack has the affordability tables ([affordability.md](affordability.md)) |
| `pipeline_local` | Local permit pipeline | | reserved |
| `zoning` | Zoning and overlays | DCED Municipal Statistics (`pa_dced_munstats`); municipal rules (`muni_zoning_rules`, see [law.md](law.md)) | built |
| `plans` | Comprehensive plans | DCED Municipal Statistics; county plan list (`county_comprehensive_plans`) | built |
| `state_law` | State law and bills | Pennsylvania: MPC text (`pa_mpc`); bill pages (`pa_hb2186`, `pa_sb1346`, `pa_hb2155`, `pa_hb1459`). Other states: the state law file (`assumptions/law/states/<st>.yaml`), the timeline (`assumptions/law/timeline.yaml`) and the already-legal calibration, under their statute sources (`ma_gl_40a`, `ma_acts_2024_c150`, `vt_acts_2026`, `vt_24_vsa_4412`, `wy_statutes`); Massachusetts 3A districts (`massgis_mbta3a_atlas`, tracts each outline reaches from `mbta_3a_district_tracts`); see [law.md](law.md) | built |
| `incentives` | Tax credits and incentive areas | | reserved |
| `transit` | Transit | National Transit Map stops (`ntm_stops`); 2020 Census blocks (`tiger_2020_tabblock`) | built |
| `infrastructure` | Water and sewer readiness | Official sewer service areas (`pa_montco_sewer_service_areas`, `pa_adams_sewer_service_areas`, `phl_pwd_wastewater`, `vt_accd_wastewater_service_areas`), public water areas (`pa_dep_pws_service_areas`), Act 537 plan status (`pa_dep_act537_plan_status`), EPA sewersheds and CWNS 2022 as context (`epa_sewersheds`, `epa_cwns_2022`) | built where the pack has the infrastructure tables ([infrastructure.md](infrastructure.md)) |
| `hazards` | Hazards and land | FEMA NFHL (`fema_nfhl`), Annual NLCD (`usgs_annual_nlcd`), 3DEP elevation (`usgs_3dep_dem`) | built where the pack has the hazard tables ([hazards.md](hazards.md)) |
| `safety` | Safety | Philadelphia city records (`phl_shootings`, `phl_crime_incidents`, `phl_li_violations`, `phl_311_requests`); FBI Crime Data Explorer (`fbi_cde_agency`) when a key was set | built where the pack has either ([safety.md](safety.md)) |
| `typology` | Places like this one | the pack's own tables, through `typology_county` and `typology_tract` | built ([typology.md](typology.md)) |
| `owners` | Institutional buyers | | reserved |
| `parcels` | Parcels and property | | reserved |
| `data_quality` | Data quality | the state data report card | built |

A reserved section shows "Coming soon: not available for this place yet" until its module exists.

### How each number is worked out

**People.** Residents and households (owner plus renter homes, B25003) are sums of the tracts. Their margin is the Census Bureau's approximation for a sum: the square root of the sum of squared margins. The share aged 65 or older is a tract's published share, or for a county the tracts' shares weighted by residents. Median household income is the Bureau's published median for a tract, a county or the state (county and state medians are in the pack's `acs_bands` table); a pack without that table falls back, for a county or the state, to the average of its tracts' medians weighted by households, marked approximate. The share of households that rent is renters / households, with the Bureau's margin formula for a proportion. The rent-to-income gauge is median gross rent x 12 / median household income: a rough gauge only; the real rent-burden share is in the Homes section.

**Homes.** Building-type shares are units in that type (B25024) / all housing units. Median home value is a tract's published median; for a county or the state, the bracket counts (B25075) are added over the tracts and the median is found by straight-line interpolation inside the bracket that holds it. Median gross rent is the published median (B25064) for a tract, a county or the state, falling back to the same bracket method (B25063) when a pack has no county medians. Rent burden is renter households paying 30% or more of income in gross rent / renter households whose share could be worked out (B25070), with the Bureau's margin formula for a proportion. Vacancy is vacant / all units (B25002). Age of homes (B25034): the share built before 1940 and in 2010 or later. Price changes (1, 5 and 10 years) come from the FHFA annual index: the county index for a county, the tract index for a tract where FHFA publishes one. The pack holds no state index, so county price changes are not compared with the state.

**Permits.** Units authorized by permit, from the Census Bureau's county files. Permits are counted only by county, so a tract shows its county's numbers and says so (`measured_at` is `county`). Facts: units in the last five years and the five before, the change between them, the yearly pace per 1,000 existing homes (five-year total / 5 / ACS housing units x 1,000), the single-family share, units in buildings of 5 or more, and each of the last ten years. The state figure is the sum of its counties. Years the Bureau partly estimated are named in the limits.

**Cost and affordability.** Three measures kept apart. (1) Cost burden from HUD CHAS 2018-2022: households in the burdened bands / households whose burden could be computed, by income as a share of HUD area median family income (AMI); headline: renters at or below 80% of AMI paying over 30% of income. County and state margins use the proportion formula with root-sum-square margins of the summed cells; tract rows have no margins. Owners are shown for counties and the state. (2) HUD income limits FY2026 for the county's HUD area: AMI for four people, the 50% and 80% limits, and the highest monthly housing cost for a 2-bedroom home at 50% and 80% of AMI (30% of the limit for 3 people / 12). (3) Assisted homes from HUD's lists: contract, public housing and tax-credit units (an upper bound; programs overlap), voucher households, and units whose contract or restriction ends within 5 and 10 years. Plus the share of renter homes with rents affordable at 50% of AMI or less, renters in buildings built before 1980, and NLIHC's statewide count of affordable and available homes per 100 extremely low-income renters (quoted with credit, context only). Method: [affordability.md](affordability.md).

**Transit.** A major transit stop follows the major-stop rule: any rail station, or a bus stop with at least 64 scheduled weekday departures. A home is near transit when its 2020 census block's centre is within 1/2 mile of a rail station or 1/4 mile of a frequent bus stop. Facts: major stops inside the place (stop points as listed; one station can have several), the share of homes near transit by the major-stop rule, and the share within 1/2 mile of any major stop. A real pack stores the per-tract counts; for Testland they are worked out from its blocks and stops with the same function the pack build uses.

**Places like this one.** The place's group from seeded k-means on z-scored features, the group's rule-made name, how many places share it, what sets the group apart, and the three nearest places in the same feature space. Method, features, k and seed: [typology.md](typology.md).

**Data quality.** One fact per model input with a source: its tier (0 = national files for every state, 1 = published for many states, 2 = local only), status and coverage, from the state data report card. Inputs taken from the Assumption Ledger are named in the limits. The card describes the whole state, so these facts are measured at the state level.

### Charts

Each chart is one record in a section's `charts` list. Exact shape (Philadelphia County, 42101, as `visionhousing profile --state PA --county 42101 --format json` gives it):

```json
{
  "id": "housing.tenure",
  "kind": "pie",
  "title": "Owners and renters",
  "unit": "households",
  "items": [
    {"key": "owner", "label": "Owner households", "value": 351905, "share": 0.5179, "moe": 4190},
    {"key": "renter", "label": "Renter households", "value": 327523, "share": 0.4821, "moe": 4547}
  ],
  "total": 679428,
  "compare": {"label": "Pennsylvania", "items": [
    {"key": "owner", "share": 0.6933},
    {"key": "renter", "share": 0.3067}
  ]},
  "source_ids": ["acs5_profile_tables"],
  "vintage": "2020-2024 5-year estimates",
  "note": "ACS B25003, occupied homes; share = count / total shown."
}
```

- `kind`: `pie` (parts of a whole), `bar` (ordered bands), `stack` (one stacked bar), `rank` (bars sorted largest first).
- `share` = `value` / `total`, rounded to 4 decimals. `total` is the sum of the items shown, so shares add up to 1 give or take rounding. The rent-burden chart leaves out renters whose share "could not be computed" (no income or a loss) and says so in its note.
- `value`, `total` and `moe` are whole numbers. `moe` is the 90% margin of the count: as published for one cell, or the Bureau's square-root-of-squares approximation for a band of several cells.
- `compare` holds the bigger area's shares for the same items, in the same order (only `key` and `share`, to keep profiles small): the state for a county, the county for a tract.

| Section | Chart id | Kind | Table | County | Tract |
|---|---|---|---|---|---|
| People | `people.age` | bar | B01001, 6 age bands | yes | yes |
| People | `people.race` | pie | B03002, 6 groups (Hispanic of any race; others not Hispanic) | yes | yes |
| People | `people.income` | bar | B19001, 6 income bands | yes | yes |
| People | `people.household_size` | bar | B11016, 1 to 5+ people | yes | no |
| People | `people.education` | bar | B15003, 5 levels, aged 25+ | yes | no |
| Homes | `housing.tenure` | pie | B25003 | yes | yes (tract table) |
| Homes | `housing.building_type` | bar | B25024, 6 types | yes | yes (tract table) |
| Homes | `housing.year_built` | bar | B25034, 6 periods | yes | yes (tract table) |
| Homes | `housing.rent_burden` | stack | B25070, under 30%, 30-49%, 50%+ | yes | yes |
| Homes | `housing.vacancy_status` | pie | B25004, 5 reasons | yes | no |
| Jobs | `jobs.sectors` | rank | LODES jobs by NAICS sector: top 6 plus "All other sectors" | yes | yes |
| Jobs | `jobs.commute_mode` | pie | B08301, 6 ways to work | yes | yes |
| Water and sewer readiness | `infrastructure.sewer_evidence` | stack | homes verified / outside / constraint / unknown (official records) | yes | yes |
| Water and sewer readiness | `infrastructure.water` | stack | homes inside / outside a public water service area (Pennsylvania) | yes | yes |
| Water and sewer readiness | `infrastructure.act537` | bar | homes by Act 537 plan age (Pennsylvania) | yes | no |
| Water and sewer readiness | `infrastructure.cwns_needs` | rank | CWNS 2022 needs by category, dollars | yes | the county's |
| Hazards and land | `hazards.floodplain_homes` | stack | homes in / outside FEMA's SFHA | yes | yes |
| Hazards and land | `hazards.developed_land` | stack | acres developed / not (NLCD) | yes | yes |
| Hazards and land | `hazards.steep_land` | stack | acres steeper than 15% / flatter (3DEP) | yes | yes |
| Cost and affordability | `affordability.burden_le30`, `burden_30_80`, `burden_gt80` | stack | CHAS Table 8: renters in each income group by share of income spent on housing (30% or less, 30-50%, over 50%) | yes | yes |
| Cost and affordability | `affordability.rent_bands` | bar | CHAS Table 15C: renter homes by the AMI level their rent is affordable to | yes | yes |
| Cost and affordability | `affordability.assisted_programs` | bar | HUD lists: contract, public housing and tax-credit units (overlap noted) | yes | yes |
| Cost and affordability | `affordability.assisted_expiry` | stack | HUD contract units ending within 5 years, in 5-10 years, later or undated, date passed | yes | yes |

The bands are defined once, in `features/acs_bands.py` (see [acs.md](acs.md)). Tracts carry fewer tables to keep the Pennsylvania pack small (budget 12 MB); tract owners and renters, building type and year built come from the tract table, which already holds those cells.

### Headlines and summary points

| Section | Headline | Summary points |
|---|---|---|
| People | residents | residents and households; median household income with its comparison; largest age group |
| Homes | median home value | median rent; share of renters paying 30% or more of income; most common building type |
| Jobs | jobs located in the place | jobs per resident; largest sector; share of working residents who drive alone |
| Cost and affordability | renters at or below 80% of AMI paying over 30% of income | AMI and the 2-bedroom rent affordable at 50% of AMI; assisted homes ending within 5 years; NLIHC statewide affordable and available homes per 100 extremely low-income renters |
| Other sections | their first fact with a value | none |

### County ranks

A county profile has a `ranks` list (a tract's is empty). Each entry: `id`, `label`, `value`, `text`, `unit`, `rank` (1 = highest), `of` (counties with a value), `tied`, `text_rank` ("12th of 67"), `words` ("12th highest of 67 counties in Pennsylvania"; the words count from the nearer end, so a county near the bottom reads "9th lowest of 67"), `direction` (`highest` or `lowest`), `source_ids` and `note`.

| Measure | How |
|---|---|
| Median household income | published county median (B19013) |
| Median gross rent | published county median (B25064) |
| Rent-burden share | renters paying 30%+ of income / renters with a computed share (B25070) |
| Jobs per resident | LODES jobs located in the county / ACS residents |
| Permits per 1,000 homes | average yearly permitted units over the last `permit_years` years (`assumptions/typology.yaml`) / homes x 1,000 |
| Population growth | ACS 2020-2024 residents / 2020 Census residents - 1 |

Ties share the best rank among them (1, 2, 2, 4) and say "tied"; values are compared after rounding to 6 significant digits, and the order of the rows never changes the result. Code: `profile/ranks.py`.

### "About the same"

The comparison words call two values "about the same" when shares are within 2 percentage points, percent changes within 2 points, and other values within 5% of each other. These bands only choose the words; the exact values are always shown next to them. They are display wording, not model assumptions, so they live in `profile/format.py`, not in the ledger.

## Adding a section

Write one module, `src/visionhousing/profile/sections/<key>.py`, with:

```python
def build(ctx: ProfileContext, geoid: str) -> Section | None: ...
```

The key and title are already in `profile/registry.py`. Use `FactMaker` from `sections/_common.py` so the place and its comparison area use one formula and every fact gets its source id and vintage. Every source id must be in `sources.yaml`. When the data are missing, return `None` or raise `MissingDataError` (or `FileNotFoundError`): the profile then shows "No data for this place yet" for that section instead of failing. Any other error is a bug and is raised.

## Use

```
visionhousing profile --state PA --county 42003 [--tract 42003020100] [--format md|html|csv|json] [--out FILE]
```

Without `--out` the profile is printed. Markdown and HTML show a county's ranks first, then per section the headline, the summary points, the facts and one small table per chart (count, share, margin and the bigger area's share). `--format html` is a self-contained page (no scripts, no outside requests) laid out for US Letter paper; its chart tables carry plain bars sized by the shares already in the profile. `--format csv` has one row per fact with every field (charts and ranks are in the JSON).

Interface: `GET /api/v1/places/{state}?q=alle` finds counties by name (`level=tract` for tracts); `GET /api/v1/profile/{state}/{geoid}` returns the profile as JSON, charts, headlines, summary points and ranks included (`format=md|html|csv` for a download). The static site saves the same JSON, so it carries them too.

**Raw numbers in the JSON are shortened; shown texts never are.** Each fact keeps its raw `value`, `moe`, `low` and `high` (and the comparison's and rank's `value`) next to the texts people see (`text`, `range_text`, the comparison's `text`). The texts are made from the exact values first. Then, in the JSON only, a raw float is cut to 4 significant digits (`105.62196741208714` becomes `105.6`), but only when the shorter number formats to the very same text; if it would not, it keeps more digits (`103893.4` dollars keeps `103893`, because `103900` would read "$103,900"). Whole numbers and integers are never changed, and chart values and totals are left exactly as they are (chart shares already have 4 decimals). This makes the files smaller (the static site's compact tract profiles by about a sixth) and changes nothing a reader sees. Markdown, HTML and CSV downloads use the exact values. Code: `tidy` in `profile/format.py`, used by the `to_dict` methods in `profile/model.py`. Checked on every Testland profile and every Pennsylvania county and tract profile: every text field is byte-identical before and after.

Screen: **Place** (`#/place/42003?v=1&s=PA`). Search a county by name, then pick a tract if you like. Each section is a card with its one-sentence summary on top and its facts below. Hover or tap a fact's `[n]` to see its source, edition, year, geography and range. "Copy share link" copies the address, which holds the state and the place. "Print / save PDF" prints the profile on US Letter without the controls.

## Files

| File | What it does |
|---|---|
| `model.py` | `Fact`, `Comparison`, `Chart`, `ChartItem`, `ChartCompare`, `Headline`, `Rank`, `Section`, `Profile`, each with `to_dict()` |
| `charts.py` | Chart records from the `acs_bands` table (and the tract table); shares, the bigger area's shares, the rent-burden share and published county medians |
| `ranks.py` | A county's ranks among the state's counties, with ties and plain words |
| `context.py` | `ProfileContext`: the pack's small tables, the source registry and vintages; `Area`: one state, county or tract and its measures (sums with margins, proportions, published or bracket medians, weighted means) |
| `format.py` | Number formatting, the comparison words, and `tidy` (raw numbers shortened for the JSON) |
| `registry.py` | Section order, titles, the reserved keys, and the build that never fails for missing data |
| `coverage.py` | Each section's registered source ids and what those sources cover (levels, data tier, the counties of a local-only source); `python -m visionhousing.profile.coverage` writes `web/js/content/coverage.json`, which the Place screen shows for a section with no data ("Not measured for this place") instead of an empty block. Checked by `tests/test_profile_coverage.py` |
| `builder.py` | `build_profile(state, geoid)` and `list_places(ctx, query)` |
| `render.py` | Markdown, HTML, CSV and JSON |
| `sections/*.py` | one module per section; `_common.py` makes facts, headlines and summary points the same way for all |

## Limits

- ACS numbers are five-year survey averages with margins of error; small tracts often have wide margins.
- County and state home values are estimated from brackets and can differ a little from published medians; county and state income and rent are published medians where the pack has `acs_bands`, else approximate.
- Tract charts cover fewer tables (age, race and ethnicity, income, commute mode, rent burden, plus owners and renters, building type and year built from the tract table) to keep the pack small; household size, education and vacancy reasons are shown for counties and the state only.
- Ranks place a county among its own state's counties only; a rank says nothing about the size of the gap to the next county.
- Permits are counted by county only; a tract shows its county.
- Cost burden (CHAS) describes 2018-2022, older than the other survey figures; income limits are per HUD area, so a tract shows its county's; assisted counts overlap across programs and cover federal programs only.
- Transit access is understated where agencies publish no readable schedule feed; distances are straight lines.
- Testland is synthetic: every vintage then reads "synthetic test data (Testland)" and every page says the numbers are not real.

## Tests

`tests/test_profile.py`, `tests/test_profile_charts.py` and `tests/test_profile_api.py`, on Testland:

```
.venv/bin/python -m pytest -q tests/test_profile*.py -p no:cacheprovider
```

`test_profile_charts.py` checks that the bands cover each table's universe and add up to its total, that every chart's shares add up to 1 (give or take rounding) and match value / total, that ranks are deterministic with ties, and that the keyless summary-file path is the default with no key and gives the same cells as the API path. They check the model, each section's numbers against the Testland tables, that every fact cites a source in `sources.yaml` with a vintage, that missing tables give "no data" sections, that reserved and newly added sections are picked up, each renderer, the command line, and that the interface returns exactly the builder's numbers.
