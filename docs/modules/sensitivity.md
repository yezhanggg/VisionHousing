# Module: Sensitivity (`src/visionhousing/sensitivity/`)

**What it does.** Says which assumptions move a reform's answer most, two ways, and lists the top five in plain words.

**Why.** Every reform page must show what drives the result and how sensitive it is to key inputs. Two methods that agree make the ranking trustworthy.

## Terms

- **Tornado.** Pin every uncertain ledger input at its central value. Then move one input to its low value, then to its high value, and record the state total each time. The **swing** is the distance between the two totals. Inputs are sorted by swing, largest first. Fixed inputs (no range) cannot swing and are left out.
- **Rank correlation (Spearman's rho).** Across the Monte Carlo draws, rank the draws by an input and by the answer, and take the correlation of the two lists of ranks. It runs from −1 to +1 and catches any steady up-or-down link, straight or curved. It checks the tornado's order when every input varies at once.
- **Baseline housing cycle.** Fees and parking act on baseline production, so the baseline's own range is one more input: its row in the tornado swings the baseline from its P10 to its P90; its rank correlation uses the baseline's position in each draw.

- **First-order variance share.** How much of the spread of the answer comes from one input: the part that would go away if that input were known exactly. Sort the draws by the input, cut them into equal-count bins (`sensitivity.variance_bins`, ten), and compute 1 − (average variance of the answer inside a bin ÷ total variance). It needs no new run and no model fit.
- **Switch run.** The reform run again with one input **fixed** at its ledger low, central or high value, every other input drawn exactly as in the run. Each ledger input has its own seeded random stream, so the other inputs keep the same values draw by draw (**paired draws**). The answer is a likely range (P10 / P50 / P90), for the state and each county, "if we knew this one input".
- **Range shrink.** The likely range scales with the standard deviation, so knowing an input with share *s* would shrink it by about 1 − √(1 − *s*).
- **Interactions and noise.** One minus the sum of the shares (at least zero): the spread from inputs acting together, and the estimate's own error. An input that does not matter scores a little above zero (about bins ÷ draws).

## Files and functions

| Function | What it does |
|---|---|
| `tornado(evaluate, ledger, ids, extra=())` | Pure. One-at-a-time swings for any function from a ledger to a number |
| `pin_central(ledger)` | Every entry fixed at its central value |
| `importance(inputs, output, labels)` | Spearman rho of each input with the output, largest first |
| `spearman(x, y)`, `rankdata(x)` | numpy only; ties share the mean rank |
| `first_order_share(x, y, bins)`, `first_order_shares(inputs, y, bins)` | `variance.py`. The share for one input, or every input (largest first) |
| `range_shrink(share)` | 1 − √(1 − share) |
| `narrowing(inputs, output, labels, ...)` | Shares as records (formula, ids), with the shrink and the leftover |
| `uncertainty(result, reform)` | "What would narrow the range" for one reform of an engine result, from exactly the draws the reform used (`input_draws`) |
| `switch_runs(result, reform, top=3)` | `switch_runs.py`. For the reform's top three tornado inputs, nine switch runs (three inputs, each fixed at low, central and high): `{input: {setting: {"state": record, "counties": {id: record}}}}`. Kept in memory and, for the default scenario, on disk beside the comparisons parts |
| `cached_switch_runs(result, reform)` | The switch runs if already worked out (memory, then disk); never computes |
| `switch_payload(state, reform)` | `switch_runs.py`. The switch runs of one reform alone as JSON, for the static file `<ST>/switch-ranges/<reform>.json` |
| `drivers(result, reform, top=5)` | Both tables for one reform of an engine result, and the top five as `Driver` objects (rank, id, plain label, swing, totals at low and high, rho, `sentence()`) |

## Example

```python
from visionhousing.engine import run
from visionhousing.sensitivity import drivers

result = run("TL", ["fees"], seed=0)
for d in drivers(result, "fees").top:
    print(d.rank, d.label, round(d.swing), round(d.rho, 2))
```

## Notes

- The tornado uses no random numbers, so it is the same for every seed.
- The input draws used for rank correlation are exactly those the reform used: its own ledger sampled with the run's seed.
- For the fee reform, the engine's tornado reproduces the fee module's own swing table exactly (tested).

**Switch runs, how.** The tornado gives one pinned total per setting. A switch run keeps the rest of the uncertainty. For input *x* at value *v*: take the ledger the reform used, set *x* to `{"central": v, "low": v, "high": v, "shape": "fixed"}`, and run the reform's adapter with the run's seed, draws and baseline positions. If *x* is the baseline's position in its range, every draw instead uses the baseline multiplier at that end (P10 ÷ P50, 1, or P90 ÷ P50) and the ledger is unchanged. County draws are kept; each record's formula names the fixed input, its value, the seed and the draws. Three inputs × two extra settings is six extra reform runs per reform (nine adapter calls with central). `visionhousing switch-ranges --state PA` works them out for the five benchmark reforms and keeps each on disk, so `GET /api/v1/switch/{st}` adds the ranges. Fixing an input removes its spread, so the central setting's range is close to the run's and a little narrower; the fixed central value is the ledger's central, so a skewed input can move the middle a little.

A permutation check (shuffle one input and rerun) would need one rerun of the reform per input, so it is not done; the rank correlations on the same draws are the cross-check.

## How it is tested

`tests/test_readouts.py`: on a known linear model the shares match the analytic values within 0.03; an input the answer does not depend on scores near zero; edge cases (no spread gives no share); on Testland the shares are sorted, between 0 and 1, and the leftover is one minus their sum; ADUs owe almost nothing to the baseline cycle.

`tests/test_switch_runs.py`: on Testland the switch runs follow the tornado's top three; every record has a formula naming the fixed input and ledger ids; the fixed input's own draws explain no variance of the answer (first-order share below 0.02, against above 0.05 in the run); low and high are paired (the draw-by-draw gap has one sign in more than 90% of draws, the tornado's sign); fixing an input the reform never reads leaves the run unchanged; the central setting's middle is within 25% of the run's; the memory and disk caches round-trip; the grade switch and the endpoint add ranges once cached.

`tests/test_sensitivity.py`: ranks and rho by hand; importance drops constant inputs; the tornado on a toy ledger holds the others at central; the engine tornado equals the fee module's table; the top five have plain ledger labels and are ranked by swing; the tornado's top five is identical for seeds 0, 1 and 2 on Testland; the importance top five agrees across seeds in at least four of five places; the tornado's leader is in the importance top three; ADUs do not depend on the baseline cycle.
