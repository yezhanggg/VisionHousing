# Fee reform (benchmark 5) and the shared cost-saving math

Code: `src/visionhousing/scenarios/fees.py`. Tests: `tests/test_scenario_fees.py`.
Method and evidence: [`docs/methods/fees.md`](../methods/fees.md).

## What it does

It estimates how many more homes are permitted in 2027–2031 if every development
fee is cut by half. A fee cut makes no new building type legal. It makes homes that
are already legal cheaper to build, so a few more get built. The model calls this
**Channel B**, the cost channel:

```
added units = baseline units × ε × (ΔC ÷ V) × k
```

| Term | Meaning | Where it comes from |
|---|---|---|
| baseline units | Permitted units 2027–2031 with no reform, by county and structure type | `baseline_by_type(result)` |
| ΔC | Cost saved per unit: fees per unit × cut share | `fees.fee_total_per_unit`, `fees.fee_cut_share` |
| V | Value of one new unit, by county and structure type | county medians from ACS brackets, `supply.new_construction_value_premium`, cap rates and running-cost shares |
| ε | Medium-run supply elasticity | `supply.elasticity_medium_run` |
| k | Share of the saving that reaches the decision to build | `fees.passthrough_k` |

This module also holds the pieces the parking reform reuses: `channel_b`,
`value_per_unit`, `market_from_tracts`, `proforma_crosscheck`, `swing_table` and the
record helpers.

## Inputs

- `baseline`: a frame indexed by county id with columns `units_1`, `units_2`,
  `units_3_4`, `units_5plus` (five-year totals). `baseline_by_type(result)` builds it
  from a projected baseline and scales each county to its P50.
- `tracts`: the tract table (`county` and the `value_*` and `rent_*` bracket columns).
- `ledger`, `n_draws`, `seed`, optional `overrides` (ledger overrides for this run).
- `view`: `"benchmark"` (the default: fees of $50,000 per unit, cut 50%) or `"state"`.
- `baseline_scale`: optional multipliers by county and draw, so the engine can carry
  baseline uncertainty through.

## Value of a new unit (V)

The county's tract bracket counts are added up and the median is read from them
(`features.brackets.median_from_brackets`).

| Structure type | V |
|---|---|
| 1-unit | median home value × (1 + premium) |
| 2-unit, 3–4-unit | median gross rent × (1 + premium) × 12 × (1 − running-cost share) ÷ cap rate, small-building values (`missing_middle.*`) |
| 5+-unit | the same with mid-rise values (`multifamily.*`) |

## Outputs (`ChannelBResult`)

| Field | What it holds |
|---|---|
| `counties`, `state`, `by_type` | Added units as P10 / P50 / P90 records with formula, ledger ids, source ids and tier |
| `crosscheck`, `crosscheck_share` | The pro-forma cross-check: implied added units, and the percentage points of sites that become feasible |
| `steps` | The explanation funnel at central values: saving → baseline → value → ratio → ε → k → added |
| `swing` | One-at-a-time swing table (tornado), largest first |
| `added`, `parts` | Draws, shape (counties, structure types, draws) |
| `notes` | Plain statements, including which inputs are drafts |

The funnel's value step is the *effective* value (average saving ÷ the
baseline-weighted average of saving ÷ value), so multiplying the steps gives the
central-value total exactly.

## How to check it

- `test_golden_fee_case`: one county, hand-worked numbers.
- Invariants: no cut gives the baseline exactly; k = 0 or ε = 0 gives zero; a deeper
  cut never lowers units in any draw; P10 ≤ P50 ≤ P90; same seed, same result;
  counties add to the state in every draw.
- `test_passthrough_tops_the_fee_tornado`: k is the widest input of the benchmark view.


## What the fee cut costs

`ChannelBResult.per_unit` keeps the per-draw baseline homes reached and the saving per unit, and `engine.readouts` turns them into the public cost: revenue given up = fee cut per unit × (baseline homes + added homes), county by county, and cost per added home = revenue given up ÷ added homes, in each draw. A cut applies to every permitted home, so most of the revenue goes to homes that would be built anyway. The state-fee-level variant reruns this module once in its `state` view; the targeted variant sums the same counties' draws within one group of similar counties (no rerun). See [engine.md](engine.md#readouts-from-the-paired-draws-readoutspy).

## Documented local view

`view="local"` (variant `fees_documented_local`) takes fees per unit by county
and structure type from `local_fees(params, counties)`: the documented city
schedules where a city's core fee categories are complete and the city is a
whole county (Philadelphia today), the labelled judgement
`fees.pa_assumed_fee_per_unit_judgement` elsewhere. The saving is that fee times
the 50% cut, so it varies by county and type; the fiscal readouts use
`engine.readouts.local_per_unit` to weight each county's fee by its baseline
and its added homes. See [fee_schedules.md](fee_schedules.md) and
[methods/fees.md](../methods/fees.md). The benchmark view never reads the
schedules.

## Fiscal screen and the deferral view

`engine.readouts.fiscal_split` splits a cut's money in every draw: waived on homes built anyway, waived on the added homes (the two add up to the revenue given up), fees still paid by the added homes, replacement funding (`fees.replacement_funding_share`, none by default) and the net change. `fee_reform(..., view="deferral")` (variant `fees_deferral`) keeps the fee but moves payment to occupancy: saving per unit = fees x construction-loan rate x deferral months / 12; `engine.readouts.deferral_readouts` gives its public cost (time value and collection loss), the fees the added homes pay, and the net change. Method: [fees](../methods/fees.md#fiscal-screen-who-gives-up-what). Tested in `tests/test_benchmark_variants.py`: the split adds up to the revenue given up; the replacement share scales it; the deferral adds fewer homes than the cut at a smaller public cost; the saving by hand.

The fiscal identity per paired draw is `revenue change = (F′ − F) × B + F′ × A` (records `fiscal_revenue_before`, `fiscal_revenue_after`, `fiscal_revenue_change`), with `fiscal_net_loss_per_added_home` (draws that add homes) beside `fiscal_zero_gain_share` (share of draws that add none, with its Monte Carlo standard error). `fee_reform(..., capacity_case="delayed")` (variant `fees_delayed_capacity`) multiplies the added homes by 1 − `fees.utility_earmark_share` × `fees.delayed_capacity_share` and records `delayed_by_capacity`; variant `fees_funded_backfill` swaps the replacement share to 1. Option `baseline_window` (variant `fees_baseline_window_pa_2022_2025`, Pennsylvania only) scales the baseline, and the fiscal B with it, to the baseline card's window diagnostic. `load_cost_settings` reads `assumptions/cost_reforms.yaml`; `window_factor` reads the diagnostic. Tested in `tests/test_fee_fiscal.py` (the plan's B = 100, A = 5 case; zero incidence adds nothing but still shows the revenue lost; the named cases).
