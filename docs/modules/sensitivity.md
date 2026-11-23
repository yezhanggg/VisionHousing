# Module: Sensitivity (`src/visionhousing/sensitivity/`)

**What it does.** Says which assumptions move a reform's answer most, two ways, and lists the top five in plain words.

**Why.** Every reform page must show what drives the result and how sensitive it is to key inputs. Two methods that agree make the ranking trustworthy.

## Terms

- **Tornado.** Pin every uncertain ledger input at its central value. Then move one input to its low value, then to its high value, and record the state total each time. The **swing** is the distance between the two totals. Inputs are sorted by swing, largest first. Fixed inputs (no range) cannot swing and are left out.
- **Rank correlation (Spearman's rho).** Across the Monte Carlo draws, rank the draws by an input and by the answer, and take the correlation of the two lists of ranks. It runs from −1 to +1 and catches any steady up-or-down link, straight or curved. It checks the tornado's order when every input varies at once.
- **Baseline housing cycle.** Fees and parking act on baseline production, so the baseline's own range is one more input: its row in the tornado swings the baseline from its P10 to its P90; its rank correlation uses the baseline's position in each draw.

## Files and functions

| Function | What it does |
|---|---|
| `tornado(evaluate, ledger, ids, extra=())` | Pure. One-at-a-time swings for any function from a ledger to a number |
| `pin_central(ledger)` | Every entry fixed at its central value |
| `importance(inputs, output, labels)` | Spearman rho of each input with the output, largest first |
| `spearman(x, y)`, `rankdata(x)` | numpy only; ties share the mean rank |
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

## How it is tested

`tests/test_sensitivity.py`: ranks and rho by hand; importance drops constant inputs; the tornado on a toy ledger holds the others at central; the engine tornado equals the fee module's table; the top five have plain ledger labels and are ranked by swing; the tornado's top five is identical for seeds 0, 1 and 2 on Testland; the importance top five agrees across seeds in at least four of five places; the tornado's leader is in the importance top three; ADUs do not depend on the baseline cycle.
