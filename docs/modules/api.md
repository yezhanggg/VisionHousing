# Module: Interface (`src/visionhousing/api/`)

**What it does.** Serves the tool on this computer. One FastAPI app serves the browser front end (`web/`) at `/` and a JSON interface under `/api/v1` that the screens call. Start it with `make app` or `visionhousing serve --port 8000`, then open http://localhost:8000.

**Why.** The screens must show the engine's numbers and nothing else. The interface adds no arithmetic: every number it returns is copied from an engine `Result`, the Assumption Ledger or the source registry.

## Terms

- **Endpoint.** One address the browser can ask, for example `GET /api/v1/states`.
- **ASGI.** The standard way a Python web server (here uvicorn) talks to a Python web app (here FastAPI).
- **Scenario.** A state, the reforms switched on, assumption overrides, a preset, a seed and a number of draws.
- **Preset.** `low`, `central` or `high`. It picks the baseline's housing cycle (`weak`, `typical`, `strong`), the engine's own scenario-level switch. Single assumptions are pinned with overrides instead.
- **Override.** A change to one ledger entry: a number (pins it), a range end (`low` / `central` / `high`, or the link codes `l` / `c` / `h`), or a mapping of `low` / `central` / `high` / `shape`.

## Endpoints

All under `/api/v1`. Errors are JSON: `{"error": code, "message": plain text}`.

