# Baseline: likely permits with no reform

Code: `src/visionhousing/baseline/`. Tests: `tests/test_baseline_rules.py`,
`tests/test_baseline_backtest.py`, `tests/test_baseline_project.py`,
`tests/test_baseline_places.py`, `tests/test_baseline_small_sample.py`,
`tests/test_baseline_contest.py`, `tests/test_baseline_calibrate.py`. Settings
for the rule contest, the bias correction, the error-by-place breakdown, surge
years, stress settings, the small-sample widening and the out-of-time
calibration test: `assumptions/backtest.yaml`.

## What it does

The baseline answers: if nothing changes, how many housing units are likely to
be authorized by building permits in 2027-2031? It answers for each county and
for the state, as a likely range (P10, P50, P90), never as one number.

Input: the tidy permit table, one row per county and year, with units in four
structure types: `units_1`, `units_2`, `units_3_4`, `units_5plus` (the Census
Building Permits Survey layout; Testland uses the same columns). A county-year
not in the table counts as "not reported", never as zero.

## The candidate rules

Each rule uses only permits up to an *origin* year (the last data year in a
real run) and projects each target year. It runs on every county and structure
type; the five years and four types are then added up. Settings come from the
ledger (`assumptions/parameters.yaml`) or from `contest` in
`assumptions/backtest.yaml`.

The seven rules of the **rule contest**:

| Rule | What it does | Settings |
|---|---|---|
| `last_5_mean` | Average of the last five years | `baseline.recent_window_years` |
| `last_3_mean` | Average of the last three years: reacts faster | `contest.short_window_years` |
| `median_5` | Median of the last five years: one freak year barely moves it | `contest.median_window_years` |
| `damped_trend` | Ten-year average plus damping x the fitted yearly slope x years from the window's middle year; never below zero | `baseline.long_window_years`, `baseline.trend_damping` |
| `shrink_to_group` | The county's five-year average as permits per home, pulled toward its place type's rate (below) | `baseline.recent_window_years`, `contest.half_weight_units` |
| `top_down_share` | The state's five-year average x the county's share of state permits over ten years | `baseline.recent_window_years`, `contest.share_window_years` |
| `average` | The simple average of the six rules above | (theirs) |

Two earlier candidates stay available to the code and the tests:
`last_10_mean` (average of ten years) and `ewm` (exponentially weighted mean:
the latest year has weight `baseline.ewm_alpha`, each earlier year (1 - alpha)
times the next).

Damping 0 gives a flat average; damping 1 carries the full straight line forward.

**Shrink to place type.** Small counties' averages are noisy. The rule works on
permits per home (homes = the tract housing-unit counts added up by county):

- own rate = county's five-year average / its homes;
- group rate = the place type's five-year averages added up / its homes added up;
- weight on the own rate `w = s / (s + half_weight_units)`, with `s` the
  county's average yearly permits (all types). With `half_weight_units` = 100,
  a county building 100 a year gets half its own rate and half its group's; one
  building 10 a year gets about a tenth of its own;
- projection = homes x (`w` x own rate + (1 - `w`) x group rate).

A state without home counts gets the plain average. The home counts are today's,
so in the back-test they are slightly later than the origin; a county's share of
its group's homes changes slowly, so this matters little.

**Top-down share.** The state's level reacts to the whole market; a county's
share of it moves slowly. Projection = the state's five-year average (each
structure type) x the county's share of the state's permits over the last ten
years. The shares add to one, so the counties add to the state's average.

On the state's own total (one series) the shrink and top-down rules are the
plain five-year average.

Example (`last_5_mean`): permits of 6, 7, 8, 9, 10 in 2021-2025 give 8 a year,
so 40 units over 2027-2031.

## The backtest (the skill table)

`validate_baseline(table, rules, origins, settings)` pretends it is an earlier
year and checks each rule against what really happened.

1. An **origin** is used if ten years of history come before it and all five
   target years after it have data. The gap from origin to first target year
   (the *lead*) matches the real run: data end in 2025, the projection starts
   in 2027, so the lead is two years.
2. For each county and origin: **ratio** = actual five-year units / projected
   five-year units. 1.2 means the rule was 20% too low.
3. Per rule the table reports:
   - **MAPE**: mean of |actual - projected| / actual.
   - **Median absolute log ratio**: the typical size of the error, the same for
     too high and too low. 0.10 means "typically about 10% off".
   - **Coverage of the 80% interval**: the share of cases whose actual total
     fell inside the interval. The interval for a county at an origin is built
     only from *other* counties' ratios in its size class (a geographic split),
     and only from cases whose five target years had all been observed by that
     origin (origin' <= origin - lead - horizon + 1, as in the out-of-time test
     below), so it is out of sample in place and in time. The first origins
     have no earlier case and no interval. A county's size class at an origin
     comes from the years up to that origin only, and a pooled case counts in
     the class its county had at the case's own origin. A good interval covers
     about 80%.
   - **Median signed error**: the median of `log(projected / actual)`. Above
     zero the rule ran high.
   - **Range width**: the mean of (P90 - P10) / projection over the cases with
     a range.

