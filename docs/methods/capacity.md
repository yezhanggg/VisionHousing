# Method: practical capacity (sewer, flood zone, steep slope)

A reform changes what the rules allow and what pencils. That is **nominal
capacity**. Some of those lots cannot be built on in practice: there is no
public sewer and the lot cannot take a septic system, the lot is in FEMA's flood
zone, or it is too steep for a small project without major grading. What is left
is **practical capacity**.

The practical capacity screen multiplies each reform's added homes by a
**practical capacity multiplier** per tract and draw, between 0 and 1. It is a
named option beside the headline. The five benchmark presets never change:
without the option every number is exactly today's, and with it today's number
is reported as the **"not screened"** case, the same draws.

| How to ask | What it does |
|---|---|
| *(nothing)* | Today's numbers (the headline). |
| `run(..., capacity_screen=True)`, API body `"capacity_screen": true` | Every reform in the run, and the package, screened beside today's numbers (`capacity` block). |
| Reform option `{"variant": "<reform>_capacity_screen"}` (or `{"capacity_screen": true}`) | That reform's own headline is screened, so it flows through the package, the readouts and the explanation like any other variant. Variants: `adu_capacity_screen`, `missing_middle_capacity_screen`, `tod_capacity_screen`, `parking_capacity_screen`, `fees_capacity_screen`. |

Asking for both at once (the run option and a screened variant) is refused, so
nothing is screened twice.

The code is `src/visionhousing/engine/capacity.py`. The rules (screens, tables,
which reform takes which septic rule, the sentences) are in
`assumptions/capacity.yaml`, each with an id. Every uncertain number is a ledger
entry `capacity.*` in `assumptions/parameters.yaml`, with its range, kind,
source and rationale; all are team judgement (status draft). Their values are in
the Technical Appendix.

## The data each screen reads

Only tables already in the state pack are read; nothing new is downloaded.

| Screen | Pack table and column | What it measures | Method of the table |
|---|---|---|---|
| Public sewer service | `infra_tract`: `sewer_share`, `sewer_known`, `constraint_share`, `sewer_status` | Share of the tract's homes inside a mapped public sewer service area and not under a constraint (verified = `sewer_share` − the constraint homes inside the area; `sewer_share` includes them), outside every area of a complete county map, under a documented constraint, or with no official answer (unknown) | [infrastructure.md](../modules/infrastructure.md) |
| Public sewer service, estimated (Massachusetts) | `ma_sewer_estimated_tract`: `est_share`, `circle_only_share` | Of the homes no official answer speaks to: inside a MassDEP estimated sewer service polygon, outside every one, or inside only a placeholder circle near a plant (left as unknown). Its own evidence class, never verified | [ma_capacity.md](../modules/ma_capacity.md) |
| FEMA flood zone | `hazard_tract.sfha_share` | Share of the tract's homes in FEMA's Special Flood Hazard Area; blank where FEMA has no digital map for most of its homes | [hazards.md](../modules/hazards.md) |
| Steep slope | `hazard_tract.steep_share` | Share of the tract's land steeper than 15% | [hazards.md](../modules/hazards.md) |
| Wildfire | none | No wildfire table is in any state pack yet | - |

## The multiplier

Per tract `t` and draw `d`, every share between 0 and 1:

    sewer  = verified + outside x septic + constraint x capacity.constraint_buildable_share
             + estimated_in x (e_in + (1 - e_in) x septic)
             + estimated_out x (e_out + (1 - e_out) x septic)
             + unknown x (served + (1 - served) x septic)
    flood  = 1 - F x (1 - capacity.flood_buildable_share)
    steep  = 1 - min(1, S x capacity.steep_lot_exposure) x (1 - capacity.steep_buildable_share)

    multiplier = sewer x flood x steep          (wildfire: 1, not measured)

- **Unknown sewer is a range, never "served".** `served` is
  `capacity.unknown_sewer_served_share`, drawn in each draw: that share of the
  unknown homes counts as on public sewer, the rest as outside it. A tract the
  table does not list, or a state without the table, is wholly unknown.
