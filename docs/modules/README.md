# Module notes

One plain-language note per part of the code. Each note says what the module does, the terms it uses, and how it is tested. The method notes in [`docs/methods/`](../methods/) explain each reform's numbers and evidence.

## Data

| Note | Code | What it covers |
|---|---|---|
| [Data fetch](data_fetch.md) | `src/visionhousing/data/http.py`, `registry.py`, `manifest.py`, `sources.yaml` | The network layer, the source registry, the data manifest and the provenance rows |
| [Building Permits Survey](bps.md) | `src/visionhousing/data/sources/bps.py` | Reads the county and state permit files into one tidy table of units by year and building type |
| [American Community Survey](acs.md) | `src/visionhousing/data/sources/acs.py`, `src/visionhousing/features/acs.py` | Reads the 5-year summary files and turns them into tract housing features |
| [Census blocks and density](blocks.md) | `src/visionhousing/data/sources/blocks.py`, `src/visionhousing/features/density.py` | Reads the block attribute table and measures tract density and a lot-size proxy |
| [Transit stops](transit.md) | `src/visionhousing/data/sources/transit.py`, `src/visionhousing/features/transit.py` | Reads public bus and rail timetables, picks the major stops and measures each tract's access to them |
| [Data report card](report_card.md) | `src/visionhousing/data/report_card.py` | Says for each model input whether a state's pack uses it, has it, assumes it or lacks it, with its tier and coverage |
| [Boundaries](boundaries.md) | `src/visionhousing/data/sources/boundaries.py` | Turns the county and tract boundary files into small GeoJSON for the map |
| [Prices, rates and costs](prices.md) | `src/visionhousing/data/sources/prices.py`, `costs.py` | House price index, mortgage rates and the county construction-pay index |
| [Place profile data](place.md) | `src/visionhousing/data/sources/jobs.py`, `incentives.py`, `safety.py`, `xlsb.py`, `place.py`; `src/visionhousing/features/jobs.py`, `incentives.py`, `safety.py`; `profile/sections/jobs.py`, `incentives.py`, `safety.py` | Jobs, tax-credit and incentive areas, and reported offenses for the place profile |
| [Safety and distress](safety.md) | `src/visionhousing/data/sources/phl_safety.py`, `safety.py`; `src/visionhousing/features/safety_local.py`, `safety.py`; `profile/sections/safety.py` | Keyless Philadelphia police, shooting, code-violation and 311 counts per tract and year, plus the FBI path when a key is set |
| [Local refinement data](local_data.md) | `src/visionhousing/data/local.py`; `src/visionhousing/data/sources/munstats.py`, `phl_opa.py`, `alle_assess.py`, `local_permits.py`, `phfa.py`, `opendata.py`; `src/visionhousing/features/zoning_status.py`, `parcels.py`, `owners.py`, `pipeline_local.py`; `profile/sections/zoning.py`, `plans.py`, `parcels.py`, `owners.py`, `pipeline_local.py` | Municipal zoning and plans (Pennsylvania), parcels and owners (Philadelphia, Allegheny), city permits and LIHTC awards for the place profile |
| [Massachusetts 3A capacity and estimated sewer](ma_capacity.md) | `src/visionhousing/data/sources/ma_capacity.py`; `src/visionhousing/features/ma_capacity.py`, `ma_capacity_build.py`; `assumptions/ma_intake.yaml`; `visionhousing ma-capacity` | Existing Section 3A modeled capacity split across tracts by area (parcels or district total, never both), and MassDEP estimated sewer service as its own evidence class |
| [Massachusetts intake](ma_intake.md) | `src/visionhousing/data/sources/ma_intake.py`; `src/visionhousing/features/ma_intake.py`; `assumptions/ma_intake.yaml` | MBTA Communities (Section 3A) districts and compliance status, and the state ADU survey, as Massachusetts pack tables |
| [Permit timing](permit_timing.md) | `src/visionhousing/data/sources/phl_permit_timing.py`, `src/visionhousing/features/permit_timing.py`, `visionhousing permit-timing` | Philadelphia zoning-to-building permit stage lag with Kaplan-Meier censoring, and new-home permit counts by month, for the approval-timing case |
| [California validation](validation_california.md) | `src/visionhousing/validation/`, `assumptions/validation_california.yaml`, `visionhousing validate-california` | California APR application, entitlement, permit and completion stages: attrition and elapsed time with Kaplan-Meier censoring, ADU permits per 1,000 lots, comparison records, transferred timing ranges and the ADU plausibility table |
| [Plausibility data](validation_plausibility.md) | `src/visionhousing/validation/plausibility.py`, `assumptions/plausibility.yaml`, `GET /api/v1/plausibility/{st}` | Each reform's rate per 1,000 eligible lots (and per 100,000 residents) per year from the run's records, beside observed rates with caveats, the observed band and a rule-written sentence |
| [State intake](intake.md) | `src/visionhousing/intake/`, `assumptions/intake.yaml`, `src/visionhousing/api/intake.py`, `visionhousing state-readiness`, `state-intake`, `reproduce` | Per-input intake manifest, the six value states, verified-exposure and transferred-take-up fractions, the five-step readiness check and "reproduce this number" metadata |
| [Donor-pool validation](validation_donor_pool.md) | `src/visionhousing/validation/donor_pool.py`, `assumptions/validation_donor_pool.yaml`, `visionhousing validate-donor-pool` | National BPS county panel; hold-out-state back-tests of the baseline's state range with errors from the state itself, Pennsylvania, all states, all counties or a matched donor pool; pre-stated adoption rule; separate baseline-performance and reform-evidence confidence signals |
| [Law, zoning and policy](law.md) | `src/visionhousing/law/`, `src/visionhousing/data/sources/legislation.py`, `assumptions/law/`; `profile/sections/zoning.py`, `plans.py`, `state_law.py` | State planning-code sections and bills, county zoning reach and plans, municipal ADU, lot and parking rules; the rule-based ordinance reader |
| [Retrospective reform panel](evidence_retro.md) | `src/visionhousing/evidence/retro.py`, `src/visionhousing/evidence/retro_audit.py`, `assumptions/evidence/retro_design.yaml`, `assumptions/evidence/retro_audit.yaml`, `visionhousing evidence-retro` | Small-building permits before and after six city reforms, against cities that did not reform, with a design fixed before any estimate; treatment-date audit (which law and phase, the year convention, before and after) |
| [Validation cards](validation_cards.md) | `src/visionhousing/validation/cards.py`, `assumptions/validation_cards.yaml`, `visionhousing validate-cards`, `GET /api/v1/validation` (`cards`) | The study, origins, target, geography and release behind each published coverage count, with an interval score, signed error, error by size, a non-overlapping subset and a fold timeline |
| [Cross-state comparison records](evidence_states.md) | `src/visionhousing/evidence/`, `assumptions/evidence/states/`, `visionhousing evidence-states`, `GET /api/v1/evidence/states` | Checked records of laws, court decisions and studies in other states and cities, and the state by policy type completeness matrix |
| [Legislative districts](districts.md) | `src/visionhousing/districts/`, `src/visionhousing/data/sources/sld.py`, `src/visionhousing/api/districts.py`, `visionhousing districts` | Tract- and county-to-district weights from 2020 blocks, per-draw district totals, split shares, district outlines and the districts endpoint |
| [Cost and affordability](affordability.md) | `src/visionhousing/data/sources/affordability.py`, `src/visionhousing/features/affordability.py`, `profile/sections/affordability.py`, `assumptions/affordability.yaml`, `visionhousing affordability` | HUD CHAS cost burden by income and tenure, income limits (AMI by household size) and affordable rents for the engine, assisted homes and expiring contracts, the county stock ledger |
| [Water and sewer readiness](infrastructure.md) | `src/visionhousing/data/sources/infrastructure.py`, `src/visionhousing/features/infrastructure.py`, `infrastructure_build.py`, `profile/sections/infrastructure.py`, `assumptions/infrastructure.yaml`, `visionhousing infrastructure` | Official sewer and water service areas per tract (verified, outside, constraint, unknown), Act 537 plan age, EPA sewersheds and CWNS needs as context, provider contacts |
| [Hazards and land](hazards.md) | `src/visionhousing/data/sources/hazards.py`, `geotiff.py`, `src/visionhousing/features/hazards.py`, `hazards_build.py`, `profile/sections/hazards.py`, `assumptions/hazards.yaml`, `visionhousing hazards` | FEMA flood-zone, Annual NLCD developed and impervious, and 3DEP slope shares per tract on a 30 m grid |
| [State data pack](pack.md) | `src/visionhousing/data/pack.py`, `visionhousing fetch` and `build` | Builds one small folder per state that the engine and the interface read |
| [Testland](testland.md) | `scripts/make_testland.py`, `tests/fixtures/testland/` | The synthetic test state the tests and the interface run on offline |

