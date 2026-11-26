# Module: `web/` — the browser interface

## What it is

The interface people use: one full-screen map with floating panels, five modes, an intro page
and a slides view. It is plain HTML, CSS and
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
  `setWorkerUrl`, so it loads from the same site. The Content Security Policy allows this site
  plus one other host, the optional basemap (`https://tiles.openfreemap.org`, in `connect-src`
  and `img-src` only).

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
| tractPoints | `GET /states/ST/tract-points` | `data/ST/tract-points.json` |
| stateTracts | `GET /states/ST/tracts?r=...` | `data/ST/tracts/{run key}.json` |
| tractFunnel | `GET /states/ST/tract-funnel?r=KEY` | `data/ST/tract-funnel/KEY.json` (each saved tract-level reform alone; `tract_funnels` in the index) |
| permitYears | `GET /states/ST/permit-years` | `data/ST/permit-years.json` (`permit_years` in the index) |
| districtOutlines | `GET /states/ST/district-outlines?chamber=lower` | none (the old probe; the layer uses `districtShapes`) |
| districtShapes (`districtShapes`) | `GET /states/ST/district-outlines?chamber=lower\|upper` | `data/ST/districts/outlines-{lower,upper}.json`, only when the index says `districts: true`; otherwise "The district view needs the local app (make app)." |
| districtsReply (`districtsReply`) | `GET /states/ST/districts?chamber=&r=...` | `data/ST/districts/{lower,upper}/{run key}.json` (default assumptions only; same index flag) |
| outlines | `GET /states/ST/outlines` | `data/ST/outlines.json` |
| evidence | `GET /evidence?reform=KEY` | `data/evidence.json` (filtered in the browser) |
| reportCard | `GET /report-card?state=ST` | `data/ST/report-card.json` |
| comparisons | `GET /states/ST/comparisons?part=P` | `data/ST/comparisons/P.json` for `interactions`, `dials`, `matrix`, `ablation`, `ranking` (`comparisons: true` in the index) |
| confidenceSignals | `GET /confidence/ST` | `data/ST/confidence.json` (`confidence` in the index) |
| plausibilityCard | `GET /plausibility/ST` | `data/ST/plausibility.json`, every benchmark reform (`plausibility`) |
| leverCard | `GET /lever/ST?r=KEY` | `data/ST/lever/KEY.json`, state level, each benchmark reform (`lever`) |
| implementationRows | `GET /implementation/ST` | `data/ST/implementation.json`, every reform and place, picked in the browser (`implementation`) |
| grades (the slide deck reads them) | `GET /grades/ST?r=KEY` | `data/ST/grades/{baseline, each benchmark, package}.json` (`grades`) |
| exportUrl | `GET /export/{csv,html}?s=..&r=..` | `data/ST/exports/{run key}.{csv,html}` |
| switch ranges (read by the back end's `GET /switch/ST` from the saved folder; no screen reads them on the static site yet) | `switch_payload` in `sensitivity/switch_runs.py` | `data/ST/switch-ranges/{benchmark key}.json` (`switch_ranges` in the index lists the keys; `switch_ranges_counties: false` when the county records were left out for room) |
| (every screen file above) | | may say `"$shared": 1`: then blocks it shares with other files of the state are in `data/ST/pool.json`, read once (see "The exporter") |
| reforms | `GET /reforms`, `/reforms/KEY` | `data/reforms.json`, `data/reforms/KEY.json` |
| scenarios | `GET /scenarios` (each scenario's kind, description and `excludes`) | none (not exported, so not asked for); without it the extras keep their own short lines and nothing is greyed out for overlap (a mix is not on the static site anyway) |
| ledger, provenance | `GET /ledger`, `/provenance` | `data/ledger.json`, `data/ST/ledger.json`, `data/provenance.json`, `data/ST/provenance.json` |
| validation | `GET /validation?state=ST` | `data/ST/validation.json` |
| places | `GET /places/ST?level=county` | `data/ST/places/counties.json` (searched in the browser) |
| tracts | `GET /places/ST?level=tract&q=FIPS` | `data/ST/places/tracts-FIPS.json` (only with tract profiles) |
| profile | `GET /profile/ST/GEOID` | `data/ST/profiles/GEOID.json`; a county saved compact (`county_profile_format: "compact"`) is rebuilt from `data/ST/profiles/county-template.json` and `data/ST/profiles/county-values.json`; a tract saved compact (`tract_profile_format: "compact"` in the index) is rebuilt from `data/ST/profiles/tract-template.json` and `data/ST/profiles/tracts-FIPS.json` (see below) |

Four helpers read the baseline rule contest out of a validation reply, picking
fields only: `baselineContest` (every rule's scores, the choosing rule, the
winner, the sentences), `baselineCalibration` (the out-of-time test of the bias
correction and place-type ranges, before and after, by group),
`baselineSurges` (surge years and the named counties' error with and without
them) and `baselineStress` (the slump and boom alternative ranges). A reply
without a contest (an older export) gives `null` or an empty list.

`data/ST/index.json` lists what was exported. Only default assumptions (seed 0, default draws)
are saved. A scenario that was not exported raises a plain note: **"Changing assumptions needs the
local app (make app)."** for custom assumptions (also shown at the top of the screen), or "This mix
of reforms needs the local app" for a mix other than one reform alone or all five. A static build made before any export shows "The data for this site has not been exported
yet" instead of failing.

### The exporter

`visionhousing export-static --state PA --out web/public/data` (or `make export-static`) calls
the app in memory, so each file holds exactly what the live interface sends. It prints a size
table. The screen files (`runs`, `compare`, `maps`, `tracts`, `tract-funnel`, `counties`, `profiles`, `comparisons`, `districts`, `lever`, `grades`) are saved **packed**
when that saves at least 5%: a block that repeats (the same assumption or source list under every
number of an explanation) is stored once in `$pool` and replaced by `{"$p": n}`. Nothing is dropped
or rounded; `unpack` in `web/js/lib/api.js` (and `visionhousing.export_static.unpack`) gives back
the live reply exactly. Then a second pass (`share_state_pool`) works across the whole state:
lists of like objects are laid out as columns (`{"$c": keys, "$v": columns}`) and every block
that two or more screen files hold (the same county records in every run, the same district
names in every district reply) is stored once in `data/ST/pool.json` and replaced by
`{"$s": n}`; such a file carries `"$shared": 1`. `file()` in `web/js/lib/api.js` fetches the
pool once per state and puts every file back exactly (Python: `export_static.read_saved`).
Nothing is rounded or dropped. Packing cut Pennsylvania's runs from about 12.5 MB to about 2.6 MB (the whole folder from 27.8 MB to 16.9 MB), which
leaves room for a second state. Files offered as downloads (ledger, sources, report card,
backtest) are never packed. Options: `--tracts auto|yes|compact|no` (tract profiles; `auto` saves them whole under
`--tract-budget-mb`, default 12, measured before packing, and **compact** over it; `make export-static` passes `TRACT_BUDGET_MB`, default 4), `--explain auto|yes|no` (each run's explanation; `auto` drops it
only if the folder would be over the budget), `--tract-cache DIR` (see below), `--comparisons yes|no` (the five comparisons parts; run `visionhousing comparisons --warm --state ST` first, or each part takes minutes on a large state), `--extras` (also save each extra scenario alone, `extra_runs` in the index; without it the extra switches say "Needs the local app (make app)."), `--law-changes yes|no` (default yes: "What this law changes" per reform in `ST/law_changes/KEY.json`, listed under `law_changes` in the index; each file carries the read-next bounds only when `python -m visionhousing.api.law_changes --state ST --read-next` has cached them), `--budget-grid yes|no` (default yes: the ADU budget-and-target grid in `ST/adu-budget-grid.json`, `adu_budget_grid: true` in the index, read from the disk cache only, so run `visionhousing adu-budget-grid --state ST` first; without it the panel says it needs the local app), `--draws`, `--seed`. The cards, the grades and the districts (for a state whose pack holds district outlines, both chambers) are always saved; each is flagged in the index only once all its files are saved. Exporting a second state adds to
`states.json`.

**Size budget.** The folder is committed for the static host. It must stay within two limits,
both set in one place (`GZIP_LIMIT_BYTES` and `SIZE_LIMIT_BYTES` in
`src/visionhousing/export_static.py`); `tests/test_web_package.py` fails above either:

- **12 MB compressed** (each file gzip-compressed, level 6, added up). This is the real
  budget: git stores every file compressed with the same method (zlib), and the host sends JSON
  gzip- or brotli-compressed, so the compressed size is what the repository grows by and what a
  visitor downloads. The files are repetitive JSON and shrink to about a fifth.
- **60 MB uncompressed**, a hard ceiling, so a checkout and the built site stay a sensible
  size even when files compress very well.

Over the budget, the exporter first drops optional extras: the law-change files are saved again
without their read-next bounds, then the hearing briefs are kept only for the package and each
benchmark reform (the baseline's and the state bills' briefs go). Then it saves the switch ranges
without their county records, then `--tracts auto` drops the tract profiles, then `--explain auto` drops the
run explanations. Each benchmark's headline range, citation, coverage and status, the baseline
card, the accounting records and the reproducibility record sit in the runs and cards, which no
step before the last one touches. The size table ends with both totals.

**Compact tract profiles.** Whole, Pennsylvania's 3,445 tract profiles take about 474 MB
(about 138 KB each); compact, about 12.0 MB (a 0.72 MB template and 67 county files, the largest,
Philadelphia, 1.35 MB), about 3.7 MB compressed.
Most of that is the same words in every tract: titles, labels, units, sources, limits, chart
titles and notes. Compact saves them once in `ST/profiles/tract-template.json` (the shape of a
profile and every value that is the same everywhere) and, per county, only the values that change
in `ST/profiles/tracts-FIPS.json`, one column per value with one entry per tract (a value the same
for the whole county is saved once). A sentence with numbers in it keeps its wording in the
template and only its numbers, as the server wrote them, in the county file. The browser
(`assembleTract` in `js/lib/api.js`) walks the template and copies each value in: nothing is
formatted, rounded or worked out. The export rebuilds every tract from the saved files and checks
it equals the live reply; a tract that does not come back exactly is saved whole in its county
file. Format: `src/visionhousing/compact_profiles.py`. The size budget still applies. With the
shared pool per state, all four exported states keep their compact tract profiles (on 2026-11-25
the folder was 52.4 MB uncompressed and 11.4 MB compressed, against 74 MB and 14.6 MB without
the pool), so `--tracts auto` keeps them; it drops them, and the tract page says to use the local
app, only if the folder would be over the budget. Raw numbers in profiles are shortened to 4 significant
digits where that shows the same text (see `docs/modules/profile.md`), which made the compact
files about a sixth smaller.

**Compact county profiles.** County profiles use the same format: `ST/profiles/county-template.json`
and one `ST/profiles/county-values.json` with every county's values (the counties take the place
of the tracts). The exporter saves them compact whenever that is smaller compressed than the whole
files, and checks that every county rebuilds to the live reply; the index says which
(`county_profile_format`: `compact` or `full`), and `api.profile` rebuilds a county the same way
it rebuilds a tract. Pennsylvania's 67 county profiles: 2.0 MB compressed whole, 0.19 MB compact.

**Fetch time, progress and resume.** Building the compact files needs every tract profile once,
so an export with tract profiles takes longer (about 15 minutes more for Pennsylvania). The
exporter prints one line per county as it goes ("PA tract profiles: county 42001 (1 of 67), 27
tracts asked, 27 so far, 0:12"). With `--tract-cache DIR` (default `data/cache/tract-profiles`,
git-ignored; `none` turns it off) each county's tract list and replies are saved as they arrive
(`DIR/ST/KEY/FIPS.json.gz`, the replies exactly as sent), so an export that was stopped picks up
at the next county ("from cache"). `KEY` is a fingerprint of the state pack, the code (the same
fingerprint a run carries), every assumption file and the profile, law and feature files, so
after any of them changes the old replies are never read; delete the folder to free the space.
Each county's tract list is asked by county code (`GET /places/ST?level=tract&county=FIPS`), and
the export stops if a list comes back cut short or holds a tract of another county.

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
| `css/app.css` | Cards, popovers, the screens' parts, tables, phone rules |
| `css/shell.css` | The shell's zones (one grid), the floating panels and their boxes, the resize handle, the mode switcher, legend, map controls, chat box, charts, rankings, the phone sheet |
| `css/slides.css` | Slides view and its print rules (one slide per page) |
| `js/main.js` | Imports the stylesheets, mounts the intro page (bare address), the shell (every mode) or the slides view |
| `js/ui/shell.js` | The shell: the map that stays mounted, the zones, the free area it tells the map (`mapInsets`), the hover card, the map controls (with "Find a county", `/` to jump there, and "North up" in 3D), the legend (with the 2D / 3D switch, Show range and Permits by year), picking a county (centred; Esc closes), Left / Right stepping through the counties by rank while the map has focus, the phone sheet, the guided tour (mounted while `tourOpen` is true; on a phone it pulls the sheet up first), and the first-visit tour offer (`TourOffer`: a small note under the mode switcher on State, shown until `tourSeen()`; "Not now" sets the same flag; it never starts the tour by itself) |
| `js/ui/dock.js` | A side panel ("dock"): header, scrolling body, fold into a box (icon, title, key number) with a size morph, and the right panel's resize handle (pointer and arrow keys; width kept in this browser) |
| `js/ui/charts.js` | Hand-written SVG charts: donut, bars, 100% stack, small range bar, sparkline, rank words, a step chart ("share of modeled draws with at least N", with each share's Monte Carlo standard error) and year columns with a likely-range whisker; each a figure with a spoken summary and a table twin. A chart's source line and method words sit behind "(i) About this chart" (`About`, a native disclosure). `ProfileChart` draws a place profile's `charts` entries |
| `js/ui/budget-target.js` | The budget-and-target panel (Compare screen, folded) and its demo path; see "Budget and target" below |
| `js/lib/budget.js` | `gridLookup`, the browser twin of the server's budget-grid lookup (no interpolation), and `cellKey` |
| `js/ui/cycle.js` | The baseline housing-cycle preset (Low / Central / High) as three buttons; written into the link as `preset` |
| `js/ui/decision.js` | The decision view (Question, Answer, Where and why, What to change, Use it; one line and one visual each), the paired readouts ("How sure": share of modeled draws with at least N and its standard error, share of the baseline, the cost row), "What would narrow the range" (variance shares and the research-agenda sentence) and the binding-constraint card. Reads the run reply and the map reply's `decision` block; each part shows only when its data is there |
| `js/ui/county-table.js` | The county table (the map's twin, in State and Reform): baseline, homes added, per 1,000 homes, change, class and evidence coverage with its three checks; sortable |
| `js/ui/rankings.js` | The Rankings tab: the server's county rankings (`rankings` of the map reply) as growing bars, top or bottom ten |
| `js/ui/ask.js` | "Ask about this run": the question box (closed by default). Suggested questions per state, an answer card with its status, run id, period, exact-view link, download and "Show the record" (cited records and sources), and a polite live region. It answers in the page with `js/lib/answers.js` over a reader built on `js/lib/api.js`; the question is never sent or kept |
| `js/lib/answers.js` | The question box's resolver, the browser twin of `src/visionhousing/api/answers.py` (match a fixed intent by words, read fixed records, fill a template). Imports nothing, so a test runs it in Node |
| `js/content/answers.json` | The intent table and templates, written from `assumptions/answers.yaml` by `uv run python -m visionhousing.api.answers` (a test checks they match) |
| `css/ask.css` | The question box's styles (colour tokens only) |
| `js/ui/law-changes.js` | "What this law changes" on the Reform screen (see "What this law changes" below): the toggle, the map key with the four answers and the value switch, the county card, the table twin and the "Read next" list; `lawView` signal (on, `units` or `capacity`); fetches `law-changes` (live) or `data/ST/law_changes/KEY.json` (static) |
| `css/law-changes.css` | Its styles (colour tokens only; the swatches use the same tokens as the map classes) |
| `js/ui/topbar.js` | The floating mode switcher and the brand mark |
| `js/lib/api.js` | The one data client: live back end or static files. A run that passes the back end's time limit (504) is asked for again a few times |
| `js/lib/data.js` | `useJson` / `useRun` / `useMap` hooks (with a small reply cache), `numberOf` (a run's "explain this number" payload) and the sources/assumptions lookups the Explain panel reads |
| `js/lib/exports.js` | CSV and brief downloads of the current scenario (made by the engine) |
| `js/lib/format.js` | Rounds raw API values that carry no text form (ledger, evidence, backtest) for display; never derives a number |
| `js/ui/map-state.js` | Signals the map's extra layers share: `mainMap` (the loaded main map), `view3d`, `showRange`, `bearing`, `mapZoom`, `hoveredTract`, `columnPoint`, `mapFunnel` (reform, stage, previewed stage), `yearView`, `districtView` (on, chamber, picked district), `dialView` (a policy dial level shown on the map, or null) |
| `js/ui/columns.js` | The 3D view (see "The 3D view" below): footprints, column layers, the one-number animations, the camera, the column hover card and the legend's column patch; and a policy dial level on the map in 2D and 3D, with its chip and key (see "Dial levels on the map") |
| `js/ui/map-funnel.js` | The funnel on the map: the stage selector (`MapFunnelSteps`, in the county panel and the Reform screen), the map key while it is on (`FunnelKey`) and the map layers and tract card (`MapFunnelLayer`) |
| `js/ui/years.js` | The year slider (`YearToggle`, `YearKey`): past permits by county and year and the baseline's projected years, with bars and the year table |
| `js/ui/districts-layer.js` | The district layer (see "The district view" below): the "Districts" button, the House / Senate switch and key, the 2D fill and caveat hatch, the 3D district columns, the hover card and "Find my district" |
| `js/ui/district-panel.js` | The district panel and its one-page printed brief (`css/district-brief.css`, `css/district-print-page.css`) |
| `js/ui/county-panel.js` | The county panel: its numbers, "Past accuracy" and "Local rules read" (`notes` of the county reply), each reform alone, the SVG tract map and tract table |
| `js/ui/panel.js` | Tabs for the side panels (arrow keys move between tabs; a pill glides to the tab that is on), `hoveredCounty` (the county pointed at on the map or in a list), `hoverPoint` (where the hover card sits; `useHoverFollow` moves the card there and returns its placement classes, which the card renders in its `class` so a re-render never drops them), `focusClass` (the class picked in the map key) and `mapMeasure` (what the map shades) |
| `js/ui/actions.js` | The scenario's actions: Slides link, the Export menu (keyboard menu button) and Share link |
| `js/ui/motion.js` | Rolling digits from the old engine text to the new (`Ticker`), a fade-in of the final text (`countUp`; it never shows in-between values), the entry reveal (`useReveal`), height glides and short exits for panels; the text is always the engine's exact text; off with reduced motion |
| `js/ui/tour.js` | The guided tour (`Tour({steps, onClose})`, mounted once by `shell.js` while the `tourOpen` signal is true; styles, and the first-visit offer's, in `css/tour.css`): a veil with a spotlight cut out around one element per step (found by CSS selector, with fallbacks and an optional text match), a short card placed on the first side where it fits without covering that element, Back / Next / Skip, Enter or Right for next, Left for back, Esc to close, focus kept inside the card and given back on close. A step whose element is not on screen shows its card in the middle. No animation with reduced motion. Closing remembers "seen" in localStorage (`vh.tour.seen.v1`, inside try/catch; `tourSeen()`, `forgetTour()`). `TOUR_STEPS` is the default path: pick a reform, read the range, why (Drivers), where (map key and 3D), trust (Explain), limits, share. The words hold no numbers |
| `js/ui/slide-face.js` | One slide's face, shared by the Slides view and the live Brief preview |
| `js/ui/tod-status.js` | The transit status of a TOD run, as the server writes it in the run reply's `status_views` (`api/status_view.py`; see "Transit status" below): `statusView` / `hiddenView` / `partialView`, `HiddenRange` (in place of a hidden statewide range: the hidden word, the measured contribution, the completeness label), `PartialLabel` (beside a shown but partial range), `PackageNotes` (under a package total), `Ladder` (one area's ladder: discovered, legal, coverage, headway, peak headway, feasible) and `StatusTable` (the Reform screen's card: the state's ladder and every county as a compact table); styles in `css/tod-status.css` |
| `js/ui/brief.js` | The Brief tab: the scenario's slides as small live cards that open the Slides view |
| `js/ui/dials.js`, `js/ui/comparisons.js` | The policy dial and the four comparison cards, with the folded section that loads them on demand (see "Policy dials and comparisons"); styles in `css/comparisons.css` |
| `js/ui/extras.js` | The extra scenarios (`kind: "extra"` in `GET /reforms`): `useCatalog(state)` (the engine's titles and descriptions from `GET /reforms` and `GET /scenarios`, and `blockers(id, on)`: the switched-on reforms whose family the scenario `excludes`, or that exclude it; the back end refuses such a package), `overlapReason` ("Not with ...: they reach the same lots or projects."), the folded "Extra scenarios" switch group under the benchmarks (State; Reform has the same group of pills), `OutsideCount` (adaptive reuse's `conversion_homes` record under "Outside the permit count", with the engine's name and range) and `RecordsCard` (every `reform_results.KEY.records.*` number of the explanation). Static site: an extra whose run is not in the state's index says "Needs the local app (make app)." Defaults (switches on, "All five reforms", Compare, the package, the tour) stay on the benchmarks |
| `js/ui/static-note.js` | The static-site notes (not exported yet; custom assumptions need the local app) |
| `js/state/codec.js` | Reads and writes the scenario in the address (pure, no browser globals) |
| `js/state/store.js` | Signals for the route and scenario; navigation; the toast; `tourOpen` and `startTour()` (switches ADU on when nothing is on, then opens the tour); layout `prefs` (localStorage) and `callAttention` (draws the eye to a card) |
| `js/content/*.js` | Words only: reform names and groups by engine key (benchmarks `adu`, `missing_middle`, `tod`, `parking`, `fees`; bills `pa_hb2186`, `pa_sb1346`; extras `transit_zoning_funded_infrastructure`, `commercial_land_housing`, `adaptive_reuse`, `starter_home_lots`, `fee_deferral_with_affordability`, so a shared link keeps them), state names (plus Testland, `s=TL`), glossary, and the reader of the engine's slide spec |
| `js/ui/*.js` | Scenario actions, popovers (glossary and Explain), map, small parts, icons |
| `js/screens/*.js` | Each mode's two panels (State, Reform, Compare, Place, Method & data), the intro page and the Slides view. "Take the tour" sits in the State Scenario panel's header (`state.js`) and beside "Open the tool" on the intro page (`intro.js`, through `onTour` from `main.js`) |

The Method & data screen's left panel lists the rules from `contestView` (the validation reply's `contest_view`): plain names, the contest's typical miss with the server's text, the rule in use, and the server's sentence when that rule is not the lowest. The data report card shows one compact block per input (name, then status, tier, edition and coverage on one line, then its limits).

The Method & data screen's *Baseline rule contest* card reads `baselineContest`, `baselineStress` and `baselineSurges`: every rule's typical miss, typical error, coverage and range width (the winner marked, the pre-stated choosing rule behind "About"), the slump and boom stress ranges with their sentences, and the surge-year note. These scores carry no text form, so they are only rounded for display (`lib/format.js`).

The Method & data screen's *Error by kind of place* card reads `by_place` from the validation reply: the rule-written findings, a table by grouping (small groups tagged "few cases"), and the county error map: in this mode the shell shades the main map by each county's back-test error class (`palette`: the diverging `--div-1`..`--div-5` tokens, blue where the baseline ran low, red where it ran high) and the legend shows those classes. A button opens the county error table with the same values.
| `css/place-print.css` | US Letter portrait page for printing a place profile; added only while the Place screen is open |

## Routes and the scenario in the address

`#/state`, `#/reform/<id>`, `#/compare`, `#/place/<county or tract code>`, `#/method`, `#/slides`,
each followed by the scenario (the place code must be 5 or 11 digits, else it is dropped):

```
#/reform/adu?v=1&s=PA&r=adu,fees&a=adu.takeup~h
```

| Key | Meaning | Rule |
|---|---|---|
| `v` | codec version | must be `1`; any other version falls back to the defaults |
| `s` | state | a known two-letter code |
| `r` | reforms on | known engine ids only, written in a fixed order, at most 8. The old id `middle` is read as `missing_middle`, so earlier links still work |
| `a` | assumption overrides | `ledger.id~l/c/h` or a plain decimal; at most 40; key ≤ 64 characters |
| `preset` | baseline housing cycle | `low` (weak) or `high` (strong); `central` (typical) is the default and is not written. Sent as `preset` in the run body and the query (`runBody`, `scenarioQuery`); the static site holds only the default, so another value is a custom scenario there. Chosen with `CycleChoice` (`js/ui/cycle.js`, Assumptions tab) |
| `p` | retired view preset | no longer written; an older link that has it opens the same view, and the link note says it was left out |

The whole query may be at most 2,000 characters. Anything unknown or out of bounds is dropped,
never guessed, and never silently: the router keeps what the decoder dropped (`linkDropped` in
`js/state/store.js`) and the link note (`js/ui/static-note.js`) lists it in plain words ("unknown
link version 9", "reform xyz", "view preset (no longer used …)"). On the static site a custom
scenario (changed assumptions, or a mix of reforms the export did not save) gets its own status,
"Custom scenario, not on this site", with no number shown and a button that switches to the
default assumptions; nothing falls back to the default run by itself. Switching a reform replaces the history entry; moving between screens adds one,
so Back works as people expect. **Share link** copies the address, which is the whole scenario.

## Interface rules it follows

- No top bar. One map fills the screen and stays mounted across modes; the brand, the mode
  switcher, the two panels, the question box, the map controls and the legend sit in fixed zones of
  one grid (`.hud` in `css/shell.css`), so they never overlap (the browser tests check this at
  1440 x 900, 1280 x 720 and on a phone). A folded or narrower panel makes its column narrower;
  the shell measures the free area and the map fits the state inside it, while a picked county
  is framed with equal padding on both sides, so it sits at the true middle of the screen. Under
  768 px the panels become one bottom sheet.
- The scenario's actions travel with the results (`js/ui/actions.js`): **Slides**, **Export**
  (Brief PDF, Slides PDF, Table CSV) and **Share link** sit in the right panel's header in every
  mode, and in the Slides toolbar (Export and Share link).
- Controls follow one model. The scenario (state, reforms, assumption changes) lives in the
  address and is written with `replaceState`, so toggles never flood Back. Layout choices (which
  panels are folded into boxes, the right panel's width, whether the question box is open) are this
  browser's own: `prefs` in `store.js`, kept in
  localStorage, never in the address. Picking and pointing (the open county, the county under
  the pointer, the class picked in the map key) are per visit and drive the map and the panels
  through signals (`hoveredCounty`, `hoverPoint`, `focusClass`). Esc closes what was opened last
  (a popover or menu first, then the county panel).
- One primary (filled) button per card. Every button shows its label; icons are decorative.
- **No typed numbers.** Every number is the engine's own text (`shown`) from the reply the screen
  loaded. The browser tests compare each screen with the API reply for the same scenario. While a
  reply loads, the panel says so in plain words; a value that is missing shows "—".
- **A new state clears the old numbers.** A reply loaded for another state is dropped the moment
  the state changes (`useJson`'s `keep` in `js/lib/data.js`; `useRun` and `useMap` pass the
  state), so the old range and sentence never sit under the new state's heading: the panels show
  their loading state until the new run arrives. Within one state the last reply stays while the
  next loads, so changed digits can roll.
- **Popovers sit above the panels.** The glossary and Explain panels are drawn in a layer at the
  end of the page (`createPortal` in `js/ui/popover.js`): the side panels are glass cards whose
  blur and transforms would otherwise offset or clip a panel placed in window coordinates. Tab on
  an open button moves into the panel and back out to the next control.
- **Decision view variation.** "Try it at its high value" keeps naming the input it moved while
  the variation is on (`triedLever` in `js/ui/decision.js`; the varied run may rank another input
  first), the headline says "Assumption changed: <input> at its high value", and "Back to the
  benchmark" removes exactly that change.
- **Explain this number.** Every number is a button (`Num`) that opens the engine's explanation:
  what it is, its likely range, formula, inputs, assumptions (with labels) and sources.
- Glossary words and **Explain** open popovers by click, Enter or Space (not hover-only); Esc
  closes them and returns focus.
- Keyboard: every control is reachable with Tab and has a visible focus ring. Tab follows the
  screen, not the page source: the mode switcher, the left panel, the map (its ring is drawn
  inside the canvas), the map controls, the right panel, the legend, then the question box;
  Shift+Tab goes back the same way (`useZoneTab` in `shell.js`; phones keep the page order).
  On the map, Left / Right step through the counties by rank and a hidden status line names the
  county and its likely range (the engine's text); Enter opens it. The Export menu moves with
  the arrow keys. The slides move with Left / Right, Home / End; Esc closes them. Play on the
  year slider and the map funnel pauses with Space or Enter on the button; any other key or
  press stops it.
- Screen readers: one polite live region per screen (the lead number), not one per number;
  the year slider says its year (and "baseline, projected") through `aria-valuetext`.
- Phones: every control in the panels, the sheet and the legend is at least 44 x 44 px (inline
  words and numbers in a sentence keep their line). The mode switcher drops its icons and
  shows "Method" for "Method & data", so all five names show whole. The sheet's handle can be
  tapped or dragged (it follows the finger; let go above or below and it settles open or
  closed).
- Colour: one accent (deep teal), cool greys, and one six-step sequential map ramp whose
  lightness rises steadily, so it reads in grey and for colour-blind readers. "No data" and
  "Not modelled" are grey. Text pairs meet WCAG AA (body text 16:1, quiet text 5.7:1, accent 6.2:1;
  on the panels' glass over the darkest map colour: 14.7:1, 5.2:1 and 5.6:1). A dimmed state
  (the classes not picked in the map key, a panel waiting for a newer run) fades the swatch or
  keeps muted text at 4.5:1 or more.
- Motion is short and is switched off when the reader asks for reduced motion. When the
  scenario changes, the digits that changed roll to the new value over the engine's exact text
  (the text itself is final from the first frame), with a short glow on what changed; range bars draw in and slide. Hovering or focusing a
  range bar shows its P10 / P50 / P90 text. Pages and cards rise in; the map's cards slide in
  from their side; tab panels slide in from the side of the tab pressed. While a reply loads,
  grey skeleton bars hold its place under a line that says what is loading; while a newer run is
  on its way, the old numbers stay, faded, with a thin loading bar in the panel header.
- The map (`js/ui/map.js`): the key floats at the top, never at the bottom, and the state is
  fitted under it. Pointing raises a county (white glow, accent outline) and shows a hover card
  by the pointer (or on the county's middle when a list row is pointed at). Clicking ripples a
  ring, dims the rest of the state and eases the view to the county. Counties whose class changes
  fill in as a west-to-east wave (a `k` feature state per county runs from faint to full). None of
  this changes a colour or a number: the colours are the engine's classes from the first frame.

## Side panels

The State screen's **Results** card and the Reform screen's side column are tabbed panels
(`js/ui/panel.js`; Left / Right / Home / End move between tabs). The Results card folds away with
its arrow button (remembered in this browser). Its header stays fixed above the scrolling tabs.
The Results tab starts with quick switches (one pill per reform, short names) so reforms can be
added and dropped without leaving the numbers; each verdict opens to say why, with a link to that
reform's funnel and drivers. With no reform on, the tabs offer the next step instead of a blank.

| Tab | State screen | Reform screen |
|---|---|---|
| Decision | — | The decision view, then "How sure" (paired readouts and the cost row) |
| Results | The sentence; with one reform on, the decision view; the three headline ranges and the verdicts | (the left column) |
| Counties | The ten counties that gain most (middle estimate), with range bars. Pointing at a row outlines the county on the map; pointing at the map lights the row and names the county over the map | — |
| Drivers | A reform alone: its top drivers (asks for the drivers run when opened). Several: how the package adds up, with links | The sensitivity tornado; pointing at a driver dims the others; then "What would narrow the range" |
| Limits | What the model cannot tell you, in folding groups | The same, for this reform |
| Brief | The scenario's slides as small live cards | The reform's slides |

The **Brief** cards are the same slides as the Slides view (`js/ui/slide-face.js`), drawn small.
They follow the scenario. As each card scrolls into view its numbers fade in, already the
engine's text, its bars draw in and its sentences fade in one after another (IntersectionObserver
and requestAnimationFrame; no library). Pressing a card opens the Slides view at that slide. The
Slides view plays the same entry on the slide shown. Print and reduced motion show the final state.

## Slides view

Each slide is drawn on a 960 × 540 logical frame that scales to the window. "Read as a page"
shows the same slides as text in one column. Printing (or **Export → Slides (PDF)**) gives one
slide per 13.333 × 7.5 inch page. The slides are the engine's own deck for the current scenario
(`explanation.slides` of `POST /run`): summary, one per reform, top drivers (a reform alone; the
view asks for the drivers then), the package waterfall and the limits. Every footer carries the
run's code, data and assumption fingerprints and the disclaimer.

## The map

MapLibre draws the county outlines (`GET /states/ST/outlines`). Under them it can show an
optional basemap: OpenFreeMap's light "positron" style (its "dark" style if the page is ever dark),
a free public tile service built from OpenStreetMap data, with no key, no account and no cookies.
It is the one third-party request the interface makes. Place labels sit above the county fills;
road shields and points of interest are left out. The credit "OpenFreeMap © OpenMapTiles Data from
OpenStreetMap" shows in the map key (on phones, on the map) whenever the basemap is on. The
**Basemap** button in the key turns it off; the choice is kept in this browser. If the style or
its tiles fail, the map goes back to the plain look with no error. Builds made with
`VITE_BASEMAP=off` (the browser tests) start with it off. Each county is filled by its class from `GET /states/ST/map`: the homes the switched-on
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
  headers, `web/public/data` is within the size budget (12 MB compressed, 60 MB uncompressed), build output is git-ignored and the data is not, and
  every import is a file in `web/` or a locked package.
- `tests/test_answers.py`: the question box's gold questions (PA, VT, MA, WY over the committed
  static files, plus Testland live against its own static export) get the expected intent and
  status; every number in an answer is in the records it cites; cited records carry the
  payload's exact shown values, sources and dates; `js/lib/answers.js`, run in Node
  (`tests/js/answers_parity.mjs`), gives the same answers as the Python resolver.
  `tests/e2e/test_ask.py` checks the box in the browser (live and static, keyboard, live region,
  "Show the record", no request carries the question, phone width).
- `tests/test_export_static.py` (Testland): the saved files equal the live replies (byte for
  byte, or exactly once a packed file is unpacked), packing round-trips exactly,
  the index lists them, the tract budget and `--explain no` work, a rerun replaces the state, a
  folder that is not an earlier export is refused, and the command line works. Compact county and
  tract profiles rebuild (in Python and in `api.js`) to the live replies, the budget counts
  compressed bytes and has its ceiling, and the tract fetch resumes from its cache.
- `tests/e2e/test_shell.py`: builds `web/` with Vite into a temporary folder, serves it with the
  real FastAPI app on Testland, opens every route on desktop and phone sizes in Chromium, and fails
  on any console error or off-site request or sideways scroll on a phone. Consistency: the State
  card, the county table, the county panel's tracts, the Reform funnel, headline, drivers, limits,
  assumptions and evidence, the Compare bars, table and waterfall, the Method tables and the
  slides (titles, big ranges, fingerprints) each equal the API reply for the same scenario. It
  also checks a custom assumption ("Yours", the run changes, Reset all), the CSV and brief
  downloads, the keyboard path through the zones and the map tools (3D, the year slider and its
  Play / Pause, the funnel's stages and Play, a county reached from the map), no overlaps, no
  sideways scroll and 44 px targets at 390 x 844, 360 x 740, 1280 x 720 and 1366 x 768, the phone
  sheet's drag, a keyboard-only path (switch a reform, open a county, go to Compare, explain a
  number), old `middle` links, the top bar by keyboard, slide keys, one-slide-per-page printing,
  Share link, the popovers, the Place screen and the codec (run in Node). The decision view, the
  paired readouts, the fee cost row, the map key's coverage switch (with the county table's
  coverage column) and the Method screen's rule contest each equal the API reply. The 3D switch
  sets `aria-pressed` and tilts the camera (and back to flat); with reduced motion it arrives at
  once and no Play button shows; the map funnel's stage tabs change the state count to the API's
  text; the year slider, its state line and the year table equal `permit-years`; and the Brief
  numbers never show a value in between (they fade in as the final text). The static tests build
  the static site, export Testland into it, serve it as plain files and check every screen (State
  for the baseline, one reform, one bill and all five; Reform; Compare; Method; Slides) against
  the exported files, and the "needs the local app" notes. Screenshots go to `nopush/logs/shots/`.
- `tests/e2e/test_districts.py`: the district view on Testland (see "The district view").
- `tests/e2e/test_comparisons_ui.py`: the policy dial and the comparison cards on their screens
  (see "Policy dials and comparisons").
- `tests/e2e/test_tour.py`: the guided tour on Testland: the first-visit offer shows once and
  sits clear of every zone; "Take the tour" opens it by keyboard; Enter and the arrows step
  through all seven steps, each card inside the window and off its spotlight; Esc closes it,
  gives focus back and sets the seen flag; the intro's button opens the tool with the tour; on a
  phone every card fits.

## Limits

- The static site holds only default-assumption results for the exported states: the baseline,
  each reform alone, each state bill alone and all five together. Custom assumptions, other mixes
  of reforms and other seeds need the local app; the screens say so.
- The county panel's tract map shades one reform alone. The 3D tract columns show the scenario's
  tract-level part (`GET /states/ST/tracts`); fees and parking stay county by county.
- The 3D view, the map funnel and the year slider need WebGL like the flat map; the county table,
  the funnel tabs' counts and the year table carry the same values without it.
- Hindcasts of past reforms are not in the engine yet; Validation shows the baseline backtest.
- `make app` and the browser tests need Node 22.12 or newer (`make setup` runs `npm ci`).


## Readout accessors (`web/js/lib/api.js`)

Pure lookups on a run reply (live or saved): `readouts(run, key)`, `readoutCounties(run, key, name)`, `readoutTexts(run, key)`, `readoutSentences(run, key)`, `narrowing(run, key)`, `frequencyText(run)`, and `countyReadouts(countyReply, key)`. They return the engine's own records and cited sentences; the browser never sums, divides or rounds. `tests/test_export_static.py` checks that they read the saved files exactly as the live replies.

Outcome-contract accessors (same rule: lookups only): `asOf(run)`, `outcomeContract(run)`, `annualSeries(run, key)` (`"baseline"`, `"package"`, `"total"` or a reform key: yearly state records and the county table for 2027-2031), `grossNet(run, key)`, `netStockCounties(run, key)`, `countyAnnual(countyReply, key)`, `countyTractsAnnual(countyReply, key)`, and on a `/validation` reply `baselineBiasCorrection`, `backtestVariants`, `statewideBacktest`, `originOverlap`.


## Decision view and the map's other measures

The decision view leads the Reform screen's right panel and follows the headline on the State
screen when exactly one reform is on. It reads the run reply (headline, `readouts`,
`narrowing`, `drivers`) and the `decision` block of the map reply for the same scenario, which
the API builds in `src/visionhousing/api/decision.py` from the engine's records:

| Field of `GET /states/ST/map` | What it holds |
|---|---|
| `decision` (one reform only; otherwise `null`) | `question` (measure, window, as-of date, settings, the reform's definition), `annual` (the engine's yearly state records with text, when the engine splits the result into years), `net` (net added homes when homes are removed), `top_county`, `binding` and `coverage` (counts by class) |
| `decision.binding` | Every funnel stage with its loss (1 − the step ÷ the step before, worked out in each draw; `lost` is the middle of the draws, `lost_p10` / `lost_p90` and `lost_range_text` the likely range, with the formula and text), who acts there, why, and the next question; the binding stage (largest loss; fees and parking name their one lever instead); the stages grouped by actor. Words and ids from `assumptions/decision.yaml` |
| `counties[].coverage` | Three checks (local data at tier 1 or richer, verified local rules read for at least the law hook's minimum share of lots, a scored back-test within `name_few_cases_beyond`) and the class they add up to: *Good* (2–3), *Partial* (1), *Thin* (0) |
| `counties[].measure_class`, `measures` | Each county's class of homes added per 1,000 existing homes and of homes added, by the fixed breaks in `assumptions/decision.yaml`, and the coverage classes; the map key lists them |

The map key's buttons set `mapMeasure`; the shell shades by `classesFor(map, measure)`, with
one palette for every class (`MAP_COLOURS` in `js/ui/map.js`), so changing the measure only
changes feature state and the fill wave plays as it does for a new scenario. An older export
has none of these fields: the buttons and the decision view's parts that need them do not show.
On a phone the measure buttons are one drop-down list, so nothing is cut off.

A county whose baseline is 0 (its middle estimate, or at least half its draws) has no share to
show: the API puts it in the class `no_base` ("No baseline", the lightest ramp colour, listed in
the key only when some county has it), leaves its `change` empty and sends `no_base.text`
("No baseline to compare; adds N homes"), which the table, the hover card and the county panel
show instead of a percent.

## The 3D view (`js/ui/columns.js`)

2D is the default. **3D** in the key tilts the map and stands a column on every county, and,
from zoom 8.2, on every census tract. MapLibre's own `fill-extrusion` layers draw them (no
added library).

| Part | How |
|---|---|
| Footprints | A square around a point inside the place, built once per state: 8 km for a county (6 km in Vermont) around the middle of its largest part; for a tract, 0.86 x the distance to the nearest other tract point (`GET /states/ST/tract-points`), kept between 300 m and 3 km. `promoteId` is the geoid |
| Levels | County columns below zoom 7.6, tract columns from 8.2; in between the county columns sink as the tract ones rise (both in one zoom expression). Tract values come from `GET /states/ST/tracts` for the scenario's tract-level reforms; fees and parking work by county, so the tract level leaves them out (the key says so). No tract reply (no reform on, a county-only reform, or a static site without that file): the county columns stay at every zoom |
| Height | Linear in homes: homes x `k` x metres per pixel at the zoom (exponential base 0.5 between zoom stops, which is exact), so a column keeps its size on screen. `k` (pixels per home) is fitted per level so the 99th-percentile P90 stands about 32% of the map's height at a 50 degree tilt, the tallest column between 25% and 80%; a new `k` eases in over 400 ms |
| Range | Solid core from 0 to P50; glass cap from P50 to P90 at 0.25 opacity (`fill-extrusion-base`); a thin dark P10 notch on the column pointed at (a one-feature source set on hover). "Show range" hides the caps |
| Colour | The class tokens of the flat map (`COLUMN_COLOURS`: `--seq-2/4/6` for counties, the tract classes on the same ramp); no data and not modelled stand as flat grey squares. The county map below turns into a neutral floor with its county lines; light `{anchor: "map", position: [1.3, 315, 45], color: "#fff", intensity: 0.4}`; footprints carry a 0.5 px surface outline |
| Motion | Only MapLibre global state moves per frame (`vh_t`, `vh_h3d`, `vh_kc`, `vh_kt`, `vh_dim`); each column reads its own delay from its properties (`dx` west to east, `dn` north to south). A scenario change sets the data once with each column's previous and next P10 / P50 / P90 (colours switch at the start) and runs `vh_t` 0 to 1: 700 ms per column, starting 0-350 ms apart west to east. The first 3D view flies in (pitch 0 to 45, bearing -12, zoom +0.25, 1,200 ms) while the columns rise north to south (150 ms + up to 600 ms, 650 ms each). Into 3D otherwise: pitch 50, bearing -15, 900 ms ease-out. Back to 2D: the columns sink (450 ms), then the map flattens (600 ms). Reduced motion: every step is instant |
| Camera | Vertical field of view 30 degrees, maximum zoom 12.5, rotating and tilting by mouse, touch and Shift+arrows in 3D only. The tilt follows the zoom (35 degrees at the state's fit, 50 at zoom 9, 55 from 11) after each zoom by the reader, until they tilt by hand. "North up" shows when the bearing is more than 3 degrees |
| Pointing | `vh_hover` (the column's geoid) turns it accent and `vh_dim` fades the others toward the floor over 250 ms (MapLibre takes one opacity per extrusion layer, so the fade is a colour mix that looks 0.55 opaque). The card sits on the column's top: its base projected, lifted by its height on screen (height in pixels x sin of the tilt). It names the tract (and county), the class and "likely P10 to P90, middle P50": the engine's text for counties, the API's rounded values for tracts. Rows in lists light their county's column the same way. The county table stays the twin |
| Key | Title "Homes added per county" (or "per tract" at tract zoom); a small SVG of three columns in the class colours at the current scale, each the tallest of its class, with glass caps and ticks at the tallest one's P50 and P90, named with its text |

Measured on a desktop GPU (Apple, `--use-angle=metal`, Pennsylvania, 67 counties and 3,445
tracts): 60 fps while orbiting at county and at tract zoom, during the fly-in and during a
scenario change. Each global-state change makes MapLibre re-read the column sources' paint
values, which this size handles in a frame.

## The funnel on the map (`js/ui/map-funnel.js`)

In the county panel (and on the Reform screen, under the funnel card) a reform that works tract
by tract gets **On the map**: tabs through its stages from `GET /states/ST/tract-funnel` (lots in
scope, physically fit, financially feasible, expected permits; acres for transit-oriented
density's land steps), Left / Right between them, and **Play** (1,100 ms a stage; any key or press
stops it; hidden with reduced motion). The county's likely range (the API's county values,
written with commas) and the state's (the engine's text) roll with the existing `Ticker`. The
map shades every county (`fv` feature state on the county source) and the open county's tracts
(a source set once per county from the county reply's outlines) by the stage: `fv` is the value
as a share of the largest of any stage in the same unit, so units never mix; it is tweened per
place over 560 ms, starting west to east across 240 ms. A unit change says "Scale changes: now
homes, not lots." in the panel and the key. Pointing at a tract shows a card with a bar per stage.
Pointing at a row of the Reform screen's funnel card previews that stage on the map (the land
and lot rows are the stages in order; every later homes row is the last stage) and dims the
other rows to 0.45. Fees and parking have no tract funnel: the panel shows the API's sentence.

## The year slider (`js/ui/years.js`)

**Permits by year** in the key loads `GET /states/ST/permit-years`: each county's homes
authorized by building permits in every year from 2000 (the first year is in
`assumptions/decision.yaml`) to the latest, the same per 1,000 existing homes and a class by
fixed breaks, all worked out by the server (`api/views.py`, `county_permit_years`), and the
baseline's own yearly county table for 2027-2031 on the same scale. The slider (arrow keys, or
Play at 900 ms a year) pushes the year's classes to the counties as feature state, so the map's
fill wave plays as the years change. Bars show the state's total each year (projected years with
a P10-P90 whisker); the year table is the twin. The 3D view and the map funnel step aside while
it is on.

## The district view (`js/ui/districts-layer.js`, `js/ui/district-panel.js`)

For a legislator's question, "what does this mean where my voters live?".

- **Districts** in the map controls turns the layer on (in the live app only when the state has
  district outlines; on the static site always, see below). The map key then holds a **House /
  Senate** switch (lower / upper chamber), the district classes and the caveat swatch.
- **2D:** each district is filled by its `class` from `GET /states/ST/districts`: homes added per
  1,000 of its 2020 homes (middle estimate) by the fixed per-1,000 breaks of
  `assumptions/decision.yaml`, `none` (grey) with no reform on. Districts the server flags with
  `caveat` (split share above `districts.split_caveat_share`) are hatched.
- **3D:** one column per district at the middle of its largest part: the core to P50, a glass
  cap to P90 (the 3D view's pattern); the county and tract columns step aside meanwhile.
- **Pointer:** the hover card names the district, its class and the server's range text, and the
  split caveat when flagged. A click opens the district panel; while the layer is on, the counties
  do not answer the pointer.
- **Find my district** replaces "Find a county" while the layer is on (press **/**). It is a
  combobox: a number ("91" finds 091), a name, or a county name, which lists every district
  holding some of the county's 2020 homes with the share (the server's county weights). Up / Down
  move, Enter opens, Esc closes the list.
- **The district panel** takes the right panel (the mode's own panel stays mounted, hidden):
  the headline (the package with two or more reforms, else the one reform; also per 1,000 of its
  2020 homes), the baseline, each reform and the package as a table with range bars, each
  reform's statewide top driver (`explanation.drivers.<key>.items[0].text.text` of `POST /run`
  with drivers, loaded on request when two or more reforms are on, because each reruns the
  reform's sensitivity), the limits (districts, then each reform), the split caveat, the counties
  touched and the plans' vintage. Esc or Close gives the right panel back.
- **Print / PDF** prints the panel alone as a one-page US Letter brief: the page is marked
  (`data-print="district"`), `css/district-print-page.css` is added for that print only (so the
  slides keep their landscape page), every limits list is opened, and all is undone after.

Every value is the server's text (`shown`); the browser only places P10 / P50 / P90 (bars and
column heights). The static site reads `data/ST/districts/...` only when its index says
`districts: true`; otherwise the map key says "The district view needs the local app (make
app)." Tested in `tests/e2e/test_districts.py` (Testland's made-up districts: toggle, chamber,
hover card, click, panel equals the API, caveat, drivers, one printed page also with all five
reforms, keyboard search by number and county, 3D columns, the static note).

## Explanation cards (confidence, plausibility, lever, implementation)

Four small cards answer "how sure", "is it plausible", "what would move it" and "is the rule in
force". Each has one heading, one headline line, one visual and its details behind "(i)". Each
takes plain props, loads its own reply through `js/lib/api.js`, and shows the shared loading and
"needs the local app" lines while it waits or when the static site lacks its file. Every number
is the API's value or text; a bar or point only places it.

| File | Component | Reads | Shows |
|---|---|---|---|
| `js/ui/confidence.js` | `ConfidencePanel({state, reform?, h?})` | `confidenceSignals(st)`: `GET /confidence/ST` · `ST/confidence.json` | Two signals side by side, never merged: baseline performance (grade word, the back-test sentence) and reform evidence per reform type (grade word, verified records, records with an observed result, records in this state). Three pips stand for the grade word's step |
| `js/ui/plausibility.js` | `PlausibilityCard({state, reform, h?})` | `plausibilityCard(st, r)`: `GET /plausibility/ST?r=` · `ST/plausibility.json` | The rule-written sentence, then a strip: our P10–P90 rate as a bar with the P50 mark, the observed band shaded and each observed rate as a point (a button: pointing at or focusing it shows its place, period, design and caveat). A log scale when values span more than twenty-fold. "No observed rate in this unit yet" when there is none |
| `js/ui/lever.js` | `LeverCard({state, county?, reform, h?})` | `leverCard(st, r, county)`: `GET /lever/ST?r=&county=` · `ST/lever/KEY.json` (state only) | The binding factor and who can act, each named variant's effect as a range around zero (variant minus benchmark, paired draw by draw; the county's when `county` is given), the evidence grade and one "Check next" step |
| `js/ui/implementation.js` | `ImplementationRows({state, geoid?, reform, h?})` | `implementationRows(st, r, geoid)`: `GET /implementation/ST?r=&geoid=` · `ST/implementation.json` (rows picked by reform and place) | One row per provision: "Legally effective · local guidance issued · permit results not yet published" as three pills; a pending bill shows as pending; "Sources and dates" folds open every step's dates (took effect, verified on) and sources, and earlier versions |
| `js/ui/cards-common.js` | `useCard`, `CardFrame`, `GradeChip`, `SourceLink` | | The shared loader, the card frame (heading level `h`, default 2) and small parts; imports `css/cards.css` |

The server side is `src/visionhousing/api/cards.py` (confidence, lever, implementation; the
plausibility card reads the existing `GET /plausibility/ST`). Its words and choices (which named
variants ground the lever's range, the next check per reform, the reform-type names, the
implementation-row words) are in `assumptions/cards.yaml`. Accessibility: each card is a section
labelled by its heading; every drawing is `role="img"` with a text summary; points and folds are
reachable by keyboard; the bars' grow-in stops with reduced motion. Tests:
`tests/test_api_cards.py` (server views) and `tests/e2e/test_shell.py`
(`test_explanation_cards_equal_the_api`: each card's line equals its API reply).

Where they are mounted: the Reform screen's Decision tab (`LeverCard`, `PlausibilityCard`,
`ImplementationRows` for a benchmark reform; a bill shows only `ImplementationRows` with
`reform="adu"`), Method & data under the validation card (`ConfidencePanel`, the one reform on
highlighted), the county panel when exactly one benchmark reform is on (`LeverCard` and
`ImplementationRows` for that county; live only), and Place under the profile
(`ImplementationRows` per reform on, or all five, and `ConfidencePanel`). On the static site a
card asks for its file only when the state's `index.json` lists it (`confidence`,
`plausibility`, `lever`, `implementation`), so an export made before them shows the card's
local-app note and no failed request.

## Policy dials and comparisons (`js/ui/dials.js`, `js/ui/comparisons.js`)

Five more cards, built like the explanation cards (one heading, one headline line, one visual,
details behind "(i)", a "Table" twin for every drawing). Each reads one part of
`GET /api/v1/states/ST/comparisons?part=...` through `api.comparisons(st, part)`; one request per
state and part per page load is shared by every card that needs it (`comparisonPart`). Every
number shown is the server's text (`shown`, `likely`, `headline`, `sentence`, `*_text`, written by
`src/visionhousing/api/comparison_text.py`); the browser only places values in a drawing.

| Component | Part | Shows | Mounted |
|---|---|---|---|
| `PolicyDial({state, reform, h?})` | `dials` | "How much reform?": a slider that snaps to the levels the engine ran (benchmark marked), the middle estimate and likely range at that level, "each step adds about ..." and the diminishing-return note, the dose-response line and band, the target seeker ("What would it take to add N homes?", "out of reach" where the curve tops out), and for ADUs a switch between its two dials and the take-up sentence. Arrow keys, Home and End move one level | Reform screen, Decision tab, after the lever and plausibility cards, in the folded "How much reform?" section (benchmark reforms only) |
| `InteractionWaterfall({state, h?})` | `interactions` | The reforms alone added up, each named overlap or saving, the package (a waterfall); last in vs alone behind "(i)" | Compare, right panel, folded "More comparisons" under the table |
| `RankingStability({state, h?})` | `ranking` | The reforms in order with their likely ranges, where the order changed when an input moved to its low or high end, and the paired win shares | Compare, same folded section |
| `AblationCard({state, h?})` | `ablation` | The sparse-data test: tiers 2 / 1 / 0 as likely-range bars per reform, the four measures and which missing dataset hurts most | Method & data, folded "Sparse-data test and ADU sensitivity" under the confidence card (also in the left panel's list; jumping there opens it) |
| `TakeupCostMatrix({state, h?})` | `matrix` | ADU take-up x construction cost: a 5 x 5 heat grid with the middle estimate in each cell, the likely range on hover or focus, the benchmark cell marked. The focused cell and the pointed-at cell are kept apart, so the live line names the focused cell even if the pointer sits over the grid or the page shifts under it | Method & data, same folded section |

Loading. A part runs the engine many times, so on a large state the first ask after a server
restart takes about a minute (Pennsylvania: one to four minutes per part; the API's 504 is asked
again by `api.js`). The screens therefore mount the cards inside `ComparisonFold`, a native
`<details>` whose cards are drawn, and so ask for their part, only once it is first opened; a
screen never fires every part at once. While a part computes, the card shows the shared skeleton
with "Computing… the first time after a server restart this takes about a minute."
(`COMPARISONS_WAIT`, passed to `CardFrame` as `wait`).

Static site. `api.comparisons` reads `ST/comparisons/<part>.json` only when the state's
`index.json` says `comparisons: true` (`comparisonsSaved` in `api.js`, next to `cardSaved`);
otherwise every card shows "These comparisons need the local app (make app)." and no file is
asked for. The exporter sets the flag once it saves the five parts.

Map hook. Each time the dial's level changes, `PolicyDial` sends `vh:dial-level`
(`DIAL_EVENT`) on `window` with `{state, reform, dial, level, label, benchmark, record,
counties, levels, moved}`, where `counties` is that level's county table (`{columns: [county,
p10, p50, p90], rows}`), `levels` every level of the dial and `moved` true once the reader has
picked a level; when the dial unmounts it sends `{state, reform, off: true}`. The map listens
(see "Dial levels on the map" below).

### Dial levels on the map (`js/ui/columns.js`, `dialView` in `js/ui/map-state.js`)

| Part | How |
|---|---|
| When | Only once the reader moves the dial (the dial drawing alone leaves the map on the scenario). `dialView` holds the level: its county values, each county's highest P90 over the dial's levels and the highest county P50 over the levels |
| 3D | The county columns grow or sink to the level's P50 (glass cap to P90) with the usual `vh_t` motion (data set once with previous and next values). One height scale for every level of the dial (fitted on each county's highest P90), so the columns visibly rise as the dial goes up. Colour `--seq-5`. Tract columns step aside (the dial is county-level) |
| 2D | The county fill shades by the level's P50 on the `--seq-1` to `--seq-6` ramp, square-root spaced, on one scale for the whole dial. Each county's previous and next P50 are feature state (`da`, `db`), set once per level; `vh_dt` (global state) runs 0 to 1 over 700 ms; no data is set per frame. The map funnel layer steps aside meanwhile; the scenario's fill colour is put back afterwards |
| Chip | Over the map's free area: "Showing: <reform> at <level label> (dial)" and "Back to the scenario". A polite live region says once that the map shows the dial level |
| Key and cards | The map key shows the ramp and what it means; the hover card (2D) and the column card (3D) give the level's "likely P10 to P90, middle P50" |
| Back to the scenario | The button; leaving the Reform screen (the dial unmounts); a new scenario or state; turning on the year slider or the district view |
| Numbers | The county table's values are the engine's own floats; the browser writes them with `plain` (three significant figures, as for tract values) and compares them only to pick the scale. Reduced motion: every change is instant |

Test: `tests/e2e/test_comparisons_ui.py::test_dial_level_shows_on_the_map_and_back` (Testland):
moving the dial shows the chip and the key; the hover card gives the level's county values; the
3D column card names the level; "Back to the scenario" and leaving the Reform screen go back.

Tests: `tests/e2e/test_comparisons_ui.py` (Testland): an arrow key on the dial changes the shown
middle estimate to the API's text for the next level, and nothing is asked before the section
opens; Compare draws the waterfall (rows, headline, step texts) and the ranking (order and
middles) from the API; Method draws the ablation tiers and the 25-cell grid (focus shows the
cell's likely range); the static site shows the local-app note on all five cards and asks for no
comparison file.

## What this law changes (`js/ui/law-changes.js`)

On the Reform screen, for the five benchmarks and the two Pennsylvania ADU bills, the button **What this law changes** under "Add to scenario" turns on a map mode (`lawView.on`). The shell then shades each county by its answer (`lawClasses`, class ids `law_<answer>` in `MEASURE_COLOURS`: already allowed `--seq-2`, condition changed `--seq-4`, newly allowed `--seq-6`, unresolved `--no-data`, outside the bill `--not-modelled`), and the map key's body becomes `LawKey`: the four answers with the statewide share of each (the server's `shown` text, formula and ids in the title), the share of added homes in unresolved places, and a two-way switch, **Units authorized** / **Legal capacity**, that changes the values in the key, the table and the card. The switch, the table and the card show only server values; nothing is added up in the browser.

- **Card.** With the mode on, a picked county (map click, "Find a county", the table) opens `LawCard` in the right panel instead of the county panel: its answer and what it means, the proposal's citation and sections, the effective date (a benchmark has none and says so), the state law today, the denominator, the chosen value, observed or fallback, whether the engine uses the county's readings (the 50% bar), a table of the four answers' shares and the model stage each changes, and the municipal rows read (rule, why, section, document, dates, weight). Esc closes it.
- **Table twin.** "County table" in the key opens `LawTable`: every county with its answer, the four shares, the denominator and the chosen value; sortable by name or by most unresolved; a county name opens its card. Keyboard and screen reader users get every value the map shows.
- **Read next.** Under the toggle, `ReadNext` asks for the bounding runs only when pressed (they take about 20 seconds the first time on Pennsylvania): for each top unread ordinance the unread lots, the review effort, the change in statewide added homes if read as permissive and as restrictive, and whether the preferred option would switch.
- **Static site.** It reads `data/ST/law_changes/KEY.json`; when the export holds no such file the key and card say "What this law changes needs the local app (make app)."
- **Motion.** Only a colour fade on the key rows and list items, and only without reduced motion.

The mode turns off when the Reform screen closes; it stays on when another reform is picked.


## Budget and target (`js/ui/budget-target.js`, `js/lib/budget.js`)

The Compare screen's folded **Budget and target (ADUs)** section (`BudgetFold`; the ADU
Decision tab's link sets `budgetOpen` so it opens at once) holds `BudgetTarget` and, in its own
fold, `BudgetDemo`.

- **Data.** `lib/api.js` `budgetGrid(st)`: live `GET /states/ST/adu-budget-grid` (the grid and
  its `view`, written by `src/visionhousing/api/budget_view.py`); static `ST/adu-budget-grid.json`
  only when the index says `adu_budget_grid: true`, else the card says it needs the local app.
  The exporter call is `budget_view.write_static(grid_payload(ctx), out/ST)`.
- **Lookup.** `gridLookup` (`js/lib/budget.js`) is the twin of the server's `grid_lookup`
  (`scenarios/adu_budget.py`): it only picks tested cells and compares them with the inputs.
  `tests/test_budget_target.py` runs both over every combination of tested, bracketed, outside
  and missing inputs and checks they agree. The allowed-options choice filters the cells before
  the lookup; the Pareto flags are the server's, over every tested option.
- **Text.** Every number shown is the view's text, found by `cellKey` (`arm|appropriation|award`):
  added homes, share of modeled draws with its Monte Carlo standard error, net public cost,
  upfront appropriation, award, taken from later years (with the sign's meaning), owners funded,
  key lever. The chart only places points (cost across, share up).
- **Flags.** `input_missing`, `outside_tested_range`, `between_tested_values` (with the bracket's
  tested values), `budget_exceeded`, `no_tested_option_meets_both`; words from
  `assumptions/cards.yaml` (`budget_target`), each with its own mark shape. A target between two
  tested ones shows two blocks, one per tested target; no block for the typed value.
- **Covered share.** The law reading behind the law change, from `GET /states/ST/law-changes?r=adu`.
- **Demo path.** The ADU run's headline with its outcome scope; the lever card's
  `adu_timing_reliable`, `adu_timing_conserved`, `adu_financing_access` (labelled "old uncapped
  variant: no appropriation limit") and `adu_finance_budget` effects; the grid's cell at the
  capped variant's settings; and "does the ranking hold": the order of two server middle values
  (the only browser judgement, no number written).
- **Accessibility.** Native inputs with labels, a polite live result line, a table twin for the
  chart, a scrollable table region with a label, distinct flag marks, motion only without reduced
  motion.

## Baseline card, headline scope, hearing brief and profile coverage (`js/ui/baseline-card.js`)

- **Baseline card.** `BaselineCard` shows the rows of the state's `baseline_card.view`
  (written by `src/visionhousing/api/baseline_view.py` from the B1 card): historical years,
  permit columns (the Building Permits Survey's estimated-units columns), the data release and
  its date, the information cutoff, the projected years with the 2026 gap, the 2026 nowcast
  (diagnostic only), Pennsylvania's window sensitivity `baseline_pa_window_2022_2025` (a
  disclosed sensitivity, never subtracted) and the state reconciliation ("matched" only when
  the server found the rule's state total equal to the baseline middle value). Each row opens
  to its formula, source ids and assumption ids. The State screen's Results tab folds it under
  the key figures (its summary shows the reconciliation status); the Method screen shows it
  open (section "Baseline card"). Data: `lib/api.js` `baselineCard(st)`: live from
  `GET /states/ST/summary`, static from `ST/baseline-card.json`.
- **Headline scope.** `ScopeNote` says what a reform's headline counts (outcome accounting,
  `reform_results.<k>.accounting.headline.scope`, e.g. ADU "all work types") with the
  Census-comparable range beside it (`reform_results.<k>.accounting.records.comparable`).
- **Hearing brief.** `HearingButton` (State screen, Results panel foot) and the Export menu's
  "Hearing brief (PDF)" open the two-page brief of the scenario in a new tab to print:
  `GET /export/hearing` live, `ST/exports/<key>.hearing.html` static. When this build cannot
  give it (custom assumptions on the static site, an export without briefs) the button is
  disabled and the reason is written beside it.
- **Static index version.** Every `ST/index.json` carries `schema_version` (2 since the
  baseline card file and the hearing briefs; none means 1). `lib/api.js` reads up to
  `STATIC_SCHEMA`; newer files switch the controls that need them off, with the reason
  (`staticSupport`), rather than showing something else.
- **Empty profile sections.** The Place screen no longer draws a section with no data as an
  empty block: such sections are listed under "Not measured for this place", each with its
  own reason and the coverage sentence from `js/content/coverage.json` (which source it is
  built from, the levels and states that source covers, and the counties of a local-only
  source). The file is generated by `python -m visionhousing.profile.coverage` and checked by
  `tests/test_profile_coverage.py`.

Tests: `tests/e2e/test_hearing_links.py` (card rows equal the API, scope note, share-link round
trip and dropped parts, static custom-scenario status, coverage text, the hearing brief's run id
and numbers, live and static, printed on two pages), `tests/test_hearing_brief.py`,
`tests/test_profile_coverage.py`.

## Transit status (`js/ui/tod-status.js`)

Every `POST /run` reply carries `status_views` (`src/visionhousing/api/status_view.py`): one
view per reform whose engine result has a transit status (today only transit-oriented density
on a state with a stop table; Testland has none, so the key is `{}`). The view is all text:
`shown` (may the statewide range be shown), `partial`, `label` (the completeness label),
`measured` (`text`: "Measured contribution only: middle …", from the explanation's own shown
middle), `text` (the sentence the question box and the hearing brief print), `package_note`
(when the run holds the package), `rule`, `stages`, `legend`, and the `state` and `counties`
rows (cells with `word` and display `kind`).

Where a view says `shown: false`, no screen shows a statewide range for that reform: the Reform
headline, its chip and the decision view show `HiddenRange`; "How sure", the lever,
plausibility and dial cards and the drivers give way to the rule; the State screen's lead, key
figures, verdict and "Each reform alone" bars use the measured text; Compare's bar has no range
bar and its table row says "Not shown" (with the measured middle); the "How the reforms add up"
table marks the row; the slides (`content/slides.js` `deckOf`) replace the reform slide's big
number and drop its bar from interval charts; package totals keep the server's package record
and add `PackageNotes`. The funnel keeps its steps (they count land near measured stops) with
the label above them. A shown but partial view adds `PartialLabel` beside the range. "Unknown"
(dashed border, hatched notice fill, mark "?") and "measured: none" (solid dark outline, plain
surface, mark "∅") never share a style, and each chip carries its word (in the compact county
table the word is visually hidden and in the title). The question box (`lib/answers.js` and
`api/answers.py`, templates `result_status`, `result_status_note`, `result_partial`,
`coverage_status`) answers a hidden reform's result or target question with the view's `text`.
The hearing brief (`report/hearing.py`) prints the same text and rows. An older static export
without `status_views` shows the screens as before until it is exported again.

Tests: `tests/test_status_view.py` (the view, the hearing brief, the question box in Python
and Node), `tests/e2e/test_tod_status_ui.py` (Wyoming: no range on Reform, Compare and the
question box, the status table and county ladder; Pennsylvania: range with the partial label;
skipped without the built WY / PA packs).
