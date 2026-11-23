# Disclosures

This file lists every AI tool, model, external service, dependency and dataset the project uses, as the Official Rules require. Sections marked "generated" are filled by `scripts/gen_docs_tables.py` from the repository's own records, so they match the code. Do not edit them by hand.

## 1. AI code-generation tools

| Tool | Models | What it did | Who reviewed it |
|---|---|---|---|
| Anthropic Claude Code | Claude Opus and Claude Fable model families | Wrote the code, tests, configuration and documents | The team lead directed every task, reviewed the output, made the decisions, and made every commit and push |

<!-- TODO(Ye): add a row for GitHub Copilot only if you used it. Otherwise delete this comment. -->

How the work was controlled:

- The assistant worked from a written plan and task list set by the team lead.
- The assistant never committed, pushed or published anything.
- Every task had to pass `make verify` (lint, tests, licence audit, secret scan) before it counted as done.
- All work was written new for this Challenge.

## 2. Pretrained models, weights and embeddings

None in the product.

The tool calls no AI model or AI service at run time. It ships no pretrained weights or embeddings. Explanations on screen are produced by deterministic code.

Statistical inputs are estimated from public data by scripts in this repository, or come from the measured calibration tables in `assumptions/calibration/`. Each is published with its range, its sources and its limits (see `DATA_PROVENANCE.md`).

## 3. External APIs and hosted services

The tool and its scripts contact only these services. All are public and free.

| Service | Publisher | Used for | Key needed |
|---|---|---|---|
| Census data downloads and the Census Data API | U.S. Census Bureau | Building permits, survey tables, boundary and block files | No. The API path needs a free key. The default summary-file path does not |
| House Price Index downloads | Federal Housing Finance Agency | Price trends | No |
| Primary Mortgage Market Survey file | Freddie Mac | Mortgage rates | No |
| Quarterly Census of Employment and Wages files | U.S. Bureau of Labor Statistics | County construction pay, as a building-cost index | No |
| National Transit Map stops and agencies services, hosted on ArcGIS Online | U.S. Department of Transportation, Bureau of Transportation Statistics | Transit stops and the agencies that run them | No |
| Mobility Database catalog and feed mirror, hosted on Google Cloud Storage | MobilityData | Finding and downloading transit agency schedule feeds | No |
| Transit agency schedule feeds (GTFS) | Each transit agency | Weekday departures per stop | No |
| Bill pages and text | Pennsylvania General Assembly | State-specific scenarios | No |
| LEHD LODES job files and County Business Patterns files | U.S. Census Bureau | Place profile: jobs by tract, commuting by county, businesses by county | No |
| Qualified Census Tract, Difficult Development Area and LIHTC property services, hosted on ArcGIS Online | U.S. Department of Housing and Urban Development | Place profile: tax-credit areas and projects | No |
| Opportunity Zone designations file | U.S. Department of the Treasury | Place profile: Opportunity Zones from 2027 | No |
| New Markets Tax Credit eligibility file | U.S. Department of the Treasury, CDFI Fund | Place profile: low-income community tracts | No |
| National Register of Historic Places map service | National Park Service | Place profile: historic listings and districts | No |
| Keystone Opportunity Zone locations service, hosted on ArcGIS Online | Pennsylvania Department of Community and Economic Development | Place profile: Keystone Opportunity Zone sites (Pennsylvania only) | No |
| Crime Data Explorer API (api.usa.gov) | Federal Bureau of Investigation, through api.data.gov | Place profile: reported offenses by police agency | Yes, a free api.data.gov key, read only from the `API_DATA_GOV_KEY` or `FBI_API_KEY` environment variable and never stored. Without it the safety table is left out |
| Municipal Statistics report (ASP.NET form, CSV export) | Pennsylvania Department of Community and Economic Development | Place profile: municipal zoning and planning status | No |
| Carto SQL service (phl.carto.com) | City of Philadelphia (OpenDataPhilly) | Place profile: property, deed and permit totals for Philadelphia, added up on the City's server | No |
| CKAN datastore SQL service (data.wprdc.org) | Western Pennsylvania Regional Data Center | Place profile: Allegheny County property and sales totals, Pittsburgh permits | No |
| Multifamily programme page and LIHTC award PDFs | Pennsylvania Housing Finance Agency | Place profile: tax-credit awards | No |
| Census Geocoder | U.S. Census Bureau | Place profile: the tract of each tax-credit award address | No |
| Fair Market Rents file | U.S. Department of Housing and Urban Development | A cross-check only. Skipped for now because scripted access is blocked | No |
| Python package index | Python Software Foundation | `make setup` installs the Python packages pinned in `uv.lock` | No |
| npm package registry | npm | `make setup` runs `npm ci` in `web/`, which installs the exact packages in `web/package-lock.json` and checks each one's integrity value (section 4.2) | No |
| Vercel (optional static hosting) | Vercel Inc. | Hosts the optional no-server version of the interface: it runs `npm ci` and `npm run build:static` from `vercel.json` and serves `web/dist/` with the precomputed results in `web/public/data/` | No (a free Vercel account, set up by the team lead) |
| Playwright browser download | Microsoft (Playwright project) | `make setup` installs the Chromium build the end-to-end tests use. It is a test tool, not part of the product | No |
| GitHub Actions, CodeQL, Dependabot | GitHub | Continuous checks on the public repository | No |

