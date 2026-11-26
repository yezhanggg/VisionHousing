# Method: the outcome contract

Every headline the tool shows means the same thing, in the same unit, for the
baseline and for every reform:

> **Additional new privately owned housing units authorized by building
> permits in the five calendar years 2027–2031.**

This page says what that measure counts, how a five-year total is split into
years, how added permits differ from added homes, and what every result says
about when it was made. The code is `src/visionhousing/engine/outcome.py`; the
rules and dates are in `assumptions/outcome.yaml`.

## The measure

- It is the Census Bureau's Building Permits Survey measure: new housing units
  authorized by a building permit. The baseline projects it county by county
  from the same series.
- A reform's number is the difference from the baseline in the same unit:
  permits the reform adds, minus permits the baseline would have issued anyway
  on the same sites (or, through the displacement shares, elsewhere).
- The survey leaves out additions, alterations, renovations and conversions of
  existing buildings. An ADU filed as an addition or a conversion is a real
  home that may not appear in the survey; ADU numbers are labelled "homes
  added, of which some may not appear in permit statistics".

### Audit of every final stage

`assumptions/outcome.yaml` (`audit`) records, for the baseline and each reform,
which stage is the headline, what it counts and what its take-up evidence
counts. Every final stage counts **authorizations**, and every take-up or
response input was framed in permits (ADU permits per year, sites permitted
per year, the percent change in permitted units for the supply response), so
no conversion from starts or completions to permit timing is needed. The audit
is part of every run's `contract`, so a reader sees it with the number.

## Five record types on every reform

Every reform result carries the same five records (`accounting` in the run's
JSON; code `engine.outcome.accounting`; words and labels in
`assumptions/outcome.yaml`, `records` and `work_types`). They have different
units and different rules, so they are kept apart and never added to each
other. Every one is worked out per draw by the engine.

| Type | Record | What it counts |
|---|---|---|
| a | `baseline_bps` | **Baseline authorizations in the permit survey's definition.** The formal baseline: new privately owned units authorized under current rules. Flags (status `baseline_flags`): places with no permit requirement are outside the survey, the Census Bureau imputes for offices that did not report, and the set of permit offices is updated every year. |
| b | `comparable`, `work_<type>`, `comparable_with_uncertain`, `headline_minus_comparable` | **Additional comparable authorizations.** Policy minus baseline units in the survey's definition, in the same years, geography and draw: the formal effect. Split by work type (below). |
| c | `local_outside_bps` | **Additional homes a local permit system authorizes outside the survey's definition** (additions and conversions). A companion line. How many of them a local system records is unknown, so its status says "bound", or "none" when the reform makes no such home. |
| d | `net_if_completed`, `demolitions_caused`, `removed`, `counterfactual_removed`, `counterfactual_on_sites` | **Authorized net change if completed.** New units less the homes the same projects demolish, against the counterfactual's own units and demolitions on those sites. It is net authorized capacity, not observed stock growth. |
| e | `timing_shifted_into_window`, `timing_newly_feasible`, `timing_moved_out_of_window` | **Completion and timing ledger.** Permits moved into or out of 2027–2031 by a timing mechanism, the rest newly feasible. Observed completions: unavailable (no completion series is read). Modelled deliveries: not modelled (no completion lag). |

### Work types

Every added unit is one work type, and the work types add up to the reform's
headline in every draw. The Building Permits Survey counts new buildings and
leaves out additions and conversions of existing ones. For attached units it
goes by the structure, so "semi-detached" alone does not decide whether a unit
is counted.

