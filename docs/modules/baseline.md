# Baseline: likely permits with no reform

Code: `src/visionhousing/baseline/`. Tests: `tests/test_baseline_rules.py`,
`tests/test_baseline_backtest.py`, `tests/test_baseline_project.py`.

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
type separately; the five years and four types are then added up.

| Rule | What it does | Ledger settings |
|---|---|---|
| `last_5_mean` | Average of the last five years | `baseline.recent_window_years` |
| `last_10_mean` | Average of the last ten years | `baseline.long_window_years` |
| `ewm` | Exponentially weighted mean: the latest year has weight alpha, each earlier year (1 - alpha) times the next | `baseline.ewm_alpha` |
| `damped_trend` | Ten-year average plus damping x the fitted yearly slope x years from the window's middle year; never below zero | `baseline.long_window_years`, `baseline.trend_damping` |

Damping 0 gives a flat average; damping 1 carries the full straight line forward.

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
     fell inside the interval. The interval for a county is built only from
     *other* counties' ratios in its size class (a geographic split), so it is
     an out-of-sample check. A good interval covers about 80%.

`choose_rule(skill, tolerance)` keeps every rule whose median absolute log ratio
is at most best x (1 + `baseline.selection_tolerance`) and picks the simplest
(plain means, then the weighted mean, then the trend).

`baseline_from_ledger(table, ledger, cycle)` does all of it: backtest, choose,
project. The command `visionhousing validate baseline` will call the same
function later.

## The likely range

1. **Point projection** for each county: the chosen rule, summed over five
   years and four structure types.
2. **Size class**: counties are split into terciles (`baseline.size_classes`)
   by their average yearly permits over the last five years. Small counties
   swing more in proportion, so each class has its own ratios.
3. **Multipliers**: from all past ratios in the class, take the 10th
   percentile, the cycle quantile, and the 90th percentile.
4. County P10 / P50 / P90 = point projection x those three multipliers.

There is no assumed error distribution; the range is the rule's own record.

### Housing cycle control

One choice, *weak / typical / strong*, sets which quantile of the ratios is the
centre: `baseline.cycle_quantile_weak` (0.2), `_typical` (0.5), `_strong` (0.8).
The P10 and P90 stay at the 10th and 90th percentile ratios. "Typical" uses the
median ratio, so it also corrects any steady bias the rule showed in the past.

### The state, and why ranges do not add

- **State P50 = the sum of county P50s.** Counties always add up to the state.
- **Percentiles do not add.** Adding county P10s gives a number below the true
  state P10, because a bad year in one county is partly offset by a good year in
  another. So the state's range comes from running the same backtest on the
  state's own yearly totals: state P10 = state P50 x (state ratio P10 / state
  ratio at the cycle quantile), and the same for P90.
- Never add county P10s or P90s to get a state range.

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

## How to check it

- `tests/test_baseline_rules.py`: each rule against a hand calculation.
- `tests/test_baseline_backtest.py`: a backtest case by hand; coverage is 100%
  with no noise and close to 80% on 60 flat series with known lognormal noise;
  the simplest-within-tolerance choice.
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
- Size classes are fixed from the latest five years; a county that grew fast is
  judged by today's size.
- Testland is synthetic. Its numbers test the mechanics, not the method's skill.
