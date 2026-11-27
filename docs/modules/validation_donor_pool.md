# Donor-pool validation: whose error record should a small state borrow?

Code: `src/visionhousing/validation/donor_pool.py`. Command:
`visionhousing validate-donor-pool`. Settings, written before any score was
computed: `assumptions/validation_donor_pool.yaml`. Output (generated):
`assumptions/calibration/donor_pool_errors.yaml`. Tests:
`tests/test_validation_donor_pool.py`.

## The question

The baseline's likely range for a state comes from its own record: how far the
chosen rule's five-year projections fell from what was really permitted in
past years (see `baseline.md`). A state with few counties has a short, noisy
record. Vermont has 14 counties, so its range is widened toward Pennsylvania's
record (`small_sample` in `assumptions/backtest.yaml`).

Pennsylvania is one large state. Is its record the right one to borrow? This
study tests another source: a **donor pool** of past cases from permit areas in
*other* states that looked like the small state at the time. It changes no
baseline run. It reports what a pre-stated rule decides, and the engine is
changed only if the rule says "adopt".

## Data

The Census Building Permits Survey (BPS) publishes one county file per year for
the whole country (source `bps_county_annual`, the same files a state pack
reads). The command copies them (or downloads any that are missing) into
`data/raw/bps_national/` and reads them one year at a time into one table: one
row per county and year, units in the four structure types, the units that
permit offices actually **reported**, and the Census Bureau's **imputed** flag
(the Bureau estimates permits for offices that did not report). The 50 states
and DC are kept; Puerto Rico is left out. A county-year missing from a file is
"not reported", never zero.

## The rule being scored

`last_3_mean` (the mean of the last three years), the contest's winner for
Pennsylvania and the rule the Pennsylvania prior is measured with. Horizon (5
years), lead (2 years: data end in 2025, the projection starts in 2027) and
minimum history (10 years) come from the Assumption Ledger, exactly as in the
baseline's own back-test. The back-test cases come from the baseline's own
functions (`backtest_cases`), used read-only.

## What is scored

The **state total** (the counties added up), because the state range is what the
small-sample widening changes. One case per state and past start year
(*origin*), 1999-2019. A case's ratio is actual five-year units / projected.

A range is P50 x (ratio P10 / ratio P50) to P50 x (ratio P90 / ratio P50), the form
the baseline uses since its P50 correction was dropped. So every method has the
same P50 (the rule's projection) and the same typical error; the methods differ
only in their ranges. A good range catches the real total about 80% of the
time (its **coverage**) and is no wider than it needs to be (**width**: P90 minus
P10, as a share of the P50).

Two ways of choosing which past cases a method may use for an origin:

- **Out of time** (the one that decides): only cases whose five target years
  had all been observed by that origin, as in a real run. Each method needs at
  least 5 such ratios, so states are scored on origins 2009-2019.
- **Other origins, in sample** (context only; never decides): cases from other
  origins whose five target years do not overlap the scored case's (origins at
  least five years apart), earlier or later. It is in sample in time: later
  years inform earlier ones. Until 2026-11-24 it used every other origin,
  including overlapping windows that share most of their target years with the
  scored case; those cases nearly repeat the answer, so the old figures
  flattered every method. Pooled over the sparse held-out states (168 cases),
  coverage / width: own 52% / 0.84, `pa` 43% / 0.62, `pa_blend` 56% / 0.88,
  `national_states` 65% / 0.97, `national_counties` 89% / 1.95, `donor` 78% /
  1.51, `donor_blend` 77% / 1.40 (before the fix: 61%, 48%, 64%, 79%, 95%, 92%,
  90%). The out-of-time results and every decision are unchanged.

## The methods

| Method | The ratios its range comes from |
|---|---|
| `own` | The held-out state's own state-total ratios |
| `pa` | Pennsylvania's state-total ratios alone |
| `pa_blend` | **The current rule**: own ends blended toward Pennsylvania's on the log scale with weight `w = min(1, n / 1,000)` on own (`n` = the state's county-origin cases), and only ever widened |
| `national_states` | Every other state's state-total ratios, pooled |
| `national_counties` | Every county outside the state, pooled, not matched |
| `donor` | The donor pool (below) alone |
| `donor_blend` | The current rule with the donor pool in place of Pennsylvania: the candidate |

## The donor pool

