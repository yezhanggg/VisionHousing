# Method: comparisons

The headline answers "what does each reform add, and all five together?". This
page explains the five comparisons that follow from it: how the reforms add up,
how much reform, which inputs decide a pair of reforms, what happens with less
data, and whether the order of the reforms holds. The code is
`src/visionhousing/engine/comparisons/`; every setting is in
`assumptions/comparisons.yaml` and `assumptions/data_tiers.yaml`, each with an id.

Two further questions about how the package adds up (which project a shared
lot gets, and whether an area's builders and lenders can take it all on) are
answered by the package option `market`: see [market.md](market.md).

## One rule for all five: common random numbers

Every comparison reruns the model with one thing changed and everything else
held exactly the same: the same seed, the same position of the baseline in its
likely range in each draw, and the same draw of every uncertain input. Two runs
then differ only because of the thing that was changed. A difference is worked
out **draw by draw** and only then summarised as P10 / P50 / P90 (a *paired*
difference). The middle of a difference is never the difference of two middles.

The comparisons start from one prepared run (the bench). Its packages are built
by the engine's own package rules, so the package of all five equals the
headline package in every draw, and the benchmark level of every dial equals
the headline exactly. The tests check both.

## 1. How the reforms add up (interaction accounting)

- **Net interaction**, per draw, for the state and each county:
  package − (sum of the five reforms run alone).
- **Last-in effect** of a reform: package of all five − package of the other
  four. It depends on no ordering. Its **interaction share** is the last-in
  effect minus the reform alone: positive when the other four make the reform
  work better, negative when they take its lots.
- **Ordered allocation**: the reforms enter one at a time in a stated order
  (`comparisons.interactions.ordering`: the Handbook's numbering, ADU, missing
  middle, TOD, parking, fees). Each is credited with what it adds to the package
  built so far. The credits add up to the package exactly, in every draw.
  Another order would give another split; that is why the order is stated and
  the last-in effects are shown beside it. A full fair-share split over every
  order (32 combinations) is not done; the 5-run last-in version answers the
  same question without an arbitrary order.
- **Named mechanisms**, each with a sentence written by rule from its numbers
  ("about zero" under `comparisons.interactions.zero_homes`):
  - the fee cut makes more ADU and 2-6 unit projects pay (their fee lines fall
    by the same share);
  - parking reform lowers their parking lines (the benchmark ADU needs no added
    parking, so that part is zero);
  - ADUs and missing middle compete for the same single-family lots;
  - parking reform and TOD near stations: measured as the package of the two
    minus each alone. It is zero by construction: the TOD pro forma already
    builds reduced parking, and the parking reform counts baseline production
    only, so no home is counted twice.
- **Waterfall**: the sum of the reforms alone, each mechanism, the package. Per
  draw the steps add up exactly; each step's P50 is the middle of its own draws,
  so the step P50s need not add up to the package's P50, and the tool says so.

## 2. How much reform (policy dials)

Each reform's main setting is turned through 3-4 levels, one dial at a time,
every other reform at its benchmark, on the same seed:

| Dial | Levels (least to most generous) | Benchmark |
|---|---|---|
| ADU: share of eligible lots reached | 25%, 50%, 75%, 100% | 100% |
| ADU: owner-occupancy rule | owner must live on the lot; no rule | no rule |
| Missing middle: homes allowed per lot | 2, 3, 4, 6 | 6 |
| TOD: distance from a major stop | 1/4 mile; 1/2 mile rail and 1/4 mile frequent bus; 1/2 mile | the middle level (the benchmark rule) |
| TOD: height near stops (floor area moves with it: FAR = 0.5 × storeys) | 4, 5, 6, 8 storeys | 5 |
| Parking: minimum left in force | today's; half; none | none |
| Fees: cut | 25%, 50%, 75%, 100% | 50% |

The owner-occupancy rule keeps, in each tract, the owner-occupied share of
occupied households (census tenure) as the share of lots it leaves open: a
proxy, because owners of single-family homes are a larger share than owners of
all homes. A halved parking minimum means the spaces still built are never
below half of today's minimum.

**Stage caching.** Stages before a dial are not redone. Data, ledger, baseline
and shared samples are prepared once. The ADU dials act on eligible lots, and
every later ADU stage scales with the lots reached, tract by tract, so each
level is the benchmark's cached tract stages times the reach (no rerun; the
tests check it equals a full rerun). Other dials rerun only their own reform.

**Readouts, written by rule.**

- *Each step adds about X homes*: the P50 over draws of (state added units at
  the most generous level − at the least generous level) ÷ the number of
  steps, paired draw by draw. The single steps' own P50s are shown beside it.
- *Diminishing return*: the first level after which a step adds less, per unit
  of the dial's axis, than half (`comparisons.readouts.diminishing_step_share`)
  of what the first step added per unit.