## Features

| Note | Code | What it covers |
|---|---|---|
| [Brackets](brackets.md) | `src/visionhousing/features/brackets.py` | Shares of homes or rents above a break-even, from published bracket counts |
| [ACS tract features](acs.md) | `src/visionhousing/features/acs.py` | Housing mix, values and rents by bedroom per tract (in the ACS note) |
| [Density features](blocks.md) | `src/visionhousing/features/density.py` | Housing density and lot size per home from blocks (in the blocks note) |
| [Transit walksheds](transit_walk.md) | `src/visionhousing/features/transit_walk.py`, `walkshed.py`; `src/visionhousing/data/sources/osm.py`, `transit_peaks.py`; `visionhousing transit-walk` | Peak frequent-bus rule (15 minutes in both weekday peaks) and network walksheds on OpenStreetMap at 1/4, 1/2 and 3/4 mile, per tract, next to straight-line circles |
| [Places like this one](typology.md) | `src/visionhousing/features/typology.py`, `profile/sections/typology.py` | Seeded k-means groups of similar counties and tracts, rule-made group names and nearest neighbours |

## Ledger

| Note | Code | What it covers |
|---|---|---|
| [Assumption Ledger](ledger.md) | `src/visionhousing/ledger/` | Loads and checks `assumptions/parameters.yaml` and `assumptions/scenarios.yaml`, applies overrides and draws values from ranges |