Donor cases are county-origin back-test cases from counties outside the target
state. Each case is described at its own origin, and the target state's total
at the scored origin, by three features computed only from years up to that
origin:

- **size**: mean yearly units over the last 5 years;
- **growth**: log((mean of the last 3 years + 1) / (mean of the 7 years before + 1));
- **completeness**: units reported by permit offices / units in the Census
  estimate, over the last 10 years.

Distance = |difference in log size| + |difference in growth| + |difference in
completeness| (weights of 1 each, in the settings). The 200 nearest cases form
the pool, at most 3 from any one county (its nearest), ties broken by county
code then origin. Size is matched on the state total, so a small state's donors
are counties that build about as much as the whole state.

## Held-out states and the decision rule

Held out: Vermont, New Hampshire, Maine, Rhode Island, Delaware, Wyoming, New
Mexico and Nevada (small or mostly rural states with few counties; each has
fewer than 1,000 county-origin cases, so the widening acts on it), plus
Pennsylvania as a sanity check (not sparse, so left out of the decision).
Connecticut is not used: its counties were replaced by planning regions in the
recent files.

The rule, fixed before scoring: adopt donor-pool errors for sparse states only
if, out of time and across the sparse held-out states,

1. the mean over states of |coverage - 80%| is lower for `donor_blend` than for
   `pa_blend` (a tie keeps the current rule); and
2. its ranges are not wider than needed: if `donor_blend` is wider on average,
   its pooled coverage must not be above 85%.

Otherwise the current widening toward Pennsylvania stays.

## The result (November 2026 run)

The file holds every number: `decision` (the outcome and its reason), `pooled`
(each method across the sparse held-out states, both variants), `holdout` (each
state and method: cases, coverage, width, typical error with and without each
method's median correction) and `sparse_states` (the donor-pool ratio quantiles
each sparse state would borrow, with the size of the pool and its own record
beside them).

In the November 2026 run the rule **kept the current widening**. Counties that
build as much as a whole small state miss by more than the state total does:
the state total adds up several counties whose surprises partly cancel. So the
donor ranges caught the real total almost every time and were much wider than
needed. The current rule's coverage was the closest of the pre-stated pair to
80%. One unplanned observation: the pooled record of all other states' totals
(`national_states`) came closer to 80% than either. It was not the pre-stated
candidate, so it decides nothing. The second test below tests it with its own
rule, written first, on states the first test did not use.

## A second test: pooled state totals, on fresh states

**Why.** A finding that was not the planned candidate can be luck: with many
methods and eight states, one of them will look best by chance. The honest
check is to state the rule first and score the method on new data.

**Written first, with a fingerprint.** The block `pooled_states_test` in
`assumptions/validation_donor_pool.yaml` fixes the candidate, the fresh states,
the scoring and the decision. Its fingerprint (a SHA-256 hash: a 64-character
code that changes if any value in the block changes) is computed from the block
as sorted JSON, so comments and layout do not count. The fingerprint was logged
before the candidate was scored on the fresh states. The results file repeats it
under `pooled_states_test.preregistration`, and a test fails if the block is
ever edited. Anyone can recompute it with `preregistration_sha256`.

**The candidate** (`states_blend`): the current small-sample rule with one
change. Today a sparse state's own record is blended toward Pennsylvania's. The
candidate blends it instead toward the pooled state-total errors of every other
state (equal weight per case, no matching or weighting by similarity), the pool
the first test saw. The blend is the form that would actually ship: wiring it
would swap only the prior. The pooled record used alone (`national_states`) and
the state's own record are shown beside it but do not decide.

**The fresh states**, picked by a stated rule (`select_fresh_states`), not by
hand: start from all states; drop the first test's nine; drop DC and every state
with a substantial county change since 1990 in the Census Bureau's list (Alaska,
Colorado, Connecticut, Florida, Montana, South Dakota, Virginia); keep only
sparse states (fewer than 1,000 county-origin cases, the only states where the
widening acts; with full weight every blend is the state's own record, so no
method could differ); rank by mean yearly units permitted, smallest first; keep
eight. Housing-unit counts are not in the national permit panel, so permit
volume is the size measure. The rule gave Hawaii, Idaho, Massachusetts, Oregon,
Utah, Maryland, New Jersey and South Carolina (Washington and Arizona were the
two eligible states left over). These are sparse because they have few counties,
not because they build little.

