# Brackets: shares above a break-even from ACS bracket counts

Code: `src/visionhousing/features/brackets.py`. Tests: `tests/test_brackets.py`.

## What it does

The model asks: what share of a tract's homes are worth more than a break-even
price, or what share of its rents are above a break-even rent? The answer is a
share, not a yes or no, so a tract can be "38% feasible".

The ACS does not publish single home values or rents. It publishes **bracket
counts**: how many homes fall in "$100,000 to $124,999", how many renters pay
"$800 to $899", and so on. This module turns those counts into a share.

## Tables

| Table | What | Brackets | Columns |
|---|---|---|---|
| B25075 | Value of owner-occupied homes | 26, from "Less than $10,000" to "$2,000,000 or more" | `value_lt_10000` … `value_2000000_plus` |
| B25063 | Gross rent of renter-occupied units | 24 cash brackets, from "Less than $100" to "$3,500 or more", plus "No cash rent" | `rent_lt_100` … `rent_3500_plus`, `rent_no_cash` |

Each bracket is a half-open interval `[low, high)`. `rent_800_899` is
`[800, 900)`. The top bracket is open: `value_2000000_plus` is `[2000000, ∞)`.
`parse_bracket(name)` turns a column name into its edges.

## The method

1. **Inside a bracket, units are spread evenly.** If the threshold sits 40% of
   the way through a bracket, 60% of that bracket counts as above it. Brackets
   wholly above count in full; brackets wholly below count as zero.

   `share above t = Σ count_j × frac_j(t) / Σ count_j`

2. **The open top bracket** has no upper edge, so it cannot be split by a
   straight line. The rule is a parameter, `top_rule`:

   | Rule | What it assumes | Threshold inside the top bracket gives |
   |---|---|---|
   | `lower_bound` (default) | Every top-bracket unit sits exactly at the bracket's lower edge | none of the top bracket above; the share above is a **lower bound** (share below an **upper bound**) and the result says so in `bound` |
   | `pareto` | A Pareto tail with shape `alpha` | `(low / t) ** alpha` of the top bracket above; an estimate, not a bound |

   Why `lower_bound` is the default: it needs no extra assumption, and it errs
   on the cautious side for feasibility (it never overstates how many tracts
   pencil out). The plan sets no Pareto `alpha`; if one is used it must come
   from the Assumption Ledger and be passed in. In practice break-evens rarely
   reach $2,000,000 home values or $3,500 rents, so the rule seldom matters.
   Every result carries `in_top_bracket` so a caller can see when it does.

3. **No cash rent is left out.** Renters who pay no cash rent have no rent to
   compare with a break-even. A rent share is a share of **cash renters**.
   `counts_from_row` never reads `rent_no_cash`.

4. **Empty tracts** (zero total units in the brackets) give `NaN` and a
   `reason`. Missing counts and a missing threshold do the same. Bad inputs from
   a programmer (misaligned arrays, negative counts, unknown rule) raise an error.

## Functions

| Function | Returns |
|---|---|
| `share_above(counts, edges, threshold, top_rule=..., alpha=...)` | `ShareResult`: `value`, `in_top_bracket`, `bound`, `rule`, `total`, `reason` |
| `share_below(...)` | the same, `1 − share_above`, with the bound flipped |
| `share_above_many(counts_2d, edges, thresholds)` / `share_below_many` | `ShareArrays`, one entry per tract; equal to a loop of the scalar version |
| `share_above_grid(counts_2d, edges, thresholds_grid)` / `share_below_grid` | the share for a whole grid of thresholds (draws × tracts) at once, as an array (`NaN` where undefined). A threshold falls in one bracket `j`: share above = (units in brackets above `j` + count_j × frac_j) / total, with the running sum worked out once per tract, so the counts are never copied per draw. Equal to `share_above_many` within a relative 1e-12 (only where one fractional term is added differs) |
| `median_from_brackets(counts, edges)` | `MedianResult`: the grouped-data median, the bracket that holds it, and `bound="lower"` if it falls in the top bracket |
| `check_labels(labels, table, vintage)` | a list of problems; empty means the ACS variable numbering matches these edges |
| `VALUE.variables()`, `RENT.variables()` | ACS variable id → column name, for the fetcher |

`edges` can be `VALUE`, `RENT`, or any `(k, 2)` array of contiguous
`[low, high)` pairs with only the last one open.

## Checking the labels

The Census Bureau has changed these brackets before (the top value bracket was
once "$1,000,000 or more"). Before trusting the numbering, a fetcher downloads
the variable labels for the vintage and passes them to `check_labels`. It
compares the last part of each label (after the final `!!`) with the expected
wording. It works offline because the labels are passed in. Vintages before
2015 are refused because their edges differ.

## How to check it

- `uv run pytest tests/test_brackets.py`
- Known answers: a uniform spread over `[0, 40)` gives a share above 15 of 0.625.
- On every Testland tract, the median from brackets lands in the same bracket as
  the published median and within 2% of it.

## What it cannot tell you

The even spread inside a bracket is an approximation. Real values bunch toward
the lower end of wide brackets, so shares near the top of a wide bracket can be
slightly off. ACS counts are survey estimates with margins of error; this module
uses the estimates only.