- **Estimated service is its own class, drawn as a range.** Where a state
  publishes only an estimate of where sewer service is (Massachusetts:
  MassDEP's estimated service areas, "for broad planning purposes"), it speaks
  to the homes no official answer covers. Inside an estimated polygon,
  a share `e_in` = `capacity.estimated_sewer_served_share`
  counts as served; outside every polygon, `e_out` =
  `capacity.estimated_outside_served_share`. Both are
  drawn in each draw; neither is ever 1, so an estimate is never treated as a
  verified answer. Homes inside only a placeholder circle stay unknown. A state
  without the table (Pennsylvania, Vermont, Wyoming) is unchanged.
- **Septic.** A lot outside public sewer can still take one added home if the
  on-lot system can be enlarged (`capacity.septic_buildable_share_single`, used
  for the ADU and starter-home families), but rarely a 2-6 unit building or an
  apartment building (`capacity.septic_buildable_share_multi`, every other
  family). The rule is `capacity.septic_rule`.
- **Flood zone.** `F` is the tract's share of homes in the flood zone. Homes on
  existing single-family lots stand in for the lots a reform makes eligible. A
  floodplain permit is harder, not impossible: `capacity.flood_buildable_share`
  of those lots are still built with mitigation (raised above the base flood
  elevation). Where the flood share is blank (no digital map), `F` is the
  county's mapped share (homes-weighted over its tracts that have one; the
  state's when the county has none) times `capacity.flood_unmapped_factor`,
  drawn in each draw; it is never taken as dry. A state with no hazard table at
  all draws `F` from `capacity.flood_share_no_data`.
- **Steep slope.** `S` is the tract's share of land steeper than 15%. Homes sit
  mostly on the flatter part of a tract, so only `capacity.steep_lot_exposure`
  of that share falls on eligible lots, and `capacity.steep_buildable_share`
  of the steep lots are still built with grading or retaining walls. A state
  with no hazard table draws `S` from `capacity.steep_share_no_data`.
- Every factor is at most 1, so **the screen never adds a home**.

## How the screens overlap: a range, not a product

The pack measures each screen on its own: the share of a tract's eligible lots
that pass the sewer test, the flood test and the slope test. It does not
measure where they coincide (no joint layer of sewer, flood and slope exists in
any pack). Multiplying the three shares assumes they are independent, which no
data shows. So the screened result is reported as a **range**:

For pass shares `a`, `b`, `c` of the same lots, the share passing all three lies
between

    max(0, a + b + c − 2)    the excluded lots never coincide (lowest)
    min(a, b, c)             they coincide as much as they can (highest)

(for two screens: between `max(0, a + b − 1)` and `min(a, b)`). The product
`a × b × c` (independence) always lies inside and stays the reported middle
case (`screened`). The ends are records `screened_disjoint` and
`screened_nested` (run option) or `capacity_screened_disjoint` and
`capacity_screened_nested` (screened variants), worked out per tract and draw,
then summed; for parking and fees they enter through the county exposure the
same way as the product. A rule-written sentence (`binding.<reform>.dependence_text`)
gives the range. If a joint measured layer is added, its own marginal totals
must agree with the single-screen shares.

## No exclusion counted twice

Where a reform's own legal rule already removed some lots, the screen works on
the lots that are left (`capacity.legal_exclusions`):

- **Starter-home lots** already drop lots outside every mapped public sewer
  area (a new lot needs a sewer lateral). Their sewer screen is the share
  buildable among the lots not outside a mapped area:
  (verified + constraint × k + unknown × (served + (1 − served) × septic)) /
  (1 − outside).
- **Transit zoning with funded infrastructure** screens water and sewer in its
  own funnel, so its sewer screen is skipped (`capacity.own_sewer_screen`).

## Tract-level and county-level reforms

- **ADU, missing middle, TOD** (and the tract-level extra scenarios) are
  screened tract by tract: screened homes = added homes x the tract's
  multiplier, in each draw. A screened variant adds a last funnel stage,
  `capacity_screened`, so the funnel still ends at the headline.
- **Parking and fees** add homes by county, on the same kinds of sites the
  baseline already builds on, and those sites have passed the sewer, flood and
  slope tests in practice. Only the share `capacity.county_reform_exposure` of
  their added homes faces the screen, at the county's homes-weighted mean
  multiplier (weights: the tract table's `housing_units`):
  screened = added x (1 - exposure x (1 - county mean multiplier)). The range of
  the exposure runs from none to all.
- **Transit zoning with funded infrastructure** already screens water and sewer
  readiness in its own funnel, so the sewer screen is skipped for it
  (`capacity.own_sewer_screen`); flood and slope still apply.
- **The package** (run option only): each county's package homes x the
  county's screened share of its reforms' homes (sum of screened reform homes /
  sum of not-screened reform homes), in each draw. So the overlap and the
  interactions shrink in proportion.

## Same draws

Every `capacity.*` entry is drawn from the run's own seeded stream for that id
(`ledger.sample`), so the screened numbers are paired with today's draw by
draw, and the run option and the variants give the same screened draws. Every
difference is worked out draw by draw, then summarised as P10 / P50 / P90.

## What is reported

For each reform (`Result.capacity`, API block `capacity`):

| Output | Meaning |
|---|---|
| `not_screened` | Today's headline record, unchanged |
| `screened` | Added homes after the screen |
| `change`, `change_pct` | Screened minus not screened (never above 0), and as a % of not screened |
| `loss_sewer`, `loss_flood`, `loss_steep` | Homes each screen removes **on its own** (added homes x (1 - that screen's multiplier)) |
| `screened_disjoint`, `screened_nested` | Screened homes at the two ends of the dependence range (above) |
| `binding` | The screen with the largest P50 loss on its own, the share of the modeled draws in which each screen removes the most (with its Monte Carlo standard error in the sentence), a rule-written sentence, and the dependence-range sentence |
| `counties` | Screened homes and the change, by county |
| `texts` | A rule-written sentence: screened against not screened |

Plus the package (`not_screened`, `screened`, `change`) and the **coverage** of
every screen: the share of tracts and of homes it measures, and a sentence. For
sewer the coverage is also split by evidence class (`homes_verified_share`,
`homes_estimated_share` with `homes_estimated_inside_share`,
`homes_unknown_share`, and `split_text`); "measured" (`homes_share`) counts
official answers only. The
rest is drawn from a range, and the wildfire screen is listed at zero coverage
so the gap is never hidden. Sentences come from `assumptions/capacity.yaml`
with the engine's numbers filled in.

## Limits

- Every `capacity.*` entry is team judgement with a wide range. The screen says
  where building is harder; it is not a site finding.
- A service-area map shows where a system serves, not spare treatment or pipe
  capacity. A home inside may still need an extension, a connection fee or an
  allocation; the screen does not model that.
- In Pennsylvania most homes have no shareable official sewer answer, so the
  sewer screen is mostly drawn from `capacity.unknown_sewer_served_share`; it is
  the widest part of the screen. Its coverage is reported with every run.
- In Massachusetts no home has an official answer in the pack; every home has
  MassDEP's estimate (inside or outside its areas; the run's coverage split
  gives the shares). The estimate is coarse (300 ft buffers around sewer lines, whole towns at least
  97% served, many areas not confirmed), and being inside an area is not the
  same as being connected.
- The flood zone is FEMA's regulatory 1% annual chance floodplain. It leaves out
  heavy-rain flooding away from mapped streams, and says nothing about insurance
  cost.
- Slope is read on a 30 m grid of all land, not on parcels; the exposure entry
  turns it into a share of lots.
- Wildfire is not screened: no state pack has a wildfire table yet.
- How the screens overlap within a tract is not measured; the result is a
  range across dependence cases, with independence as the middle case.
- The septic rule is by reform family, not by lot size or soil.
- The county-level reforms' exposure is a judgement about where their marginal
  homes land; no data in the pack measures it.
