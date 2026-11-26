# Plausibility data: our rate beside observed rates

Code: `src/visionhousing/validation/plausibility.py`. Settings:
`assumptions/plausibility.yaml`. Address:
`GET /api/v1/plausibility/{st}?r=adu` (same scenario fields as the other
state addresses). Tests: `tests/test_plausibility.py`.

## The question

Is a result of a few thousand ADUs, or a few hundred missing-middle homes,
believable? A total says little on its own. A rate can be set beside what
happened in places that passed similar reforms.

## Our rate

Read from the engine run's own records; nothing is computed in the browser.

| Measure | Formula |
|---|---|
| Homes per 1,000 eligible lots per year | added units authorized 2027-2031 / the reform's eligible lots / 5 years x 1,000, worked out in each draw (the same draw's added units and eligible lots), then P10 / P50 / P90 over the draws |
| Homes per 100,000 residents per year | the run's own `per_100k` record of the reform |

The per-lot rate is never a percentile of added units divided by a percentile
of eligible lots: added units and eligible lots move together across draws, so
that shortcut would stretch the range. Both per-draw arrays are read from the
run itself (the reform's per-draw state total and its `eligible` stage, summed
over tracts), read-only. A saved run (`to_dict()`) keeps no draws, so it has no
per-lot rate (`missing`, with the reason); its per-100,000 rate is still the
run's own record. Until 2026-11-24 the card divided each added-units
percentile by the eligible-lots P50; the per-draw rule moved the ranges (see
the build log).

TOD's eligible step is land (acres), so it is shown per 100,000 residents
only. Parking and fees act on the cost of homes that would be built anyway,
not on lots: their rate is `not_applicable`, with that reason.

The V2 California study (`validation_california.md`) used ADUs built before
displacement; this card uses the reform's headline (added units), so its ADU
rate is a little lower.

## Observed rates

| Source | What is read |
|---|---|
| `assumptions/evidence/california_apr.yaml` | Rate after each reform in the APR comparison records (statewide; San Diego with its local program; San Jose with state law only; Los Angeles), ADU homes permitted per 1,000 one-unit detached homes per year |
| `assumptions/calibration/adu_rate_plausibility.yaml` | The lowest and highest statewide California year |
| `assumptions/evidence.yaml` | Findings in "permits per 100k people per year" for places with permissive rules (the wording filter is in the settings) |
| `assumptions/evidence/states/` | Comparison records whose observed result is in one of those units |

Each point carries its place, period, identification and caveat. A value found
in two files is kept once, with the other file named in `also_in`. A finding
with no denominator (a count of permits) is not turned into a rate.

## The band and the sentence

The band is the lowest to highest observed rate in the same measure. The
sentence follows a fixed rule:

| Our numbers | Sentence |
|---|---|
| P90 below the band | "sits below all of the observed rates", plus the stated reason |
| P10 above the band | "sits above all of the observed rates", plus the stated reason |
| P50 below (or above) the band, range overlapping | "Our middle estimate … sits below (above) …, though the likely range reaches into them", plus the reason |
| P50 inside | "sits within the observed rates" (no reason needed) |
| No observed rate in that measure | says there is nothing to set it beside yet |
| Zero in every draw | says the result is zero in every likely case |

The reasons are written once per reform and side in
`assumptions/plausibility.yaml`, so the same situation always gives the same
words.

## Limits

- Observed places differ in market, rule and record length; every point has a
  caveat, and the band is context, not a target.
- California's denominator is one-unit detached homes; our eligible lots also
  include attached single-family homes, so our rate is lower for the same
  count.
- No missing-middle or TOD rate per lot was found in the evidence files yet;
  the retrospective reform panel will add rates per 1,000 homes when its
  results are written.
- Nothing here enters a model run.
