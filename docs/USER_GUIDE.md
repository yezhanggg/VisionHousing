# User guide

> **Status.** The interface is being built. The screens below describe the design. Parts that do not work yet are marked **Planned**. Until the engine is connected, the screens show no results.

## Start the tool

```
make setup
make data STATE=PA
make app
```

Then open `http://localhost:8000`. **Planned:** `make data` and `make app` are not built yet.

## The top bar

One bar, the same on every screen:

- **Left:** the product name. It takes you to the State screen.
- **Centre:** State, Reform, Compare, Method & data.
- **Right:** Slides, Export, Share link.

On a narrow screen the tabs and buttons fold into a menu.

## The four screens

### 1. State

The home screen.

- **Left card:** pick a state. Turn reforms on and off. Each reform shows whether it is in force, proposed or an idea. "Back to baseline" turns them all off.
- **Map:** counties shaded by added units per 1,000 existing homes. Colours keep the same breaks when you change the scenario, so maps can be compared.
- **Right card:** one sentence and three numbers (baseline units, added units, percent change), each as a range. A short verdict for each reform that is on.

**Planned:** results on this screen need the engine.

### 2. Reform

One reform at a time.

- **The funnel:** eligible sites, then sites that fit, then sites that pay off, then units built in five years. Each step shows a range and its formula.
- **Top drivers:** the inputs that move the result the most.
- **Assumptions:** for each input pick Low, Central, High or your own value. Each shows its source and a reset button.
- **Evidence:** where the reform has been tried, predicted vs. observed.
- **What this cannot tell you.**

**Planned:** results, drivers and evidence need the engine.

### 3. Compare

All reforms side by side as ranges against the baseline. A waterfall shows how a package combines: sum of parts, minus overlap, plus interaction. A table you can export.

**Planned.**

### 4. Method & data

- A data report card for the state (what is available, used or assumed, by tier).
- The provenance table: source, version, access date, licence, cleaning, limits.
- The full assumption ledger, searchable.
- Validation results.
- The glossary.

**Planned.**

## Everywhere

- **Explain this number.** Click any number to see its formula, inputs, sources and range. **Planned.**
- **Grey means no result.** "No data" and "not modelled" are shown in grey and never count as a result.

## Slides

The Slides button opens a 16:9 slide view of the current scenario, one slide per page. "Read as a page" shows the same content as plain text. Use your browser's print dialog to save it as a PDF.

**Planned:** slide content needs the engine. The slide route and printing are being built.

## Export

The Export menu offers:

- **Slides (PDF):** opens the slides and the print dialog.
- **Brief:** a print-ready page. **Planned.**
- **Data (CSV):** the numbers behind the current view. **Planned.**

Each export will carry the code version, the data manifest fingerprint and the assumption fingerprint, so anyone can rerun it. **Planned.**

## Share link

The Share link button copies the page address. The whole scenario lives in the address: the state, the reforms that are on, and every assumption you changed. Anyone who opens the link sees the same view. There are no accounts and nothing is saved on a server.
