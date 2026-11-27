# Module: Nested rolling-origin selection (`src/visionhousing/validation/nested_selection.py`)

**What it does.** Re-chooses the baseline's projection rule at each past start year using only outcomes that were known then, and scores the chosen rule on what followed. It shows how favourable the published "share caught" is because the shipped rule was chosen on the full record with today's place types.

**Why.** The baseline contest (`baseline/contest.py`) picks each state's rule using every past year, including the years later used to score it, and describes counties with today's typology and tract homes. The validation cards and the limits say so. This study measures the effect instead of only disclosing it.

Command: `visionhousing validate-nested-selection` (permits only; a few seconds). Settings and the pre-registered block: `nested_selection` in `assumptions/validation_nested_selection.yaml`, fingerprint in `assumptions/preregistration.yaml` (written and logged before the study code existed or any score was computed). Results: `assumptions/calibration/nested_selection.yaml`.

## Terms

- **Origin.** The last data year of a past projection.
- **Matured by o.** A past case with origin o' whose five target years had all been observed by o: o' + lead + horizon − 1 ≤ o. Lead and horizon are the forecast's own, so each case has the same information gap as the 2027–2031 projection.
- **Permit-only place info at o.** Size classes from permit years up to o; no county typology, no tract homes.

## Arms

| Arm | Rule | Place info |
|---|---|---|
| `full_record` | the shipped rule (chosen on the full record) | today's (as shipped) |
| `full_record_permit_only` | the shipped rule | permit-only at o |
| `nested` | at each o, the contest's own choosing rule applied to county cases matured by o | permit-only at o |

All arms score at the same start years: those where the reliability study scores the state range (at least 8 matured state cases). Scoring is the reliability study's ([baseline](baseline.md), "Measured reliability"): the state range rebuilt from matured past state-total cases with the same widening, and the county range from other counties' matured cases in the same size class. The bias-correction choice, the small-sample prior and the growth-aware widening stay as chosen on the full record in every arm.

## What it reports

Per state and arm: state covered / cases, mean width ((P90 − P10) / actual), median |log(actual / P50)|, each start year's range and whether it caught the total; county covered / cases; the rule at each start year and how often the nested pick equals the shipped rule. A **fold audit** lists, for each start year, the selection origins, their last target year (never after the start year), and the evaluation years. A **parity check** requires the `full_record` state counts to equal the reliability study's.

It is descriptive. No result changes the shipped rule or any range.

## Reading the results

Compare `nested` with `full_record`. The gap is the effect of choosing the rule on the full record and with today's place types; `full_record_permit_only` splits off the place-type part. The state counts rest on eight overlapping windows per state (about one housing cycle), so a one-case difference is within noise. The county counts carry more cases but share the same years.

## How it is tested

`tests/test_validation_nested_selection.py`: the block is registered and a changed block is refused; the matured rule; on Testland the parity check passes, every fold's selection ends before its start year and its evaluation starts after it, and each range is ordered and its "caught" flag is right; permit-only place info has no typology or homes; the saved results cover PA, VT, MA and WY with parity and the audit passing.

## Limits

- The bias correction and both widenings are still set on the full record.
- Permit-only place info drops the typology; it is not a historical typology rebuilt from data of the time.
- Overlapping start years share target years.