## Pro forma

| Note | Code | What it covers |
|---|---|---|
| [Pro formas](proforma.md) | `src/visionhousing/proforma/` | Simple project budgets: the ADU break-even rent and the residual land value of small and mid-rise buildings |

## Scenarios

| Note | Code | What it covers |
|---|---|---|
| [ADU scenario](scenario_adu.md) | `src/visionhousing/scenarios/adu.py` | The ADU funnel from eligible lots to net added homes, with the Pennsylvania bills |
| [Missing-middle scenario](scenario_missing_middle.md) | `src/visionhousing/scenarios/missing_middle.py` | The 2 to 6 home funnel from eligible lots to net added homes |
| [Fee reform](scenario_fees.md) | `src/visionhousing/scenarios/fees.py` | The 50% fee cut and the shared cost-saving math |
| [Parking reform](scenario_parking.md) | `src/visionhousing/scenarios/parking.py` | Removing parking minimums: the cost saving and the site yield |
| [Transit-oriented density](scenario_tod.md) | `src/visionhousing/scenarios/tod.py` | More height near major stops: the funnel from land near transit to added homes, with the walkshed variants |
| [Extra scenarios](scenario_extra.md) | `src/visionhousing/scenarios/{tod_infrastructure,commercial_land,starter_lots,fee_affordability,extra_common}.py`, `src/visionhousing/engine/extra_adapters.py` | Transit zoning with funded infrastructure, housing on commercial land and adaptive reuse, starter-home lots, fee deferral or waiver with affordability terms |

## Engine