**Scoring and decision.** Out of time, as before. The gap is the mean over
states of |coverage - 80%|; the width is the mean (P90 - P10) / P50. Adopt the
candidate only if (1) its gap, rounded to three decimals, is lower than the
current rule's (on a tie, only a strictly narrower candidate wins), and (2) it
is not wider than needed: if it is more than 10% wider than the current rule,
its pooled coverage must sit inside 75-85%. At least six fresh states must be
scored, or the test is inconclusive.

**Result (November 2026 run): keep the current widening.** All eight states
were scored (88 cases, origins 2009-2019). The table is in the results file under
`pooled_states_test.decision.methods`; in short, the candidate's gap was lower
than the current rule's, but its ranges were more than 10% wider and its pooled
coverage was outside the band, so clause 2 kept the current rule.

Two things the result shows, stated openly:

- **Clause 2 was worded too broadly.** It was meant to stop ranges that are
  wider than needed (coverage above 85%). As written it also rejects a wider
  candidate whose coverage is *below* 75%, where the extra width was needed. That
  is what happened: the candidate covered fewer than 80% of cases. The outcome
  follows the clause as written; changing it now would be choosing the rule after
  seeing the scores. A fair retest would need a new rule and new held-out states,
  and only two eligible states are left.
- **Every method under-covers on these states.** On the fresh states the current
  rule caught the real total well under 80% of the time, and so did the
  candidate, mostly because of fast-growing states (Idaho, Utah) whose recent
  permits fell well short of what came next. This is a limit of the baseline's
  ranges for such states, not only of the borrowed record.

The results file also gives Vermont's likely range for 2027-2031 under the
current rule and under the candidate (`pooled_states_test.implied_range`), so
the size of the change can be seen in homes. The candidate's range is wider,
mostly on the high side.

## A third test: growth-aware coverage

**Why.** In the second test every method caught the real total well under 80%
of the time on the fresh states, worst in fast-growing ones. A possible reason:
the scored rule projects the mean of the last three years, so a state that has
grown for years keeps building above that mean, and its misses all fall on one
side. Code: `src/visionhousing/validation/growth_coverage.py`; command
`visionhousing validate-growth-coverage`; results in
`assumptions/calibration/growth_coverage.yaml`.

**Growth class.** At each past start year (origin), each state's growth is the
log of (mean yearly units permitted in the five years up to the origin + 1)
over (the mean of the five years before them + 1). Only years up to the origin
are used. At each origin the states are ranked by growth (ties by postal code)
and cut into three equal groups: slow, middle, fast. DC (not a state) and
Connecticut (its counties were replaced in the recent files) are not ranked.

**Step 1: diagnosis (describes, decides nothing).** For every ranked state, out
of time, the study records whether the current range caught the real total,
and if not, whether the total was above or below the range, plus the signed
error log(real total / projection). It reports these by growth class, for all
states and for the sparse states (`diagnosis` in the results file).

What it showed (November 2026 run): the misses are one-sided. Almost all of
them are real totals *above* the range, in every class. But the fast-growing
class did not miss most; the slow class missed most. The scored origins
(2009-2019, out of time) are the years after the housing bust, when states that
had fallen furthest recovered fastest. So the guess about fast growers was not
supported; the one-sided misses look like the recovery from the bust, which
the trailing mean always lags.

**Step 2: written first, with a fingerprint.** After the diagnosis and before
the candidate was scored, the block `growth_coverage_test` in
`assumptions/validation_donor_pool.yaml` fixed the candidate, the evaluation
and the adoption rule. Its fingerprint is in the results file under
`preregistration`, and a test fails if the block is edited. The candidate was
kept as first planned, not tuned to the diagnosis.

**The candidate** (`growth_widen`). Start from the current range. Pool the past
errors (real total / projection) of every *other* state in the same growth
class, using only cases whose five years had been observed by the origin. Take
the pool's 10th and 90th percentiles *as they are*, not centred on the pool's
median, so a class that tends to build more than projected widens the top of
the range more than the bottom. Blend each end of the current range toward the
pool's with the same weight the current rule uses (the state's share of a full
record), and only ever widen. A state with a full record of its own keeps its
current range exactly; a pool with fewer than five errors leaves it unchanged.

**Evaluation.** Leave one state out, out of time, across every sparse state
(fewer than 1,000 county-origin cases) except DC and Connecticut. Pennsylvania
is scored the same way for clause 3.

