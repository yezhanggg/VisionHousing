# Result records

Code: `src/visionhousing/engine/records.py`.

## What it is

Every number the engine gives back is a small record:

| Field | Meaning |
|---|---|
| `name`, `unit` | What the number is |
| `value` | A single number, or |
| `p10`, `p50`, `p90` | A likely range from the Monte Carlo draws |
| `formula` | How it was worked out, in words and numbers |
| `inputs` | The inputs as they were used |
| `assumption_ids` | Ledger entries behind it |
| `source_ids` | Sources behind it |
| `tier` | Data tier 0, 1 or 2 (none for a pure assumption) |

A record has either a value or a full range, never both. In a range, P10 ≤ P50 ≤ P90.

## Why the formula cannot drift

The helpers `mul`, `div`, `add`, `sub` and `product` do the arithmetic and write the formula from it. For example, the benchmark fee saving:

```
Development fees per unit, all major categories ($50,000/unit) × Cut applied to every fee category (0.5) = $25,000/unit
```

The text and the number come from the same step, so they always agree. The result also takes the assumption ids and source ids of both inputs. Its tier is the lowest tier among its inputs, because a number is only as detailed as its coarsest input.

`from_parameter` turns a ledger entry into a record. `from_draws` sums up Monte Carlo draws as P10 / P50 / P90 and says how many draws it used.

## JSON

`record.to_json()` and `Record.from_json(text)` turn a record into JSON and back with nothing lost. The interface, exports and documents read the same JSON.

## Limits

The arithmetic helpers work on single values only. For ranges, the engine works out each draw and then sums up the draws with `from_draws`. Percentiles of a product are not the product of percentiles.