| Note | Code | What it covers |
|---|---|---|
| [Baseline](baseline.md) | `src/visionhousing/baseline/` | Likely permits with no reform, chosen and checked by backtest |
| [Engine](engine.md) | `src/visionhousing/engine/` | The single `run()` call: baseline, each reform, the package, Monte Carlo draws and roll-up, the paired readouts and costs, and the outcome contract (years, gross and net, as-of date) |
| [Result records](records.md) | `src/visionhousing/engine/records.py` | The format every result takes, with its formula, sources and assumption ids |
| [Comparisons](comparisons.md) | `src/visionhousing/engine/comparisons/`, `assumptions/comparisons.yaml`, `assumptions/data_tiers.yaml`, `visionhousing comparisons`, `visionhousing run --data-tier` | Package vs reforms alone (last-in effects, ordered allocation, mechanisms, waterfall), policy dials with target seeker and ADU adoption threshold, take-up × cost matrix, data ablation by tier, ranking stability |
| [Project choice and shared market](market.md) | `src/visionhousing/engine/market.py`, `assumptions/market.yaml` | The package option `market`: each shared lot used once for its best use (ADU, 2-6 unit building, conventional house, retain), and a soft cap on homes per county group and year; local gain, statewide net, net stock, stock losses, fee effects, substitution and shifted years kept apart |
| [Sensitivity](sensitivity.md) | `src/visionhousing/sensitivity/` | Which assumptions move each answer most, by tornado and by rank correlation, what would narrow the range (variance shares), and the likely range with one top input fixed (switch runs, `visionhousing switch-ranges`) |
| [Explanations](explain.md) | `src/visionhousing/explain/`, `assumptions/limits.yaml` | The one sentence and three numbers, verdict words, funnels, drivers, the waterfall, limits, "explain this number" and the slide spec, every number cited |
| [Evidence grades and number trace](grades.md) | `src/visionhousing/grades/`, `assumptions/grades.yaml`, `src/visionhousing/api/grades.py` | A to D grades for every headline number by a published rule, the step-by-step trace of a number with its check, and each reform's top three drivers at low / central / high |
| [Report writer](report.md) | `src/visionhousing/report/`, `visionhousing report` | The brief as Markdown and print-ready HTML, CSV tables and JSON, stamped with the three hashes |
| [Practical capacity screen](capacity.md) | `src/visionhousing/engine/capacity.py`, `assumptions/capacity.yaml` | The run option `capacity_screen` and the `*_capacity_screen` variants: each reform's homes x a per-tract multiplier for public sewer service, FEMA flood zone and steep slope (wildfire listed, not measured); change against not screened, the binding screen, county records and the coverage of every screen |
| [Place profiles](profile.md) | `src/visionhousing/profile/`, `visionhousing profile` | Plain, sourced facts about one county or tract (people, homes, permits, transit, data quality) for market research |

## Web

| Note | Code | What it covers |
|---|---|---|
| [Local server and JSON interface](api.md) | `src/visionhousing/api/`, `src/visionhousing/paths.py` | Serves the screens and the `/api/v1` endpoints; copies engine numbers and adds no arithmetic |
| [Browser interface](web.md) | `web/`, `src/visionhousing/export_static.py`, `src/visionhousing/compact_profiles.py`, `vercel.json` | The screens, the slides view, the Vite build, and the no-server static site |

## Method notes

| Note | What it covers |
|---|---|
| [ADU method](../methods/adu.md) | How added ADUs are estimated, the numbers used and their sources |
| [Missing-middle method](../methods/missing_middle.md) | How added 2 to 6 home buildings are estimated |
| [Outcome contract](../methods/outcome.md) | What every headline counts (permits authorized, 2027-2031), years, gross and net, as-of date and vintages, the bias-correction rule |
| [Fee method](../methods/fees.md) | Why a fee cut works through cost, and how much of the saving reaches builders |
| [Parking method](../methods/parking.md) | What developers still build, and how the saving and site yield are counted |
| [TOD method](../methods/tod.md) | Which stops count as major, how far people walk, and how added mid-rise homes are estimated |
| [Extra scenarios method](../methods/extra_scenarios.md) | The four scenarios beyond the benchmarks: formulas, in-service dates, conversions outside the permit measure, appraisal flag, fiscal and cash timing, limits |
| [Evidence library](../methods/evidence.md) | Real reforms and what was permitted before and after, used to set and check ranges |

## Code without its own note yet

Every package has a note. Two smaller points:

- `src/visionhousing/engine/variants.py` and `readouts.py` are covered inside the [Engine](engine.md) note, `src/visionhousing/law/timeline.py` inside the [Law](law.md) note;
- `src/visionhousing/validate/` is an empty package.
