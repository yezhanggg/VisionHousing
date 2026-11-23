# Module: `web/` — the browser interface

## What it is

The interface people use: one top bar, four screens and a slides view. It is plain HTML, CSS and
ES modules. There is **no build step and no Node**: the browser loads the files as they are.

## How the libraries arrive

| Library | Version | Licence | Used for |
|---|---|---|---|
| preact | 11.2.0 | MIT | drawing the interface |
| htm | 3.1.1 | Apache-2.0 | HTML-like templates in plain JavaScript |
| @preact/signals (+ signals-core 1.14.5) | 2.11.4 | MIT | the app's state |
| maplibre-gl | 6.17.0 | BSD-3-Clause | the county map |

- `web/package.json` pins exact versions. `web/package-lock.json` records each tarball's address and
  SHA-512 integrity value. Both exist so the libraries appear in the software bill of materials, in
  Dependabot and in the licence audit (`scripts/licence_audit.py` reads the lockfile).
- `scripts/vendor_web.py` (run by `make setup`) downloads each tarball from the npm registry,
  checks its hash against the lockfile, and copies only the needed files into `web/vendor/`
  (git-ignored). One wrong hash stops the run before anything is written. A second run with an
  unchanged lockfile needs no network. `--check` re-verifies the copied files.
- `web/index.html` holds an **import map**: library names such as `preact` point at the files
  in `web/vendor/`. Nothing is fetched from a CDN at run time.

## Files

| Path | What it does |
|---|---|
| `index.html` | Page shell, import map, stylesheet links |
| `css/tokens.css` | Design tokens: colours, type scale, spacing, radius, shadow, focus ring, motion |
| `css/app.css` | Top bar, cards, popovers, screens, phone layout |
| `css/slides.css` | Slides view and its print rules (one slide per page) |
| `js/main.js` | Mounts the app and picks the screen for the route |
| `js/state/codec.js` | Reads and writes the scenario in the address (pure, no browser globals) |
| `js/state/store.js` | Signals for the route and scenario; navigation; the toast |
| `js/content/*.js` | Words only: reform names, state names, glossary, the placeholder slide spec |
| `js/ui/*.js` | Top bar and Export menu, popovers (glossary and Explain), map, small parts, icons |
| `js/screens/*.js` | State, Reform, Compare, Place, Method & data, Slides |
| `css/place-print.css` | US Letter portrait page for printing a place profile; added only while the Place screen is open |

## Routes and the scenario in the address

`#/state`, `#/reform/<id>`, `#/compare`, `#/method`, `#/slides`, each followed by the scenario:

```
#/reform/adu?v=1&s=PA&r=adu,fees&a=adu.takeup~h&p=policymaker
```

| Key | Meaning | Rule |
|---|---|---|
| `v` | codec version | must be `1`; any other version falls back to the defaults |
| `s` | state | a known two-letter code |
| `r` | reforms on | known ids only, written in a fixed order, at most 8 |
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
- **No typed numbers.** Until the engine is connected every value is "—" and every panel says
  what will appear there. A browser test fails if any value slot shows anything else.
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
slide per 13.333 × 7.5 inch page. For now three placeholder slides come from a slide-spec array
(`js/content/slides.js`); the engine will fill the same shape from a run.

## The map

MapLibre draws the map with no basemap and no tile server. County shapes will come from the
state data pack as simplified GeoJSON. Until then the canvas is empty and says so. If the browser
cannot draw WebGL, the page says that in plain words.

## How it is tested

- `tests/test_vendor_web.py`: good hash passes, bad hash fails and writes nothing, loose pins and
  other hosts are refused, unsafe archive paths are refused, the real pins are exact, the import
  map points only at vendored files, and every module import resolves inside `web/`.
- `tests/e2e/test_shell.py`: serves `web/` locally, opens every route on desktop and phone sizes in
  Chromium, and fails on any console error or off-site request. It checks the top bar and tabs by
  keyboard, slide keys, one-slide-per-page printing, Share link, the Export menu, the popovers,
  the reform switches and the codec. Screenshots go to `nopush/logs/shots/`.

## Run it alone

`make serve-static` serves `web/` at http://localhost:8001 without the engine.

## Limits

- No results yet: every number waits for the engine (`src/visionhousing/`).
- The Brief (PDF) and Table (CSV) exports say so and do nothing until results exist.
- The `p` preset is stored and shared but nothing reads it yet.
