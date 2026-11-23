# Module: `web/` — the browser interface

## What it is

The interface people use: one top bar, five screens and a slides view. It is plain HTML, CSS and
JavaScript modules. The screens use **htm** templates (HTML-like text inside JavaScript), so
nothing needs compiling; a build tool only bundles the files.

## What Vite is, and why

**Vite** (MIT) is a build tool for web pages. In development it serves `web/` and reloads the page
the moment a file changes. For release it **bundles**: it joins the screens, the stylesheets and
the libraries into a few compressed files in `web/dist/`, each named by a fingerprint of its
content so browsers can cache it safely.

Why it replaced the earlier copy-the-libraries script: libraries now install the standard way
(`npm ci` from the lockfile), the page loads in a few requests instead of dozens, MapLibre loads
only when the map is first shown, and the same build runs on a static host (Vercel) with no Python.

| Library | Version | Licence | Used for |
|---|---|---|---|
| preact | 11.2.0 | MIT | drawing the interface |
| htm | 3.1.1 | Apache-2.0 | HTML-like templates in plain JavaScript |
| @preact/signals (+ signals-core 1.14.5) | 2.11.4 | MIT | the app's state |
| maplibre-gl | 6.17.0 | BSD-3-Clause | the county map |
| vite (build only) | 7.3.8 | MIT | development server and bundler |

- `web/package.json` pins exact versions; `web/package-lock.json` records every package's address
  and SHA-512 integrity value. `npm ci` installs exactly that. Vite 7 is used, not 8: Vite 8 needs
  lightningcss, whose MPL-2.0 licence the project does not allow.
- `web/.npmrc` keeps new pins exact and the registry the public one.
- No plugins. MapLibre's worker file is copied into the build as is and named with
  `setWorkerUrl`, so it loads from the same site (the Content Security Policy allows only that).

## Three ways to run it

| Mode | Command | What happens |
|---|---|---|
| Local app | `make app` | `npm run build` writes `web/dist/`, then FastAPI serves it at http://localhost:8000 with the engine under `/api/v1`. Every scenario works. |
| Development | `make dev` | FastAPI on :8000 and Vite on http://127.0.0.1:5173 together. Vite passes `/api/...` to FastAPI. Edits show at once. Ctrl-C stops both. (Or two terminals: `uv run visionhousing serve`, and `cd web && npm run dev`.) |
| Static site | `make export-static` then `make site` | No server. The exporter saves the default results as JSON in `web/public/data/`; `npm run build:static` builds a site that reads them. This is what Vercel runs. |

If `web/dist/` has not been built, FastAPI still answers `/api/v1/...` and every page address
shows a short page saying to run `make app`.

### Where the front end gets data: `js/lib/api.js`

Every screen asks `api` (never `fetch` directly). The build sets which version it gets:

| Setting | Default | Meaning |
|---|---|---|
| `VITE_DATA_MODE` | `live` | `static` reads `/data/...` files instead of the back end (`npm run build:static` sets it) |
| `VITE_API_BASE` | `/api/v1` | where the live back end answers |

In static mode each call maps to a file written by the exporter:

| Call | Live address | Static file |
|---|---|---|
| states | `GET /states` | `data/states.json` |
| summary | `GET /states/ST/summary` | `data/ST/summary.json` |
| run | `POST /run` | `data/ST/runs/{baseline, adu, missing_middle, tod, parking, fees, package}.json`; for PA (and Testland) also `pa_hb2186`, `pa_sb1346` (`extra_runs` in the index). A reform alone is saved with its top drivers |
| compare | `POST /compare` | `data/ST/compare/{same keys}.json` |
| county | `GET /states/ST/counties/FIPS` | `data/ST/counties/FIPS.json` (the five-reform run, with `tract_results`) |
| map | `GET /states/ST/map?r=...` | `data/ST/maps/{run key}.json` |
| outlines | `GET /states/ST/outlines` | `data/ST/outlines.json` |
| evidence | `GET /evidence?reform=KEY` | `data/evidence.json` (filtered in the browser) |
| reportCard | `GET /report-card?state=ST` | `data/ST/report-card.json` |
| exportUrl | `GET /export/{csv,html}?s=..&r=..` | `data/ST/exports/{run key}.{csv,html}` |
| reforms | `GET /reforms`, `/reforms/KEY` | `data/reforms.json`, `data/reforms/KEY.json` |
| ledger, provenance | `GET /ledger`, `/provenance` | `data/ledger.json`, `data/ST/ledger.json`, `data/provenance.json`, `data/ST/provenance.json` |
| validation | `GET /validation?state=ST` | `data/ST/validation.json` |
| places | `GET /places/ST?level=county` | `data/ST/places/counties.json` (searched in the browser) |
| tracts | `GET /places/ST?level=tract&q=FIPS` | `data/ST/places/tracts-FIPS.json` (only with tract profiles) |
| profile | `GET /profile/ST/GEOID` | `data/ST/profiles/GEOID.json` |

