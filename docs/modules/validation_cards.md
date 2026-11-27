# Validation cards

Code: `src/visionhousing/validation/cards.py`. Rules (fixed before any score): `assumptions/validation_cards.yaml`, block `validation_card_scores`, fingerprint in `assumptions/preregistration.yaml`. Results (generated): `assumptions/calibration/validation_cards.yaml`. Command: `uv run visionhousing validate-cards`. API: `GET /api/v1/validation?state=XX` returns the state's cards in `cards`. Tests: `tests/test_validation_cards.py`.

## What it is for

Screens and documents quote counts such as "caught 8 of 8", "10 of 11" or "6 of 11". Each says how often a range caught the real five-year permit total in past years. But they come from different studies:

| Quote | Card | Study | Geography | Start years |
|---|---|---|---|---|
| 8/8 | `baseline_reliability.state.PA` | Measured reliability of the baseline range | Pennsylvania state total | 2012-2019 |
| 545/1,004 | `baseline_reliability.county.PA` | Same study, county ranges | Every Pennsylvania county | 2005-2019 |
| 10/11 | `state_holdouts.state.WY` | State confidence check | Wyoming state total | 2009-2019 |
| 6/11 | `state_holdouts.state.MA` | State confidence check | Massachusetts state total | 2009-2019 |
| 7/8, 6/8 | `national_state_calibration.*.state.*` | National state-total calibration (not adopted) | One state total | 2012-2019 |

A **validation card** sits beside each count and names: the study and its command, its pre-registered block, the method, the rule scored, the geography, the target (five-year total of units authorized by building permits), the origin set (the start years, lead and horizon) and the release (the data files and the date the count was made). So a reader can tell which exercise a number refers to.

## What it adds

For the studies whose cases can be re-derived here (the baseline's measured reliability, state and county; the state confidence check), the card first rebuilds every scored case with the study's own building blocks (read-only) and checks it gets exactly the published count (**parity**). If parity fails, the card keeps the published count and shows no new score. All re-derived cards currently match.

Then, per card:

- **Interval score.** For a central range that should hold 80% of outcomes (alpha = 0.2), with lower end L, upper end U and actual total y:

  IS = (U − L) + (2 / alpha) × (L − y) if y < L, + (2 / alpha) × (y − U) if y > U,

  divided by y so a state and a small county compare. Lower is better. It punishes both a range that misses and a range that is too wide to say anything. The card shows the mean, the median and the two parts (width and miss penalty).
- **Signed error.** log(actual / centre), where the centre is the study's own (P50 for the state range, the rule's projection elsewhere). Above zero: more homes were permitted than the centre said. Median, mean, and the counts of misses above and below.
- **Error by size.** County cards: by the study's size class at each start year (small, medium, large). State cards: a cross-state table orders the states by their mean five-year total.
- **Non-overlapping subset.** Start years one year apart share four of their five target years, so 8 overlapping windows are closer to 2 independent checks than to 8. The card counts only start years five years apart, anchored at the latest one (for example 2014 and 2019), and repeats this for every other anchor, giving the lowest and highest coverage across anchors.
- **Fold timeline.** For each start year: the train years (errors from cases whose five target years had ended by then), the gap year(s), the evaluation years, and the selection years.

## What it discloses

- **The rule was chosen on the full record.** Each study scores a rule (and the baseline's bias-correction and widening settings) that was picked using all years, including the evaluation years of every fold. The fold timeline flags this overlap. The counts are therefore somewhat favourable.
- **Present-day place types.** The baseline contest that chose each state's rule describes counties with today's typology and tract mix, not what was known at each start year. County ranges use size classes computed at each start year. Origin-specific typologies are not rebuilt in this release.
- **Measured, not only disclosed.** A pre-registered nested rolling-origin test re-chooses the rule at each start year from matured cases only, with permit-only place types, and scores it the same way: [validation_nested_selection.md](validation_nested_selection.md).
- **Different rules.** The state confidence check scores one fixed rule (`last_3_mean`) for every state, not each state's shipped rule; the card names the rule it scored.

## Pre-registration

The block was written and fingerprinted on 2026-11-25 before any of its scores was computed (the coverage counts themselves were published earlier by their own studies). The command refuses to run if the block changed. On the first run the county parity check failed for Pennsylvania and Wyoming because counties with zero permits had been dropped before counting; the fix counts every case for coverage and leaves only those cases out of the relative scores (they are counted in `excluded_from_relative_scores`). No rule changed. The national state-total calibration is listed with its counts only, as pre-registered.

## Limits

- The non-overlapping subsets hold two or three cases per state. They show how little an overlapping "8 of 8" certifies about a future 80% guarantee; they are not a better coverage estimate.
- All start years share one national housing cycle.
- Nothing here changes an estimate or a range.