The tool itself, once set up, contacts none of these. It serves the local data pack and the built interface (`web/dist/`). It loads nothing from a content delivery network and uses no map tile server.

The full list of data sources is `src/visionhousing/data/sources.yaml`. See `DATA_PROVENANCE.md`. The calibration inputs in section 5 were used once, outside the product. The tool and `make data` never contact them.

## 4. Third-party software dependencies

Third-party software enters only as declared dependencies. No third-party source code is copied into the repository. The licence audit (`scripts/licence_audit.py`, run by `make verify`) checks every locked Python package and every package in the npm lockfile against an allow-list of permissive licences, and fails on anything else.

### 4.1 Dependency table (generated)

Group says why a package is installed: "run time" for the product, "build" for the data build, "dev" for tests and lint, "spatial" and "research" for optional groups, "browser" for the interface.

<!-- generated: dependency_table -->
*31 Python packages from `uv.lock` and 92 npm packages from `web/package-lock.json`. Licences read by `scripts/licence_audit.py`. The project itself is not listed.*

| Ecosystem | Package | Version | Group | Licence |
|---|---|---|---|---|
| Python | annotated-doc | 0.0.7 | run time | MIT |
| Python | annotated-types | 0.8.0 | run time | MIT |
| Python | anyio | 4.16.0 | run time | MIT |
| Python | click | 8.5.1 | run time | BSD-3-Clause |
| Python | colorama | 0.4.6 | dev | BSD-3-Clause |
| Python | fastapi | 0.165.0 | run time | MIT |
| Python | greenlet | 3.5.6 | dev | MIT AND PSF-2.0 |
| Python | h11 | 0.16.0 | run time | MIT |
| Python | idna | 3.20 | run time | BSD-3-Clause |
| Python | iniconfig | 2.3.1 | dev | MIT |
| Python | numpy | 2.5.5 | run time | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| Python | opentelemetry-api | 1.45.2 | run time | Apache-2.0 |
| Python | packaging | 26.3 | dev | Apache-2.0 OR BSD-2-Clause |
| Python | pandas | 3.1.1 | run time | BSD-3-Clause |
| Python | playwright | 1.65.2 | dev | Apache-2.0 |
| Python | pluggy | 1.7.0 | dev | MIT |
| Python | pydantic | 2.14.4 | run time | MIT |
| Python | pydantic-core | 2.50.5 | run time | MIT |
| Python | pygments | 2.21.0 | dev | BSD-2-Clause |
| Python | pypdf | 7.1.1 | build | BSD-3-Clause |
| Python | pyshp | 3.1.9 | build | MIT |
| Python | pytest | 9.2.0 | dev | MIT |
| Python | python-dateutil | 4.0.0 | run time | BSD-3-Clause |
| Python | pyyaml | 6.0.4 | run time | MIT |
| Python | ruff | 0.17.6 | dev | MIT |
| Python | starlette | 1.10.0 | run time | BSD-3-Clause |
| Python | truststore | 0.10.5 | run time | MIT |
| Python | typing-extensions | 4.16.0 | run time | PSF-2.0 |
| Python | typing-inspection | 0.4.5 | run time | MIT |
| Python | tzdata | 2026.6 | run time | Apache-2.0 |
| Python | uvicorn | 0.54.0 | run time | BSD-3-Clause |
| npm | @esbuild/aix-ppc64 | 0.28.2 | web build, inside vite | MIT |
| npm | @esbuild/android-arm | 0.28.2 | web build, inside vite | MIT |
| npm | @esbuild/android-arm64 | 0.28.2 | web build, inside vite | MIT |
| npm | @esbuild/android-x64 | 0.28.2 | web build, inside vite | MIT |
| npm | @esbuild/darwin-arm64 | 0.28.2 | web build, inside vite | MIT |
| npm | @esbuild/darwin-x64 | 0.28.2 | web build, inside vite | MIT |
| npm | @esbuild/freebsd-arm64 | 0.28.2 | web build, inside vite | MIT |
| npm | @esbuild/freebsd-x64 | 0.28.2 | web build, inside vite | MIT |
| npm | @esbuild/linux-arm | 0.28.2 | web build, inside vite | MIT |
| npm | @esbuild/linux-arm64 | 0.28.2 | web build, inside vite | MIT |
| npm | @esbuild/linux-ia32 | 0.28.2 | web build, inside vite | MIT |
| npm | @esbuild/linux-loong64 | 0.28.2 | web build, inside vite | MIT |
| npm | @esbuild/linux-mips64el | 0.28.2 | web build, inside vite | MIT |
| npm | @esbuild/linux-ppc64 | 0.28.2 | web build, inside vite | MIT |
| npm | @esbuild/linux-riscv64 | 0.28.2 | web build, inside vite | MIT |
| npm | @esbuild/linux-s390x | 0.28.2 | web build, inside vite | MIT |
| npm | @esbuild/linux-x64 | 0.28.2 | web build, inside vite | MIT |
| npm | @esbuild/netbsd-arm64 | 0.28.2 | web build, inside vite | MIT |
| npm | @esbuild/netbsd-x64 | 0.28.2 | web build, inside vite | MIT |
| npm | @esbuild/openbsd-arm64 | 0.28.2 | web build, inside vite | MIT |
| npm | @esbuild/openbsd-x64 | 0.28.2 | web build, inside vite | MIT |
| npm | @esbuild/openharmony-arm64 | 0.28.2 | web build, inside vite | MIT |
| npm | @esbuild/sunos-x64 | 0.28.2 | web build, inside vite | MIT |
| npm | @esbuild/win32-arm64 | 0.28.2 | web build, inside vite | MIT |
| npm | @esbuild/win32-ia32 | 0.28.2 | web build, inside vite | MIT |
| npm | @esbuild/win32-x64 | 0.28.2 | web build, inside vite | MIT |
| npm | @mapbox/jsonlint-lines-primitives | 2.0.3 | browser, inside maplibre-gl | MIT |
| npm | @mapbox/point-geometry | 1.1.0 | browser, inside maplibre-gl | ISC |
| npm | @mapbox/tiny-sdf | 2.3.0 | browser, inside maplibre-gl | BSD-2-Clause |
| npm | @mapbox/unitbezier | 1.0.0 | browser, inside maplibre-gl | BSD-2-Clause |
| npm | @mapbox/vector-tile | 3.0.0 | browser, inside maplibre-gl | BSD-3-Clause |
| npm | @maplibre/geojson-vt | 6.3.3 | browser, inside maplibre-gl | ISC |
| npm | @maplibre/maplibre-gl-style-spec | 26.9.0 | browser, inside maplibre-gl | ISC |
| npm | @maplibre/mlt | 1.3.0 | browser, inside maplibre-gl | (MIT OR Apache-2.0) |
| npm | @maplibre/vt-pbf | 4.3.3 | browser, inside maplibre-gl | MIT |
| npm | @preact/signals | 2.11.4 | browser | MIT |
| npm | @preact/signals-core | 1.14.5 | browser | MIT |
| npm | @rollup/rollup-android-arm-eabi | 4.68.8 | web build, inside vite | MIT |
| npm | @rollup/rollup-android-arm64 | 4.68.8 | web build, inside vite | MIT |
| npm | @rollup/rollup-android-x64 | 4.68.8 | web build, inside vite | MIT |
| npm | @rollup/rollup-darwin-arm64 | 4.68.8 | web build, inside vite | MIT |
| npm | @rollup/rollup-darwin-x64 | 4.68.8 | web build, inside vite | MIT |
| npm | @rollup/rollup-freebsd-arm64 | 4.68.8 | web build, inside vite | MIT |
| npm | @rollup/rollup-freebsd-x64 | 4.68.8 | web build, inside vite | MIT |
| npm | @rollup/rollup-linux-arm-gnueabihf | 4.68.8 | web build, inside vite | MIT |
| npm | @rollup/rollup-linux-arm-musleabihf | 4.68.8 | web build, inside vite | MIT |
| npm | @rollup/rollup-linux-arm64-gnu | 4.68.8 | web build, inside vite | MIT |
| npm | @rollup/rollup-linux-arm64-musl | 4.68.8 | web build, inside vite | MIT |
| npm | @rollup/rollup-linux-loong64-gnu | 4.68.8 | web build, inside vite | MIT |
| npm | @rollup/rollup-linux-loong64-musl | 4.68.8 | web build, inside vite | MIT |
| npm | @rollup/rollup-linux-ppc64-gnu | 4.68.8 | web build, inside vite | MIT |
| npm | @rollup/rollup-linux-ppc64-musl | 4.68.8 | web build, inside vite | MIT |
| npm | @rollup/rollup-linux-riscv64-gnu | 4.68.8 | web build, inside vite | MIT |
| npm | @rollup/rollup-linux-riscv64-musl | 4.68.8 | web build, inside vite | MIT |
| npm | @rollup/rollup-linux-s390x-gnu | 4.68.8 | web build, inside vite | MIT |
| npm | @rollup/rollup-linux-x64-gnu | 4.68.8 | web build, inside vite | MIT |
| npm | @rollup/rollup-linux-x64-musl | 4.68.8 | web build, inside vite | MIT |
| npm | @rollup/rollup-openbsd-x64 | 4.68.8 | web build, inside vite | MIT |
| npm | @rollup/rollup-openharmony-arm64 | 4.68.8 | web build, inside vite | MIT |
| npm | @rollup/rollup-win32-arm64-msvc | 4.68.8 | web build, inside vite | MIT |
| npm | @rollup/rollup-win32-ia32-msvc | 4.68.8 | web build, inside vite | MIT |
| npm | @rollup/rollup-win32-x64-gnu | 4.68.8 | web build, inside vite | MIT |
| npm | @rollup/rollup-win32-x64-msvc | 4.68.8 | web build, inside vite | MIT |
| npm | @types/estree | 1.0.10 | web build, inside vite | MIT |
| npm | @types/geojson | 7946.0.16 | browser, inside maplibre-gl | MIT |
| npm | bidi-js | 1.1.0 | browser, inside maplibre-gl | MIT |
| npm | earcut | 3.2.4 | browser, inside maplibre-gl | ISC |
| npm | esbuild | 0.28.2 | web build, inside vite | MIT |
| npm | fdir | 6.5.0 | web build, inside vite | MIT |
| npm | fsevents | 2.3.3 | web build, inside vite | MIT |
| npm | gl-matrix | 3.4.4 | browser, inside maplibre-gl | MIT |
| npm | htm | 3.1.1 | browser | Apache-2.0 |
| npm | json-stringify-pretty-compact | 4.0.0 | browser, inside maplibre-gl | MIT |
| npm | kdbush | 4.1.0 | browser, inside maplibre-gl | ISC |
| npm | maplibre-gl | 6.17.0 | browser | BSD-3-Clause |
| npm | minimist | 1.2.8 | browser, inside maplibre-gl | MIT |
| npm | murmurhash-js | 1.0.0 | browser, inside maplibre-gl | MIT |
| npm | nanoid | 3.3.20 | web build, inside vite | MIT |
| npm | pbf | 5.2.0 | browser, inside maplibre-gl | BSD-3-Clause |
| npm | picocolors | 1.1.1 | web build, inside vite | ISC |
| npm | picomatch | 4.0.7 | web build, inside vite | MIT |
| npm | postcss | 8.5.33 | web build, inside vite | MIT |
| npm | potpack | 2.1.0 | browser, inside maplibre-gl | ISC |
| npm | preact | 11.2.0 | browser | MIT |
| npm | protocol-buffers-schema | 3.6.1 | browser, inside maplibre-gl | MIT |
| npm | quickselect | 3.0.0 | browser, inside maplibre-gl | ISC |
| npm | require-from-string | 2.0.2 | browser, inside maplibre-gl | MIT |
| npm | rollup | 4.68.8 | web build, inside vite | MIT |
| npm | source-map-js | 1.2.4 | web build, inside vite | BSD-3-Clause |
| npm | tinyglobby | 0.2.17 | web build, inside vite | MIT |
| npm | tinyqueue | 3.0.0 | browser, inside maplibre-gl | ISC |
| npm | vite | 7.3.8 | web build | MIT |
<!-- /generated: dependency_table -->

