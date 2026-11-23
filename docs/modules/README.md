# Module notes

One plain-language note per part of the code. Each note says what the module does, the terms it uses, and how it is tested. The method notes in [`docs/methods/`](../methods/) explain each reform's numbers and evidence.

## Data

| Note | Code | What it covers |
|---|---|---|
| [Data fetch](data_fetch.md) | `src/visionhousing/data/http.py`, `registry.py`, `manifest.py`, `sources.yaml` | The network layer, the source registry, the data manifest and the provenance rows |
| [Building Permits Survey](bps.md) | `src/visionhousing/data/sources/bps.py` | Reads the county and state permit files into one tidy table of units by year and building type |
| [American Community Survey](acs.md) | `src/visionhousing/data/sources/acs.py`, `src/visionhousing/features/acs.py` | Reads the 5-year summary files and turns them into tract housing features |
| [Census blocks and density](blocks.md) | `src/visionhousing/data/sources/blocks.py`, `src/visionhousing/features/density.py` | Reads the block attribute table and measures tract density and a lot-size proxy |
| [Boundaries](boundaries.md) | `src/visionhousing/data/sources/boundaries.py` | Turns the county and tract boundary files into small GeoJSON for the map |
| [Prices, rates and costs](prices.md) | `src/visionhousing/data/sources/prices.py`, `costs.py` | House price index, mortgage rates and the county construction-pay index |
| [Place profile data](place.md) | `src/visionhousing/data/sources/jobs.py`, `incentives.py`, `safety.py`, `xlsb.py`, `place.py`; `src/visionhousing/features/jobs.py`, `incentives.py`, `safety.py`; `profile/sections/jobs.py`, `incentives.py`, `safety.py` | Jobs, tax-credit and incentive areas, and reported offenses for the place profile |
| [Local refinement data](local_data.md) | `src/visionhousing/data/local.py`; `src/visionhousing/data/sources/munstats.py`, `phl_opa.py`, `alle_assess.py`, `local_permits.py`, `phfa.py`, `opendata.py`; `src/visionhousing/features/zoning_status.py`, `parcels.py`, `owners.py`, `pipeline_local.py`; `profile/sections/zoning.py`, `plans.py`, `parcels.py`, `owners.py`, `pipeline_local.py` | Municipal zoning and plans (Pennsylvania), parcels and owners (Philadelphia, Allegheny), city permits and LIHTC awards for the place profile |
| [State data pack](pack.md) | `src/visionhousing/data/pack.py`, `visionhousing fetch` and `build` | Builds one small folder per state that the engine and the interface read |
| [Testland](testland.md) | `scripts/make_testland.py`, `tests/fixtures/testland/` | The synthetic test state the tests and the interface run on offline |

## Features

| Note | Code | What it covers |
|---|---|---|
| [Brackets](brackets.md) | `src/visionhousing/features/brackets.py` | Shares of homes or rents above a break-even, from published bracket counts |
| [ACS tract features](acs.md) | `src/visionhousing/features/acs.py` | Housing mix, values and rents by bedroom per tract (in the ACS note) |
| [Density features](blocks.md) | `src/visionhousing/features/density.py` | Housing density and lot size per home from blocks (in the blocks note) |

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

## Engine

| Note | Code | What it covers |
|---|---|---|
| [Baseline](baseline.md) | `src/visionhousing/baseline/` | Likely permits with no reform, chosen and checked by backtest |
| [Engine](engine.md) | `src/visionhousing/engine/` | The single `run()` call: baseline, each reform, the package, Monte Carlo draws and roll-up |
| [Result records](records.md) | `src/visionhousing/engine/records.py` | The format every result takes, with its formula, sources and assumption ids |
| [Sensitivity](sensitivity.md) | `src/visionhousing/sensitivity/` | Which assumptions move each answer most, by tornado and by rank correlation |
| [Explanations](explain.md) | `src/visionhousing/explain/`, `assumptions/limits.yaml` | The one sentence and three numbers, verdict words, funnels, drivers, the waterfall, limits, "explain this number" and the slide spec, every number cited |
| [Report writer](report.md) | `src/visionhousing/report/`, `visionhousing report` | The brief as Markdown and print-ready HTML, CSV tables and JSON, stamped with the three hashes |
| [Place profiles](profile.md) | `src/visionhousing/profile/`, `visionhousing profile` | Plain, sourced facts about one county or tract (people, homes, permits, transit, data quality) for market research |

## Web

| Note | Code | What it covers |
|---|---|---|
| [Browser interface](web.md) | `web/`, `src/visionhousing/export_static.py`, `vercel.json` | The screens, the slides view, the Vite build, and the no-server static site |

## Method notes

| Note | What it covers |
|---|---|
| [ADU method](../methods/adu.md) | How added ADUs are estimated, the numbers used and their sources |
| [Missing-middle method](../methods/missing_middle.md) | How added 2 to 6 home buildings are estimated |
| [Fee method](../methods/fees.md) | Why a fee cut works through cost, and how much of the saving reaches builders |
| [Parking method](../methods/parking.md) | What developers still build, and how the saving and site yield are counted |

**Planned:** notes for transit-oriented density, the local server and validation will be added as those modules are built.