## Choosing the winner (`contest.py`)

The choosing rule was written into `assumptions/backtest.yaml` (`contest`)
before any contest score was computed, and the code applies it as written
(`choose_winner`):

1. **Eligible**: rules whose P10-P90 coverage is inside `coverage_band`
   (75% to 85%). A rule whose ranges catch the truth far less or far more often
   than 80% describes its own uncertainty badly, however small its error.
2. **Best**: the lowest median absolute error among eligible rules.
3. **Winner**: the simplest eligible rule (lowest `complexity`: means and the
   median 1, top-down 2, trend and shrink 3, the average 4) whose error is at
   most best x (1 + `baseline.selection_tolerance`). Ties go to the lower
   error, then to the order of the list.
4. If no rule is eligible, the rule whose coverage is closest to 80% wins.

The choice of rule uses every origin at once, so it is in-sample: only the
ranges and the corrections are scored out of time. The findings say so.

The Method screen shows the whole table, losers included, with the choosing
rule in words. The rule is applied to each state's own back-test, so a state
with different data can pick a different winner.

`baseline_from_ledger(table, ledger, cycle, places=...)` does all of it: the
contest, the winner, the pools the out-of-time test adopts, and the projection.
`places` gives each county's place type and homes
(`contest.place_info_for_context`); without it, size classes stand in for place
types and there are no home counts. The engine always passes it.

`choose_rule(skill, tolerance)` is the earlier chooser (simplest within
tolerance of the best, no coverage band). It is kept for the earlier candidates'
tests.

## Bias correction and ranges by place type

The baseline's P50 is the rule's projection x the median past ratio of actual
to projected units; P10 and P90 use the 10th and 90th percentile ratios. At the
median this corrects the rule's typical past bias. The question is whose past
cases to use: the **pool**.

| Pool | Cases used for a county |
|---|---|
| `size_class` | Its size class (small, medium or large by recent permits) |
| `place_type` | Its place type (the "places like this one" county type; size class when the pack has no county types), if that type has at least `calibration.min_group_cases` (30) past cases; otherwise all counties pooled |