### 4.2 Browser libraries and how they arrive

The interface uses four browser libraries: preact, htm, @preact/signals (with its core package, @preact/signals-core) and maplibre-gl. One build tool, Vite (MIT), bundles them with the interface's own files into `web/dist/`.

- `web/package.json` pins exact versions. `web/package-lock.json` records each package's address on the public npm registry and its integrity value (a SHA-512 digest).
- `npm ci` (run by `make setup`) installs exactly the lockfile. It refuses to run if `package.json` and the lockfile disagree, and stops if a download does not match its integrity value.
- `npm run build` (run by `make app`) writes the bundled page, scripts and styles into `web/dist/`, which is not committed. The page loads nothing from a content delivery network.
- Packages marked "web build" in section 4.1 are Vite and what it needs (esbuild, Rollup, PostCSS and their per-platform binaries). They run only on the build machine and are not part of the page. Packages marked "inside maplibre-gl" are already compiled into the maplibre-gl files.
- `tests/test_web_package.py` checks that every pin is exact, every lockfile entry comes from the public registry with a SHA-512 value, and every npm package passes the licence audit. CI also runs `npm audit --audit-level=high`.

<!-- generated: browser_libraries -->
| Library | Version | Licence | Downloaded from | Integrity check |
|---|---|---|---|---|
| @preact/signals | 2.11.4 | MIT | registry.npmjs.org | SHA512 |
| @preact/signals-core | 1.14.5 | MIT | registry.npmjs.org | SHA512 |
| htm | 3.1.1 | Apache-2.0 | registry.npmjs.org | SHA512 |
| maplibre-gl | 6.17.0 | BSD-3-Clause | registry.npmjs.org | SHA512 |
| preact | 11.2.0 | MIT | registry.npmjs.org | SHA512 |
<!-- /generated: browser_libraries -->

