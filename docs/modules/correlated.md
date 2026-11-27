# Module: Uncertainty structure (`src/visionhousing/engine/correlated.py`)

**What it does.** Two things, neither of which changes a benchmark number:

1. **Conservation checks.** In every Monte Carlo draw, for every state with a pack, the state equals the sum of its counties and the five-year total equals the sum of its years: for the baseline, each reform, the package and the total.
2. **Correlated-uncertainty sensitivity.** Named scenarios, shown beside the independent benchmark, in which uncertainty is shared: a statewide macro shock with regional residuals, and a joint "high cost + tight credit + slow review" draw that moves the relevant ledger inputs, and the baseline, together.

**Why.** The benchmark places the baseline at one statewide housing-cycle position per draw (every county weak or strong together) and draws every ledger input on its own. Both are simplifications. A reader should see whether the ranges and the order of the reforms survive when they are relaxed.

Command: `visionhousing uncertainty-structure --state PA` (all scenarios, the five benchmark reforms) or `--baseline-only` (conservation and the regional baseline). Settings: `assumptions/uncertainty_structure.yaml`. Results: `assumptions/calibration/uncertainty_structure.yaml` (one entry per state; other states are kept when one is rerun).

## Terms

- **Macro shock.** The benchmark's statewide position in the baseline's range for one draw. Unchanged in every scenario.
- **Regional residual.** One multiplier per county group (the baseline's place type: the pack's "places like this one" typology where it has one, else the size class) per draw, on top of the macro shock.
- **Common factor.** One standard-normal number per draw that several inputs follow. Positive means costly, tight credit and slow review.
- **Adverse corner.** The draws whose common factor is in its top fifth (`correlated.adverse_quantile`).
- **Reconciled.** County multipliers rescaled in each draw so the state total equals the benchmark's.

## How the county draws meet the state range

The state's range is calibrated on the state's own past totals, separately from the county ranges. The engine reconciles them like this, in every draw *d*:

```
county c baseline (d) = county c P50 x state multiplier (d)
state baseline (d)    = sum over counties of county c baseline (d)      (exactly)
state multiplier (d)  = from the draw's position u (d) in the state range: straight lines
                        through (0.1, state P10/P50), (0.5, 1), (0.9, state P90/P50)
```

Since the state P50 is the sum of the county P50s, the state draw is exactly the sum of the county draws, and the state's P10 / P50 / P90 are read off those sums. No percentile is ever added. The cost: inside the draws, each county swings by the state's proportion. Its own record's range (from its pool's past errors) is usually wider. `county_reconciliation` reports the state's spread ratios and, across counties, the ratio of each county record's width to the width of its draws (`county_reconciliation` in the results). The county records stay what they are: marginal ranges for one county read alone.

## Regional residuals (`regional` and `regional_unreconciled`)

The spread of each group is measured, not assumed. With the shipped rule's back-test cases, for each past origin:

```
residual (g, origin) = log(group actual / group projected) - log(state actual / state projected)
sigma_g              = standard deviation of residual (g, .) over origins (ddof 1)
```

with counties summed inside the group and the state. Groups with fewer than `regional.min_origins` usable origins take the pooled standard deviation. In each draw a group's multiplier is `exp(sigma_g z - sigma_g^2 / 2)` (mean one), `z` from the group's own seeded stream.

- **`regional` (shown).** The multipliers are rescaled in each draw so that the P50-weighted mean is one: the state total is the benchmark's in every draw, and counties trade places inside it. Reforms that work county by county on baseline production (fee cut, parking) see the new county mix.
- **`regional_unreconciled` (diagnostic).** No rescaling. The state range was calibrated on state totals, so it already holds these regional swings; stacking them on top counts them twice. It is reported to show the size of that double count, not as a result.

## The correlated scenario (`correlated`) and its control

For each listed ledger entry (`correlated.components`: high cost, tight credit, slow review), with sign *s* (+1: higher is adverse, -1: lower is adverse) and the loading *rho*:

```
latent (d) = s x rho x F (d) + sqrt(1 - rho^2) x e (d)
```

`F` is the common factor and `e` the entry's own standard normal, both from seeded streams. The entry's **own draws** (same range, shape and values as the benchmark) are sorted and placed in the order of the latent. So its distribution is exactly the benchmark's; only which draw gets which value changes, and the listed inputs move together (pairwise rank correlation about *rho* squared). The baseline's position is reordered in the same way with `baseline_sign` = -1, so an adverse draw is also a weak-cycle draw. Both arms of every paired difference (baseline and reform) read the same draws, so the link holds on both sides.

Entries fixed in the ledger (today every `start_delay_years` is fixed at zero) cannot move; `moved_inputs` in the results says so. The slow-review component therefore acts through the adoption ramps and the fee module's delayed-capacity share.

The loading (`correlated.loading`) is a team judgement: no source gives the joint distribution of these inputs. The `control` scenario uses the same machinery with loading zero; it should differ from the benchmark by sampling noise only.

## What the results hold

For each state and scenario: the conservation table (largest per-draw break, in units, by quantity) and whether it holds; P10 / P50 / P90 records of the baseline, each reform, the package and the total, each read from the scenario's own draws with its formula and ledger ids; residual diagnostics for three draws (those whose benchmark state baseline is nearest its P10, P50 and P90: state, sum of counties, the difference, the years, the five-year total minus the years, and the baseline by county group); group ranges. With reforms: the order of the reforms by P50, the share of draws in which each adds the most, the pairwise shares, and against the benchmark the change in each P50, the ratio of widths and whether the order is the same. For `correlated` and `control`: the adverse corner's ranges and ranking. Shares carry a Monte Carlo standard error (`mc_se_at_half`, the largest it can be).

## Hooks

Two context hooks, active only inside `run_scenario`:

- `ledger.sampling.joint_ranks(seed, n, ranks)`: inside it, `sample()` places the named entries' draws in the given order (same seed and draw count only).
- `engine.montecarlo.baseline_scale_hook(hook)`: inside it, `baseline_scale` asks the hook for the county multipliers.

Outside them nothing changes: the benchmark scenario equals a plain run draw for draw (tested).

## How it is tested

`tests/test_uncertainty_structure.py`: `sample()` unchanged outside the hook; the hook reorders without changing values and leaves other entries, seeds and draw counts alone; the scale hook is scoped and shape-checked; in every scenario on Testland the state equals its counties and the years add to the five-year total for every quantity; county draws equal P50 x the state multiplier; the benchmark scenario equals a plain run; reconciling keeps the weighted total; `regional` keeps the state draws and moves the counties, `regional_unreconciled` moves the state; factor ranks follow their signs and vanish at loading zero; the correlated run keeps every marginal and links the factor to the inputs, the baseline and the fee reform; the report's diagnostic draws reconcile; the saved results hold for PA, VT, MA and WY.

## Limits

- The loading is a judgement, and one value is shown. A different loading gives a different width.
- Regional spreads come from overlapping past windows of about one housing cycle, so they are rough.
- The groups are today's place types.
- The scenarios change the dependence between inputs, not their ranges: "high cost" here means the costly draws coincide, not that costs are higher than the ledger says.
