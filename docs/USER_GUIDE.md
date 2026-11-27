# User guide

> **Status.** Every screen shows the engine's results. The district view appears only for states whose data pack has district tables (and Testland's made-up districts). A guided tour is built in (see "Take the tour").

## Run it

You need Python 3.12 with [uv](https://docs.astral.sh/uv/) and Node.js 22.12 or newer (with npm).

| What you want | Command | Then open |
|---|---|---|
| Install everything once | `make setup` | |
| Build a state's data | `make data STATE=PA` (or `STATE=VT`) | |
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

## The layout

There is no top bar. One map fills the screen and stays put; everything else floats over it in
fixed places, so nothing covers anything else:

- **Top left:** the product mark. It takes you to the State mode.
- **Top centre:** the mode switcher: State, Reform, Compare, Place, Method & data. The map stays
  when the mode changes; only the two side panels change.
- **Left and right panels:** what they hold depends on the mode (see below). The **–** button in a
  panel's header folds it into a small box with its title and one key number; press the box to
  open the panel again. Drag the right panel's inner edge (or focus it and press Left / Right)
  to make it wider or narrower. This browser remembers both choices.
- **Bottom left:** "Ask about this run", the question box (see [Ask about this run](#ask-about-this-run)).
- **Bottom centre:** "Find a county" (press **/** to jump there; type a county name and press
  Enter to open it), zoom out, zoom in, "Fit view" (the open county or the whole state) and the
  Basemap switch. Two more appear when they apply:
  - **Districts**, where the state has legislative districts. It shows the district view (see
    "My district" below).
  - **North up**, in 3D once the map is turned more than a few degrees.
- **Bottom right:** the map's legend.

**Slides, Export and Share link** sit in the right panel's header in every mode, and in the
Slides view's toolbar (Export and Share link).

Clicking a county centres it in the middle of the screen and opens it in the right panel (on
Place it opens its profile). Esc closes it.

The bare address (`#/`) shows a short introduction with an "Open the tool" button; a link to a
mode (`#/state`, `#/reform/adu`, ...) opens the tool directly.

On a phone the panels become one sheet at the bottom; tap "Show all controls" or drag the
handle up to pull it up, and drag it down (or tap "Show the map") to lower it. The question box and
the legend sit just above the sheet and step aside while it is up.

With a keyboard, Tab moves through the screen in reading order: the modes, the left panel, the
map, the map controls, the right panel, the legend and the question box. On the map, Left and Right
step through the counties from the most homes added down, and Enter opens the one named.

## Ask about this run

The question box in the bottom-left corner answers a plain question from the run on screen.
Press "Ask" to open it, type a question and press Enter, or press one of the suggested
questions for the state. It can answer:

- **How many homes?** The likely range a reform (or all five) adds, its middle estimate and the
  baseline it is compared with.
- **Why does the baseline look high (or low)?** The baseline's range, its rule, the past years it
  reads and how often ranges like it caught the real total. When a separate check is on record
  (for Pennsylvania, the window that leaves out 2021), it names it with its id.
- **What drives the result?** The top three inputs, each moved alone from low to high.
- **Is it law?** The rule records for the reform: citation, status, start date, the date it was
  checked and its scope. No record means "unknown", never "no rule".
- **What is missing?** For a result with no measured number (for example transit-oriented density
  in Wyoming), the box says so, quotes the reason and names the next missing observation. It
  never puts a number there.
- **Will it reach a target?** Only the targets that were worked out in advance (the policy dials'
  target rows). Any other target is out of range for this run, and the answer says so.
- **Will ADUs reach a target within a budget?** ("Will ADUs reach 5,000 homes with a $20 million
  budget?") The answer reads the budget-and-target grid's tested cells: for the target, the
  options on the trade-off between cost and the share of draws meeting it, each with its share,
  standard error, net cost and range. A target between two tested ones gets both tested cells;
  one outside them is out of range. Without the grid in the build, it says so.
- **Can I build at this address?** The tool works at state, county and tract level and makes no
  lot-level decision; with a county picked it lists the local rules on record and their official
  links.
- **Can I share this number?** The exact link that reopens the view, the run id and the table
  download.

Each answer has a status ("From this run", "Not known from this run", "Out of range", "Not in this
run", "Can't answer"), the run id and period, a link that reopens the exact view, and "Show the
record", which opens every record the answer cites with its formula and sources. A scenario with
changed assumptions on the static site says it is not in this run; it never falls back to the
default run's number.

No AI model is involved: the box matches words to a fixed list of question types and fills a
written template with the run's own text. Your question is answered inside the page. It is not
sent to the server or anywhere else, and it is not kept.

## Take the tour

A short guided tour walks a first-time reader through the State screen in seven steps: pick a
reform, read the range, see why (Drivers), see where (the map key and 3D), check any number
(Explain), know the limits, and share it. Each step darkens the screen except for the part it
explains and puts a small card beside it, never on top of it.

- **Start it** with "Take the tour" in the Scenario panel's header (State mode), or on the
  introduction page. If no reform is on, the tour switches on accessory dwelling units first, so
  the range and the drivers have something to show.
- **First visit:** the State screen shows a small note under the mode switcher, "New here? A
  short tour shows how to read the results", with "Take the tour" and a close button ("Not
  now"). It never starts the tour by itself. Closing the note, or finishing or skipping the
  tour, stops the note coming back in this browser.
- **Keys:** Enter or Right goes to the next step, Left goes back, Esc closes. Tab stays inside
  the card. When the tour closes, the keyboard is back where it was.
- **On a phone** the tour pulls the panel sheet up first. A step whose part is not on the screen
  (the map key is hidden while the sheet is up) shows its card in the middle instead, so the
  tour always has the same seven steps.
- With reduced motion turned on, nothing slides or fades.

## My district

For "what does this reform mean where my voters live?".

1. Press **Districts** at the bottom of the map. The map key shows **House** and **Senate**:
   pick the chamber.
2. Each district is shaded by the homes the switched-on reforms add there per 1,000 of its 2020
   homes (middle estimate). Grey means no reform is on. **Hatching** marks a district where more
   than a set share of its homes (the ledger value `districts.split_caveat_share`; the map key
   shows it) are in census tracts it shares with another
   district: inside those tracts the added homes are shared out by where homes stood in 2020, so
   read its numbers with more care. In 3D each district stands as one column (the middle
   estimate, with a glass cap up to the likely high value).
3. Point at a district for its likely range. Click it to open its brief in the right panel.
4. Or use **Find my district** (press **/**): type the district number, its name, or your
   county. A county lists every district that holds some of its homes. Up and Down move through
   the list; Enter opens the district.
5. The brief shows the added homes for the scenario, the baseline, each reform's range, each
   reform's top driver (statewide; with several reforms on, press "Show each reform's top
   driver"), what the numbers cannot tell you, the counties the district covers and which plans
   were used. **Print / PDF** prints it on one page. Esc or **Close** goes back.

District numbers are shares of the tract and county results, not separate estimates. The fee
cut, parking and the baseline are worked out by county, so a district gets them in proportion to
its homes in each county. The static website includes the district view for every exported state.

## The modes

Every number on every screen comes from the engine. Click (or press Enter on) any underlined
number to see where it comes from.

### 1. State

The home screen.

- **Left panel (Scenario):** pick a state. Turn reforms on and off. "All five reforms" switches
  on the five benchmark reforms together; "Back to baseline" turns everything off. In
  Pennsylvania two bills (HB 2186 and SB 1346) are offered too. Each bill reaches the same lots
  as the ADU reform, so switching one on switches the ADU reform off.
- **Legend (bottom right):** its top row holds the **2D / 3D** switch (2D is the default),
  **Show range** (3D only) and **Permits by year** (see below). With a reform on, it shows the
  three classes; press one to fade the
  other classes on the map (press it again to show all). The County table button sits there too.
  A row of buttons (one drop-down list on a phone) switches what the map shades: *% of baseline* (the default),
  *Per 1,000 homes* (homes added per 1,000 existing homes), *Homes added*, or *Coverage* (how
  much evidence stands behind each county: local data, verified local rules read, and a
  baseline back-test that was not far off; *Good* is two or three of these, *Partial* one,
  *Thin* none). The classes and their breaks come from the server and never move with the
  scenario. The County table shows the same values, including each county's coverage checks.
- **Map:** each county is shaded by the homes the reforms add there as a share of its own
  baseline (middle estimate): *Little effect*, *Adds some* or *Adds a lot*. The breaks are the
  verdict thresholds of the Assumption Ledger (the legend shows them), so the colours mean the
  same thing whatever is switched on. Grey means no data. When a reform is switched on, the
  counties that change fill in from west to east. Point at a county to see a card with its
  likely range; click it to centre it and open its panel (the rest of the state dims; Esc or
  "Close panel" goes back).
- **Keys on the map:** click the map (or Tab to it), then Left / Right step through the counties
  from most homes added to least, with the card on each; Enter opens the one shown; Esc lets go.
  Shift with the arrows turns and tilts the map in 3D.
- **A county with no baseline:** where the baseline for a county is 0, a share of it means
  nothing, so the county is put in its own class, *No baseline*, and the table and card say
  "No baseline to compare; adds N homes" instead of a percent.
- **3D view:** press **3D** in the legend. The map tilts and a column stands on each county; its
  height is the homes added there (with no reform on, the baseline). The solid part reaches the
  middle estimate (P50) and a pale glass cap reaches the likely high value (P90); point at a
  column to see a thin dark mark at the likely low value (P10) and a card with the likely
  range. **Show range** hides the caps. Zoom in past the county columns and one column stands on
  each census tract instead (the reforms that work tract by tract; fees and parking work by
  county, so the tract columns leave them out). The column sizes are fitted to the screen and
  keep their size as you zoom; the legend shows three columns at the same scale, with ticks at
  the tallest one's middle and high values. Colours are the same classes as the flat map. The
  first time, the columns rise from north to south; a new scenario grows or sinks them from
  west to east. Drag with the right mouse button (or two fingers) to turn and tilt; the tilt
  follows the zoom until you tilt by hand. Press **2D** to lay the map flat again. With reduced
  motion asked for, every change is instant.
- **Permits by year:** press it in the legend to shade the counties by the homes permitted in
  one year per 1,000 existing homes (Census Building Permits Survey, from 2000 to the latest
  year), in four fixed classes. Drag the slider (or use the arrow keys) or press **Play**; the bars
  show the state's total each year, and the last five stops are the baseline's projected years
  2027-2031 with their likely range. The year table lists every county's homes and rate for the
  year shown; "est." marks the Census Bureau's estimates for places that did not report. The 3D
  view is off while the slider is on.
- **County table:** the map's values as a table you can sort, for keyboard and screen-reader
  use. It opens in the right panel. Press a county name to open its panel.
- **County panel:** the county's baseline, the homes added and the change, each reform alone in
  the county, and a small map of its census tracts shaded by the homes one reform adds in each
  tract, with the same values in a table. "Past accuracy" says how this tool's baseline did for
  the county in past years (how far off, and how often its range caught the real total). Where
  the county's zoning rules have been read and checked for most residents, "Local rules read"
  says the ADU estimate uses them instead of the statewide guess, and lists the rules.
  **On the map, stage by stage:** for a reform that works tract by tract, tabs step through its
  funnel (lots in scope → physically fit → financially feasible → expected permits; for
  transit-oriented density the first three are acres of land). The map shades every county,
  and the open county's tracts, by that stage; the panel shows the county's and the state's
  likely range for it. Left / Right move between stages and **Play** steps through them (any key
  or click stops it). Shading compares stages of the same unit only; when the unit changes the
  key says "Scale changes: now homes, not lots." Point at a tract for a small bar per stage. For
  fees and parking, which work by county, the panel says so instead.
- **Right panel (Results):** tabs.
  - *Results* leads with one number (the homes added, a likely range) and one sentence. With one
    reform on, the decision view follows (see Reform below). Then the three key figures
    (baseline homes, homes added, change from the baseline), each reform alone as bars, and a
    verdict for each reform that is on. Under each reform's headline a small line says what it
    counts (for ADUs: all work types) with the Census-comparable range beside it, and a folded
    **What the baseline is built from** card shows the baseline card (see Method & data) with its
    reconciliation status.
  - *Rankings* lists the counties by homes added, by homes added per 1,000 existing homes, or by
    the change as a share of the baseline (top ten or bottom ten). The order and the ranks come
    from the server. Press a county to centre it.
  - *Drivers*, *Limits* and *Brief* work as in the Reform mode below.

### 2. Reform

One reform at a time, run alone. The map shades the counties by what this reform alone adds.
The left panel holds the reform picker, what it adds, the funnel and the binding constraint; the
right panel has tabs for the decision view, the drivers, the limits, the assumptions, the
evidence and the brief.

- **Decision view** (the first tab): the briefing in five steps, one line and one small chart
  each. *Question*: the reform, what is counted (additional homes authorized by building permits),
  the years 2027-2031 and the date the model is frozen at; "The reform as modelled" opens its
  definition. *Answer*: the homes added (likely range), how often that happens across the
  simulated futures, and the years one by one when the engine splits them. *Where and why*: the
  county that gains most, the binding constraint, the top driver, and how many counties have
  good, partial or thin evidence; Drivers and Limits are one press away. *What to change*: the
  question to ask next, and one variation: the input that matters most moved to its high value,
  on the same simulated futures (on the static website this needs the local app). *Use it*: the
  one-page brief, the table and the share link.
- **How sure:** the share of the modeled draws in which the reform adds at least each of a few
  set numbers of homes (the thresholds are Assumption Ledger entries), as a step chart, each with
  its Monte Carlo standard error (draw-to-draw noise only); the homes added as a share of the baseline in the same simulated future; and what it
  costs. For the fee cut: the fee revenue given up, the public cost per added home, and the cost
  per added home if the cut were limited to one group of counties. For parking: the builders'
  saving. Other reforms have no direct public cost, and the page says so.
- **Binding constraint:** the funnel stage that loses the largest share of the step before it
  (worked out in each simulation draw; the card gives the middle draw and the likely range),
  with who acts there (state, municipality, utility, builder or household) and the next question
  to ask. Fees and parking have no site funnel; the card names the one lever they work through.
- **What would narrow the range** (under the drivers): how much of the uncertainty each input
  causes, and the research-agenda sentence (which input, if known, would shrink the range most).
- **About this chart:** the (i) under a chart opens its source and the longer method words.

- **What it adds:** the homes added and the change from the baseline, with the verdict.
- **Alternatives** (button under the headline, benchmark reforms only): one headline per reform,
  and behind this one click a compact table of every named alternative the tool has built for
  it, for example verified local parking minimums, the older Massachusetts 3A acre-share rule,
  budget-capped ADU programs, the practical-capacity screen, project choice on shared lots and
  the Census-comparable scope. Each row gives the homes added (middle and likely range), the
  change from the headline worked out on the same simulated futures (the alternative minus the
  headline in each future, then its middle and range), how often it is below or above the
  headline, one line on why it exists, and whether it is built. An alternative that is not built
  says why. The first opening after a change can take a few minutes on a large state; the static
  website shows it only when it was exported.
- **The funnel:** eligible lots, then lots with room, then lots where building pays, then homes
  permitted in five years. Each step shows a likely range and its formula. Pointing at a step
  shades the map by that stage, county by county (the other steps dim); the later homes steps
  all show the last map stage, expected permits. Under the card, **On the map** steps through
  the stages with tabs and Play, as in the county panel (with a county open, its tracts too).
  Fees and parking work through cost, not lots, so they have no map funnel; a sentence says so.
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
- **How much reform?** (Decision tab, folded, benchmark reforms only): the policy dial (see
  [How much reform?](#how-much-reform)).

**What this law changes.** The button under "Add to scenario" (the benchmarks and the two
Pennsylvania ADU bills) shades each county by one of four answers: **already allowed** (the rule
in force already gives what the reform gives, so it adds nothing there), **allowed, condition
changed** (allowed today, but behind a hearing, an owner or size rule, a parking minimum or a fee
the reform removes or cuts), **newly allowed** (the rule in force does not allow it), and
**unresolved** (nobody has read a rule in force that answers the question; the model uses its
statewide range there). Places a bill does not reach, such as Philadelphia and Pittsburgh under
a planning-code bill, are "outside this bill" and are left out of the shares. The map key gives
each answer's share of the state (in single-family lots for ADUs and missing middle, residents
for parking and fees, homes near a major stop for transit density) and the share of the added
homes that sits in unresolved places. Switch between **Units authorized** (the engine's added
homes) and **Legal capacity** (how many homes the law change makes legal: a range, from the
places read up to every unresolved lot). Click a county for its card: the bill's section and
effective date, the denominator, whether the number is observed or a fallback, the model step
each answer changes, and every municipal rule read there with its section and dates. "County
table" in the map key lists the same values for every county. **Read next** lists the unread
ordinances that matter most, with how much the statewide number would move if each were read as
permissive or restrictive, whether that would change which option adds more homes, and the
review effort. The static website shows this only when it was exported; otherwise it says it
needs the local app.

### Transit status (transit-oriented density)

Transit-oriented density adds homes only near major transit stops, and whether a stop is major is
known only where an agency's schedule could be found, read and counted. The **Transit status**
card on the Reform screen answers five questions for the state, and for every county under
"Every county": was a schedule feed **discovered**, is its **use allowed**, was the **service
measured** (how many bus stops had their departures counted), is there a **frequent stop**
(and one frequent at peak), and is the mid-rise building **feasible** there. Each answer has a
word and a mark. **Unknown (not measured)** is dashed and hatched; **Measured: none** (every stop
was measured and none qualifies) is a solid outline. They never mean the same thing: unknown is
not zero. "What the answers mean" lists every answer. A county card (pick a county) shows the
same ladder for that county.

When fewer than half of a state's bus stops have measured service (Wyoming today), the tool does
not show a statewide range for transit-oriented density anywhere: the headline, the decision view,
Compare, the slides, the hearing brief and the question box say **Statewide range not shown**,
give the completeness label ("Partial footprint: … bus stops … have measured service") and the
**measured contribution** (the middle of the homes near stops that were measured). The lever,
plausibility and dial cards and the drivers are left out for that reform, with the rule in their
place. A package total keeps its range, with a note that it counts transit-oriented density by
its measured contribution only. When the footprint is partial but above the rule (Pennsylvania,
Massachusetts, Vermont), the range is shown with its partial-footprint label beside it.

### Extra scenarios

Five more reforms answer questions the five benchmarks leave open: transit zoning with funded
infrastructure, housing by right on commercial land, adaptive reuse (office and retail
conversions), starter-home lots, and fee deferral or waiver with affordability terms. The method
note is [methods/extra_scenarios.md](methods/extra_scenarios.md).

- **Where:** in the State mode's Scenario panel, the folded **Extra scenarios** group under the
  benchmark switches; in the Reform mode, the folded **Extra scenarios** group under the reform
  picker. The group opens by itself when an extra is on. The list, the titles and the
  descriptions come from the engine (`GET /api/v1/reforms`, `kind: "extra"`, and
  `GET /api/v1/scenarios`).
- **Never a default.** "All five reforms", the Compare mode, the package and the tour use the five
  benchmarks only.
- **One that overlaps is greyed out.** Some extras reach the same lots or projects as a benchmark
  (funded transit zoning and the transit reform, commercial land and both transit reforms,
  starter-home lots and the ADU and missing-middle reforms, fee deferral and the fee cut). The
  engine will not count those homes twice, so it refuses the pair. With one of them on, the other
  switch (and its "Add to scenario" button) is greyed out and says why: "Not with ...: they reach
  the same lots or projects." Switch the first one off to use the other.
- **Reform mode:** an extra shows its range, funnel, drivers, limits, assumptions and brief like a
  benchmark, plus "Everything this scenario reports": each of its records with its likely range
  and formula. The lever, plausibility, policy-dial and implementation cards stay benchmark only.
- **Adaptive reuse** adds no permits: a conversion changes an existing building, which the
  Building Permits Survey does not count. Its homes show apart, under **Outside the permit
  count**, with the engine's own record name and range, and never join a package total.
- **Static website:** an extra whose run was not exported shows "Needs the local app (make app)."
  under its switch, and its Reform page says "This scenario needs the local app (make app)."

### 3. Compare

The map shades the counties by all five reforms together. Left panel: each reform run alone, as
likely-range bars on one scale (the mark is the middle estimate). Right panel: the package card shows how the five reforms add up: each reform alone added up, minus lots counted
twice, plus savings that feed another reform, equals all five together. The table lists the same
numbers; "Download CSV" saves every state and county record of that run. **More comparisons**,
under the table, opens how the reforms add up and whether their order holds (see
[Comparisons](#comparisons)). **Budget and target (ADUs)**, below it, compares tested ADU
options against a target and a budget (see [Budget and target](#budget-and-target)).

### Budget and target

A housing committee asks: "with this budget, which option has the best chance of reaching our
target, and at what net cost?" Open **Budget and target (ADUs)** on the Compare screen, or press
**Budget and target** on the ADU reform's Decision tab.

- **Inputs.** A target (additional ADU authorizations, 2027–2031), a budget ceiling in dollars
  (blank for none), the accepted chance (the smallest share of modeled draws you accept), the
  award size, and which options are allowed: the law change only, a state program only (today's
  law), or both.
- **What you get.** For every tested option: the five-year added homes (likely range), the share
  of modeled draws that meet the target with its Monte Carlo standard error, the net public cost,
  the upfront appropriation, the award, the authorizations taken from later years, the owners
  funded, the key lever (the law change or the program money), whether it is on the Pareto set
  (no other tested option is cheaper with an equal or higher share), and its status: meets the
  target at your accepted share, below it, or over budget. A chart plots cost against the share
  meeting the target, with the Pareto set joined; its **Table** button lists the same points.
- **Flags.** Each has its own mark and words: *input missing* (no target), *outside the tested
  range*, *between tested values*, *budget exceeded* and *no tested option meets both*.
- **Nothing is interpolated.** The grid holds only tested appropriations, awards and targets
  (`assumptions/adu_budget_grid.yaml`; they are illustrative settings, not estimates). A target
  between two tested ones is shown as a bracket: the cells of both tested targets, never a value
  made up between them.
- **Covered share.** The law reading behind the law change: the statewide shares of
  single-family lots already allowed, changed, newly allowed and unresolved, and the share of
  added homes in unresolved places (the same reading for every cell).
- **How to demo it** (folded; it runs the ADU variants, so the first time takes a few minutes):
  the ADU benchmark range with its outcome scope and date; predictable approvals; financed access
  on the same draws (the old variant with **no appropriation limit**, labelled as such); a
  budget-capped program; and whether the ranking of the two levers holds once the budget is
  capped.

On the website the panel needs the grid file; without it, it says it needs the local app.

### Place

A plain profile of one county or census tract. Left panel: find a county by name (or click it on
the map), pick a tract, and the place in brief: a grid of key figures, one per section (press one
to go to that section). Right panel: how the
county ranks among the state's counties, then each section led by its headline and a few points,
its charts (each with a "Table" button that shows the same values) and its facts, folded below.
Every fact names its source and year. "Print / save PDF" prints the profile. A section with no
data for the place is not shown as an empty box: it is listed under **Not measured for this
place**, with why, which source the section is built from and what that source covers (for
example "published for every state, by county", or "local only: only for counties 42101"). Not
measured never means zero.

### 4. Method & data

The map shades each county by the baseline's back-test error (blue where it ran low, red where
it ran high). The left panel lists the sections; pressing one scrolls the right panel to it.

- **Data report card:** each model input, whether it is used, available, assumed or missing, its
  tier, edition, coverage and limits.
- **Sources:** source, version, access date, licence, cleaning and limits.
- **Assumption Ledger:** every assumption, searchable.
- **Validation:** the baseline backtest: each simple projection rule scored on years it did not
  see, and the rule the baseline uses.
- **Baseline rule contest:** every rule's typical miss, typical error, how often its range caught
  the real total and how wide the range was, with the winner and the choosing rule that was
  fixed before any score was computed. Below it: the stress ranges (the baseline's range if the
  next five years look like past slump years, or past boom years) and the surge-year note.
- **Sparse-data test and ADU sensitivity** (folded, under the confidence card): the method with
  less data, and ADU take-up with construction cost (see [Comparisons](#comparisons)).
- **Baseline card:** what the no-change baseline is built from: the past years it reads, the
  permit columns it counts (the Census Building Permits Survey's estimated units in 1-, 2-, 3-4
  and 5+ unit buildings), the data release and the date it was posted, the information cutoff
  (nothing released after it is used), the projected years 2027-2031 and the gap year 2026, a
  2026 nowcast from this year's monthly permits (a diagnostic: no estimate uses it), for
  Pennsylvania the 2022-2025 window sensitivity (disclosed, never subtracted from any result),
  and the state reconciliation (the rule applied to the state's own yearly totals, "matched"
  when it equals the baseline's middle value; Vermont is an outside control). Each row opens to
  its formula, sources and assumption ids. The State screen shows the same card folded under the
  key figures.
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

## Explanation cards

Four short cards help you judge a result. Each has one line on top, one picture, and more behind
the (i).

- **How sure: two separate signals.** On the left, how well the baseline's past ranges caught
  what was really permitted. On the right, how much outside evidence exists for each reform type
  (tested elsewhere, observed elsewhere, law on record). The two are never added into one score.
- **Is this rate plausible?** Our likely range of homes per 1,000 eligible lots per year (or per
  100,000 residents) on a strip, beside rates seen in real places. Point at or tab to a dot to see
  the place and its caveat. When nothing comparable has been observed, the card says so.
- **What would move it.** The step that loses the most homes (the binding factor), who can act on
  it, how much a named change (for example easier homeowner financing) would add or remove as a
  likely range, the evidence grade, and one thing to check next. For a county it shows the
  county's range. It runs extra model runs, so the static website shows it only where it was
  saved; the local app (`make app`) always can.
- **Is the rule in force?** One row per law or bill that touches the reform here, for example
  "Legally effective · local guidance issued · permit results not yet published". A pending bill
  shows as pending and does not change the baseline. "Sources and dates" lists every step's date
  and source.

Where they appear: on the **Reform** screen's Decision tab (what would move it, is this rate
plausible, is the rule in force; for a Pennsylvania bill, only "is the rule in force", under the
ADU reform); on **Method & data**, under the back-test (how sure); in the **county panel** when
exactly one reform is on (what would move it there, is the rule in force there; local app only);
and on **Place**, under the profile (is the rule in force for each reform switched on, or all
five, and how sure).

## How much reform?

A reform can go further or less far than its benchmark. The **policy dial** shows what each
step does. Open it on the **Reform** screen: on the Decision tab, under the explanation cards,
press **How much reform?** (the five benchmark reforms only).

- **The dial** snaps to the levels the model actually ran, for example how many eligible lots
  an ADU rule reaches. The benchmark level is marked. Drag it, or tab to it and use the arrow
  keys (Home and End jump to the ends).
- **At the chosen level:** the middle estimate of homes added in 2027-2031 and its likely range.
- **Each step:** about how many homes one more step adds, and a note when the steps add less and
  less (diminishing returns).
- **The chart** shows every level at once: the line is the middle estimate, the band the likely
  range (P10 to P90). "Table" shows the same values.
- **What would it take?** Pick a number of homes; the card names the level that gets there in the
  middle future and in 9 of 10 futures, or says "out of reach" when no level the model ran does.
- **ADUs** have two dials (how many lots the rule reaches, and the owner-occupancy rule) with a
  switch, plus a sentence on how much homeowner take-up alone would need to change.

Every other reform stays at its benchmark, and every level uses the same simulated futures, so
the levels differ only because of the dial. The (i) gives the rules behind each sentence.

## Comparisons

Four more cards answer bigger questions. Each sits in a folded section and is worked out only
when you open it. On a large state the first time after the app starts can take about a
minute; the card says "Computing…" meanwhile. After that it opens at once.

- **How the reforms add up** (Compare, under the table, in **More comparisons**): the five reforms
  run alone and added up, each named overlap or saving between them, and all five together, as a
  waterfall. The (i) has each reform alone beside "last in" (all five minus the other four).
- **Does the order of the reforms hold?** (same section): the reforms from most to fewest homes
  with their likely ranges, the cases where moving one input to its low or high end changed the
  order, and how often one reform adds more than another across the simulated futures.
- **With less data** (Method & data, in **Sparse-data test and ADU sensitivity**): the same
  method run with fewer inputs (data tiers 2, 1 and 0), to show how it behaves in a state with
  less complete data: the likely range per tier, how far the middle moves, how much wider the
  range gets, and which missing dataset hurts most.
- **ADU take-up and construction cost together** (same section): a 5 x 5 grid of added ADUs as
  both inputs move. Point at or tab to a cell for its likely range; the benchmark cell is marked.

On the static website these cards say "These comparisons need the local app (make app)." unless
that state's comparisons were saved with the site.

## Export

The Export menu offers:

- **Brief (PDF):** saves the engine's print-ready brief as a page; open it and print to PDF.
- **Slides (PDF):** opens the slides and the print dialog.
- **Table (CSV):** every state and county result of the current scenario with its range.
- **Hearing brief (PDF):** two pages for a hearing memo, opened in a new tab to print: the
  question, the answer as a likely range with what the headline counts (for ADUs, all work types,
  with the Census-comparable range beside it), the baseline card, what the law changes (when
  known), the main drivers, what is unknown, the limits, and the run id with the command that
  rebuilds it. The State screen's Results panel has a **Hearing brief** button too; when the
  brief is not available (for example custom assumptions on the website) the button is greyed
  out and says why. From the command line: `visionhousing hearing-brief --state PA --reforms adu`.

Each file carries the code, data and assumption fingerprints, so anyone can rerun it. The static
website holds these for each reform alone, all five together and the baseline; other mixes need
the local app (`make app`).

## Share link

The Share link button copies the page address. The whole scenario lives in the address: the state, the reforms that are on, and every assumption you changed. Anyone who opens the link sees the same view. There are no accounts and nothing is saved on a server.

The baseline housing cycle (Low, Central, High, under the Assumptions tab of a reform) is part of
the link too: `preset=low` or `preset=high` (Central is the default and is not written).

A link is never quietly changed into another view. If a link has parts this version cannot use
(an unknown reform, a bad assumption value, a link from a newer version, or the old `p` view
preset, which is no longer used), a note at the top lists what was left out. On the website, a
link with changed assumptions or a non-default housing cycle says **Custom scenario, not on this site** and shows no number;
open it in the local app, or press **Use the default assumptions instead**.