`data/ST/index.json` lists what was exported. Only default assumptions (seed 0, default draws)
are saved. A scenario that was not exported raises a plain note: **"Changing assumptions needs the
local app (make app)."** for custom assumptions (also shown at the top of the screen), or "This mix
of reforms needs the local app" for a mix other than one reform alone or all five. A static build made before any export shows "The data for this site has not been exported
yet" instead of failing.

### The exporter

`visionhousing export-static --state PA --out web/public/data` (or `make export-static`) calls
the app in memory, so each file holds exactly the bytes the live interface sends. It prints a size
table. Options: `--tracts auto|yes|no` (tract profiles; `auto` keeps them only under
`--tract-budget-mb`, default 4), `--explain auto|yes|no` (each run's explanation; `auto` drops it
only if the folder would pass 30 MB), `--draws`, `--seed`. Exporting a second state adds to
`states.json`. The folder is committed for the static host, so it must stay under **30 MB** uncompressed (about a tenth of that once compressed by git and the host);
`tests/test_web_package.py` fails above that.

### Vercel

`vercel.json` (repository root) sets everything, so the Vercel project needs no build settings:
framework none, install `cd web && npm ci`, build `cd web && npm run build:static`, output
`web/dist`. It sends the same safety headers as FastAPI (Content Security Policy, nosniff, no
referrer, no framing, same-origin isolation, permissions policy), long caching for the
fingerprinted `assets/`, and revalidation for `data/`. The root `package.json` only pins Node
(22.12 or newer) for the host.

## Files