**Adoption rule.** Adopt only if all three hold: (1) the mean over states of
|coverage - 80%|, rounded to three decimals, is lower than the current rule's
(a tie keeps the current rule); (2) the candidate's pooled coverage is at most
85%; this clause only stops ranges wider than needed, so coverage below 75%
does not on its own reject the candidate (the second test's clause was worded
too broadly; this one says what it means); (3) Pennsylvania's coverage: if the
current rule's is inside 75-85%, the candidate's must be too; if it is already
outside, the candidate may not move it further from 80%. At least ten sparse
states must be scored.

**Result (November 2026 run): adopt.** Twenty sparse states were scored. The
candidate was closer to 80% on average and its pooled coverage stayed under
85%; Pennsylvania's coverage did not move. The gain came from the slow and
middle classes; the fast class was unchanged. The table by class and state is
in the results file (`decision.methods`, `by_class`, `by_state`). The
candidate still covers less than 80% overall: it narrows the shortfall, it does
not close it. `implied_range` gives the real-run range for Vermont (wider on
the high side only) and Pennsylvania (unchanged, since it has a full record).

**What adoption means.** The decision lets the engine use the candidate, and
it does: `widen_growth_class` in `visionhousing.baseline.project` applies it to
each listed sparse state's range (`docs/modules/baseline.md`, "Sparse states:
growth-aware widening"). The state confidence check below still scores the
range without it.

## A state confidence check: the current range, held out, per state

The readiness check (`intake.md`, step 3) asks whether a validation set holds
the state out. The first test held out VT, NH, ME, RI, DE, WY, NM and NV, but
not every state the tool publishes (Massachusetts was scored only in the second
test, under other methods). This check scores the method the baseline uses
today, for each state listed in `assumptions/validation_state_holdouts.yaml`
(block `state_holdout_check`: MA, WY and VT).

- Method: `pa_blend`, the state's own state-total error record blended toward
  Pennsylvania's with w = min(1, n / full_weight_cases), only ever widened. The
  growth-aware widening the third test adopted is not included, because the
  engine does not use it yet; scoring it would be a new block.
- Out of time only, with the first test's rule, quantiles and minimum cases.
- Reported per state: cases scored, coverage, misses above and below, mean
  width, the weight on the state's own record, and whether coverage sits inside
  75-85%.
- Descriptive: no candidate and no adoption rule. Readiness counts a state as
  held out when at least five of its cases were scored; the coverage is shown
  beside it and never gates.

The block's fingerprint was recorded in `assumptions/preregistration.yaml` and
the decision log before its first score; `visionhousing validate-state-holdouts`
checks it (and `donor_pool_rules`) first and stops on a mismatch. Results:
`assumptions/calibration/state_holdouts.yaml`.

First run (November 2026, origins 2009-2019, 11 cases each): Massachusetts
6 of 11 caught (55%; all 5 misses above the range; mean width 0.87);
Wyoming 10 of 11 (91%; 1 miss below; width 1.06); Vermont 11 of 11 (100%;
width 0.90). These equal the current-rule figures of the third test, as they
should (same method, same cases). Massachusetts' range is too narrow on the
high side; the growth-aware widening, once wired, is the change aimed at that.
Wyoming and Vermont are above the band: their ranges are wider than needed
there.

## Confidence signals for the interface

`confidence_signals(state)` returns two separate signals that are never
combined into one score:

- **Baseline performance**: whose error record the state's range uses (`own`,
  `pennsylvania_prior` or `donor_pool`), the state's county-origin cases and
  weight on its own record, and, if the state was held out, how often the range
  caught the real total out of time ("In 11 past start years, the state's likely
  range caught the real five-year total N times"). Grade: `back-tested` (own
  record, coverage within 75-85%), `borrowed` (the range leans on another record)
  or `limited`.
- **Reform evidence**: for each reform type, how many cross-state comparison
  records exist (`assumptions/evidence/states/`), how many are verified and have
  an observed result, how many are in this state, and a grade from the best
  verified result: causal or matched = "tested elsewhere", descriptive =
  "observed elsewhere", verified law records only = "law on record", none = "no
  records".

A state can have a well-tested baseline and thin evidence for a reform, or the
reverse; showing them apart keeps that visible.

## Pre-registration check

