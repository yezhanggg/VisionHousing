# Disclosures

This file lists every AI tool, model, external service, dependency and dataset the project uses, as the Official Rules require. Sections marked "generated" are filled by `scripts/gen_docs_tables.py` from the repository's own records, so they match the code. Do not edit them by hand.

## 1. AI code-generation tools

| Tool | Models | What it did | Who reviewed it |
|---|---|---|---|
| Anthropic Claude Code | Claude Opus and Claude Fable model families (Anthropic) | Planned the tasks (Fable), and wrote the code, tests, configuration and documents (Opus) | The team lead directed every task, reviewed the output, made the decisions, and made every commit and push |

No other AI tool was used to write code, tests or documents. In particular, GitHub Copilot and other editor assistants were not used. <!-- TODO(Ye): confirm the sentence above is true for every team member and every editor; if any other AI tool was used, add a row instead. If you want exact model versions listed, confirm which were used over the whole period (at the end of the build: Claude Opus 5.5 and Claude Fable 5.2). -->

How the work was controlled:

- The assistant worked from a written plan and task list set by the team lead.
- The assistant never committed, pushed or published anything.
- Every task had to pass `make verify` (lint, tests, licence audit, secret scan) before it counted as done.
- All work was written new for this Challenge. <!-- TODO(Ye): confirm this line against Official Rules section 7(a) ("original work of the Team, created during the Registration and/or Coding Periods, except for properly disclosed pre-existing open-source components and public data"): every piece of your own earlier work used as reference was created on or after the Registration Period opened, and nothing came from anyone outside the team. If not, disclose it here instead. -->

## 2. Pretrained models, weights and embeddings

None in the product.

