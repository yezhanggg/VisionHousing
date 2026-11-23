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
| `GET /states/{st}/counties/{fips}` | One county under the scenario in the query: facts, baseline, each reform's and the package's county record, tract ids, tract outlines |
| `GET /reforms` | Every runnable reform: key, title, family |
| `GET /reforms/{id}` | The reform's definition from `assumptions/scenarios.yaml` (Handbook wording, sources, notes) and every ledger parameter it uses. A scenario with no engine module yet comes back with `runnable: false` |
| `POST /run` | `Result.to_dict()` of one run, plus `explanation` and `explanation_status` |
| `POST /compare` | Without `scenarios`: each reform alone, the package and the total, from one run. With `scenarios` (up to 6, each with a `label`): each run on the same state, seed and draws |
| `GET /ledger` | The Assumption Ledger. Filters: `q` (search in id, label, unit, rationale, sources), `used_by`, `kind`, `status`, `state` (the state's calibrated ledger) |
| `GET /provenance` | One row per registered source: version, access date, licence, terms, cleaning, limits. With `state=`, also the pack's file list and hash |
| `GET /validation` | The baseline backtest skill table for `state` (default `TL`): each candidate rule's error and 80% interval coverage, and the rule chosen |
| `GET /export/{fmt}` | The scenario in the query as a file: `csv` (state and county records, every row stamped with the three hashes), `json` (the same as `POST /run`) or `html` (the print-ready brief from the report writer) |

### The run body (`POST /run`, `POST /compare`)

```json
{"state": "PA", "reforms": ["adu", "fees"], "overrides": {"fees.passthrough_k": "h"},
 "preset": "central", "seed": 0, "draws": 2000}
```

`reforms` may also be a mapping of key to options, for example `{"fees": {"view": "benchmark"}}`. `"explain": false` leaves out the explanation payload (quicker; `explanation_status` is then `skipped`). `"drivers": true` adds each reform's top drivers to the explanation. They come from the sensitivity tornado, which reruns the reform once per uncertain input, so they are off by default; the screen can ask for them in a second call, which reuses the cached run.

### The scenario in a query (county, export)

The front end's link format (`web/js/state/codec.js`): `s` state, `r` reforms (comma list), `a` overrides (`id~l,id~0.5`), plus `seed`, `draws` and `preset`. `v` must be `1` when given; `p` (a view preset such as a slide-deck preset) is checked and ignored. So a shared link and its export ask for the same run.

## Limits and errors

| Check | Answer |
|---|---|
| Body over 64 KB (declared or streamed) | 413 `body_too_large` |
| More than 8 reforms or 40 overrides; draws outside 1–4,000; a malformed id, value, state code or JSON; an unknown field | 400 `invalid_request` / `invalid_query` |
| An unknown reform, two reforms of the same family, an override the ledger refuses | 400, with the engine's own message |
| A state with no built pack; a county not in the state | 404 |
| A run past the time limit (120 s by default, `--timeout` or `VISIONHOUSING_RUN_TIMEOUT`) | 504 `run_timeout`. The run keeps going and its result is cached, so asking again later is quick |

Validation errors list where and what, never the input itself.

## Running the engine

`runner.py` holds a thread pool (2 workers), the time limit, and an in-memory LRU cache (32 results; least recently used dropped first). The cache key is the checked request plus the state's data, calibration and ledger fingerprints, so a rebuilt pack or an edited ledger is never served from an old result. Identical requests that arrive while a run is going share it. `ContextStore` loads each state's data once and reloads it when a file changes.

The engine is called through its one entry point, `engine.run`, with the loaded state context.

Measured on Pennsylvania through the interface (2,000 draws, no explanation, a laptop under load from other jobs): ADU + fees + parking about 6 s; missing middle alone about 10 s (about 19 s with the explanation); all four together about 50 s; a repeat from the cache, under 0.1 s. Times stretch several-fold when the machine is busy; the 504 message says so and the result is still cached when it finishes.

## Security

`security.py` wraps the app in two small ASGI layers:

- **Body limit**: refuses a body over 64 KB before the app reads it.
- **Headers on every response**: a Content Security Policy (scripts from this site only; framing banned with `frame-ancestors 'none'`; no plug-ins; `blob:` workers for the map library), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, and `Cache-Control: no-store` on the JSON interface. Any `server` header is dropped, and `visionhousing serve` starts uvicorn with its banner off.

`web/index.html` has one inline script, the import map. The policy allows it by its SHA-256 fingerprint, computed from the file when the app starts, so the policy follows the file if it changes. No other inline script can run.

CSV exports put a `'` in front of any text cell that starts with `=`, `+`, `-` or `@`, so a spreadsheet does not read it as a formula.

## Explanations and the HTML brief

`POST /run` and the JSON export carry `explanation` (the payload of `visionhousing.explain.explain(result)`: the headline sentence, verdicts, funnels, top drivers, the waterfall, limits and "explain this number" for every record, each number cited to its record) and `explanation_status`: `ok`, `skipped` (asked for none), `not built yet` (the layer is missing) or `failed` (the layer raised; the numbers still come back and the error is logged). The explanation is computed on the same thread pool, inside the same time limit, and cached with the run. The HTML export is the report writer's brief (`visionhousing.report.render(result, "html")`).

## Dependencies

FastAPI and uvicorn, core installs only. No `httpx` or `requests` (both pull in `certifi`, MPL-2.0). The tests call the app in memory through `tests/asgi_client.py`, a small ASGI caller written for this project.

## Tests

- `tests/test_api.py`: every endpoint on Testland, the cache, the time-limit error, `visionhousing serve`.
- `tests/test_api_security.py`: 400 on bad bodies and queries, 413 on a big body (declared and streamed), the headers on every response including errors, the import-map hash.
- `tests/test_api_consistency.py`: `POST /run` equals `engine.run(...).to_dict()` for the same inputs; compare, county, summary, validation and both exports carry the same records.

Run them with `make test` (or `uv run pytest tests/test_api*.py`).