A rule written before the scores protects against fitting the rule to the
answer only if nobody can change it quietly later. So the expected fingerprint
of every fixed block lives in a separate file,
`assumptions/preregistration.yaml` (block name, SHA-256, the date it was
recorded): `donor_pool_rules` (the first test's keys, listed there),
`pooled_states_test`, `growth_coverage_test`, `state_holdout_check`
(`assumptions/validation_state_holdouts.yaml`), and the retrospective panel's
`retro_design` (`evidence_retro.md`), plus two blocks recorded on 2026-11-25
before their first scores: the panel's treatment-date audit
`retro_treatment_audit` (`assumptions/evidence/retro_audit.yaml`, checked by
`evidence-retro audit`) and the validation cards' `validation_card_scores`
(`assumptions/validation_cards.yaml`, checked by `validate-cards`; see
`validation_cards.md`), and two recorded on 2026-11-26 before their first
scores: the nested rolling-origin selection test `nested_selection`
(`assumptions/validation_nested_selection.yaml`, checked by
`validate-nested-selection`; see `validation_nested_selection.md`) and the
retrospective panel re-scored on today's comparison pool `retro_current_pool`
(`assumptions/evidence/retro_current_pool.yaml`, checked by
`evidence-retro current-pool`; see `evidence_retro.md`). Before any score is computed,
`validate-donor-pool` checks `donor_pool_rules` and `pooled_states_test`,
`validate-growth-coverage` checks `donor_pool_rules` and
`growth_coverage_test`, and `evidence-retro run` checks `retro_design`
(`visionhousing.validation.preregistration.verify`). On a mismatch the command
stops and names the block; nothing is scored. The verified fingerprints are
written into each results file (`preregistration_checks`, or
`preregistration.checks`). A deliberate change means a new entry in that file,
with its date and reason.

What this can and cannot show: the file was first written on 2026-11-24,
after each study had already run once. It guards the rules from now on; it
cannot prove what they were before that day. The second and third tests'
fingerprints were also written in the decision log before their first scores,
and those logged values are the ones in the file; the first test's rules and
the retrospective design had no fingerprint logged before their first run.

## How to check it

`tests/test_validation_donor_pool.py` uses small synthetic panels and no
network: the features by hand (a missing year is not a zero); the donor rule
(nearest first, the cap per county, the pool size, the completeness floor); the
blend only widens; state cases add up their counties; out-of-time ends do not
move when later cases change; the target state is never in its own pool; every
method is scored on the same cases with the same P50; each branch of the
decision rule; the whole study gives the same document twice; the county files
are read by header name and Puerto Rico is left out; the pack's cached files are
reused without network; the two confidence signals; the command is registered.
For the second test: the pre-registered block still has the logged fingerprint
and the results carry it; the fresh-state rule gives the same states whatever
the input order (ties by postal code); each branch of the second decision rule
(including the tie-break and the inconclusive case); the implied range holds the
P50 and the blend only widens. The pre-registration check: the registry holds
the logged fingerprints and today's blocks match them; a changed rule (first
test or second test) stops `run_study` before any case is built; the results
carry the verified fingerprints. The context variant never uses a case whose
target years overlap the scored case's.
For the third test (`tests/test_validation_growth.py`): the block still has its
logged fingerprint and the second test's block is unchanged; the growth measure
by hand; the growth classes give the same result in any row order, with ties by
postal code; the class pool uses raw errors and needs at least five; the
candidate only ever widens, and a full record keeps the current range; the
held-out state is never in its own pool; each branch of the adoption rule
(including that under-coverage alone does not reject, and both Pennsylvania
cases); the command is registered.

## Limits

- Each held-out state is scored on about a dozen overlapping five-year windows,
  so one state's coverage moves in steps of several points. The decision rests
  on 8 states together.
- All origins share the national housing cycle (2009-2019 out of time), so the
  held-out states' errors move together.
- The 2003 county file reports no imputation (reported equals estimated), so
  completeness is overstated for windows that hold 2003.
- County boundaries changed in a few places (Connecticut, Alaska, Virginia's
  independent cities); a county missing a year simply has fewer cases. The
  second test leaves those states out of its fresh set.
- The third test's sparse states were already seen by the first two tests;
  only its candidate and rule are new. Its origins cover one cycle, the
  recovery after the housing bust, so a growth class's past errors may not
  describe the next cycle.
- Matching on size, growth and completeness is a choice; other features could
  match better. Any new choice needs its own rule written before scoring.
