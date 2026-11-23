# VisionHousing

VisionHousing is an open-source tool that shows how state housing-policy reforms are likely to change the number of new homes permitted over five years, compared with a no-change baseline.

Results are likely ranges, not forecasts. It is decision support, not an official state document.

> **Status.** The project is being built. Parts marked **Planned** below do not exist yet. Nothing in this README describes a feature as working unless it is in the repository today.

## Who it is for

| Person | Question they bring | What they leave with |
|---|---|---|
| State legislator or staffer | "If we pass this, how many units, and where?" | A range, the top reasons, and the caveat, on one screen |
| State housing agency analyst | "Which assumptions drive this, and can I change them?" | The assumption ledger, a sensitivity ranking and a downloadable run |
| County or municipal planner | "What does it mean for my county?" | County ranges and the data tier behind them |
| Economist or reviewer | "Why should I believe the take-up rate?" | The evidence, the backtests and the interval coverage |
| Advocate or journalist | "Which reform does the most?" | The side-by-side comparison and a short brief |

## Run it

You need [uv](https://docs.astral.sh/uv/) and `make`. uv installs Python 3.12 for you if needed. No key is required.

| Step | Command | What it does | Status |
|---|---|---|---|
| 1 | `make setup` | Installs the pinned dependencies from `uv.lock` and the test browser | Built |
| 2 | `make data STATE=PA` | Fetches every source from its publisher and builds the Pennsylvania data pack | **Planned** (the network layer and source registry are built, the fetch and build commands are not) |
| 3 | `make verify` | Lint, tests, licence audit and secret scan | Built |
| 4 | `make app` | Serves the tool at `http://localhost:8000` | **Planned** |

Other commands:

- `make test` runs the test suite. The tests use Testland, a small synthetic state built into the repository, so they need no download.
- `make help` lists every target.
- `uv run visionhousing doctor` checks your machine (Python, uv, disk space, optional keys, a writable data folder).
- `make clean-clone` copies the published files to a temporary folder and runs setup and verify there.

An optional free Census API key raises the request limit. Copy `.env.example` to `.env` and fill in `CENSUS_API_KEY`. Every source also works without it.

## Rules compliance

Each pass/fail requirement of the Challenge, and where the repository meets it.

| Requirement | Where it is met | Status |
|---|---|---|
| Approved open-source licence | `LICENSE` (Apache-2.0, full text) and `NOTICE` | Done |
| Runs from documented setup with no hidden steps | This README, the `Makefile`, `make clean-clone` | Setup and verify done. Data and app **Planned** |
| Automated tests of the core logic, with a documented command | `tests/`, run with `make test` or `make verify` | Done for the parts built so far. Engine tests **Planned** with the engine |
| Tests pass on a clean checkout | `make clean-clone` and the CI workflow `.github/workflows/ci.yml` | Done |
| No committed secrets, secret scanning on | `scripts/secret_scan.py` (inside `make verify`), `.gitignore`, `.env.example` with empty values, `SECURITY.md`. GitHub secret scanning and push protection are repository settings | Local scan done. <!-- TODO(Ye): confirm GitHub secret scanning and push protection are on --> |
| CodeQL code scanning | `.github/workflows/codeql.yml` (Python and JavaScript) | Workflow done. <!-- TODO(Ye): confirm code scanning is enabled in repository settings --> |
| Dependabot alerts and updates | `.github/dependabot.yml` (pip, npm manifest, GitHub Actions) | Config done. <!-- TODO(Ye): confirm Dependabot alerts are enabled --> |
| Permissive dependencies only | `scripts/licence_audit.py` (inside `make verify`), summary in `DISCLOSURES.md` | Done |
| Software bill of materials | Exported from GitHub (Insights, Dependency graph, Export SBOM). Summary in `DISCLOSURES.md` | **Planned** (export after the first public push) |
| Data fetched by script, no large datasets in git | `src/visionhousing/data/` (registry, network layer, manifest), `data/README.md`, `.gitignore` | Registry and network layer done. Fetch command **Planned** |
| Disclosures | `DISCLOSURES.md` | Skeleton done. Generated tables **Planned** |
| Data provenance | `DATA_PROVENANCE.md`, built from the source registry | Skeleton done. Generated table **Planned** |

## How AI was used, and what it never does

| Topic | What is true |
|---|---|
| **AI coding assistants wrote code under review** | An AI coding assistant wrote the code, tests, configuration and documents. The team lead directed the work, reviewed it and made every decision and every commit. Details are in `DISCLOSURES.md`. |
| **No AI in the product** | The tool makes no call to any AI model or AI service. Every explanation on screen is written by deterministic code from the engine's own numbers. |
| **No pretrained models in the product** | The tool uses no pretrained models, weights or embeddings. Any fitted coefficients come from public data by scripts in this repository and are shown with their intervals. |

## Sources

All data is public and is downloaded by script from the publisher. None of it is stored in this repository. The full table, with version, access date, licence, cleaning steps and limits, is in [`DATA_PROVENANCE.md`](DATA_PROVENANCE.md).

| Source | Publisher | Used for |
|---|---|---|
| Building Permits Survey, county annual files | U.S. Census Bureau | The outcome and the baseline |
| American Community Survey 5-year estimates | U.S. Census Bureau | Housing stock, home values, rents, households |
| Cartographic boundary and TIGER/Line block files | U.S. Census Bureau | Tracts, counties and block attributes |
| House Price Index | Federal Housing Finance Agency | Price trends |
| Primary Mortgage Market Survey | Freddie Mac | Mortgage rates |
| National Transit Map, stops | U.S. Department of Transportation | Major transit stops |
| Bill pages and text (HB 2186, SB 1346) | Pennsylvania General Assembly | State-specific scenarios |

## Limitations

- The outcome is units **authorized by building permits**. It is not units completed or occupied.
- The horizon is five years (2027 to 2031). Effects after year five are not modelled.
- Results are computed by census tract and reported by county and state. The tool gives no parcel-level or address-level answer.
- The tool cannot say who will live in new units, what they will rent for, or whether local governments will comply.
- Take-up of a new permission is the most uncertain input. Its range is shown, not hidden.
- States with less complete data get wider ranges. Every number shows its data tier.

The full list is in `docs/MODEL_AND_ASSUMPTIONS.md` (Limitations and known edge cases).

## Nothing is mocked

Every number the tool shows is computed by the engine from fetched public data and the assumption ledger (`assumptions/parameters.yaml`). No number on a screen or in a document is typed by hand. Where a value is not built yet, the tool and the documents say "not modelled" or show a marked placeholder. They never show a made-up figure.

## Documentation

| Document | What it covers |
|---|---|
| [`docs/MODEL_AND_ASSUMPTIONS.md`](docs/MODEL_AND_ASSUMPTIONS.md) | Purpose, assumptions, data, method, limits, sensitivity, users |
| [`docs/POLICY_SCENARIO_COMPARISON.md`](docs/POLICY_SCENARIO_COMPARISON.md) | A short non-technical comparison of the reforms |
| [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) | How to use the screens, slides, export and share link |
| [`docs/glossary.md`](docs/glossary.md) | Plain definitions of the terms used |
| [`docs/modules/README.md`](docs/modules/README.md) | One note per code module |
| [`DISCLOSURES.md`](DISCLOSURES.md) | AI tools, services, dependencies and datasets |
| [`DATA_PROVENANCE.md`](DATA_PROVENANCE.md) | Every dataset, where it comes from and how it is cleaned |
| [`SECURITY.md`](SECURITY.md) | How to report a security problem |

## Licence

Apache License 2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE). The code licence does not cover the data. Each dataset keeps its publisher's terms, listed in `DATA_PROVENANCE.md`.