### 4.3 Exceptions (generated)

<!-- generated: licence_exceptions -->
None. Every locked package passes the licence audit.

Installed only on other platforms, so the licence comes from the audit's hand-checked table: `colorama` 0.4.6 (BSD-3-Clause).
<!-- /generated: licence_exceptions -->

### 4.4 Notes the audit handles openly

**Composite licence expressions.** Some permissive packages declare several licences because they bundle small components. numpy is one: it is BSD-licensed and bundles small parts under 0BSD, MIT, zlib and CC0 terms. The audit checks each part of the expression, and every part must be allowed. Each such package is listed here with its full expression.

<!-- generated: composite_licences -->
| Package | Version | Full licence expression |
|---|---|---|
| greenlet | 3.5.6 | MIT AND PSF-2.0 |
| numpy | 2.5.5 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| @maplibre/mlt | 1.3.0 | (MIT OR Apache-2.0) |
<!-- /generated: composite_licences -->

**Bundled compiler runtimes.** Binary wheels of numeric libraries can ship compiler runtime files next to their own code. The package metadata the audit reads does not list them, so they are named here from each wheel's own licence file.

| Package | Bundled runtime | Licence stated in the wheel | Status |
|---|---|---|---|
| numpy (run time) | GCC runtime library libgfortran | GPL-3.0-or-later WITH GCC-exception-3.1 | Accepted. The GCC Runtime Library Exception expressly permits this use. The audit accepts only this exact pairing |

