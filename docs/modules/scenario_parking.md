# Parking reform (benchmark 4)

Code: `src/visionhousing/scenarios/parking.py`. Tests: `tests/test_scenario_parking.py`.
Method and evidence: [`docs/methods/parking.md`](../methods/parking.md).

## What it does

It estimates how many more homes are permitted in 2027–2031 if residential parking
minimums are removed. Builders still build most of the spaces; the ones they skip
save money (the cost channel, as for fees) and free land on tight sites (the site
yield).

| Step | Formula | Ledger ids |
|---|---|---|
| Spaces required | per unit, by structure type: apartments (5+) and attached (2, 3–4) | `parking.required_spaces_per_unit_multifamily`, `parking.required_spaces_per_unit_attached` |
| **Spaces still built** | required × share still built, by tract class | `parking.share_still_built_transit_rich`, `parking.share_still_built_elsewhere` |
| Spaces avoided | required − still built | |
| Cost per space | surface / structured / underground mix × cost per space | `parking.cost_per_space_*`, `parking.mix_*` |
| Saving ΔC | spaces avoided × cost per space | |
| Cost channel | baseline × ε × (ΔC ÷ V) × k | `supply.elasticity_medium_run`, `supply.saving_passthrough_k` |
| Site yield | baseline × site-yield gain × share of spaces avoided | `parking.site_yield_gain` |

1-unit buildings are not reached. "Spaces still built" is an explicit ledger value
and can be changed per run, for example
`overrides={"parking.share_still_built_elsewhere": 1.0}`. With both shares at 1 the
effect is exactly zero.

## Transit-rich tracts

`transit_rich_tracts(blocks, stops, ledger, boundaries=...)` flags a tract when either:

1. at least half of its housing units are in blocks whose centre is within ½ mile
   of a rail station or ¼ mile of a bus stop with 64 or more weekday departures
   (the major-stop rule; `parking.transit_*` ids), or
2. its boundary contains a major stop (when tract polygons are given).

Speed, with the same answer: blocks and stops are sorted by latitude, and each
batch of 2,000 blocks is measured only against the stops inside its latitude band
(the batch's latitudes widened by the largest radius). A great-circle distance is
never shorter than the earth's radius times the latitude gap, so a stop outside
the band cannot be within reach. The stop-inside-a-tract test checks only the
stops inside each outline's bounding box. `tests/test_perf_scenarios.py` checks
the flags equal measuring every block against every stop. The engine works the
flags out once per state and keeps them on the state context.

`transit_weights` then gives, for each county and structure type, the share of the
county's existing homes of that type in transit-rich tracts. The county's saving is
the two tract classes' savings weighted by that share.

## Inputs and outputs

Inputs: the baseline frame (see `scenario_fees.md`), tracts, blocks, stops, the
ledger, and optionally `boundaries`, `transit_rich` (flags to use as given),
`overrides`, `baseline_scale`. The output is the same `ChannelBResult` as for fees;
`parts` splits the draws into `channel_b` and `site_yield`. The funnel adds the
steps `transit`, `required`, `still_built`, `avoided`, `cost_per_space` before the
shared Channel B steps, and ends with `site_yield` and `total`.

## How to check it

- `test_golden_parking_case`: one county, all transit-rich or all not, hand-worked.
- `test_saving_by_hand_with_mixed_county`: the weighting between tract classes.
- Invariants: zero reduction gives exactly zero; k = 0 or ε = 0 zeroes the cost
  channel; less parking or dearer spaces never lowers units; ranges ordered; same
  seed, same result.
- Transit flag: distance rule, stop-in-tract rule, and Testland's urban counties.


## What the builders save

The reform has no public cost. `ChannelBResult.per_unit` keeps the per-draw baseline homes, spaces required, spaces avoided and saving per unit, by county and building type. `engine.readouts` weights them by the baseline homes the reform reaches: private saving per home = spaces avoided per home × cost per space avoided, in each draw, and the total saving over baseline and added homes. See [engine.md](engine.md#readouts-from-the-paired-draws-readoutspy).

## Matched prototypes, bounds and variants

`parking_reform(..., response="share" | "provided" | "voluntary")`. All three apply one rule per matched design: spaces avoided = max(v, m_before) − max(v, m_after), with `v` the spaces a project provides with no minimum and `m` the legal minimum (`expected_provided`, `share_below`; closed forms over an even spread of `v`). `share` is the benchmark (`v` = minimum × share still built). `voluntary` (variant `parking_voluntary_floor`) spreads `v` across projects by `parking.voluntary_project_spread`, so some projects already exceed the old minimum. Every run also writes the binding share and the added homes at each level of a partial cut (`binding_share_kept_<pct>`, `added_kept_<pct>`, `binding_share_before`, `binding_share_after`; levels in `assumptions/cost_reforms.yaml`, function `binding_records`), so a flat response to a partial cut is explained by a binding share near zero. Option `baseline_window` (variant `parking_baseline_window_pa_2022_2025`, Pennsylvania only) scales the baseline to the baseline card's window diagnostic (record `baseline_window_factor`); shown as a sensitivity, never subtracted. Tested in `tests/test_parking_binding.py` (no old minimum or `v ≥ m_before` gives zero avoided spaces; half equals full removal where the half minimum never binds; the curve's ends). `provided` sets the spaces still built to `min(minimum, spaces builders provide with no minimum)` by building type and transit class (`parking.voluntary_spaces_*`). `ChannelBResult.records` holds the spaces per home before and after in four matched prototypes (`provided_<apartment|small>_<transit_rich|elsewhere>_<before|after>`) and the saving per home if every space avoided were surface or structured (`saving_all_surface`, `saving_all_structured`). Variants: `parking_provided_spaces`, `parking_no_response` (exactly zero), `parking_all_surface`, `parking_all_structured`. Tested in `tests/test_benchmark_variants.py`.

Verified local minimums: `parking_reform(..., minimums=minimum_mix(...))` (variant `parking_verified_minimums`, engine option `minimums: verified`). `visionhousing.law.parking_rules.verified_parking_minimums` keeps the verified, in-force municipal rows with a parking number (read-only; Pittsburgh's Ordinance 31 counts as 0 from October 29, 2026). `minimum_mix` splits each tract's homes of a building type among its municipalities by 2020 housing units, within each transit class; unread places keep the labelled ledger default. Spaces avoided = max(v, m) − max(v, m left) averaged over the mix, with `v` from the default minimum, so a verified minimum at or below `v` avoids nothing. Records `parking_verified_share`, `parking_default_share`, `parking_verified_zero_share`, `parking_legal_minimum`, `parking_provided_before` (function `minimum_records`). Settings: `verified_minimums` in `assumptions/cost_reforms.yaml`. Tested in `tests/test_parking_verified_minimums.py`.