| Method and address | Returns |
|---|---|
| `GET /states` | Testland (`TL`) plus every state with a built pack in `data/packs/` |
| `GET /states/{st}/summary` | State facts, the baseline (state and each county) at the default seed and draws, the runnable reforms, the three hashes |
| `GET /states/{st}/counties/{fips}` | One county under the scenario in the query: facts, baseline, each reform's and the package's county record, tract ids, tract outlines, and `tract_results`: the homes each tract-level reform (run alone) adds in every tract, with fixed class breaks for the county, and its map-funnel `steps` for the county's tracts (as in `tract-funnel`); `notes`: `backtest` (one sentence on how the baseline did for this county in the back-test) and `local_law` (plain words and citations when the county's ADU share comes from read local law, else `null`); `annual` (the baseline's and package's years 2027-2031 for the county) and, per reform, `annual`, `tracts_annual` (each tract's years, tract-level reforms) and `net_stock` (net added homes where homes are demolished); `as_of` |
| `GET /reforms` | Every runnable reform: key, title, family, kind (`benchmark`, `state`, `extra`; a screen keeps its benchmark defaults by `kind`) |
| `GET /scenarios` | Every scenario in `assumptions/scenarios.yaml` (baseline, benchmarks, bills, extra scenarios): id, title, kind, description, reforms, runnable, `run_with`, `excludes` (families it may not share a package with), variants, notes, sources ([scenario_extra.md](scenario_extra.md)) |
| `GET /reforms/{id}` | The reform's definition from `assumptions/scenarios.yaml` (Handbook wording, sources, notes) and every ledger parameter it uses. A scenario with no engine module yet comes back with `runnable: false` |
| `POST /run` | `Result.to_dict()` of one run (with `as_of`, `contract`, `annual` and each reform's `gross_net`, `net_stock_counties`, `annual`; see `engine.md`), plus `explanation` and `explanation_status` |
| `POST /compare` | Without `scenarios`: each reform alone, the package and the total, from one run. With `scenarios` (up to 6, each with a `label`): each run on the same state, seed and draws |
| `GET /ledger` | The Assumption Ledger. Filters: `q` (search in id, label, unit, rationale, sources), `used_by`, `kind`, `status`, `state` (the state's calibrated ledger) |
| `GET /provenance` | One row per registered source: version, access date, licence, terms, cleaning, limits. With `state=`, also the pack's file list and hash |
| `GET /validation` | The baseline backtest skill table for `state` (default `TL`): each candidate rule's error and 80% interval coverage, and the rule chosen; `by_place` adds the chosen rule's back-test error and range coverage by kind of place, each county's median signed error with its map class, and the sentences written from them; `contest` adds the baseline rule contest (every rule's scores, the choosing rule, the winner), the out-of-time test of the bias correction and place-type ranges by group (before and after), the surge-year check, the slump and boom alternative ranges, and their sentences; `bias_correction` (the keep-or-drop decision on the P50 correction with both versions' out-of-time error and coverage), `variants` (the contest on all years and on fully reported years only), `statewide` (each rule back-tested on state totals) and `origins` (independent five-year periods among the overlapping origins) (see `baseline.md`); `contest_view` (`views.contest_view`) lists every rule by its plain name on the contest's own measure, the typical miss either way, with its text, marks the rule the baseline uses and adds one sentence when that rule is not the lowest on that measure (or ties with another at the shown rounding), for the Method screen's left panel; `cards` lists the state's validation cards (the study, origins, target, geography and release behind each coverage count, with the interval score, signed error, error by size, non-overlapping subset and fold timeline; see [validation_cards.md](validation_cards.md)) |
| `GET /export/{fmt}` | The scenario in the query as a file: `csv` (state and county records, every row stamped with the three hashes), `json` (the same as `POST /run`), `html` (the print-ready brief from the report writer) or `hearing` (the two-page hearing brief, `report/hearing.py`, served inline to print; its style rules at `GET /export/hearing.css`) |
| `GET /states/{st}/map` | Each county under the scenario in the query (`r`, `a`, ...): its baseline, the homes the reforms add there, and that change as a percent of the county's own baseline (P10 / P50 / P90 of the engine's draws), each with its text form and formula, plus a map class (`little` / `some` / `a_lot` by the ledger's verdict thresholds; `none` with no reform; `nodata` with no baseline; `no_base` when the county's baseline is 0, its middle estimate or in at least half the draws, yet homes are added: then `change` is `null` and `no_base.text` reads "No baseline to compare; adds N homes", and `classes` lists `no_base` only when some county has it). Ids shared by every county are given once in `ids`. Each county also has its evidence `coverage` (three checks and a class) and `measure_class` (per 1,000 homes, homes added); `measures` lists those measures, and `decision` holds the decision view for a run of exactly one reform (`api/decision.py`) |
| `GET /states/{st}/tract-points` | Every tract's id (`geoids`, sorted), county (`county_index` into `counties`) and an interior point (`lon` / `lat`, 5 decimals): on the largest part of the tract's outline in the pack, the middle of the widest stretch inside it along a horizontal line between two vertex heights, so always inside the tract, unlike a centroid. Does not depend on the scenario; load it once |
| `GET /states/{st}/tracts` | Every tract under the scenario in the query (`r`, or `reforms`, plus `a`, ...), column by column in the same `geoids` order: `added` (P10 / P50 / P90 of the homes the scenario's tract-level reforms add, the package's tract-level part with overlap and interactions inside, plus `counties` and `total`, the same draws summed by county and over the state), `class` (each tract's P50 against fixed breaks from the ledger, `explain.tract_class_break_*`), `by_reform` (each tract-level reform's P50, run alone, and its county ranges), `funnels` (which reforms have a tract funnel), `county_only` (fees and parking work by county: their county values, never spread to tracts) and `scenario_total`. Each column says its formula, assumption ids and source ids; values are rounded (whole numbers from 100, one decimal below). The baseline has no tract split. Tract P50s do not add up to the county or state P50; use the summed values given |
| `GET /states/{st}/tract-funnel` | Each tract-level reform's map funnel, run alone: lots in scope, physically fit, financially feasible, expected permits (the module stages `eligible`, `fit` or `developable`, `feasible`, and `built`, `units` or `absorbed`; TOD's land steps are in acres), each with P10 / P50 / P90 per tract and per county (the county's tracts summed in each draw) and the module's state record. Ask with one reform: a package's funnel is each reform alone, with the same numbers |
| `GET /states/{st}/outlines` | The county outlines as GeoJSON (no tracts) |
| `GET /states/{st}/adu-budget-grid?target=&budget=&award=&accept=&seed=&draws=` | The ADU budget-and-target grid (`api/budget_grid.py`, `scenarios/adu_budget.py`): appropriations x award sizes x {law only, program only, both} on the same draws, each cell with added authorizations, net public cost, share of draws meeting each target and a Pareto flag; with `target`, the tested options within `budget` that meet the `accept` share, never interpolated, with the flags `input_missing`, `outside_tested_range`, `between_tested_values`, `budget_exceeded`, `no_tested_option_meets_both`. Read from the comparisons disk cache when `visionhousing adu-budget-grid --state ST` has kept it. The reply also holds `view` (`api/budget_view.py`): every cell's numbers as shown text keyed `arm|appropriation|award`, the tested values and accepted-share choices as text, the flag words, the key lever and the uncapped-variant note; the static site saves `{grid, view}` as `ST/adu-budget-grid.json` (`budget_view.write_static`) |
| `GET /states/{st}/law-changes?r=` | What one reform or bill (`adu`, `missing_middle`, `tod`, `parking`, `fees`, and in Pennsylvania `pa_hb2186`, `pa_sb1346`) changes in each county and each municipality with a law row: one of four answers (already allowed, allowed with a changed condition, newly allowed, unresolved; `outside` where a bill does not reach), the bill's or benchmark's citation and effective date, the denominator (single-family lots, residents, or homes near a major stop) and each answer's share of it, observed or fallback, whether the engine uses the county's readings, the model stage each answer changes, legal additional capacity (a low-to-high range) and the additional units authorized (the reform's own county records), and the municipal rows read (section, dates, the rule read). Statewide: each answer's share, the share outside, and the share of the added homes in unresolved places. See [Law changes](#law-changes) |
| `GET /states/{st}/law-changes/read-next?r=` | The top unread ordinances (the review-priority score) with bounding runs: statewide added homes (P50) if each is read as permissive and as restrictive, for the reform and one other option, whether the preferred option would switch, the county's reading coverage after the reading, and the review effort. Slow the first time (about 20 seconds for Pennsylvania), then from the comparisons disk cache |
| `GET /states/{st}/permit-years` | For the year slider: each county's homes authorized by building permits in every year from `permit_years.first_year` (`assumptions/decision.yaml`, 2000) to the pack's latest (the sum of the permit table's 1, 2, 3-4 and 5+ unit columns), the same per 1,000 existing homes (the pack's county housing units), its class by the fixed `permit_years.breaks` (`q1`..`q4`), whether the Census Bureau imputed it, text forms, and the state's total per year; `projected`: the baseline run's own yearly county table for 2027-2031 (P10 / P50 / P90, the rate on the same stock and class) and its yearly state records. Does not depend on the scenario |
| `GET /states/{st}/districts` | One chamber's state legislative districts under the scenario (`chamber=lower` default, or `upper`; `district=` for one): baseline, each reform and the package as P10 / P50 / P90 of the per-draw district sums, 2020 units, split shares and a `caveat` flag, the plans' vintage, a `state_check` and the district `brief` pointers, dressed for the screens with text forms, a headline, a map class, the caveat text, the counties touched and the limits (`api/districts.py`; method in [districts](districts.md)). `GET /states/{st}/district-outlines?chamber=` sends the outlines (Testland: its made-up districts as rectangles) |
| `GET /grades/{st}?r=&level=` | Evidence grades A to D for each reform's headline and the baseline, `level=state` or `county` (`county=` for one): letter, score, five components with their inputs, sentence and rule id (`api/grades.py`; method in [grades](grades.md)) |
| `GET /trace/{st}?r=&id=` | The step-by-step waterfall behind one headline: each funnel step's P50 and P10 to P90, the ratio applied, ledger ids and sources, a check against the reported number, and the reproduce receipt (`id`: a record id, or with one reform `state`, a county id or `counties`) |
| `GET /switch/{st}?r=&county=` | Each reform's top three drivers from the tornado at low / central / high, stored with the static runs when current, else worked out; plus each setting's likely range (state, and `county` if given) when the engine's switch runs are cached or exported (see [grades](grades.md)) |
| `GET /evidence?reform=` | The evidence library (`assumptions/evidence.yaml`): every entry, or one reform's |
| `GET /evidence/states?state=&verified=` | Cross-state comparison records (`assumptions/evidence/states/`) and the state by policy type completeness matrix; see [evidence_states.md](evidence_states.md) |
| `GET /report-card?state=` | The state's data report card (`visionhousing.data.report_card`); each input carries a `value_state` (a feed failure is never shown as zero) |
| `GET /intake/{st}` | The state's intake manifest (one row per data source, ledger entry, law row and comparison record, every blank field with its value state), the verified-exposure and transferred-take-up fractions, and the five-step `readiness` check; see [intake.md](intake.md) |
| `GET /reproduce?id=ST:run:path` | What reproduces one record: the command, seed, draws, reforms, overrides, the run's three hashes, the pack hash and the code version, read from the saved run (a state with no saved run is run once at the defaults); see [intake.md](intake.md) |
| `GET /plausibility/{st}?r=` | Each reform's rate (homes per 1,000 eligible lots per year; per 100,000 residents per year) from the run in the query, beside observed rates with their caveats, the observed band and a rule-written sentence; see [validation_plausibility.md](validation_plausibility.md) |
| `GET /confidence/{st}` | The two confidence signals, side by side and never merged: baseline performance (hold-out back-test of the donor-pool study) and reform evidence by reform type (grade, records, verified, with an observed result, in this state), plus the reform-type names (`api/cards.py`; `validation.donor_pool.confidence_signals` unchanged) |
| `GET /lever/{st}?r=&county=` | The counterfactual lever card for one reform: the binding factor and who can act (`api/decision.py`), each named variant's effect as the paired difference (variant minus benchmark, draw by draw, same seed and draws; also for `county`), the evidence grade and one next check. Variants and checks from `assumptions/cards.yaml`; runs through the cached runner; a real state answers only the default seed and draws |
| `GET /implementation/{st}?r=&geoid=&on=` | Implementation-status rows from the verified records of `assumptions/law/timeline.yaml`: for each provision covering the place (state, county, or municipality inside it) and the reform's housing types, the legal status, local step and permit step, each with its dates and sources, earlier versions as history; pending bills marked pending |
| `POST /answer` | The question box's answer, for scripts and tests (the browser answers in the page and never sends the question): body `{"question": text (1-500 characters), "context": {state, reforms, overrides, route, param, county}}`; the reply is `visionhousing.api.answers.resolve` read through the app's own addresses in memory (intent, status, title, lines, text, cited records, sources, run id, period, link, download, offers). Nothing is logged or kept. See [Question box](#question-box) |
| `GET /places/{st}` | Place search: counties (or tracts with `level=tract`) whose name or code contains `q`, sorted by name, at most `limit` (1-500, default 25) |
| `GET /profile/{st}/{geoid}` | The place profile of a county (5 digits) or tract (11 digits), exactly as `visionhousing.profile.build_profile` makes it. `format=json` (default), or `md`, `html`, `csv` as a download. See [profile.md](profile.md) |

### The run body (`POST /run`, `POST /compare`)

```json
{"state": "PA", "reforms": ["adu", "fees"], "overrides": {"fees.passthrough_k": "h"},
 "preset": "central", "seed": 0, "draws": 2000}
```

`reforms` may also be a mapping of key to options, for example `{"fees": {"view": "benchmark"}}`. `"explain": false` leaves out the explanation payload (quicker; `explanation_status` is then `skipped`). `"drivers": true` adds each reform's top drivers to the explanation. They come from the sensitivity tornado, which reruns the reform once per uncertain input, so they are off by default; the screen can ask for them in a second call, which reuses the cached run.
`"market": "choice"` or `"choice+capacity"` (also per scenario in `POST /compare`) adds the package under project choice and the shared capacity limit beside today's package, as a `market` block (`engine.market`, see [market.md](market.md)); without it the reply is unchanged. Another value is a 400.

`"capacity_screen": true` (also per scenario in `POST /compare`) adds every reform and the package screened for practical capacity (sewer, flood zone, steep slope) beside today's numbers, as a `capacity` block (`engine.capacity`, see [capacity.md](capacity.md)); without it the reply is unchanged.

### The scenario in a query (county, map, tracts, export)

The front end's link format (`web/js/state/codec.js`): `s` state, `r` reforms (comma list), `a` overrides (`id~l,id~0.5`), plus `seed`, `draws` and `preset`. `v` must be `1` when given; `p` (a view preset such as a slide-deck preset) is checked and ignored. So a shared link and its export ask for the same run.

## Limits and errors

| Check | Answer |
|---|---|
| Body over 64 KB (declared or streamed) | 413 `body_too_large` |
| More than 8 reforms or 40 overrides; draws outside 1–4,000; a malformed id, value, state code or JSON; an unknown field | 400 `invalid_request` / `invalid_query` |
| An unknown reform, two reforms of the same family, an override the ledger refuses | 400, with the engine's own message |
| A state with no built pack; a county not in the state | 404 |
| A real state's scenario GET address (`/states/{st}/map`, `/tracts`, `/tract-funnel`, `/counties/{fips}`, `/districts`, `/export/{fmt}`, `/grades`, `/trace`, `/switch`, `/plausibility`, `/lever`) with a seed other than 0 or draws other than the default (2,000) | 400 `default_sampling_only`. These addresses serve the default runs the screens use; another seed or draw count goes through `POST /run`. Testland, the small made-up state, accepts any seed and draws within the limits above. The static exporter (`export_static.py`), which asks for its own seed and draws, builds the app with `any_sampling=True` |
| Every worker busy and the waiting queue full (2 workers plus 4 waiting places by default) | 503 `busy` with a `Retry-After` header (15 s by default): wait that many seconds and ask again. Nothing is queued for a refused request |
| A run past the time limit (120 s by default, `--timeout` or `VISIONHOUSING_RUN_TIMEOUT`) | 504 `run_timeout`. If no one else is waiting for the same run, a run still in the queue is cancelled. A run already started cannot be stopped: it finishes, and asking again while it runs picks it up. Once it finishes with no one waiting, its result is kept only for a default scenario (default seed and draws, no overrides); a custom scenario's result is dropped |

Validation errors list where and what, never the input itself.

`views.py` builds the map, tract, tract-column, outline, evidence and report-card replies. It does no new
arithmetic of its own: the percent change uses the engine's own rule (`100 x added / baseline` in
each draw) and its `from_draws` summary; text forms come from the explanation layer's `show`.
Tested in `tests/test_api_views.py`.

`decision.py` builds the map reply's decision view: the binding constraint (the funnel stage that loses the largest share of the step before it: the share lost is worked out in each draw from the funnel's per-draw step totals and summarised as P10 / P50 / P90, never as a ratio of two percentiles; the middle ranks the stages; with who acts there and the next question), each county's evidence coverage (local data, verified local rules read, back-test; *Good* / *Partial* / *Thin*), the classes of the map's other measures by fixed breaks, and, for one reform, the question, the yearly answer, the top county and the coverage counts. The words, actors, ids and breaks live in `assumptions/decision.yaml`. Tested in `tests/test_api_decision.py`.

## Running the engine

`runner.py` holds a thread pool (2 workers) with a small waiting queue (4 places), the time limit, and an in-memory LRU cache (least recently used entries dropped first). The work queue is bounded so that a stream of requests (say, 40 different seeds) cannot pile up runs or results: past 2 running plus 4 waiting jobs, a new job is refused at once with 503.

The cache has two parts, because one finished engine result can be very large (a Testland run of all four reforms at 2,000 draws holds about 27 MB of draws; a Pennsylvania run, several gigabytes):

- **Rendered replies** (the JSON or file bytes an address sends) are kept up to a byte budget, 256 MB by default. A reply larger than the budget is not kept.
- **Whole engine results** are kept up to a **result budget** of estimated bytes, 3,072 MB (3 GB) by default, least recently used first out. A result's size is estimated by `estimate_nbytes`: it walks the result's fields, mappings and lists and adds up the bytes of every numpy array and pandas table it reaches, each once (a few milliseconds; the state's loaded data and the ledger are shared by every result and not counted). A result larger than the whole budget is **not kept at all**; only its rendered replies are. Inside the budget, at most 4 results are kept, and at most 1 of them may be a custom scenario (another seed, draw count or assumption overrides). Default scenarios are kept first.

A result that was dropped is not slimmed or kept in part: when an address needs it again (a county panel, the district, grade, trace or lever cards all read per-draw arrays), the run is simply made again, under the same queue and time limits. Most addresses never need that: **every GET looks up its rendered reply before it asks for the run** (summary, county, map, tracts, tract funnel, permit years, the exports, and `POST /run` too), so a reply made once is served from the reply cache even after its whole result is gone.

A **memory watchdog** (`MemoryWatchdog`) is the last line of defence. While the server runs, a background thread reads the process's resident memory (RSS: the part of its memory held in RAM) every 30 seconds with `ps -o rss= -p <pid>` (the standard library only reports the peak, which never falls). Past the limit, 6,000 MB by default, it empties both caches, asks Python to free what is unused, and logs a warning with the memory before and after. It does nothing when the caches are already empty. After a clear it is disarmed until memory falls below 90% of the limit; until then it clears again only after a back-off wait (60 seconds, doubling to at most 30 minutes), so it does not empty the caches at every check while Python holds on to freed memory. Results a request is still using are freed when that request ends.

The limits are server settings, not model assumptions; change them with `VISIONHOUSING_RUN_QUEUE` (waiting places), `VISIONHOUSING_CACHE_MB` (reply byte budget), `VISIONHOUSING_RESULT_MB` (whole-result budget), `VISIONHOUSING_RSS_MB` (the watchdog's limit; `0` switches it off) and `VISIONHOUSING_RETRY_AFTER` (seconds in `Retry-After`). On a machine with 18 GB of RAM the defaults keep the server's caches near 3.3 GB and empty them if the process passes about 6 GB. Raise `VISIONHOUSING_RESULT_MB` above one Pennsylvania result's size if county panels should not rerun the package on a machine with more memory.

The static exporter builds its app with one whole result held at a time and no byte limit on it (`cache_size=1`, `result_mb=inf`): it asks for each run's replies one after another, so one result is all it needs, and it never holds more than one.

The cache key is the checked request plus the state's data, calibration and ledger fingerprints, so a rebuilt pack or an edited ledger is never served from an old result. Addresses that read other files add their stamp to the key: the district reply adds the district table's change time, and the grade and intake replies add a fingerprint of every file in the state's folder and every assumption file (path, size and change time). A rebuilt district table or an edited intake setting is never served from an old reply. Identical requests that arrive while a run is going share it. `ContextStore` loads each state's data once and reloads it when a file changes.

The engine is called through its one entry point, `engine.run`, with the loaded state context.

**Comparisons disk cache.** The comparisons parts (`interactions`, `dials`, `matrix`, `ablation`, `ranking`, and the opt-in `market` and `tod_factorial`) take about one to four minutes each for Pennsylvania, and the caches above live in memory only. `comparison_cache.py` also keeps each finished part on disk, one gzip-compressed JSON file per part, at `data/cache/comparisons/<ST>/<fingerprint>/<part>.json.gz`, and serves it again after a restart. A part is read back only when its key matches: a SHA-256 fingerprint of the state's data pack, calibration and ledger fingerprints, the code fingerprint (every `.py` file of the package), an assumptions stamp (the bytes of every `assumptions/**/*.yaml` file), the seed, the draws, and the part's own options (its name; the address offers no overrides). Change any of them and the old file is simply never read. Parts missing from disk are computed together on one bench and each is written.

- **Safe writes**: each file is written under a temporary `.part` name in the same folder, then renamed in one step, so a reader never sees half a file.
- **Bounded size**: after each write, files are removed oldest-used first (a read refreshes a file's time) until the folder is under its budget, 1,024 MB by default (`VISIONHOUSING_COMPARISONS_DISK_MB`). A part larger than the whole budget is not kept.
- **Missing drive**: `data/cache` may be a link to another drive. If it is not there, the disk cache switches itself off with one logged warning and parts are computed as before; a read or write error does the same for that one file. It never stops a reply.
- **Settings**: `VISIONHOUSING_COMPARISONS_DIR` moves the folder (`none` switches the disk cache off; the test suite does this, except in the cache's own tests).
- **Warming**: `visionhousing comparisons --warm --state PA` computes every part, `market` included, on one shared bench and keeps each on disk (parts already there are skipped), so a demo server starts warm. `--part`, `--seed` and `--draws` choose what to warm; the server's default is seed 0 and 2,000 draws.

Tested in `tests/test_comparisons_cache.py`: the key changes when any input changes; a disk hit computes nothing (also through the interface, after a restart); a stale or damaged file is recomputed; a missing drive falls back with one warning and writes nothing; a failed rename leaves the old file and no `.part` file; the budget removes the oldest-used files first; `--warm` keeps parts equal to what a request computes.

Measured on Pennsylvania through the interface (2,000 draws, no explanation, a laptop under load from other jobs): ADU + fees + parking about 6 s; missing middle alone about 10 s (about 19 s with the explanation); all four together about 50 s; a repeat from the cache, under 0.1 s. Times stretch several-fold when the machine is busy; the 504 message says so. A default run that passes the time limit is still cached when it finishes.

## Security

`security.py` wraps the app in two small ASGI layers:

- **Body limit**: refuses a body over 64 KB before the app reads it.
- **Headers on every response**: a Content Security Policy (scripts from this site only; framing banned with `frame-ancestors 'none'`; no plug-ins; `blob:` workers for the map library; data and images from this site plus the optional basemap host `https://tiles.openfreemap.org`), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, and `Cache-Control: no-store` on the JSON interface. Any `server` header is dropped, and `visionhousing serve` starts uvicorn with its banner off.

`web/index.html` has one inline script, the import map. The policy allows it by its SHA-256 fingerprint, computed from the file when the app starts, so the policy follows the file if it changes. No other inline script can run.

CSV exports put a `'` in front of any text cell that starts with `=`, `+`, `-` or `@`, so a spreadsheet does not read it as a formula.

The import-map fingerprint is found with Python's own HTML parser (`html.parser`), not a regular expression, so script tags are read the way a browser reads them (any letter case, `</script >`). It only fingerprints our own built page; the app never cleans or filters HTML that users send.

## Input validation

Some request values become part of a file path: the state code (path or query), and inside the code the saved-run name. A crafted value such as `../../etc/passwd` must never reach a file outside `data/packs/`, `tests/fixtures/testland/` or `web/public/data/` ("path traversal"). One small module, `src/visionhousing/paths.py`, builds every such path:

- `state_code(value)` accepts exactly two letters and returns them in capitals. Anything else raises `ValueError`.
- `safe_child(base, *parts)` joins a trusted base folder with plain names only: letters, digits, `_`, `-` and `.`, not starting with a dot, no `..`, no `/` or `\`, no NUL byte, not absolute, at most 128 characters. It then normalises the joined path and checks that it lies inside the base folder, both as written and with links resolved. A failure raises `ValueError`.

The interface turns that `ValueError` into a 400 reply (`invalid_state`) before any file is opened. Place ids (`geoid`, `fips`) must be 5 or 11 digits, and record ids (`/reproduce?id=`) are checked part by part, also with a 400. `ContextStore.folder`, the place-profile loader and the stored-run readers (`grades/switch.py`, `intake/reproduce.py`, `intake/readiness.py`) all go through `safe_child`. Static files are served by Starlette's `StaticFiles`, which refuses paths outside `web/dist/`.

`tests/test_paths.py` sends traversal attempts (`../`, `%2e%2e`, absolute paths, backslashes, NUL bytes, drive letters) to every address that takes a state, place or id, in both raw and decoded spelling, and checks each gets a 400 (or a 404 when the value holds a `/` and so matches no address) and never shows a file from outside the data folders.

## Explanations and the HTML brief

`POST /run` and the JSON export carry `explanation` (the payload of `visionhousing.explain.explain(result)`: the headline sentence, verdicts, funnels, top drivers, the waterfall, limits and "explain this number" for every record, each number cited to its record) and `explanation_status`: `ok`, `skipped` (asked for none), `not built yet` (the layer is missing) or `failed` (the layer raised; the numbers still come back and the error is logged). The explanation is computed on the same thread pool, inside the same time limit, and cached with the run. The HTML export is the report writer's brief (`visionhousing.report.render(result, "html")`).

## Dependencies

FastAPI and uvicorn, core installs only. No `httpx` or `requests` (both pull in `certifi`, MPL-2.0). The tests call the app in memory through `tests/asgi_client.py`, a small ASGI caller written for this project.

## Tests

- `tests/test_api.py`: every endpoint on Testland, the cache, the time-limit error, `visionhousing serve`.
- `tests/test_api_security.py`: 400 on bad bodies and queries, 413 on a big body (declared and streamed), the headers on every response including errors, the import-map hash.
- `tests/test_paths.py`: the path helper on its own, and traversal attempts against every address that takes a state, place or id (see "Input validation").
- `tests/test_api_runner_memory.py`: memory safety. A fake result larger than the result budget is not kept, whole results are evicted by bytes, the watchdog empties the caches past its limit (once by hand, once from its thread), a cached reply is served with no engine run after its whole result is gone, and a real state's scenario GET addresses refuse another seed or draw count (the exporter's flag lets it through).
- `tests/test_api_runner.py`: load safety. 40 distinct seeds: the queue never holds more than its places, extra jobs get 503 with `Retry-After`, real-state GET addresses answer 400, the caches stay inside their limits. A queued job whose caller timed out never runs, an abandoned custom result is dropped, asking again picks up a running job, and a rebuilt district table changes the cache key.
- `tests/test_api_consistency.py`: `POST /run` equals `engine.run(...).to_dict()` for the same inputs; compare, county, summary, validation and both exports carry the same records.

Run them with `make test` (or `uv run pytest tests/test_api*.py`).


## Readouts in the replies

- `POST /run`: each `reform_results.<key>` carries `readouts`, `readout_counties` and `readout_texts` (see [engine.md](engine.md)); the `explanation` carries `readouts`, `narrowing` and `headline.frequency` (see [explain.md](explain.md)).
- `POST /compare`: each reform's `readouts` (state level) and `readout_texts`.
- `GET /states/ST/counties/FIPS`: each reform's `readouts` for that county (share of baseline; revenue given up and cost per added home for the fee cut; the builders' saving for parking).
- `GET /export/csv`: readout rows, with scope `state readout` or `county readout`, after each reform's own rows.

The static export saves these replies as they are, so the static site carries them too.

## Question box

`api/answers.py` answers a plain question from the records of one run, with no language model.

1. **Match.** The question is lower-cased and stripped of punctuation. State and reform words are
   picked out first (`states`, `reforms`, `package` in `assumptions/answers.yaml`). Each intent
   (share, address, law, target, coverage, drivers, baseline, result, outside) scores one point
   per listed word or pattern it finds in what is left; the highest score wins and the file's
   order breaks a tie. No point: "unsupported".
2. **Read.** Each intent reads a fixed set of records through a *reader*: the run's
   `explanation.numbers` (result, share, baseline), the drivers (drivers), `GET /validation` and
   `GET /states/ST/permit-years` (baseline, including the baseline card and its diagnostics when
   present), `GET /implementation` (law, address), the run's reform notes and `GET /report-card`
   (coverage), and the policy dials' precomputed target rows (target). A target question with a
   money word ("budget", "$20 million", "program") about ADUs reads the budget-and-target grid
   instead (`budget_grid`: `GET /states/ST/adu-budget-grid`, static `ST/adu-budget-grid.json`):
   the tested options on the Pareto set for that target within any budget named
   (`grid_lookup`, never interpolated; a target between two tested ones gets both tested cells),
   each with its share of modeled draws, Monte Carlo standard error, net cost and range, all the
   view's text. With no grid in the build it falls back to the dial rows and says the grid is
   missing. `StaticReader` reads the
   static site's files exactly as `web/js/lib/api.js` does; `LiveReader` asks the interface's own
   addresses. Anything the build does not hold raises `UnavailableError` and the answer says "not
   in this run"; a custom scenario on the static site is never answered with the default run.
3. **Fill.** A template from the YAML is filled with text copied from those records: shown values
   (already rounded by the engine), names, units, the period, dates and sources. The resolver
   does no arithmetic. A result whose range is zero and whose reform notes say the zero is a data
   gap is answered as coverage (with the next missing observation from the report card), never
   as a number.

Every answer lists its cited `records` (id, name, unit, shown value, formula, source and
assumption ids), its `sources` (citation and address), the `run_id`
(`ST/run key/code.data.assumptions hashes/seed-N/draws-N`), the `period`, a canonical `link` in
the front end's address format and, when the build has one, the run's CSV `download`. When an answer uses a reform's transit status (the run's
`status_views`: a hidden statewide range, a partial footprint, or the package note), it cites
that view as a record of kind `status` (`run:status_views.KEY`: the status text, the
completeness label, the rule, and its source and ledger ids); with the range hidden, that is the
only record cited, so no range is cited that the answer does not show.

The browser runs the twin resolver `web/js/lib/answers.js` over the same records (live or
static) with the table copied to `web/js/content/answers.json` (`uv run python -m
visionhousing.api.answers` writes it). `tests/test_answers.py` checks the copy, the gold questions
and that Python and the browser give the same answers.

## Law changes

`api/law_changes.py` (settings in `assumptions/law_changes.yaml`) answers "which existing rule is this bill changing?" for every county and every municipality with a law row.

- **Four answers.** For the ADU reform and bills: the law layer's categories (`law.coverage.adu_category`): `already_permitted` is already allowed, `by_right_conditions` and `discretionary` are a changed condition, `prohibited` is newly allowed, `unknown` is unresolved. Missing middle compares today's by-right homes in the single-family district with the reform's top count (`law.rights.legal_increment`): one home today is newly allowed, more than one but fewer than the top is a changed condition, the top or more is already allowed. Parking: a minimum of zero is already allowed, any minimum above zero a changed condition. Fees and transit density: no reading answers the question yet, so every place is unresolved and the engine uses its ledger fallback. A row counts only when the law layer reads it (verified and in force on the as-of date); anything else is unresolved, never "no".
- **Outside a bill.** A bill's `scope` (`state_law_provisions`) sets who it reaches: `mpc` leaves out the places listed in `outside_mpc` (Philadelphia and Pittsburgh), `*_zoned` leaves out places with no zoning. Those are `outside`, reported apart, and left out of the denominator. The engine's bill run takes out only the places its scenario names; its numbers for places with no zoning still use the statewide already-legal share (the card says which applies).
- **County answer.** The answer holding the largest share of the county's denominator; ties go to unresolved first (`answer_tie_order`).
- **Capacity.** Low end: lots with a known gain x the added homes per lot (`adu.units_per_lot`; for missing middle `missing_middle.max_units` less today's count). High end: plus every unresolved lot at the full gain. Parking, fees and transit density add no legal homes per lot and have none.
- **Units authorized.** The engine's county records of the reform run alone (default seed and draws), unchanged. The share of the added homes in unresolved places is worked out in each draw (the sum over counties of the county's added homes x its unresolved share, over the state's added homes in that draw) and reported as the P50 (`value`) with `p10` and `p90`; percentiles are never added or divided.
- **Read next.** `review_priorities` ranks the unread review units; the top `read_next.top` (5) with a positive score are each set once to a permissive reading and once to a restrictive one (the rows in `read_next.treatments`, counted as verified and in force), the local law is rebuilt with the law layer's own `local_law_for_engine`, and the engine runs on a copy of the context (`dataclasses.replace(ctx, local_law=...)`, uncached because the copy keeps the data hashes) on the same seed and draws. Each bound's `change` is worked out in each draw (statewide added homes with the reading minus with today's readings, same draw) and reported as its P50 (`value`) with `p10` and `p90`; `impact` runs from the lower to the higher of the two bounds' change P50s. A reading changes a number only when it puts the county over the 50% bar or the county already passes it; the reply says so. The preferred option is the one adding more homes statewide (P50). Only reforms whose county values come from readings (ADU family, parking) are bounded.
- **Static site.** `law_changes.write_static(ctx, out, results, tables)` writes `<ST>/law_changes/<KEY>.json` (`{changes, read_next}`, read-next only when already cached). The exporter has not called it yet; Pennsylvania's seven files are about 0.21 MB gzip (3.7 MB raw). Without the file the screen says it needs the local app.
- **Command.** `python -m visionhousing.api.law_changes --state PA [--estimate] [--read-next] [--out DIR]` prints the sizes, fills the read-next cache, or writes the static files.

Tests: `tests/test_law_changes.py` (shares add up to 100% per denominator, unknown stays unknown, bill scope, missing-middle increment, capacity, every number's formula and ids, read-next bounds and the switch, the addresses on Testland, the static file and its size) and `tests/e2e/test_law_changes_ui.py`.


## Budget-and-target text (`api/budget_view.py`)

`GET /states/{st}/adu-budget-grid` returns the engine's grid (`scenarios/adu_budget.py`) and
`view`, which writes every number of every cell as text once, on the server, so the panel and
the question box never format a number themselves:

- each cell gets a key `arm|appropriation|award` (whole numbers, the browser's `cellKey` writes the
  same), its words (`assumptions/cards.yaml`, `budget_target`), and the shown text of the added
  homes, comparable homes, authorizations taken from later years (with what a negative value
  means), net public cost, owners funded, awards to owners building anyway, unused money, cost per
  added comparable home and the program's addition on top of the law;
- for each tested target: the share of modeled draws meeting it (`share_percent`, never finer
  than the draws support), its Monte Carlo standard error in percentage points, the count of
  draws, the Pareto flag and whether the share rises with the appropriation;
- the key lever: law only = the law change; program only = the program money; both = whichever of
  "law alone" and "the program on top of it" has the larger middle (the cell's own records);
- the tested targets, budgets and awards, the accepted-share choices, the flag words, the
  reference records (benchmark, law only with the hazard method, method difference) and the note
  that the original financing variant has no appropriation limit.

`write_static(grid, out/ST)` saves `{grid, view}` as `ST/adu-budget-grid.json` and sets
`adu_budget_grid: true` in `ST/index.json`. Pennsylvania (2026-11-25): 539 KB raw, 38 KB gzip (grid 0.36 MB, view
0.19 MB). Tests: `tests/test_budget_target.py` and `tests/e2e/test_budget_target_ui.py`.

## Transit status as text (`api/status_view.py`)

`POST /run` (and every saved run of the static site) carries `status_views`: for each reform
whose result has a transit status (`features/transit_status.py`; today transit-oriented density
on a state with a stop table), the words the screens, the hearing brief and the question box
show: whether the statewide range is shown (`status.statewide.shown`), the completeness label,
the measured contribution (the explanation's own shown middle) when it is not, the package note,
the rule (`tod.status_min_measured_bus_share`), and the ladder for the state and every county
(each answer's word and display kind; "unknown" = not measured, never shown like "measured:
none"). Nothing is computed there. Testland's runs carry `{}`. See `docs/modules/web.md`
("Transit status").

## Baseline card rows (`api/baseline_view.py`)

`baseline_card` in `GET /states/{st}/summary` and `GET /validation` is the B1 card
(`baseline.card.card_for`) plus `view`: the rows the screens and the hearing brief show, with
every number already written out on the server, each row with its formula, source ids and
assumption ids. Rows, in order: historical years, permit columns, data release, information
cutoff, projected years and the 2026 gap, the 2026 nowcast (diagnostic only), any window
sensitivity (`baseline_pa_window_2022_2025` for Pennsylvania: disclosed, never subtracted) and
the state reconciliation. `view.reconciliation.matched` is true only when the card's state
component equals the run's baseline middle value to the unit (false says "does NOT match"; a
control state such as Vermont has `null`). `python -m visionhousing.api.baseline_view --data
web/public/data` writes `ST/baseline-card.json` for each exported state from its saved summary
(no engine run). Tests: `tests/test_hearing_brief.py`.