The tool calls no AI model or AI service at run time. It ships no pretrained weights or embeddings. Explanations on screen are produced by deterministic code. The question box ("Ask about this run") is deterministic too: it matches a question to one fixed intent by the words listed in `assumptions/answers.yaml` and fills a written template with text copied from the run's own records (`src/visionhousing/api/answers.py`, `web/js/lib/answers.js`). It uses no language model. The browser answers inside the page, so a question is never sent to the back end or anywhere else, and it is not stored.

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
| Transit agency schedule feeds (GTFS), read a second time for peak hours | Each transit agency | Peak-hour departures per platform-level stop and side of the street (frequent-bus rule of the walkshed table) | No |
| Overpass API (overpass-api.de, mirror overpass.private.coffee) | OpenStreetMap community servers; data © OpenStreetMap contributors, ODbL | The walking network (streets, sidewalks, paths) near major transit stops, for network walksheds (`visionhousing transit-walk`) | No |
| Bill pages, bill text and the Municipalities Planning Code text (palegis.us) | Pennsylvania General Assembly | State-specific scenarios; the law tables (bill status, cited planning-code sections) | No |
| Vermont Statutes Online (legislature.vermont.gov), read once by hand, not at build time | Vermont General Assembly | 24 V.S.A. § 4412(1)(D) and (1)(E), cited in `assumptions/calibration/already_legal_vt.yaml` (Vermont's already-legal ADU and missing-middle ranges) | No |
| Municipal ordinance PDFs and legislation pages on each municipality's own site (read once by hand, not at build time) | Pennsylvania municipalities; City of Pittsburgh (Legistar); City of Philadelphia (phila.gov) | The verified municipal zoning rows in `assumptions/law/municipal_rules.yaml`. Commercial code hosts (eCode360, Municode, American Legal) are never read; the Pennsylvania Code and Bulletin site is linked only, because its robots file disallows automated access | No |
| Zoning district and overlay map services, hosted on ArcGIS Online (www.arcgis.com search for discovery; services.arcgis.com, services1.arcgis.com) | City of Philadelphia (Department of Planning and Development); City of Pittsburgh (Department of City Planning) | The zoning-district pilot (`visionhousing zone-rules`): base districts, a dated December 2021 Philadelphia snapshot, overlays, and Pittsburgh's Parking Reduction overlay. Raw outlines stay in the local cache; only derived lot counts and shares enter the pack | No |
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
| Carto SQL service (phl.carto.com), safety tables | City of Philadelphia (OpenDataPhilly) | Place profile: police incidents, shootings, code violations and 311 requests per tract and year | No |
| ArcGIS Online map services (services.arcgis.com and others) | City of Philadelphia (permits layer); HUD (assisted housing, public housing, vouchers); MassGIS (3A atlas: districts, parcels with modeled capacity, station radii); Vermont ACCD (wastewater areas); EPA (sewersheds, Clean Watersheds Needs Survey); Adams County, PA (sewer areas) | Permit timing, affordability, Massachusetts districts and 3A capacity, water and sewer readiness | No |
| Commonwealth of Massachusetts ArcGIS server (arcgisserver.digital.mass.gov) | MassDEP, served by MassGIS (estimated sewer service areas) | Massachusetts estimated sewer service per tract | No |
| HUD User downloads (huduser.gov) | U.S. Department of Housing and Urban Development | CHAS cost-burden tables and income limits | No |
| PASDA map services (mapservices.pasda.psu.edu) | Pennsylvania Spatial Data Access, serving PA DEP and county files | Public water and sewer service areas | No |
| PA DEP files (files.dep.state.pa.us) | Pennsylvania Department of Environmental Protection | Act 537 sewage plan status by municipality | No |
| FEMA National Flood Hazard Layer service (hazards.fema.gov) | Federal Emergency Management Agency | Flood-zone shares per tract | No |
| MRLC Annual NLCD downloads and The National Map 3DEP elevation service | U.S. Geological Survey | Developed land, impervious cover and slope per tract | No |
| California open data portal (data.ca.gov) | California Department of Housing and Community Development | Annual Progress Report Tables A and A2, for validation only | No |
| Massachusetts state pages (www.mass.gov, malegislature.gov) | Massachusetts HLC; the General Court | 3A status lists and the ADU survey (a browser-saved copy when scripted access is refused); the statutes cited | No |
| Python package index | Python Software Foundation | `make setup` installs the Python packages pinned in `uv.lock` | No |
| npm package registry | npm | `make setup` runs `npm ci` in `web/`, which installs the exact packages in `web/package-lock.json` and checks each one's integrity value (section 4.2) | No |
| Vercel (optional static hosting) | Vercel Inc. | Hosts the optional no-server version of the interface: it runs `npm ci` and `npm run build:static` from `vercel.json` and serves `web/dist/` with the precomputed results in `web/public/data/` | No (a free Vercel account, set up by the team lead) |
| Playwright browser download | Microsoft (Playwright project) | `make setup` installs the Chromium build the end-to-end tests use. It is a test tool, not part of the product | No |
| GitHub Actions, CodeQL, Dependabot | GitHub | Continuous checks on the public repository | No |
| OpenFreeMap public tile service (tiles.openfreemap.org) | OpenFreeMap (MIT-licensed project); map data © OpenStreetMap contributors (ODbL), tiles © OpenMapTiles | Optional basemap under the county map: style, vector tiles, fonts and icons, fetched by the reader's browser. Free, no limits on map views, commercial use allowed; credited on the map. Can be turned off with the Basemap button | No (no key, no account, no cookies) |

The tool itself, once set up, contacts none of these except one optional request: the reader's browser fetches the county map's basemap from OpenFreeMap (above) while the Basemap button is on. The tool serves the local data pack and the built interface (`web/dist/`). It loads no code from a content delivery network; the basemap brings only map styles, tiles, fonts and icons, and the Content Security Policy allows that one host for data and images only. With the basemap off, or if it cannot be reached, the map shows the county outlines alone.

The full list of data sources is `src/visionhousing/data/sources.yaml`. See `DATA_PROVENANCE.md`. The calibration inputs in section 5 were used once, outside the product. The tool and `make data` never contact them.

## 4. Third-party software dependencies

Third-party software enters only as declared dependencies. No third-party source code is copied into the repository. The licence audit (`scripts/licence_audit.py`, run by `make verify`) checks every locked Python package and every package in the npm lockfile against an allow-list of permissive licences, and fails on anything else. It also reads every licence file inside each installed Python package and lists the native libraries a wheel bundles (`.dylib`, `.so`, `.dll` files that are not the package's own Python modules), and fails when a licence file names a copyleft licence for bundled code. A package's declared licence covers only its own code: a BSD-licensed wheel can still ship an LGPL library inside it. That is why Shapely (which bundles GEOS, LGPL-2.1) was removed on 2026-11-24 and its geometry rewritten in NumPy (`features/planar.py`), as SciPy (bundled libquadmath) was before.

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

A CycloneDX 1.5 SBOM is generated from the lockfiles (`uv.lock`, `web/package-lock.json`) by `scripts/sbom.py` and kept at [`sbom/visionhousing.cdx.json`](sbom/visionhousing.cdx.json). Each component lists its version, package URL, licence (the same lookup the licence audit uses) and hashes. `make sbom` regenerates it; `make sbom-check` (part of `make verify` and CI) fails if it no longer matches the lockfiles. GitHub's dependency-graph export is an alternative view of the same manifests.

<!-- TODO(Ye): optionally also export the SBOM from GitHub (Insights, Dependency graph, Export SBOM) after the first public push. -->

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
| Pennsylvania House Bill 2155 (municipal parking minimums), bill page and text | Pennsylvania General Assembly | Public legislative record | [terms](https://www.palegis.us/legislation/bills/2025/hb2155) |
| Pennsylvania House Bill 1459 (multiple-unit dwellings in commercial zones), bill page and text | Pennsylvania General Assembly | Public legislative record | [terms](https://www.palegis.us/legislation/bills/2025/hb1459) |
| Pennsylvania Municipalities Planning Code (Act 247 of 1968, as amended), full text | Pennsylvania General Assembly | Public legislative record | [terms](https://www.palegis.us/statutes/unconsolidated/law-information?sessYr=1968&sessInd=0&actNum=247) |
| Pennsylvania Code and Bulletin (state regulations), linked only | Pennsylvania Legislative Reference Bureau | Public legislative record; the site's robots file disallows automated reading | [terms](https://www.pacodeandbulletin.gov/) |
| City of Pittsburgh legislation (Bill 2025-1545, Ordinance 31 of 2026, accessory dwelling units) | City of Pittsburgh, City Council | Public legislative record of the City of Pittsburgh | [terms](https://pittsburgh.legistar.com/) |
| City of Philadelphia, L&I Accessory Dwelling Unit Checklist (PZ_004_INF, Rev 3.2022) | City of Philadelphia, Department of Licenses and Inspections | Public guidance of the City of Philadelphia; cited, not copied | [terms](https://www.phila.gov/terms-of-use/) |
| Pennsylvania municipal zoning ordinances published on each municipality's own website | Pennsylvania municipalities (each its own) | Public ordinances of each municipality; cited, not copied | [terms](https://dced.pa.gov/local-government/) |
| Vermont and Wyoming town, city and county zoning bylaws published on each jurisdiction's own website | Vermont towns and cities; Wyoming cities and counties (each its own) | Public bylaws of each jurisdiction; cited, not copied | [terms](https://www.barrecity.org/land-use-and-other-ordinances.html) |
| Vermont Statutes, Title 24, section 4412 (required provisions and prohibited effects of zoning bylaws) | Vermont General Assembly | Public legislative record | [terms](https://legislature.vermont.gov/statutes/) |
| Building Permits Survey, state annual files | U.S. Census Bureau | Public domain (U.S. government work) | [terms](https://www.census.gov/about/policies/open-gov/open-data.html) |
| Building Permits Survey, state monthly year-to-date files | U.S. Census Bureau | Public domain (U.S. government work) | [terms](https://www.census.gov/about/policies/open-gov/open-data.html) |
| American Community Survey 5-year summary file geography list (2020-2024) | U.S. Census Bureau | Public domain (U.S. government work) | [terms](https://www.census.gov/programs-surveys/acs/data/summary-file.html) |
| American Community Survey 5-year table shells (2020-2024) | U.S. Census Bureau | Public domain (U.S. government work) | [terms](https://www.census.gov/programs-surveys/acs/data/summary-file.html) |
| Quarterly Census of Employment and Wages, annual averages, construction (NAICS 23) | U.S. Bureau of Labor Statistics | Public domain (U.S. government work) | [terms](https://www.bls.gov/cew/additional-resources/open-data/) |
| National Transit Map, agencies | U.S. Department of Transportation, Bureau of Transportation Statistics | Public domain (U.S. government work) | [terms](https://geodata.bts.gov/datasets/usdot::national-transit-map-agencies) |
| Mobility Database catalog of transit schedule feeds | MobilityData | Catalog published openly by MobilityData; each listed feed keeps its agency's own terms | [terms](https://mobilitydatabase.org/) |
| Transitland Atlas catalogue of transit feeds (static schedule addresses) | Interline Technologies and contributors (Transitland) | Creative Commons Attribution 4.0 (CC-BY 4.0); attribution to Transitland Atlas (github.com/transitland/transitland-atlas) | [terms](https://github.com/transitland/transitland-atlas/blob/main/LICENSE.txt) |
| Transit agency schedule feeds (GTFS), via their published addresses or the catalog mirror | Each transit agency (copies mirrored by MobilityData) | Each agency's own open-data terms (for example SEPTA and Pittsburgh Regional Transit developer terms). Massachusetts: MBTA and regional transit authority schedules are provided by MassDOT under its Developers License Agreement (use and redistribution allowed; MassDOT acknowledged as the provider; no endorsement implied). Wyoming: START Bus (Town of Jackson) and Cheyenne Transit Program feeds are published at their operators' scheduling-vendor addresses with no licence attached; each feed's legal record is in assumptions/transit_feeds.yaml | [terms](https://mobilitydatabase.org/) |
| OpenStreetMap walking network (streets, sidewalks and paths) near major transit stops, via the Overpass API | OpenStreetMap contributors (served by the public Overpass API instances) | Open Database Licence (ODbL 1.0). Attribution: "© OpenStreetMap contributors". The transit_walk_tract and transit_walk_county tables are a derived database of OpenStreetMap: if they are published (in a state pack or a download), they are offered under ODbL 1.0, with the attribution, and anyone may rebuild them with visionhousing transit-walk. Maps and results made from them are produced works and carry the attribution. The raw tiles stay in the local cache and are never published. | [terms](https://www.openstreetmap.org/copyright) |
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
| 2020 Census Block Assignment Files, incorporated places and CDPs | U.S. Census Bureau, Geography Division | Public domain (U.S. government work) | [terms](https://www.census.gov/geographies/reference-files/time-series/geo/block-assignment-files.html) |
| Philadelphia Properties and Assessment History (opa_properties_public) | City of Philadelphia, Office of Property Assessment (via OpenDataPhilly, Carto SQL) | City of Philadelphia open data; licence to be confirmed before release (only derived totals are kept, and the City is credited) | [terms](https://opendataphilly.org/datasets/philadelphia-properties-and-assessment-history/) |
| Philadelphia Real Estate Transfers (rtt_summary) | City of Philadelphia, Department of Records (via OpenDataPhilly, Carto SQL) | City of Philadelphia open data; licence to be confirmed before release (only yearly totals are kept) | [terms](https://opendataphilly.org/datasets/real-estate-transfers/) |
| Philadelphia Licenses and Inspections building permits (permits) | City of Philadelphia, Department of Licenses and Inspections (via OpenDataPhilly, Carto SQL) | City of Philadelphia License (OpenDataPhilly listing, https://metadata.phila.gov/#help/help-faqs/what-are-the-terms-of-use/); only tract totals, counts and day quantiles are kept | [terms](https://opendataphilly.org/datasets/licenses-and-inspections-building-and-zoning-permits/) |
| Allegheny County Property Assessments | Allegheny County Office of Property Assessments (via the Western Pennsylvania Regional Data Center) | Creative Commons CC0 (public domain dedication) | [terms](https://data.wprdc.org/dataset/property-assessments) |
| Parcel Centroids in Allegheny County with Geographic Identifiers (March 2025) | Western Pennsylvania Regional Data Center | Creative Commons CC0 (public domain dedication) | [terms](https://data.wprdc.org/dataset/parcel-centroids-in-allegheny-county-with-geographic-identifiers) |
| Allegheny County Real Estate Sales | Allegheny County Office of Property Assessments (via the Western Pennsylvania Regional Data Center) | Creative Commons CC0 (public domain dedication) | [terms](https://data.wprdc.org/dataset/real-estate-sales) |
| City of Pittsburgh PLI Permits | City of Pittsburgh, Department of Permits, Licenses and Inspections (via WPRDC) | Creative Commons Attribution 4.0 (CC BY 4.0) | [terms](https://data.wprdc.org/dataset/pli-permits) |
| PHFA Low-Income Housing Tax Credit award lists | Pennsylvania Housing Finance Agency (PHFA) | Public information published by a Pennsylvania state agency; no licence stated | [terms](https://www.phfa.org/mhp/) |
| Census Geocoder (geographies, one-line address) | U.S. Census Bureau | Public domain (U.S. government service) | [terms](https://geocoding.geo.census.gov/geocoder/) |
| County comprehensive plans (citation list in assumptions/plans.yaml) | Pennsylvania county planning commissions | Public documents of each county; cited, not copied | [terms](https://dced.pa.gov/local-government/) |
| Ownership Profile of Single-Family Residence Properties in Philadelphia: A Focus on Large Corporate Investors (cited benchmark, not ingested) | Federal Reserve Bank of Philadelphia | Cited publication; one figure quoted with attribution, no data copied | [terms](https://www.philadelphiafed.org/community-development/housing-and-neighborhoods/ownership-profile-of-single-family-residence-properties-in-philadelphia-large-corporate-investors) |
| Single-Family Home Sales in Philadelphia (cited benchmark, not ingested) | The Pew Charitable Trusts | Cited publication; one figure quoted with attribution, no data copied | [terms](https://www.pew.org/en/research-and-analysis/reports/2024/07/single-family-home-sales-in-philadelphia) |
| Philadelphia Shooting Victims (Carto table shootings) | City of Philadelphia, Police Department (via OpenDataPhilly, Carto SQL) | City of Philadelphia License (OpenDataPhilly terms of use; free use of the data as is, the City keeps its rights in the database, no warranty, user holds the City harmless); only tract and county totals are kept | [terms](https://opendataphilly.org/datasets/shooting-victims/) |
| Philadelphia Crime Incidents, Part I and II (Carto table incidents_part1_part2) | City of Philadelphia, Police Department (via OpenDataPhilly, Carto SQL) | City of Philadelphia License (OpenDataPhilly terms of use; free use of the data as is, the City keeps its rights in the database, no warranty, user holds the City harmless); only tract and county totals are kept | [terms](https://opendataphilly.org/datasets/crime-incidents/) |
| Philadelphia Licenses and Inspections Code Violations (Carto table violations) | City of Philadelphia, Department of Licenses and Inspections (via OpenDataPhilly, Carto SQL) | City of Philadelphia License (OpenDataPhilly terms of use; free use of the data as is, the City keeps its rights in the database, no warranty, user holds the City harmless); only tract and county totals are kept | [terms](https://opendataphilly.org/datasets/licenses-and-inspections-code-violations/) |
| Philadelphia 311 Service and Information Requests (Carto table public_cases_fc) | City of Philadelphia, Philly311 (via OpenDataPhilly, Carto SQL) | City of Philadelphia License (OpenDataPhilly terms of use; free use of the data as is, the City keeps its rights in the database, no warranty, user holds the City harmless); only tract and county totals are kept | [terms](https://opendataphilly.org/datasets/311-service-and-information-requests/) |
| American Community Survey 5-year tables for the place-profile charts (2020-2024) | U.S. Census Bureau | Public domain (U.S. government work) | [terms](https://www.census.gov/programs-surveys/acs/data/summary-file.html) |
| Block equivalency files, 2022 state legislative districts (upper and lower chambers) | U.S. Census Bureau, Redistricting Data Program | Public domain (U.S. government work) | [terms](https://www.census.gov/programs-surveys/decennial-census/about/rdo/mapping-files.html) |
| Cartographic boundary files, state legislative districts, 2024, 1:500,000 | U.S. Census Bureau | Public domain (U.S. government work) | [terms](https://www.census.gov/geographies/mapping-files/time-series/geo/cartographic-boundary.html) |
| Housing Element Annual Progress Report (APR) data, Table A (housing development applications received) | California Department of Housing and Community Development (via the California Open Data Portal) | Portal listing states "No restrictions on public use" (rights field); licence field "not specified"; the state's open data handbook releases portal data in the public domain. Only aggregates are kept. | [terms](https://data.ca.gov/dataset/housing-element-annual-progress-report-apr-data-by-jurisdiction-and-year) |
| Housing Element Annual Progress Report (APR) data, Table A2 (entitled, permitted and completed units) | California Department of Housing and Community Development (via the California Open Data Portal) | Portal listing states "No restrictions on public use" (rights field); licence field "not specified"; the state's open data handbook releases portal data in the public domain. Only aggregates are kept. | [terms](https://data.ca.gov/dataset/housing-element-annual-progress-report-apr-data-by-jurisdiction-and-year) |
| Comprehensive Housing Affordability Strategy (CHAS) data, Tables 8, 12 and 15C | U.S. Department of Housing and Urban Development, Office of Policy Development and Research (special tabulation of ACS data by the Census Bureau) | Public domain (U.S. government work) | [terms](https://www.huduser.gov/portal/datasets/cp.html) |
| HUD income limits (Section 8), area median family income and 30%, 50% and 80% limits by household size | U.S. Department of Housing and Urban Development, Office of Policy Development and Research | Public domain (U.S. government work) | [terms](https://www.huduser.gov/portal/datasets/il.html) |
| HUD Multifamily Properties - Assisted (map service) | U.S. Department of Housing and Urban Development (HUD eGIS open data) | Public domain (U.S. government work) | [terms](https://hudgis-hud.opendata.arcgis.com/datasets/HUD::multifamily-properties-assisted) |
| HUD Public Housing Developments (map service) | U.S. Department of Housing and Urban Development (HUD eGIS open data) | Public domain (U.S. government work) | [terms](https://hudgis-hud.opendata.arcgis.com/datasets/HUD::public-housing-developments) |
| HUD Housing Choice Vouchers by Tract (map service) | U.S. Department of Housing and Urban Development (HUD eGIS open data) | Public domain (U.S. government work) | [terms](https://hudgis-hud.opendata.arcgis.com/datasets/HUD::housing-choice-vouchers-by-tract) |
| The Gap: A Shortage of Affordable Homes, 2026 (cited benchmark, not ingested) | National Low Income Housing Coalition | Cited publication; NLIHC terms allow linking and citing with credit, not copying or redistributing. A few state figures are quoted with attribution; no data are copied. | [terms](https://nlihc.org/terms-of-use) |
| Rental Housing Affordability Data Explorer (cited method reference, not ingested) | Federal Reserve Bank of Philadelphia | Cited publication; no download or reuse terms were found (site footer "All rights reserved"), so no figures are copied. | [terms](https://www.philadelphiafed.org/surveys-and-data/rental-housing-affordability-data-tool-methodology) |
| Philadelphia L&I permits map layer (ArcGIS FeatureServer PERMITS, layer 0), field list and counts only | City of Philadelphia, Department of Licenses and Inspections | City of Philadelphia License (OpenDataPhilly listing, https://metadata.phila.gov/#help/help-faqs/what-are-the-terms-of-use/) | [terms](https://opendataphilly.org/datasets/licenses-and-inspections-building-and-zoning-permits/) |
| Massachusetts ADU tracker and ADU survey composite (applications and approvals by municipality) | Massachusetts Executive Office of Housing and Livable Communities | Massachusetts state government publication; terms of use on mass.gov | [terms](https://www.mass.gov/massgov-terms-of-use) |
| Open States legislative data (discovery of state bills; not fetched) | Plural Policy (Open States project) | Open States terms make no copyright claim over the data and need no attribution; public domain dedication unless noted | [terms](https://open.pluralpolicy.com/tos/) |
| NYU Furman Center Land Use Reform Tracker (linked only, never fetched) | NYU Furman Center | No terms of use or data licence found; linked only | [terms](https://www.furmancenter.org/land-use-reform-tracker/) |
| AEI Housing Center state housing supply legislation tracker (linked only, never fetched) | American Enterprise Institute Housing Center | Terms page covers privacy only; reuse terms unclear; linked only | [terms](https://www.aei.org/about/terms-of-use/) |
| MBTA Communities (3A) District Atlas, districts layer (map service) | MassGIS for the Massachusetts Executive Office of Housing and Livable Communities (HLC) | "Data are public" (item licence); meant for policy analysis, research and public understanding of zoning. Not a legal record of permitted uses; HLC's compliance determination letter is the dispositive record | [terms](https://www.arcgis.com/home/item.html?id=8ba86e99e59d4f20a75e3860e46a8ed8) |
| MBTA Communities (3A) District Atlas, parcels layer with modeled unit capacity (map service) | MassGIS for the Massachusetts Executive Office of Housing and Livable Communities (HLC) | "Data are public" (item licence); meant for policy analysis, research and public understanding of zoning. Not a legal record of permitted uses; HLC's compliance determination letter is the dispositive record. Owner names and addresses are not read. Only tract and district sums enter the pack; parcel outlines stay in the local cache. | [terms](https://www.arcgis.com/home/item.html?id=8ba86e99e59d4f20a75e3860e46a8ed8) |
| MBTA Communities (3A) District Atlas, transit station half-mile radii (map service) | MassGIS for the Massachusetts Executive Office of Housing and Livable Communities (HLC) | "Data are public" (item licence). Used only as a check; nothing from it enters the pack. | [terms](https://www.arcgis.com/home/item.html?id=8ba86e99e59d4f20a75e3860e46a8ed8) |
| MassDEP estimated sewer system service area boundaries (POTW and non-POTW) and unmapped systems | Massachusetts Department of Environmental Protection, Water Utility Resilience Program (served by MassGIS) | Public state record. The item's terms are a data limitations disclaimer (estimates for broad planning; not for site- or parcel-specific analysis; use at the user's risk) and a requested citation: "MassDEP. Water Utility Resilience Program. 2025. Publicly-Owned Treatment Work and Non-Publicly-Owned Sewer Service Areas (PubV2025_10)." Redistribution is not addressed, so only per-tract shares of homes enter the pack; the polygons stay in the local cache. | [terms](https://www.arcgis.com/home/item.html?id=a2f07c0cf4a841f78ed74bda97b19cd5) |
| MBTA Communities compliance status sheet (CSV) | Massachusetts Executive Office of Housing and Livable Communities (HLC) | Public record posted for download by the Commonwealth; mass.gov terms of use apply | [terms](https://www.mass.gov/massgov-terms-of-use) |
| MBTA Communities community categories and capacity calculations (CSV) | Massachusetts Executive Office of Housing and Livable Communities (HLC) | Public record posted for download by the Commonwealth; mass.gov terms of use apply | [terms](https://www.mass.gov/massgov-terms-of-use) |
| 3A Development Tracker (CSV of developments HLC links to 3A zoning) | Massachusetts Executive Office of Housing and Livable Communities (HLC) | Public record posted for download by the Commonwealth; mass.gov terms of use apply | [terms](https://www.mass.gov/massgov-terms-of-use) |
| Massachusetts ADU survey composite, municipal file (CSV) | Massachusetts Executive Office of Housing and Livable Communities (HLC) | Public record posted for download by the Commonwealth; mass.gov terms of use apply | [terms](https://www.mass.gov/massgov-terms-of-use) |
| Massachusetts General Laws chapter 40A (Zoning), sections 1A, 3 and 3A | The General Court of the Commonwealth of Massachusetts | Public legislative record | [terms](https://malegislature.gov/StateHouse/TermsAndConditions) |
| Acts of 2024, chapter 150 (the Affordable Homes Act), session law text | The General Court of the Commonwealth of Massachusetts | Public legislative record | [terms](https://malegislature.gov/StateHouse/TermsAndConditions) |
| Building Permits Survey, place (permit office) annual files by census region | U.S. Census Bureau | Public domain (U.S. government work) | [terms](https://www.census.gov/about/policies/open-gov/open-data.html) |
| Montgomery County (PA) sewer service areas, July 2022 edition (via PASDA) | Montgomery County, Pennsylvania (Planning Commission); served by PASDA | Public county record distributed through PASDA, Pennsylvania's official open geospatial clearinghouse; the service metadata states no use constraint. Credited to Montgomery County. Only per-tract shares of homes are kept. | [terms](https://www.pasda.psu.edu/) |
| Adams County (PA) sewer service areas (existing), county GIS | Adams County, Pennsylvania (Office of Planning and Development GIS) | Public county record; the county's notice says the data are for information only and that any service area should be verified with the provider. No redistribution limit is stated. Only per-tract shares of homes are kept. | [terms](https://www.arcgis.com/home/item.html?id=3d16a7f1f8ba4d2ea79a8181bf8cab4a) |
| Philadelphia Water Department statement of citywide sewer service | Philadelphia Water Department | Public statement of a city agency; quoted briefly, nothing else is kept | [terms](https://water.phila.gov/about-pwd/) |
| City of Philadelphia permit fee schedules (L&I construction and zoning fee summaries, building permit fee page, Development Impact Tax page) | City of Philadelphia (Department of Licenses and Inspections; Department of Revenue) | Public fee schedules of the City of Philadelphia; amounts cited with short quotes, documents not copied | [terms](https://www.phila.gov/terms-of-use/) |
| Philadelphia Water Department Rates and Charges (effective 2026-09-01) and Regulations Chapter 5 (sewer connection fees) | City of Philadelphia, Water Department | Public tariff and regulations of the City of Philadelphia; amounts cited with short quotes | [terms](https://www.phila.gov/terms-of-use/) |
| Pittsburgh Water and Sewer Authority, Permits page (residential permit fee, typical tap and meter cost) | Pittsburgh Water and Sewer Authority | Public web page of a municipal authority; two amounts cited with short quotes | [terms](https://www.pgh2o.com/robots.txt) |
| Vermont known wastewater service areas in towns with zoning and subdivision rules (HOME Act map, 2023) | Vermont Agency of Commerce and Community Development (published by VCGI) | Public state record published by the Vermont Center for Geographic Information; no licence is stated on the item. Credited to Vermont ACCD. Only per-tract shares of homes are kept. | [terms](https://www.arcgis.com/home/item.html?id=34ed176dbe814ba2a011e4d34c9f7e23) |
| Vermont existing wastewater service areas, statewide compilation (ACCD open data, 1993-2008) | Vermont Agency of Commerce and Community Development, Department of Housing and Community Development (layer credit: Regional Planning Commissions and DHCD; published on the ANR map server) | Public state record; no licence or use constraint is stated on the layer, so only derived aggregates (shares of homes per tract or town) may be kept or published, never the polygons themselves. | [terms](https://anrmaps.vermont.gov/arcgis/rest/services/map_services/ACCD_OpenData/MapServer) |
| Pennsylvania public water supplier service areas (DEP, via PASDA) | Pennsylvania Department of Environmental Protection; served by PASDA | Public state record (credited to PA DEP) distributed through PASDA; no use constraint is stated. Only per-tract shares and provider names are kept. | [terms](https://www.pasda.psu.edu/) |
| Pennsylvania DEP Act 537 official plan status (plan age by municipality) | Pennsylvania Department of Environmental Protection | Public state record (PDF tables on DEP's website); no reuse limit stated | [terms](https://www.pa.gov/agencies/dep/programs-and-services/water/clean-water/wastewater-management/act-537-sewage-facilities-program/plan-status-map) |
| EPA sewersheds of the United States (version 1.3) and their endpoint treatment plants | U.S. Environmental Protection Agency (Office of Research and Development, Office of Water) | U.S. government work; the data and code are also released under the MIT licence | [terms](https://github.com/USEPA/Sewersheds) |
| EPA Clean Watersheds Needs Survey 2022, documented needs by county | U.S. Environmental Protection Agency (Office of Water) | Public domain (U.S. government work) | [terms](https://www.epa.gov/cwns) |
| FEMA National Flood Hazard Layer (flood hazard zones and NFHL availability), public map service | Federal Emergency Management Agency | Public domain (U.S. government work); FEMA's map service is keyless | [terms](https://www.fema.gov/flood-maps/national-flood-hazard-layer) |
| USGS Annual NLCD land cover and fractional impervious surface (Collection 1), MRLC coverage service | U.S. Geological Survey (Multi-Resolution Land Characteristics Consortium) | Public domain (U.S. government work) | [terms](https://www.mrlc.gov/data) |
| USGS 3D Elevation Program bare-earth elevation (3DEPElevation image service) | U.S. Geological Survey (The National Map) | Public domain (U.S. government work) | [terms](https://www.usgs.gov/3d-elevation-program) |
| Wyoming Statutes Titles 15 and 18 (city, town and county zoning) and session bill digests | Wyoming State Legislature | Public legislative record | [terms](https://wyoleg.gov/) |
| Vermont Acts 152 (S.325) and 179 (S.328) of 2026, as enacted, with their bill status pages | Vermont General Assembly | Public legislative record | [terms](https://legislature.vermont.gov/) |
| Philadelphia Zoning Base Districts, current layer (map service) | City of Philadelphia, Department of Planning and Development | City of Philadelphia License ("Public Use; Free"; the City reserves all rights in the database and gives no title to it; data as is, without warranty). Retrieval and analysis: allowed. Redistribution of the raw outlines: not granted, so they stay in the local cache. Derived counts and shares: published with attribution, to be confirmed with the City before release | [terms](https://opendataphilly.org/datasets/zoning-base-districts/) |
| Philadelphia Zoning Base Districts, dated snapshots (December 2021 used) | City of Philadelphia, Department of Planning and Development | City of Philadelphia License, as for the current layer: raw outlines kept locally; derived counts only in the pack | [terms](https://opendataphilly.org/datasets/zoning-base-districts/) |
| Philadelphia Zoning Overlays (map service) | City of Philadelphia, Department of Planning and Development | City of Philadelphia License, as for the base districts: raw outlines kept locally; derived counts only in the pack | [terms](https://opendataphilly.org/datasets/zoning-overlays/) |
| City of Pittsburgh zoning districts (map service "Zoning") | City of Pittsburgh, Department of City Planning | City of Pittsburgh licence notice: for informational purposes, no guarantee of completeness or accuracy, the user indemnifies the City; nothing on redistribution. Retrieval and analysis: allowed. Raw outlines: kept in the local cache. Derived counts and shares: published with attribution | [terms](https://www.arcgis.com/home/item.html?id=e67592c2904b497b83ccf876fced7979) |
| City of Pittsburgh Parking Reduction zoning overlay (map service) | City of Pittsburgh, Department of City Planning | City of Pittsburgh licence notice, as for the zoning districts | [terms](https://www.arcgis.com/home/item.html?id=1d7bccc801464eac951d21184f72c4d6) |
| TIGER/Line 2020 tabulation block outlines (sample counties only) | U.S. Census Bureau | Public domain (U.S. government work) | [terms](https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html) |
| [U.S. Department of Agriculture, National Agriculture Imagery Program (NAIP), Pennsylvania, 2022](https://naip-usdaonline.hub.arcgis.com/) | calibration file `adu_fit.yaml` | Public domain (U.S. government work) | - |
| [Microsoft, US Building Footprints](https://github.com/microsoft/USBuildingFootprints) | calibration file `adu_fit.yaml` | ODbL 1.0; the shares here are a produced work and are attributed | - |
| Parcel layers published by the City of Philadelphia, Allegheny County and four further Pennsylvania counties | calibration file `adu_fit.yaml` | Varies by county; several state no licence. Only aggregate shares are used. | - |
| Zoning ordinances of 221 Pennsylvania municipalities, accessory-dwelling sections, read from each municipality's published code | calibration file `adu_legal_share.yaml` | Cited only. No data from it is kept. | - |
| City of Pittsburgh, Ordinance 31 of 2026 (Bill 2025-1545), signed Oct 29, 2026: accessory dwelling units by right citywide | calibration file `adu_legal_share.yaml` | Cited only. No data from it is kept. | - |
| Philadelphia Code section 14-604(11), accessory dwelling units | calibration file `adu_legal_share.yaml` | Cited only. No data from it is kept. | - |
| [Pennsylvania Department of Community and Economic Development, Municipal Statistics, land-use ordinance fields (municipalities with no zoning ordinance)](https://apps.dced.pa.gov/Munstats-public/) | calibration file `adu_legal_share.yaml` | Cited only. No data from it is kept. | - |
| [U.S. Census Bureau, 2020 Census Redistricting Data (P.L. 94-171), population by municipality](https://www.census.gov/programs-surveys/decennial-census/about/rdo/summary-files.html) | calibration file `adu_legal_share.yaml` | Cited only. No data from it is kept. | - |
| [California Department of Housing and Community Development (via the California Open Data Portal), Housing Element Annual Progress Report (APR) data, Table A (housing development applications received)](https://data.ca.gov/dataset/81b0841f-2802-403e-b48e-2ef4b751f77c/resource/c78b769d-cc02-4050-91ef-79ded665b5a8/download/tablea.csv) | calibration file `adu_rate_plausibility.yaml` | Portal listing states "No restrictions on public use" (rights field); licence field "not specified"; the state's open data handbook releases portal data in the public domain. Only aggregates are kept. | - |
| [California Department of Housing and Community Development (via the California Open Data Portal), Housing Element Annual Progress Report (APR) data, Table A2 (entitled, permitted and completed units)](https://data.ca.gov/dataset/81b0841f-2802-403e-b48e-2ef4b751f77c/resource/fe505d9b-8c36-42ba-ba30-08bc4f34e022/download/tablea2.csv) | calibration file `adu_rate_plausibility.yaml` | Portal listing states "No restrictions on public use" (rights field); licence field "not specified"; the state's open data handbook releases portal data in the public domain. Only aggregates are kept. | - |
| [U.S. Census Bureau, American Community Survey 5-year tables for the place-profile charts (2020-2024)](https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/acsdt5y2024-b25024.dat) | calibration file `adu_rate_plausibility.yaml` | Public domain (U.S. government work) | - |
| [Massachusetts General Laws, chapter 40A, § 1A (definition of accessory dwelling unit) and § 3 (ADU paragraph)](https://malegislature.gov/Laws/GeneralLaws/PartI/TitleVII/Chapter40A/Section3) | calibration file `already_legal_ma.yaml` | Cited only. No data from it is kept. | - |
| [Acts of 2024, chapter 150 (the Affordable Homes Act), sections 7, 8 and 143](https://malegislature.gov/Laws/SessionLaws/Acts/2024/Chapter150) | calibration file `already_legal_ma.yaml` | Cited only. No data from it is kept. | - |
| [Vermont Statutes, Title 24, Chapter 117, § 4412(1)(D) (duplexes and multiunit dwellings) and § 4412(1)(E) (accessory dwelling units)](https://legislature.vermont.gov/statutes/section/24/117/04412) | calibration file `already_legal_vt.yaml` | Cited only. No data from it is kept. | - |
| [California Department of Housing and Community Development (via the California Open Data Portal), Housing Element Annual Progress Report (APR) data, Table A (housing development applications received)](https://data.ca.gov/dataset/81b0841f-2802-403e-b48e-2ef4b751f77c/resource/c78b769d-cc02-4050-91ef-79ded665b5a8/download/tablea.csv) | calibration file `apr_timing_donor_ca.yaml` | Portal listing states "No restrictions on public use" (rights field); licence field "not specified"; the state's open data handbook releases portal data in the public domain. Only aggregates are kept. | - |
| [California Department of Housing and Community Development (via the California Open Data Portal), Housing Element Annual Progress Report (APR) data, Table A2 (entitled, permitted and completed units)](https://data.ca.gov/dataset/81b0841f-2802-403e-b48e-2ef4b751f77c/resource/fe505d9b-8c36-42ba-ba30-08bc4f34e022/download/tablea2.csv) | calibration file `apr_timing_donor_ca.yaml` | Portal listing states "No restrictions on public use" (rights field); licence field "not specified"; the state's open data handbook releases portal data in the public domain. Only aggregates are kept. | - |
| [U.S. Census Bureau, American Community Survey 5-year tables for the place-profile charts (2020-2024)](https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/acsdt5y2024-b25024.dat) | calibration file `apr_timing_donor_ca.yaml` | Public domain (U.S. government work) | - |
| [U.S. Census Bureau, Building Permits Survey, county annual files](https://www2.census.gov/econ/bps/County/) | calibration file `baseline_calibration.yaml` | Public domain (U.S. government work) | - |
| [U.S. Census Bureau, Building Permits Survey, state annual files](https://www2.census.gov/econ/bps/State/) | calibration file `baseline_reconciliation.yaml` | Public domain (U.S. government work) | - |
| [U.S. Census Bureau, Building Permits Survey, state monthly year-to-date files](https://www2.census.gov/econ/bps/State/) | calibration file `baseline_reconciliation.yaml` | Public domain (U.S. government work) | - |
| [U.S. Census Bureau, Building Permits Survey, county annual files](https://www2.census.gov/econ/bps/County/) | calibration file `baseline_reliability.yaml` | Public domain (U.S. government work) | - |
| [U.S. Census Bureau, Building Permits Survey, county annual files, 1990-2025](https://www2.census.gov/econ/bps/County/co{year}a.txt) | calibration file `donor_pool_errors.yaml` | Public domain (U.S. government work) | - |
| [U.S. Census Bureau, Building Permits Survey, county annual files](https://www2.census.gov/econ/bps/County/) | calibration file `national_state_calibration.yaml` | Public domain (U.S. government work) | - |
| [U.S. Census Bureau, Building Permits Survey, county annual files](https://www2.census.gov/econ/bps/County/) | calibration file `nested_selection.yaml` | Public domain (U.S. government work) | - |
| [City of Philadelphia, Department of Licenses and Inspections, building and zoning permits (Carto table permits)](https://opendataphilly.org/datasets/licenses-and-inspections-building-and-zoning-permits/) | calibration file `permit_lag_phl.yaml` | City of Philadelphia open data licence (OpenDataPhilly); only counts and day quantiles are stored | - |
| [U.S. Census Bureau, Building Permits Survey, county annual files, 1990-2025](https://www2.census.gov/econ/bps/County/co{year}a.txt) | calibration file `state_holdouts.yaml` | Public domain (U.S. government work) | - |
| [U.S. Census Bureau, Building Permits Survey, county annual files](https://www2.census.gov/econ/bps/County/) | calibration file `validation_cards.yaml` | Public domain (U.S. government work) | - |
<!-- /generated: dataset_licences -->

The code licence (Apache-2.0) does not cover the data. Each dataset keeps its publisher's terms. No dataset is stored in this repository.