| Work type | Counted by the survey? |
|---|---|
| `new_standalone`: a new building of its own | yes |
| `new_attached`: a new building sharing a wall | yes |
| `new_unsplit`: new construction, structure type not split (the fee and parking reforms, which scale the baseline's own new buildings) | yes |
| `addition`: added to an existing building | no |
| `conversion`: made inside an existing building | no |
| `uncertain`: an attached ADU, which a permit office may class as a new attached structure or as an addition | unknown |

`comparable` = the counted work types, per draw. `comparable_with_uncertain`
adds the uncertain ones: an upper bound. `local_outside_bps` = additions plus
conversions, per draw.

### Which number is the headline

The headline did not change. For missing middle, transit-oriented density, the
fee cut and the parking reform it **equals** the comparable record: every unit
they add is new construction. For ADUs the headline counts **every** ADU,
including those made inside the house (conversions) and attached ADUs whose
classification is uncertain. The ADU result says so (`headline.scope` = "all
work types") and shows `comparable` beside it, with the difference drawn per
draw (`headline_minus_comparable`). The ADU method page gives the split.

## Added permits and added homes (gross and net)

A project that tears down a home and builds several counts every new unit as
a permit, but adds fewer homes to the stock. The tool keeps both:

- **Added units authorized** (the headline): new units authorized on the
  redeveloped sites, minus the baseline's own authorizations there, minus
  displacement.
- **Net added homes**: added units authorized, minus the reform's share of the
  homes demolished.

For missing middle, every redeveloped lot has four event fields, the same in
both arms: the new units the policy project authorizes and the homes it
demolishes, and the new units and demolitions the counterfactual (the
baseline) would have had on the same lot. One function
(`engine.outcome.site_account`) turns them into both accounts:

```
added units authorized = (policy units − counterfactual units) × (1 − x)
net added homes        = added units authorized − (policy demolitions − counterfactual demolitions)
```

Per lot the policy builds `k` units and demolishes `d` homes
(`missing_middle.demolished_per_project`). With chance `q`
(`missing_middle.baseline_replacement_probability`) the counterfactual tears
the same house down anyway and rebuilds `r` homes
(`missing_middle.baseline_units_per_replacement`). `x` is the displacement
share; it moves no demolition.

**The accounting fixture** (a test, `tests/test_outcome_accounting.py`):

| Policy | Counterfactual | Added authorizations | Net if completed |
|---|---|---|---|
| 4 units, 1 demolition | 1 replacement unit, 1 demolition | 3 | 3 |
| 4 units, 1 demolition | house left untouched (0, 0) | 4 | 3 |
| Already-permitted duplex under a two-unit ceiling (2, 1) | the same duplex (2, 1) | 0 | 0 |

The third case is why a lot that already allows two homes is not eligible: the
reform changes nothing there.

For transit-oriented density, per square metre of redeveloped land, the
baseline's own authorizations are `q × today's floor area` worth of apartments
(`tod.baseline_replacement_probability`), and the homes demolished are the
homes on the eligible land today (2020 block counts). Because `q ≤ 1` and
demolitions are never negative, added units authorized are never below net
added homes. ADUs, the fee cut and the parking reform remove no home, so the
two are equal.

## Years: 2027, 2028, … 2031

Each five-year total is split into years in every draw.

- **A reform** follows its adoption curve, a start delay and a ramp, each a
  ledger entry (`assumptions/outcome.yaml`, `timing`):

  ```
  year factor(t) = a(t) × ramp(k(t)),  a(t) = clip(t − delay, 0, 1),  k(t) = max(1, ceil(t − delay))
  ramp(k)        = min(1, r1 + (1 − r1)(k − 1)/(Y − 1))
  year value(t)  = five-year total × year factor(t) / Σ year factors
  ```

  The five yearly values add up to the five-year total in every draw, by
  construction (a test checks it for the state, every county and every tract).
  For ADUs, missing middle and transit-oriented density the ramp also sets the
  total, because it scales take-up; for the fee cut and the parking reform it
  only spreads the total, which comes from the supply response.
- **The baseline** follows its own projection: each county's yearly point
  projections, as shares of its five-year projection, times the county's
  five-year draw.
- **The package** splits each part by its own curve: each reform by its own,
  the ADU-lot overlap by missing middle's, each interaction by the curve of the
  reform it reruns.

The benchmark reforms are in force on January 1, 2027, so every start delay
is 0. A user can set a delay to model a later effective date.

### Moved in time or newly feasible

A reform that shortens approvals would move some permits that would have been
issued after 2031 into the window. No benchmark reform changes approval times,
so every added permit is newly feasible; each reform's yearly series says so,
with a zero record for "shifted into the window". The ADU timing variants
(`adu_timing_reliable`, `adu_both`) are the exception: their yearly series
carries the authorizations shifted into the window by a lag model, and newly
feasible = the five-year total less those
([`adu_levers.md`](adu_levers.md)).

## As of when

Every result carries a `contract` block:

- **as_of**: the date the model is frozen at (`assumptions/outcome.yaml`).
- **vintages**: for each data input, its version and access date from the
  state pack's manifest, flagged when fetched after the as-of date; and the
  policy vintage: when the state law provisions and the municipal rules were
  last checked, bills not yet enacted, and enacted rules whose effective date
  is after the as-of date.
- **effective dates**: a rule enacted by the as-of date with a later effective
  date is current law from that date. Inside the window it is modelled as a
  reform with a start delay; a bill not enacted is a scenario, never baseline.
- **hold constant**: interest rates, construction costs, rents, home values and
  incomes are held at the ledger's levels for the whole window (with the
  ledger ids); the baseline repeats recent permit trends and does not predict
  a recession or a boom.
- **baseline correction**: the bias-correction decision below.

## How the baseline rule is chosen, and what is tested out of time

Seven simple rules compete on the same past cases (`assumptions/backtest.yaml`,
`contest`). A rule is eligible when its P10–P90 ranges caught the real total in
75–85% of back-test cases. Each case's range is built only from other
counties' errors at earlier origins, whose five target years had all been
observed by the case's origin, so no later year leaks into a range. A county's
size class at an origin is worked out from the years up to that origin; place
types from the county typology are today's grouping, used for every origin.
When no rule is eligible, the rule whose coverage is closest to 80% wins.

The choice of rule itself uses every origin at once, so it is in-sample; the
ranges and the corrections are scored out of time. The Method screen says so
in its findings.

Tested that way, every rule's ranges catch the real total less often than 80%.
A calibration written down before it was scored (`oot_calibration` in
`assumptions/backtest.yaml`: finite-sample ranks of the errors already
observed, by size class) was tested in four states and not adopted, because
its pooled coverage stayed below the 75–85% band. The shipped ranges are
unchanged and their shortfall is stated as a limit (method and table:
`docs/modules/baseline.md`, Technical Appendix B).

In sparse states the state range is then widened toward the past errors of
other states that grew at a similar pace (growth-aware widening; adopted by a
test written before it was scored, `assumptions/calibration/growth_coverage.yaml`).
It only widens, and a state with a full record of its own keeps its range.

## The baseline's bias correction: kept or dropped by a rule fixed in advance

The baseline's middle value can be the rule's own projection, or the
projection times the median past ratio of actual to projected permits in the
county's pool (the "P50 pool-ratio correction"). The rule that decides was
written into `assumptions/backtest.yaml` (`bias_correction`) before any score
of it was computed:

1. Out of time: for each past origin, pools hold only cases whose five target
   years had all been observed by then; later origins are scored.
2. Corrected: P50 = projection × pool median ratio; P10, P90 = projection ×
   pool 10th, 90th percentile ratios. Uncorrected: P50 = projection; the same
   range shape divided by the pool median.
3. Keep the correction only if its median absolute error is lower **and** its
   P10–P90 coverage is inside 75–85%. Otherwise drop it.

The engine applies the outcome; the coverage of both versions, the errors and
the reason are in every run's `contract.baseline_correction` and on the
`/validation` reply (`contest.bias_correction`).

## Back-test variants

The `/validation` reply adds three views to the rule contest:

- **Observed only vs all years.** The Building Permits Survey fills in figures
  for permit offices that did not report. "All years" scores the rules on the
  published figures; "observed only" removes every county-year with any
  imputed figure from both the history a rule reads and the years it is scored
  on.
- **Statewide totals.** Each rule back-tested on the state's own yearly totals,
  one case per origin, each range from the other origins.
- **Overlapping origins.** Consecutive origins share four of five target
  years. The reply gives the number of non-overlapping five-year periods the
  origins hold, so case counts are not read as independent evidence.