- *Target seeker*: each curve is inverted into a small table for the targets in
  `comparisons.readouts.targets`: the first level whose state P50 reaches N
  homes (the axis value between two levels interpolated linearly), "out of
  reach" when even the most generous level falls short. The same is given for
  "9 in 10 futures" (P10 at or above N). The browser only looks up rows.
- *ADU adoption threshold*: "what annual take-up would this ADU policy need to
  reach N homes in five years?". The whole take-up range
  (`adu.takeup_feasible_annual`) is multiplied by m. m is always found by
  bisection: the smallest multiplier whose state P50 (and P10) is within
  `comparisons.adoption.tolerance_homes` of N; the search starts at
  `comparisons.adoption.max_multiplier` and doubles the bound if that is not
  enough. Take-up acts only on the last two stages, so this runs on the cached
  feasible stage. The answer is the rate m × the range's central value, and
  whether it is inside, below or above the ledger's range. The ceiling is every
  feasible lot building one ADU, net of displacement (feasible lots × kept
  share); "out of reach" means even that falls short.

## 3. Two inputs at once (matrix)

State added ADUs, P50 and P10-P90, for each pair of a take-up multiplier and a
construction-cost multiplier (`comparisons.matrix`). A multiplier scales an
input's whole range, so each draw keeps its place in the range and every cell
is paired with the benchmark. Cost changes which lots pay, so each cost column
runs the ADU funnel once; take-up rows come from the column's cached feasible
stage. The benchmark cell is the headline exactly.

## 4. With less data (data ablation)

The method must run for states with less complete data. The only place where
the full-data answer is known is a data-rich state, so the test hides some of
its data and compares (`visionhousing run --data-tier 0|1|2`).

| Tier | Inputs kept | What hiding the rest does |
|---|---|---|
| 2 | Everything (full data) | — |
| 1 | National and state-level sources | Ordinance readings off (statewide already-legal ADU share, parking minimums reach every county); the borrowed ADU fit range widened as for any state without its own lots (`assumptions/transfer.yaml`); walksheds off (straight line); county pay → county mean |
| 0 | National sources only | Also: the state's own already-legal table off (national range); state pay → national |

Nothing is invented: a masked input is simply absent, and the fallback the
engine already uses for a state without it fires. Construction pay is listed
but the engine reads a cost factor only where a pack has one; where it has
none, the test says "no effect".

Four measures, each tier against full data, per reform, same seed:

| Measure | Formula | Good result |
|---|---|---|
| Middle shift | P50 over draws of (ablated ÷ full − 1), state; the paired difference (ablated − full) and the per-draw ratio are also shown with P10 / P50 / P90 | Small, and explained |
| Range widening | (P90 − P10) ablated ÷ (P90 − P10) full, state | At least 1 |
| County rank agreement | Spearman rank correlation of county P50s (a descriptive screen) | Above about 0.9 |
| Coverage | Share of counties whose full-data P50 lies inside the ablated P10-P90 | Near 80% or higher |

Each input is also hidden alone, and the inputs are ranked by the absolute
middle shift of the package of all compared reforms (one paired, per-draw
measure on one set of draws), then by the package's range widening: "which
missing dataset hurts most", direct advice to a sparse-data state on what to
collect first. The mean shift over the reforms is shown beside it, not ranked
on. The masked runs rebuild the state's calibration from the same base ledger
the full-data run started from. A shift below `data_tiers.measures.still_shift`
is written as "does not move".

The property "a lower tier is never narrower" is checked on every run and
reported, not assumed. On the Testland fixture with all three tiers it holds at
the default 2,000 draws (with a few hundred draws, percentile noise can hide a
small widening).

## 5. Does the order of the reforms hold? (ranking stability)

The reforms are ranked by state P50. For each reform, its uncertain inputs are
screened by Spearman rank correlation with its state total across the draws (a
descriptive screen, not a causal measure), and the top ones
(`comparisons.ranking.top_per_reform`) are pinned at the low and then the high
end of their ledger range, same seed. The reforms that read the input are
rerun and ranked again. The tool reports in how many cases the order changed,
which reforms moved, which pairs have overlapping likely ranges (P10-P90), and
in what share of the paired draws one reform adds more than the other.
Overlapping ranges mean the model does not settle the order of that pair.

## What these comparisons cannot tell

- Dial levels between the computed ones are interpolated; the model was not run
  there.
- The owner-occupancy proxy uses all occupied households, not single-family
  owners only.
- The ablation can only hide data the state has; it cannot show what data a
  state lacks entirely would have said.
- Ranking stability moves one input at a time; inputs that move together are
  covered by the likely ranges themselves.

## Where the numbers are

`GET /api/v1/states/{st}/comparisons?part=interactions|dials|matrix|ablation|ranking|all`,
`visionhousing comparisons --state PA`, and the Compare and Method screens.
Every number there is a record with its formula and the ids above.
