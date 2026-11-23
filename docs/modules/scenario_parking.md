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
   (decision D-07; `parking.transit_*` ids), or
2. its boundary contains a major stop (when tract polygons are given).

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