**Out-of-time test** (`out_of_time_test`). A correction learned from all past
years and scored on the same years looks better than it is. So for each past
origin the test uses only cases whose five target years had all been observed by
that origin (origin' <= origin - lead - horizon + 1). Origins with fewer earlier
cases than `min_group_cases` are skipped. Size-class pools use the classes as
they stood at each origin (each earlier case in its class at its own origin);
place types from the county typology are today's grouping (current data),
applied to every origin, and the findings say so. Each method is scored on the same
cases: median signed and absolute error of its P50, and coverage of its
P10-P90:

- `none`: the rule's own projection, no correction (error only);
- `size_class`, `place_type`: as in the table above;
- `place_type_own`: place type, with own-error widening (below).

**Adoption rule** (`choose_pools`, written into `calibration` before any score):
the engine uses place-type pools if, out of time, their P50 has a lower median
absolute error than size-class pools and their coverage is inside the band (or,
when neither is inside, closer to 80%). Otherwise it keeps size-class pools.

The Method screen shows each method's error and coverage for all counties and
for every group of the error-by-place breakdown: the "before" (the five-year
mean, and the uncorrected projection) and the "after".

**Keep or drop the P50 correction** (`bias_correction_decision`, rule written
into `bias_correction` of `assumptions/backtest.yaml` before it was scored). On
the same out-of-time cases, with the adopted pools, compare the corrected method
(P50 = projection x pool median ratio, range from the pool's P10 / P90 ratios)
with the uncorrected projection (P50 = projection, the same range divided by the
pool median). Keep the correction only if its median absolute error is lower
**and** its coverage is inside the band; otherwise drop it. Coverage is
computed for both. `baseline_from_ledger` applies the outcome
(`project_baseline(..., correct_bias=False)` divides every ratio quantile by the
pool's median ratio, and the county formula says so). The decision is in
`BaselineResult.correction`, in every run's `contract.baseline_correction` and
in `/validation` (`contest.bias_correction`).

**Back-test variants** (in `/validation`, `contest`):

- `variants`: the contest table on all years and on fully reported county-years
  only (`observed_only_table`: every county-year with an imputed figure is set to
  "not reported", so it is neither history nor target);
- `statewide`: every rule back-tested on the state's own yearly totals, one case
  per origin, each range from the other origins' ratios (`statewide_backtest`);
- `origins`: how many non-overlapping five-year periods the rolling origins
  hold, `floor((last origin - first origin) / horizon) + 1`
  (`origin_overlap`), with a note that case counts overstate the independent
  evidence.

**Own-error widening.** One county can be far off in a way its pool does not
show (in Pennsylvania, Philadelphia). A county with at least
`calibration.min_own_cases` (10) back-test cases whose typical error
(exp(median signed error) - 1) is at least `name_few_cases_beyond` (a third)
away from zero gets each end of its range from the wider of its pool's ratio
and its own. Its P50 is unchanged. Its formula says so.

## Surge years

A county-year is a **surge year** when its units exceed `surge_factor` (3) x the
median of the county's other years in the same five-year window
(`surge_check`). The Method screen names any surge year in the window the
baseline reads, and shows each named county's back-test error in the cases
whose five target years hold a surge year and in the others. This tests the
idea that one exceptional year (for Philadelphia, the rush of permits in 2021
before a tax-abatement change) explains the county's error. A median-based rule
ignores one such year; a mean does not; the contest decides between them on the
whole state's record.

## Stress settings

Each back-test origin is a **slump** when the state's actual units in its five
target years were below its units in the five years up to the origin, and a
**boom** otherwise (`origin_kinds`). The baseline is re-run with only one kind's
errors (both the county pools and the state range), giving two alternative
ranges: "if the next five years look like the past slumps" and "... like the
past booms" (`stress_baselines`). They are shown next to the main range, not
in its place.

## The likely range

1. **Point projection** for each county: the chosen rule, summed over five
   years and four structure types.
2. **Pool**: the size class or the place type, whichever the out-of-time test
   adopted (above). Size classes are terciles (`baseline.size_classes`) by
   average yearly permits over the last five years; small counties swing more in
   proportion, so each class has its own ratios.
3. **Multipliers**: from all past ratios in the pool, take the 10th
   percentile, the cycle quantile, and the 90th percentile (widened for a
   far-off county as above).
4. County P10 / P50 / P90 = point projection x those three multipliers.

There is no assumed error distribution; the range is the rule's own record.

### Housing cycle control

One choice, *weak / typical / strong*, sets which quantile of the ratios is the
centre: `baseline.cycle_quantile_weak` (0.2), `_typical` (0.5), `_strong` (0.8).
The P10 and P90 stay at the 10th and 90th percentile ratios. Since the P50
correction was dropped (see "Keep or drop the P50 correction" above), "typical"
centres on the rule's own projection, and the range keeps the shape of the
pool's ratios around their median; it no longer shifts the centre to correct a
past bias.

### The state, and why ranges do not add

- **State P50 = the sum of county P50s.** Counties always add up to the state.
- **Percentiles do not add.** Adding county P10s gives a number below the true
  state P10, because a bad year in one county is partly offset by a good year in
  another. So the state's range comes from running the same backtest on the
  state's own yearly totals: state P10 = state P50 x (state ratio P10 / state
  ratio at the cycle quantile), and the same for P90.
- Never add county P10s or P90s to get a state range.

### Few cases: widening the state range

A small state's back-test rests on few county histories (Vermont: 14 counties), so
the spread of its state ratios is itself uncertain and can come out narrower than a
large state's. `widen_small_sample` applies one rule to every state
(`small_sample` in `assumptions/backtest.yaml`):

- `n` = usable county-origin cases in the state's back-test;
  `w = min(1, n / full_weight_cases)` (`full_weight_cases` = 1,000);
- each end of the state range, as a log distance from the cycle quantile, is
  blended toward a prior: `w x own + (1 - w) x prior`;
- the end used is the wider of the state's own and the blend, so the range only
  ever widens.

The prior is the ratio quantiles of Pennsylvania's state back-test, the largest the
tool has (`tests/test_baseline_small_sample.py` re-checks them against the
Pennsylvania pack when it is built). Pennsylvania has more than 1,000 cases, so its
range is unchanged. The state record's formula says when the rule was applied, with
`n` and `w`. County ranges are not widened: they pool all counties of a size class.
The engine applies it to every real state (`baseline_from_ledger(..., widen=True)`); the
synthetic Testland is never widened, because its prior would be a real state's record.

### Sparse states: growth-aware widening

A test written before it was scored (`docs/modules/validation_donor_pool.md`, "A third
test") found that sparse states' ranges miss mostly on the high side and adopted one more
step, applied after the small-sample widening by `widen_growth_class` (`growth_widen` in
`assumptions/backtest.yaml`, ledger switch `baseline.growth_widen`): with the same `w`, each
end becomes the wider of itself and `w x end + (1 - w) x log(pool ratio)`, where the pool
ratios are the raw 10th and 90th percentile ratios of real to projected totals of the other
states in the state's growth class (`sparse_states` in
`assumptions/calibration/growth_coverage.yaml`; not centred, so the shift stays one-sided).
It runs only when the study's decision is `adopt`, the state is listed and its pool holds at
least `min_pool_cases` cases; Pennsylvania and Testland are never listed. The state formula
names the class, the pool and `w`, and the record carries the ledger id and the study file.
The study scored the typical cycle; for the weak and strong cycles the same ends are used and
the formula says "not back-tested for this cycle". The study's errors were measured with the
mean of the last three years; a state whose contest picks another rule uses them as they are.

## Out-of-time calibrated ranges (`calibrate.py`, tested, not adopted)

Tested only with errors known at the time, every rule's P10–P90 range caught
the real five-year total far less often than the 80% it aims for. One fix was
written down, and its fingerprint recorded in `assumptions/preregistration.yaml`,
before it was scored (block `oot_calibration` in `assumptions/backtest.yaml`):

1. **Past cases only.** For a past start year (origin) *o*, use the rule's
   county cases whose five target years had all been observed by *o*
   (`o' + lead + horizon - 1 <= o`). The county's own earlier cases count,
   because they were known by then.
2. **Pool.** Keep the past cases whose county was in the same size class (at
   that case's own origin) as the county is at *o*; with fewer than
   `min_pool_cases`, use all past cases of the state.
3. **Ends from ranks.** Sort the *n* pooled ratios (actual / projected). The
   low end is the *k*-th smallest with *k* = max(1, floor(0.1 (n + 1))); the
   high end the *k*-th smallest with *k* = min(n, ceil(0.9 (n + 1))). This
   finite-sample rank ("conformal-style") is the same as reading percentiles of
   the log errors without interpolation.
4. **Own-error widening** as for the shipped range, from the county's own
   past cases.
5. **P50 = the rule's own projection**; P10 and P90 are the projection times
   the two ends, stretched just enough to hold the P50.

**Test.** Every origin with at least `min_past_cases` past cases is scored, for
every contest rule, in Pennsylvania, Vermont, Massachusetts and Wyoming.
Coverage is the share of scored cases inside the range; width is the mean of
(P90 − P10) / P50; the error is the median of |log(P50 / actual)|. The
contest's choosing rule is re-run unchanged, with each rule's coverage and
width replaced by these, and the test is judged on each state's re-run winner
(the rule that would ship).

**Adoption rule, fixed in advance.** Adopt only if the four states' scored
cases, pooled (each case counts once), are covered 75–85% of the time (edges
count) **and** every state reaches at least 70%. Below the band, above it
(the ranges would be wider than an 80% range should be; the levels are not
re-tuned after the fact), or with any state short, the engine keeps today's
ranges. Had it been adopted, the order would have been: calibrated ends, then
small-sample widening, then growth-aware widening, each only widening; the
state range would have used the same steps on the state's own yearly totals.

**Result.** Not adopted: the calibrated ranges are wider and catch more real
totals, but pooled coverage stays well below the band and no state reaches the
70% floor. Nothing in the engine changed. The table, by state, with each
rule's before and after coverage, width and error and a descriptive check on
state totals, is generated into the Technical Appendix (section B) from
`assumptions/calibration/baseline_calibration.yaml`. Command:
`visionhousing validate-baseline-calibration` (reads only each pack's permit,
tract and county-type tables; it refuses to run if the block has changed).

Why it falls short: the earliest scored origins can rank only errors from the
boom before the national bust, and they are asked to cover the bust and the
recovery. Ranking past errors honestly cannot see a new housing cycle coming,
so the shipped "likely range" should be read as narrower than a true 80% range
when the next five years break from the last cycle.

## Measured reliability: honest labels (`reliability.py`)

Because the out-of-time test shows the ranges catching the real total less
often than the 80% a P10-P90 range aims for, the baseline range is not called
a "likely range". Each baseline record states how often ranges like it caught
the real total in past years:

> In past years, ranges like this caught the real five-year total in N of M
> cases (out of time).

and its short label is "range (caught N% of past totals)". N and M are counts,
not settings: `visionhousing validate-baseline-reliability` (permits only;
light) writes them to `assumptions/calibration/baseline_reliability.yaml`, by
the rule fixed before any count (block `baseline_reliability` in
`assumptions/backtest.yaml`, fingerprint in `assumptions/preregistration.yaml`;
the command refuses to run if the block changed).

- **County ranges.** The contest's own out-of-time test: each past county case
  gets a range from other counties' earlier errors in its size class (only
  cases whose five target years were all observed by its start year). Counted
  for the state and for each size group (small, medium, large, by the county's
  size at the case's start year). A county record carries its current size
  group's count.
- **State range.** The state range as shipped, rebuilt at each past start year
  from the state's earlier yearly totals only: ratio quantiles q10, q50, q90 of
  those cases; P50 = state projection x q50 when the bias correction is kept,
  else the projection; P10 = P50 x q10 / q50, P90 = P50 x q90 / q50; then the
  same small-sample and growth-aware widening the engine applies. Start years
  with at least eight earlier state cases are counted (`min_past_origins`).

The engine adds the sentence, with the label, to the formula of the state's
and each county's baseline record (`attach`), only when the count was made on
the range this run builds: the rule the contest picks today, the same
bias-correction choice and the same growth widening (all three are stored with
each state's counts), and the typical cycle (the weak and strong cycles move the
median and the widened ends, and were not back-tested). Otherwise no count is
shown (`reliability_matches`). The explanation's headline labels the
baseline number with it, and `/validation` returns the counts with their
sentences (`reliability`). No value changes. The reform ranges keep the words
"likely range": they are simulation ranges, conditional on the baseline and on
the assumptions, and no past totals exist to count them against.

Read the two counts differently. The county count rests on hundreds of cases,
but they share years, so it is closer to a handful of periods. The state count
rests on eight overlapping windows per state; a state range that caught all
eight has not been shown to be an 80% range, only not to have missed yet. The
widening inputs (the small-sample prior and the growth-class pool) were set
from the full record, which favours the widened states a little. So were the
rule itself and its bias-correction choice: each past range uses only errors
known at its start year, but the contest that picked the rule saw the same
target years. The coverage is therefore slightly favourable; both points are
written into the results file's limits.

## National state-total calibration (`validation/national_state.py`, tested, not adopted)

The out-of-time calibration above caught Pennsylvania's state total at none of
its scored start years: one state's record holds one housing cycle, and its
errors ran to one side. An earlier test found that pooling the state-total
errors of all states came closest to 80%. So a second calibration of the
**state** range was written into `assumptions/backtest.yaml`
(`national_state_calibration`) and fingerprinted before it was scored:

1. Data: the national county panel of the donor-pool study; each state's yearly
   total is its counties added up, projected as a one-area series. The four
   states with a pack use the rule the engine's contest picks for them; every
   other state uses the three-year mean.
2. Pool: for a state and a start year, the state-total cases of the same rule
   of **every** state whose five target years were observed by then.
3. Two forms. *pooled*: the range's ends are the finite-sample 10th and 90th
   percentile ranks of the pooled ratios. *shock*: each past start year's
   statewide shock is the median log error over states, the rest is each
   state's own part, and the ends add the rank ends of the shocks (one per
   start year) and of the own parts, so a run of one-sided years counts as
   few draws, not many.
4. P50 = the rule's own state projection; the range holds the P50.

Each form passes only if the coverage of every state's scored totals, pooled,
lies in 75% to 85% and Pennsylvania, Vermont, Massachusetts and Wyoming each
reach 70%. Above 85% is a fail too (wider than an 80% range should be), and the
levels are not re-tuned. One passing form would be adopted; two, the one closer
to 80%. If adopted, the state range would be the state's projection times the
form's ends at the real start year, holding the P50, replacing the state's own
ends and both widenings (county ranges unchanged).

**Result.** Neither form passed: the single pool caught too few state totals
and the shock form too many (the table is generated into the Overview, section
4.2, from `assumptions/calibration/national_state_calibration.yaml`). The state
range is unchanged, and the engine does not read this file (`national_ends_for`
is how an adopted decision would be wired in). Each named state's rule is
today's contest winner on the full record, which saw the same target years, so
its coverage is slightly favourable; the results file says so in its limits.
Command: `visionhousing validate-national-state-calibration` (reads the national
panel cache and each pack's permit tables).

## Baseline card, state check and the 2026 bridge (`card.py`)

**What it is for.** A reader should see in one place what the baseline is
built from, and be able to check its largest number by hand. The card says:

- **Historical years.** The years the shipped rule reads for the state level
  (Pennsylvania: 2021-2025; Massachusetts and Wyoming: 2023-2025), the years a
  county's share is measured over (top-down share only), and the years the
  range's back-test uses.
- **Permit columns.** The Building Permits Survey's *estimated units* in 1-,
  2-, 3-4 and 5+ unit buildings. The "reported only" columns are not used.
- **Data release.** The vintage (the final 2025 annual files) and the date the
  state file was posted, read from the file's `Last-Modified` date on the
  Census server.
- **Information cutoff.** The model's as-of date (`as_of` in
  `assumptions/outcome.yaml`). Data released after it are not used.
- **Forecast gap.** The last full year of data is 2025 and the projected years
  are 2027-2031, so 2026 has no full-year count. The shipped method carries the
  rule's yearly level flat through 2026 ("carry annual through the gap"). The
  gap year adds nothing to the five-year total.

**State check (a release fixture).** The shipped rule of each state, applied
to the state's own yearly totals, gives the state part of the shipped middle
value. Pennsylvania's rule (state five-year mean, shared out to counties) gives
the 2021-2025 sum; the mean of the last three years gives five times the
2023-2025 mean in Massachusetts and Wyoming. Vermont's middle value comes from
county shrinkage, which a state series cannot reproduce, so its series is an
outside control only. `tests/test_baseline_card.py` fails if the values move.

**Pennsylvania window sensitivity (`baseline_pa_window_2022_2025`).**
Pennsylvania's 2021 total is far above the next four years. Five times the
2022-2025 mean shows how much that one year moves the middle value. This
window was named after the 2021 spike had been seen. So it is a disclosed
sensitivity, not a pre-registered test, and not a replacement baseline. It is
recorded only. It is never subtracted from a reform or package result.

**2026 nowcast (`baseline_bridge_nowcast_2026`, diagnostic only).** The
Census Bureau posts a year-to-date state file each month. Only files posted on
or before the information cutoff are used. For the latest such month m:

```
s(y)     = units in Jan..m of year y (as posted) / final annual units of y,  y = 2021-2025
nowcast  = units in Jan..m of 2026 / s      (low, middle, high: highest, median, lowest s)
```

A year-to-date count is never treated as a full year. Monthly and annual files
differ in reporting and imputation; `s` absorbs that difference as well as the
season. The history files are as posted now, so this is a revised-data
exercise. If no monthly file was posted by the cutoff, the nowcast is recorded
as unavailable. No estimate reads the nowcast.

**Run it.** `visionhousing baseline-card` (state files only; no engine run)
writes `assumptions/calibration/baseline_reconciliation.yaml`. The API adds the
card to the state summary and to `/validation` (`baseline_card`). Settings:
`assumptions/baseline_card.yaml` (kept out of `backtest.yaml`, which is part
of every run's fingerprint). The monthly files are
fetched by `visionhousing.data.sources.bps_monthly` (registry id
`bps_state_monthly_ytd`).

<!-- generated: baseline_reconciliation -->
*Generated from `assumptions/calibration/baseline_reconciliation.yaml` (command `visionhousing baseline-card`, settings `assumptions/baseline_card.yaml`). Units: Estimated units in 1-, 2-, 3-4 and 5+ unit buildings (Building Permits Survey) (columns `est_1_units`, `est_2_units`, `est_3-4_units`, `est_5+_units`). Data release: 2025 annual files (final), posted 2026-05-14 (Last-Modified date of the state annual file). Information cutoff: 2026-11-24. Projected years: 2027-2031; forecast gap: 2026 (no full-year count; the rule's yearly level is carried through it).*

| State | 2021 | 2022 | 2023 | 2024 | 2025 | Shipped rule | Level years | State check |
|---|---|---|---|---|---|---|---|---|
| PA | 47,894 | 28,149 | 25,320 | 24,810 | 25,709 | `top_down_share` | 2021-2025 | **151,882** = state component = 5 x mean of 2021-2025 state units = 5 x (47,894 + 28,149 + 25,320 + 24,810 + 25,709) / 5 = 151,882 units (rule top_down_share, state series only; county shares do not change the sum) |
| MA | 19,853 | 16,830 | 13,214 | 14,338 | 12,477 | `last_3_mean` | 2023-2025 | **66,715** = state component = 5 x mean of 2023-2025 state units = 5 x (13,214 + 14,338 + 12,477) / 3 = 66,715 units (rule last_3_mean, state series only; county shares do not change the sum) |
| WY | 2,706 | 3,005 | 1,681 | 2,135 | 2,238 | `last_3_mean` | 2023-2025 | **10,090** = state component = 5 x mean of 2023-2025 state units = 5 x (1,681 + 2,135 + 2,238) / 3 = 10,090 units (rule last_3_mean, state series only; county shares do not change the sum) |
| VT | 2,319 | 2,403 | 2,456 | 2,654 | 2,294 | `shrink_to_group` | 2021-2025 | outside control only (county shrinkage; not reproducible from state totals) |

**Window sensitivity `baseline_pa_window_2022_2025`** (PA, record only): 5 x (28,149 + 25,320 + 24,810 + 25,709) / 4 = 5 x 25,997 = 129,985 units; difference from the shipped state component 151,882 = -21,897 units (-14.4%). Disclosed historical-window sensitivity. Named after the spike was seen, so not pre-registered. Not a replacement baseline; never subtracted from any reform or package result.

**2026 bridge, nowcast `baseline_bridge_nowcast_2026`** (diagnostic only; no estimate uses it). Latest monthly file released by the cutoff: `st2609y.txt` (posted 2026-10-27); the next file, `st2610y.txt`, was posted 2026-11-25 and is not used. Nowcast = year-to-date units / s, s = the same months' share of the full year in 2021-2025 (low, middle and high use the highest, median and lowest s). A year-to-date count is never a full year.

| State | Units Jan-Sep 2026 | Nowcast low-high | Nowcast middle | Carried yearly level |
|---|---|---|---|---|
| PA | 19,096 | 25,228-32,110 | 26,998 | 30,376 |
| MA | 11,156 | 13,255-16,690 | 15,097 | 13,343 |
| WY | 1,354 | 1,804-1,939 | 1,833 | 2,018 |
| VT | 1,210 | 1,380-1,811 | 1,651 | 2,425 (state-series mean) |
<!-- /generated: baseline_reconciliation -->

## What each record holds

Every county and the state get a `Record` with `p10`, `p50`, `p90`, unit
`units`, a formula that names the rule, the years, the point projection, the
size class, the multipliers and how many past cases they rest on, the ledger ids
used, source `bps_county_annual`, and tier 0 (national data, available for every
state). The result also has a tidy table of annual point projections by county,
structure type and year, and the multiplier table by size class.

## Edge cases

- A county with no permits in the rule's window, or no records at all, gets
  0 / 0 / 0, and the formula says why.
- A size class with no usable ratios uses all counties' ratios (the formula
  says so). With no ratios at all the range is the point projection.
- A projection of zero has no ratio; such past cases are left out.
- Data that already reach the first projected year are refused.

## Error by kind of place (`places.py`)

The skill table adds every back-test case together. A reader who asks "can I
trust the range for *my* county?" needs to know whether the baseline has been
weaker in some kinds of place. `place_backtest(table, rule, settings, rules, ...)`
takes the chosen rule's back-test cases (one per county and past start year) and
splits them five ways. The groupings and their thresholds live in
`assumptions/backtest.yaml`; none of them changes an estimate.

| Grouping | How a county is placed | Input |
|---|---|---|
| County size | The baseline's own terciles by recent permits (small, medium, large) | permit table |
| County type | Its "places like this one" group (k-means, named by rule) | pack table `typology_county`, or worked out from the pack's tables when the pack has none |
| Urban or rural | Rural when under `urban_rural.rural_below` of its homes are in Census urban areas (`urban_unit_share`; without it, tracts whose `area_type` is listed in `urban_area_types`) | tract table |
| Qualified Census Tract share | QCT tracts / all tracts, above the state's median county = "higher share" | pack table `incentives_county` |
| Named counties | Each county listed for the state under `named_counties` (Pennsylvania: Philadelphia, Allegheny), and the rest | the settings file |

For every group (and for all counties together) it reports:

- **Typical error**: the median of the signed error `log(projected / actual)`,
  turned back into a percent with `exp(median) - 1`. Positive means the baseline
  projected more than was permitted ("runs high"). Within `about_right` of zero
  it is called "about right".
- **Typical miss**: the median of `|log(projected / actual)|`, the skill table's
  measure, shown as a percent.
- **Range caught the real total, N of M**: each case's P10-P90 range is built
  only from *other* counties' past ratios in the same size class (the
  leave-one-county-out ranges of the skill table), and N is how many of the M
  cases fell inside. The overall row matches the skill table's coverage exactly.

**Small groups are flagged, not hidden.** A group with fewer than
`min_counties` counties or `min_cases` scored cases keeps its row, gets a
"few cases" tag, and its sentence ends "read this as a few places, not a
pattern". One named county is always flagged: its numbers are one county's
record.

**A grouping the data cannot support is listed as not available**, with the
reason (for example a state pack with no incentives table). The other
groupings still run, so the method works for states with less complete data.

**Sentences by rule** (`findings`): the all-county sentence; for each grouping,
the unflagged group with the largest signed error either way; the largest
signed error of any group when that group is flagged; and the group whose range
caught the real total least often, against the share a P10-P90 range aims for.
Every figure in them is computed from the back-test, never typed.

**County error map** (`county_errors`): each county's own median signed error,
put in one of five classes symmetric around zero (`map_breaks`: within the
smaller break, between the breaks, beyond the larger, low or high). The Method
screen draws it as a diverging map (blue: ran low; red: ran high) with a table
twin carrying the same values.

**Limits** (`weakest_groups`, `limits_sentence`): the `limits_worst_groups`
unflagged groups with the largest typical miss are named, without figures, in
the baseline's and every reform's "what the model cannot tell you" list
(`explain.payload.limits_block`): a reform's change is measured against the
baseline, so where the baseline has been least accurate the change is least
certain too. One named county is always flagged "few cases", so this rule alone
would never name it; `badly_off_named` names it anyway, first, when its typical
error is at least `name_few_cases_beyond` (a third) away from zero
(Pennsylvania: Philadelphia, where the baseline has run far too low). The
sentence says it is one county.

**County notes** (`county_note`): one sentence per county, from its row of the
county error map: "In past years, this tool's baseline for this county ran about
X% low; its ranges caught the real total in N of M years" (each case is one past
start year). The county panel ("Past accuracy") and the place profile's permits
summary show it; a tract's profile shows its county's.

The API's `GET /validation` carries all of it under `by_place`, and the rule
contest, the out-of-time test by group, the surge check, the stress ranges and
their sentences under `contest`; the static site saves the same reply as
`<ST>/validation.json`. `web/js/lib/api.js` reads the contest parts with
`baselineContest`, `baselineCalibration`, `baselineSurges` and `baselineStress`
(they only pick fields; every number comes from the engine).

Read with care: the back-test start years fall in a long stretch of falling and
then recovering permits, so a steady sign in the errors partly reflects that
period. The breakdown scores the rule's own projection, which is also the
typical-cycle P50 now that the pool-median correction is dropped; a bias that
differs between kinds of place is what this breakdown shows, and what the
out-of-time test by group measures.

## How to check it

- `tests/test_baseline_rules.py`: each rule against a hand calculation.
- `tests/test_baseline_backtest.py`: a backtest case by hand; coverage is 100%
  with no noise and close to 80% on 60 flat series with known lognormal noise;
  the simplest-within-tolerance choice.
- `tests/test_baseline_places.py` on Testland: the sign of the signed error;
  group statistics by hand; Testland's counties land in the expected size,
  urban/rural (from `area_type`), QCT (median split) and named groups; a missing
  table leaves only that grouping out; every grouping splits the same cases;
  small groups are flagged and kept; the sentences' wording and counts; the
  map classes; the limits sentence names the worst groups and carries no digit;
  the overall row equals the skill table's coverage.
- `tests/test_baseline_contest.py`: each new rule against a hand calculation
  (three-year mean, median with a freak year and missing years, shrink with
  two counties, top-down share, the average); the choosing rule (band, error,
  simplicity, ties, the fallback) gives the same answer every time; the pool
  adoption rule; the place-type pool falls back to all counties below the
  minimum; own-error widening only for far-off counties with enough cases; the
  out-of-time test does not move when later cases change, and does when an
  earlier one does; surge years; slump and boom origins; the contest on
  Testland marks one winner whatever the row order; the `/validation` payload.
- `tests/test_baseline_calibrate.py`: the block's fingerprint is pinned and a
  changed block is refused; the finite-sample ranks by hand; past means all
  five target years observed; spoiling every later case leaves a scored
  origin's range unchanged (no leakage); the size-class pool and its fallback;
  the range always holds the P50; the coverage helper by hand; every adoption
  branch (in band, under, over, a state short, the edges, a state with no
  cases); the engine reads only an adopted decision.
- `tests/test_baseline_reliability.py`: the block's fingerprint is pinned and a
  changed block is refused; the sentence and label by hand; county counts
  agree with the contest's coverage and add up over size groups; the state
  count uses only earlier totals (spoiling later totals leaves it unchanged);
  the record keeps its values and gains the sentence; a count made on another
  rule is not shown.
- `tests/test_validation_national_state.py`: the block's fingerprint is pinned;
  pooled and shock ends by hand; only earlier start years enter the pool; the
  range holds the P50; every pass and decision branch (under, over, a named
  state short, one passes, both pass); the engine reads only an adopted
  decision.
- `tests/test_baseline_card.py`: the state check reproduces the Pennsylvania,
  Massachusetts and Wyoming state components from the yearly totals through
  the engine's own rules; the window sensitivity and the nowcast by
  hand; a file posted after the cutoff is never downloaded; no monthly file
  gives "unavailable"; the API card. With `data/raw` present it rebuilds the
  card from the Census files and compares.
- `tests/test_baseline_project.py` on Testland: P10 <= P50 <= P90 for every
  rule and cycle; counties add to the state P50; the same input (even with rows
  shuffled) gives the same output; zero history gives zero with a reason.

## Limits

- The rules carry the recent past forward. They do not know about interest
  rates, prices, or a coming recession; the cycle control is the only lever.
- Coverage is measured across counties of one state. Counties share the same
  housing cycle, so their errors move together and the measured coverage can
  look better than it would be in a year unlike the past.
- Overlapping backtest windows share years, so the ratios are not independent.
- In the back-test, size classes come from the years up to each origin. Place
  types from the county typology are today's grouping, used for every origin.
- The rule contest chooses on every origin at once (in-sample); only the ranges
  and corrections are tested out of time.
- Ranges tested on later years (the out-of-time test) catch the real total less
  often than ranges tested on other counties of the same years: a new housing
  cycle moves every county at once, and the earliest origins have only one
  cycle behind them. The Method screen shows both coverages. A calibration
  written in advance (finite-sample ranks of past errors, own county
  included) widened the ranges but did not bring coverage near 80% in any test
  state, so it was not adopted; read the likely range as narrower than a true
  80% range (Technical Appendix B). Each baseline range therefore states its
  measured share ("range (caught N% of past totals)"). A second calibration
  of the state range from every state's past state-total errors was not
  adopted either.
- The stress settings split a short record: each kind rests on a handful of
  overlapping five-year windows.
- Testland is synthetic. Its numbers test the mechanics, not the method's skill.
- 2026 has no full-year permit count by the cutoff. The baseline carries the
  rule's level through it; the monthly nowcast is shown beside it as a check,
  not used. Pennsylvania's middle value leans on its high 2021 total (window
  sensitivity above).
- The error-by-place groups are descriptive. A group's error is not caused by
  being rural or by its Qualified Census Tract share; groups overlap (most small
  counties are rural), and one county's cases share years, so a group's cases
  are not independent.