Wheels for other platforms can bundle different runtime files. The rows above are read from the macOS wheels pinned in `uv.lock`.

scipy is not a dependency. Its macOS wheel links an LGPL quad-precision runtime, so the optional groups that pulled it in were removed. Calibration uses numpy only.

### 4.5 Software bill of materials

GitHub generates the SBOM from the dependency manifests (`pyproject.toml`, `uv.lock`, `web/package.json`, `web/package-lock.json`). **Planned:** the export and its summary will be added after the first public push.

<!-- TODO(Ye): export the SBOM from Insights, Dependency graph, Export SBOM, and save it with the submission. -->

## 5. Datasets and their licences (generated)

The first rows are the sources `make data` downloads. The rows marked "calibration file" are the inputs behind the two calibration tables. Only aggregated shares from them are kept, and no source data is redistributed. The Microsoft building footprints are under the ODbL, and the attribution is in `DATA_PROVENANCE.md`.

<!-- generated: dataset_licences -->
| Dataset | Publisher or use | Licence | Terms page |
|---|---|---|---|
| Building Permits Survey, county annual files | U.S. Census Bureau | Public domain (U.S. government work) | [terms](https://www.census.gov/about/policies/open-gov/open-data.html) |
| American Community Survey 5-year table-based summary files (2020-2024) | U.S. Census Bureau | Public domain (U.S. government work) | [terms](https://www.census.gov/programs-surveys/acs/data/summary-file.html) |
| American Community Survey 5-year estimates via the Census Data API (2020-2024) | U.S. Census Bureau | Public domain (U.S. government work); API terms of service apply | [terms](https://www.census.gov/data/developers/about/terms-of-service.html) |
| Cartographic boundary file, census tracts, 2020, 1:500,000 | U.S. Census Bureau | Public domain (U.S. government work) | [terms](https://www.census.gov/geographies/mapping-files/time-series/geo/cartographic-boundary.html) |
| Cartographic boundary file, counties, 2020, 1:500,000 | U.S. Census Bureau | Public domain (U.S. government work) | [terms](https://www.census.gov/geographies/mapping-files/time-series/geo/cartographic-boundary.html) |
| TIGER/Line 2020 tabulation blocks (attribute table only) | U.S. Census Bureau | Public domain (U.S. government work) | [terms](https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html) |
| FHFA House Price Index, annual, counties | Federal Housing Finance Agency | Public domain (U.S. government work) | [terms](https://www.fhfa.gov/data/hpi/datasets) |
| FHFA House Price Index, annual, census tracts | Federal Housing Finance Agency | Public domain (U.S. government work) | [terms](https://www.fhfa.gov/data/hpi/datasets) |
| Primary Mortgage Market Survey, weekly rate history | Freddie Mac | Published freely by Freddie Mac; use under its website terms, with attribution | [terms](https://www.freddiemac.com/terms) |
| HUD Fair Market Rents, county and metro area file | U.S. Department of Housing and Urban Development | Public domain (U.S. government work) | [terms](https://www.huduser.gov/portal/datasets/fmr.html) |
| National Transit Map, stops | U.S. Department of Transportation, Bureau of Transportation Statistics | Public domain (U.S. government work) | [terms](https://geodata.bts.gov/datasets/usdot::national-transit-map-stops) |
| Pennsylvania House Bill 2186 (accessory dwelling units), bill page and text | Pennsylvania General Assembly | Public legislative record | [terms](https://www.palegis.us/legislation/bills/2025/hb2186) |
| Pennsylvania Senate Bill 1346 (accessory dwelling units), bill page and text | Pennsylvania General Assembly | Public legislative record | [terms](https://www.palegis.us/legislation/bills/2025/sb1346) |
| Building Permits Survey, state annual files | U.S. Census Bureau | Public domain (U.S. government work) | [terms](https://www.census.gov/about/policies/open-gov/open-data.html) |
| American Community Survey 5-year summary file geography list (2020-2024) | U.S. Census Bureau | Public domain (U.S. government work) | [terms](https://www.census.gov/programs-surveys/acs/data/summary-file.html) |
| American Community Survey 5-year table shells (2020-2024) | U.S. Census Bureau | Public domain (U.S. government work) | [terms](https://www.census.gov/programs-surveys/acs/data/summary-file.html) |
| Quarterly Census of Employment and Wages, annual averages, construction (NAICS 23) | U.S. Bureau of Labor Statistics | Public domain (U.S. government work) | [terms](https://www.bls.gov/cew/additional-resources/open-data/) |
| National Transit Map, agencies | U.S. Department of Transportation, Bureau of Transportation Statistics | Public domain (U.S. government work) | [terms](https://geodata.bts.gov/datasets/usdot::national-transit-map-agencies) |
| Mobility Database catalog of transit schedule feeds | MobilityData | Catalog published openly by MobilityData; each listed feed keeps its agency's own terms | [terms](https://mobilitydatabase.org/) |
| Transit agency schedule feeds (GTFS), via their published addresses or the catalog mirror | Each transit agency (copies mirrored by MobilityData) | Each agency's own open-data terms (for example SEPTA and Pittsburgh Regional Transit developer terms) | [terms](https://mobilitydatabase.org/) |
| LEHD Origin-Destination Employment Statistics (LODES 8), workplace area characteristics, all jobs | U.S. Census Bureau, Longitudinal Employer-Household Dynamics program | Public domain (U.S. government work) | [terms](https://lehd.ces.census.gov/data/) |
| LEHD Origin-Destination Employment Statistics (LODES 8), home-to-work flows, all jobs | U.S. Census Bureau, Longitudinal Employer-Household Dynamics program | Public domain (U.S. government work) | [terms](https://lehd.ces.census.gov/data/) |
| County Business Patterns, county file | U.S. Census Bureau | Public domain (U.S. government work) | [terms](https://www.census.gov/programs-surveys/cbp/technical-documentation/methodology.html) |
| Low-Income Housing Tax Credit Qualified Census Tracts | U.S. Department of Housing and Urban Development | Public domain (U.S. government work) | [terms](https://www.huduser.gov/portal/sadda/sadda_qct.html) |
| Low-Income Housing Tax Credit Difficult Development Areas | U.S. Department of Housing and Urban Development | Public domain (U.S. government work) | [terms](https://www.huduser.gov/portal/sadda/sadda_qct.html) |
| 2020 ZIP Code Tabulation Area to census tract relationship file | U.S. Census Bureau | Public domain (U.S. government work) | [terms](https://www.census.gov/geographies/reference-files/time-series/geo/relationship-files.html) |
| Qualified Opportunity Zones designated for 2027 (second decennial round) | U.S. Department of the Treasury | Public domain (U.S. government work) | [terms](https://home.treasury.gov/policy-issues/tax-policy/data-transparency/qualified-opportunity-zones) |
| New Markets Tax Credit low-income community tracts (2016-2020 ACS) | U.S. Department of the Treasury, CDFI Fund | Public domain (U.S. government work) | [terms](https://www.cdfifund.gov/news/537) |
| Low-Income Housing Tax Credit properties (HUD LIHTC database) | U.S. Department of Housing and Urban Development | Public domain (U.S. government work) | [terms](https://www.huduser.gov/portal/datasets/lihtc.html) |
| Keystone Opportunity Zone (KOZ) locations, public view | Pennsylvania Department of Community and Economic Development | Public Commonwealth map layer; no licence stated | [terms](https://dced.pa.gov/programs/keystone-opportunity-zones-kozs/) |
| National Register of Historic Places, listed property points and polygons | U.S. Department of the Interior, National Park Service | Public domain (U.S. government work) | [terms](https://www.nps.gov/subjects/nationalregister/data-downloads.htm) |
| FBI Crime Data Explorer API, agency list and agency offense counts (summary reporting) | U.S. Department of Justice, Federal Bureau of Investigation | Public domain (U.S. government work); the API needs a free api.data.gov key | [terms](https://api.data.gov/docs/) |
| Municipal Statistics, County / Municipal Planning, Zoning & Building Code report | Pennsylvania Department of Community and Economic Development (DCED) | Public information published by a Pennsylvania state agency; no licence stated. Cited as "PA DCED Municipal Statistics". | [terms](https://dced.pa.gov/local-government/) |
| City and Town Population Totals (subcounty resident population estimates) | U.S. Census Bureau, Population Estimates Program | Public domain (U.S. government work) | [terms](https://www.census.gov/data/datasets/time-series/demo/popest/2020s-total-cities-and-towns.html) |
| 2020 Census Block Assignment Files, county subdivisions | U.S. Census Bureau, Geography Division | Public domain (U.S. government work) | [terms](https://www.census.gov/geographies/reference-files/time-series/geo/block-assignment-files.html) |
| Philadelphia Properties and Assessment History (opa_properties_public) | City of Philadelphia, Office of Property Assessment (via OpenDataPhilly, Carto SQL) | City of Philadelphia open data; licence to be confirmed before release (only derived totals are kept, and the City is credited) | [terms](https://opendataphilly.org/datasets/philadelphia-properties-and-assessment-history/) |
| Philadelphia Real Estate Transfers (rtt_summary) | City of Philadelphia, Department of Records (via OpenDataPhilly, Carto SQL) | City of Philadelphia open data; licence to be confirmed before release (only yearly totals are kept) | [terms](https://opendataphilly.org/datasets/real-estate-transfers/) |
| Philadelphia Licenses and Inspections building permits (permits) | City of Philadelphia, Department of Licenses and Inspections (via OpenDataPhilly, Carto SQL) | City of Philadelphia open data; licence to be confirmed before release (only tract totals are kept) | [terms](https://opendataphilly.org/datasets/licenses-and-inspections-building-and-zoning-permits/) |
| Allegheny County Property Assessments | Allegheny County Office of Property Assessments (via the Western Pennsylvania Regional Data Center) | Creative Commons CC0 (public domain dedication) | [terms](https://data.wprdc.org/dataset/property-assessments) |
| Parcel Centroids in Allegheny County with Geographic Identifiers (March 2025) | Western Pennsylvania Regional Data Center | Creative Commons CC0 (public domain dedication) | [terms](https://data.wprdc.org/dataset/parcel-centroids-in-allegheny-county-with-geographic-identifiers) |
| Allegheny County Real Estate Sales | Allegheny County Office of Property Assessments (via the Western Pennsylvania Regional Data Center) | Creative Commons CC0 (public domain dedication) | [terms](https://data.wprdc.org/dataset/real-estate-sales) |
| City of Pittsburgh PLI Permits | City of Pittsburgh, Department of Permits, Licenses and Inspections (via WPRDC) | Creative Commons Attribution 4.0 (CC BY 4.0) | [terms](https://data.wprdc.org/dataset/pli-permits) |
| PHFA Low-Income Housing Tax Credit award lists | Pennsylvania Housing Finance Agency (PHFA) | Public information published by a Pennsylvania state agency; no licence stated | [terms](https://www.phfa.org/mhp/) |
| Census Geocoder (geographies, one-line address) | U.S. Census Bureau | Public domain (U.S. government service) | [terms](https://geocoding.geo.census.gov/geocoder/) |
| County comprehensive plans (citation list in assumptions/plans.yaml) | Pennsylvania county planning commissions | Public documents of each county; cited, not copied | [terms](https://dced.pa.gov/local-government/) |
| Ownership Profile of Single-Family Residence Properties in Philadelphia: A Focus on Large Corporate Investors (cited benchmark, not ingested) | Federal Reserve Bank of Philadelphia | Cited publication; one figure quoted with attribution, no data copied | [terms](https://www.philadelphiafed.org/community-development/housing-and-neighborhoods/ownership-profile-of-single-family-residence-properties-in-philadelphia-large-corporate-investors) |
| Single-Family Home Sales in Philadelphia (cited benchmark, not ingested) | The Pew Charitable Trusts | Cited publication; one figure quoted with attribution, no data copied | [terms](https://www.pew.org/en/research-and-analysis/reports/2024/07/single-family-home-sales-in-philadelphia) |
| [U.S. Department of Agriculture, National Agriculture Imagery Program (NAIP), Pennsylvania, 2022](https://naip-usdaonline.hub.arcgis.com/) | calibration file `adu_fit.yaml` | Public domain (U.S. government work) | - |
| [Microsoft, US Building Footprints](https://github.com/microsoft/USBuildingFootprints) | calibration file `adu_fit.yaml` | ODbL 1.0; the shares here are a produced work and are attributed | - |
| Parcel layers published by the City of Philadelphia, Allegheny County and four further Pennsylvania counties | calibration file `adu_fit.yaml` | Varies by county; several state no licence. Only aggregate shares are used. | - |
| Zoning ordinances of 221 Pennsylvania municipalities, accessory-dwelling sections, read from each municipality's published code | calibration file `adu_legal_share.yaml` | Cited only. No data from it is kept. | - |
| City of Pittsburgh, Ordinance 31 of 2026 (Bill 2025-1545), signed Oct 29, 2026: accessory dwelling units by right citywide | calibration file `adu_legal_share.yaml` | Cited only. No data from it is kept. | - |
| Philadelphia Code section 14-604(11), accessory dwelling units | calibration file `adu_legal_share.yaml` | Cited only. No data from it is kept. | - |
| [Pennsylvania Department of Community and Economic Development, Municipal Statistics, land-use ordinance fields (municipalities with no zoning ordinance)](https://apps.dced.pa.gov/Munstats-public/) | calibration file `adu_legal_share.yaml` | Cited only. No data from it is kept. | - |
| [U.S. Census Bureau, 2020 Census Redistricting Data (P.L. 94-171), population by municipality](https://www.census.gov/programs-surveys/decennial-census/about/rdo/summary-files.html) | calibration file `adu_legal_share.yaml` | Cited only. No data from it is kept. | - |
<!-- /generated: dataset_licences -->

The code licence (Apache-2.0) does not cover the data. Each dataset keeps its publisher's terms. No dataset is stored in this repository.