| Path | What it does |
|---|---|
| `index.html` | Page shell; Vite adds the bundled script and stylesheet links |
| `package.json`, `package-lock.json`, `.npmrc` | Exact package pins, the lockfile and npm settings |
| `vite.config.js` | Dev server (port 5173, `/api` passed to :8000), build into `dist/`, the `static` mode |
| `public/data/` | The static-site export (committed; see above). Copied into `dist/data/` by the build |
| `css/tokens.css` | Design tokens: colours, type scale, spacing, radius, shadow, focus ring, motion |
| `css/app.css` | Top bar, cards, popovers, screens, phone layout |
| `css/slides.css` | Slides view and its print rules (one slide per page) |
| `js/main.js` | Imports the stylesheets, mounts the app and picks the screen for the route |
| `js/lib/api.js` | The one data client: live back end or static files. A run that passes the back end's time limit (504) is asked for again a few times |
| `js/lib/data.js` | `useJson` / `useRun` / `useMap` hooks (with a small reply cache), `numberOf` (a run's "explain this number" payload) and the sources/assumptions lookups the Explain panel reads |
| `js/lib/exports.js` | CSV and brief downloads of the current scenario (made by the engine) |
| `js/lib/format.js` | Rounds raw API values that carry no text form (ledger, evidence, backtest) for display; never derives a number |
| `js/ui/county-panel.js` | The county panel: its numbers, each reform alone, the SVG tract map and tract table |
| `js/ui/static-note.js` | The static-site notes (not exported yet; custom assumptions need the local app) |
| `js/state/codec.js` | Reads and writes the scenario in the address (pure, no browser globals) |
| `js/state/store.js` | Signals for the route and scenario; navigation; the toast |
| `js/content/*.js` | Words only: reform names and groups by engine key (`adu`, `missing_middle`, `tod`, `parking`, `fees`, `pa_hb2186`, `pa_sb1346`), state names (plus Testland, `s=TL`), glossary, and the reader of the engine's slide spec |
| `js/ui/*.js` | Top bar and Export menu, popovers (glossary and Explain), map, small parts, icons |
| `js/screens/*.js` | State, Reform, Compare, Place, Method & data, Slides |
| `css/place-print.css` | US Letter portrait page for printing a place profile; added only while the Place screen is open |

## Routes and the scenario in the address

`#/state`, `#/reform/<id>`, `#/compare`, `#/place/<county or tract code>`, `#/method`, `#/slides`,
each followed by the scenario (the place code must be 5 or 11 digits, else it is dropped):

```
#/reform/adu?v=1&s=PA&r=adu,fees&a=adu.takeup~h&p=policymaker
```

| Key | Meaning | Rule |
|---|---|---|
| `v` | codec version | must be `1`; any other version falls back to the defaults |
| `s` | state | a known two-letter code |
| `r` | reforms on | known engine ids only, written in a fixed order, at most 8. The old id `middle` is read as `missing_middle`, so earlier links still work |
| `a` | assumption overrides | `ledger.id~l/c/h` or a plain decimal; at most 40; key ≤ 64 characters |
| `p` | preset | lower-case letters, digits and dashes, ≤ 24 characters |

The whole query may be at most 2,000 characters. Anything unknown or out of bounds is dropped,
never guessed. Switching a reform replaces the history entry; moving between screens adds one,
so Back works as people expect. **Share link** copies the address, which is the whole scenario.

## Interface rules it follows

- One top bar, same place on every screen. Left: the name. Centre: the four screens. Right:
  Slides, Export (Brief PDF, Slides PDF, Table CSV) and Share link. Under 768 px it folds into a
  labelled **Menu**, and the State screen's cards become one bottom sheet.
- One primary (filled) button per card. Every button shows its label; icons are decorative.
- **No typed numbers.** Every number is the engine's own text (`shown`) from the reply the screen
  loaded. The browser tests compare each screen with the API reply for the same scenario. While a
  reply loads, the panel says so in plain words; a value that is missing shows "—".
- **Explain this number.** Every number is a button (`Num`) that opens the engine's explanation:
  what it is, its likely range, formula, inputs, assumptions (with labels) and sources.
- Glossary words and **Explain** open popovers by click, Enter or Space (not hover-only); Esc
  closes them and returns focus.
- Keyboard: every control is reachable with Tab and has a visible focus ring. The Export menu
  moves with the arrow keys. The slides move with Left / Right, Home / End; Esc closes them.
- Colour: one accent (deep teal), cool greys, and one six-step sequential map ramp whose
  lightness rises steadily, so it reads in grey and for colour-blind readers. "No data" and
  "Not modelled" are grey. Text pairs meet WCAG AA (body text 16:1, quiet text 5.7:1, accent 6.2:1).
- Motion is short and is switched off when the reader asks for reduced motion.

## Slides view (decision D-22)

Each slide is drawn on a 960 × 540 logical frame that scales to the window. "Read as a page"
shows the same slides as text in one column. Printing (or **Export → Slides (PDF)**) gives one
slide per 13.333 × 7.5 inch page. The slides are the engine's own deck for the current scenario
(`explanation.slides` of `POST /run`): summary, one per reform, top drivers (a reform alone; the
view asks for the drivers then), the package waterfall and the limits. Every footer carries the
run's code, data and assumption fingerprints and the disclaimer.

## The map

MapLibre draws the county outlines (`GET /states/ST/outlines`) with no basemap and no tile
server. Each county is filled by its class from `GET /states/ST/map`: the homes the switched-on
reforms add there as a share of its own baseline (P10 / P50 / P90 of the engine's draws), put in
*Little effect* / *Adds some* / *Adds a lot* by the verdict thresholds of the Assumption Ledger.
The breaks never move with the scenario. Grey is no data; with no reform on, counties show the
"No reform on" fill. The **County table** button shows the same values as a sortable table (the
map's twin for keyboard and screen-reader users). Clicking a county, or its name in the table,
opens the county panel; its tract map is plain SVG drawn from the county reply's outlines, shaded
by `tract_results` with fixed breaks for that county. If the browser cannot draw WebGL, the page
says so and the table still works.

## How it is tested

- `tests/test_web_package.py` (no Node needed): every pin is exact and matches the lockfile, every
  lockfile entry comes from registry.npmjs.org with a SHA-512 value, every npm package passes the
  licence audit, Node is pinned, `vercel.json` builds from `web/` and sends exactly the FastAPI
  headers, `web/public/data` is under 30 MB, build output is git-ignored and the data is not, and
  every import is a file in `web/` or a locked package.
- `tests/test_export_static.py` (Testland): the saved files equal the live replies byte for byte,
  the index lists them, the tract budget and `--explain no` work, a rerun replaces the state, a
  folder that is not an earlier export is refused, and the command line works.
- `tests/e2e/test_shell.py`: builds `web/` with Vite into a temporary folder, serves it with the
  real FastAPI app on Testland, opens every route on desktop and phone sizes in Chromium, and fails
  on any console error or off-site request or sideways scroll on a phone. Consistency: the State
  card, the county table, the county panel's tracts, the Reform funnel, headline, drivers, limits,
  assumptions and evidence, the Compare bars, table and waterfall, the Method tables and the
  slides (titles, big ranges, fingerprints) each equal the API reply for the same scenario. It
  also checks a custom assumption ("Yours", the run changes, Reset all), the CSV and brief
  downloads, a keyboard-only path (switch a reform, open a county, go to Compare, explain a
  number), old `middle` links, the top bar by keyboard, slide keys, one-slide-per-page printing,
  Share link, the popovers, the Place screen and the codec (run in Node). The static tests build
  the static site, export Testland into it, serve it as plain files and check every screen (State
  for the baseline, one reform, one bill and all five; Reform; Compare; Method; Slides) against
  the exported files, and the "needs the local app" notes. Screenshots go to `nopush/logs/shots/`.

## Limits

- The static site holds only default-assumption results for the exported states: the baseline,
  each reform alone, each state bill alone and all five together. Custom assumptions, other mixes
  of reforms and other seeds need the local app; the screens say so.
- The tract map shades one reform alone; the combined package is shown per county only (the
  engine combines reforms at county level).
- The `p` preset is stored and shared but nothing reads it yet.
- Hindcasts of past reforms are not in the engine yet; Validation shows the baseline backtest.
- `make app` and the browser tests need Node 22.12 or newer (`make setup` runs `npm ci`).
