# User guide

> **Status.** Every screen shows the engine's results. Hindcasts of past reforms and a guided tour are not built yet.

## Run it

You need Python 3.12 with [uv](https://docs.astral.sh/uv/) and Node.js 22.12 or newer (with npm).

| What you want | Command | Then open |
|---|---|---|
| Install everything once | `make setup` | |
| Build a state's data | `make data STATE=PA` | |
| Use the tool (engine and interface) | `make app` | http://localhost:8000 |
| Change the interface with live reload | `make dev` (Ctrl-C stops both servers) | http://127.0.0.1:5173 |
| Save results for the no-server site | `make export-static STATE=PA` | writes `web/public/data/` |
| Build and try the no-server site | `make site` | http://127.0.0.1:4173 |

- `make app` builds the interface with Vite (`cd web && npm run build`) and then starts the
  engine's server, which also serves the built pages.
- The no-server site is what the public web address shows. It holds the default results only.
  Changing assumptions there shows a note: "Changing assumptions needs the local app (make app)."
- Without the state data, try the tool on Testland, the small made-up test state: open
  `http://localhost:8000/#/place/99001?v=1&s=TL`.

## The top bar

One bar, the same on every screen:

- **Left:** the product name. It takes you to the State screen.
- **Centre:** State, Reform, Compare, Method & data.
- **Right:** Slides, Export, Share link.

On a narrow screen the tabs and buttons fold into a menu.

## The four screens

Every number on every screen comes from the engine. Click (or press Enter on) any underlined
number to see where it comes from.

### 1. State

The home screen.

- **Left card:** pick a state. Turn reforms on and off. "All five reforms" switches on the five
  benchmark reforms together; "Back to baseline" turns everything off. In Pennsylvania two bills
  (HB 2186 and SB 1346) are offered too. Each bill reaches the same lots as the ADU reform, so
  switching one on switches the ADU reform off.
- **Map:** each county is shaded by the homes the reforms add there as a share of its own
  baseline (middle estimate): *Little effect*, *Adds some* or *Adds a lot*. The breaks are the
  verdict thresholds of the Assumption Ledger (the map key shows them), so the colours mean the
  same thing whatever is switched on. Grey means no data.
- **County table:** the map's values as a table you can sort, for keyboard and screen-reader
  use. Press a county name (or click the county on the map) to open its panel.
- **County panel:** the county's baseline, the homes added and the change, each reform alone in
  the county, and a small map of its census tracts shaded by the homes one reform adds in each
  tract, with the same values in a table.
- **Right card:** one sentence and three numbers (baseline homes, homes added, change from the
  baseline), each a likely range, and a verdict for each reform that is on.

### 2. Reform

One reform at a time, run alone.

- **What it adds:** the homes added and the change from the baseline, with the verdict.
- **The funnel:** eligible lots, then lots with room, then lots where building pays, then homes
  built in five years. Each step shows a likely range and its formula.
- **Top drivers:** the sensitivity tornado. Each input is moved alone from its low to its high
  value; the bar spans the homes added at each end. On a large state this takes a few minutes the
  first time, so it loads after the rest of the page.
- **What the model cannot tell you:** the limits of this reform and of the whole model.
- **Assumptions:** every Assumption Ledger entry the reform uses, with its low, central and high
  value. By default the model draws each across its range ("Range"). Pick Low, Central or High
  to pin it, or type your own value and press "Set custom". Changed rows are marked "Yours";
  "Reset all" undoes them. Your choices go into the share link and apply on every screen.
  On the static website this needs the local app, and the page says so.
- **Evidence:** places where a similar rule was tried, with what was permitted before and after
  and how far it carries over.

### 3. Compare

Each reform run alone, as likely-range bars on one scale (the mark is the middle estimate). The
package card shows how the five reforms add up: each reform alone added up, minus lots counted
twice, plus savings that feed another reform, equals all five together. The table lists the same
numbers; "Download CSV" saves every state and county record of that run.

### 4. Method & data

- **Data report card:** each model input, whether it is used, available, assumed or missing, its
  tier, edition, coverage and limits.
- **Sources:** source, version, access date, licence, cleaning and limits.
- **Assumption Ledger:** every assumption, searchable.
- **Validation:** the baseline backtest: each simple projection rule scored on years it did not
  see, and the rule the baseline uses.
- **Glossary** and **Downloads** (the results table, the brief and the data files).

## Everywhere

- **Explain this number.** Every underlined number opens a panel with what it is, its likely
  range, the formula, its inputs, the assumptions behind it and the sources.
- **Grey means no result.** "No data" is shown in grey and never counts as a result.

## Slides

The Slides button opens a 16:9 slide view of the current scenario, written by the engine from the
same run: a summary, one slide per reform, the top drivers (for one reform), the package waterfall
(for several) and the limits. Every slide footer carries the run's three fingerprints (code, data,
assumptions). "Read as page" shows the same content as plain text. "Print or PDF" prints one slide
per page.

## Export

The Export menu offers:

- **Brief (PDF):** saves the engine's print-ready brief as a page; open it and print to PDF.
- **Slides (PDF):** opens the slides and the print dialog.
- **Table (CSV):** every state and county result of the current scenario with its range.

Each file carries the code, data and assumption fingerprints, so anyone can rerun it. The static
website holds these for each reform alone, all five together and the baseline; other mixes need
the local app (`make app`).

## Share link

The Share link button copies the page address. The whole scenario lives in the address: the state, the reforms that are on, and every assumption you changed. Anyone who opens the link sees the same view. There are no accounts and nothing is saved on a server.
