# Method: development-fee reduction (benchmark 5)

## The question

How many more homes would be permitted in 2027–2031 if every development fee were
cut by half? The Handbook fixes the benchmark: fees total $50,000 per unit across all
major categories, and the reform cuts them by 50%, so the saving is $25,000 per unit
(decision D-09).

## Why a cost channel

A fee cut changes no zoning. It lowers the cost of homes that are already legal. The
model therefore uses its cost channel (Channel B) rather than a site-by-site funnel:

```
added units = baseline units × ε × (ΔC ÷ V) × k
```

ΔC ÷ V is the saving as a share of what a new unit is worth. ε turns a 1% better
margin into a percent change in permits. k is the part of the saving that reaches
the decision to build; the rest is captured by landowners as higher land prices. The
formula is applied to every county and structure type of the baseline, in every
Monte Carlo draw.

## The supply elasticity ε

`supply.elasticity_medium_run` (central 1.0, range 0.3–2.0, draft judgement). Two
published studies bracket it; both are recorded in the ledger as evidence entries,
not as inputs:

| Evidence | Value | Ledger id |
|---|---|---|
| Saiz (2010), Quarterly Journal of Economics 125(3), Table VI: Philadelphia | 1.65 | `supply.evidence_saiz_philadelphia` |
| Saiz (2010), Table VI: Pittsburgh | 1.20 | `supply.evidence_saiz_pittsburgh` |
| Baum-Snow and Han (2024), Journal of Political Economy 132(6): average tract unit supply elasticity | 0.3 | `supply.evidence_tract_unit_elasticity` |

Saiz measures long-run metro supply of the housing stock; Pennsylvania metros run
from about 1.2 (Pittsburgh) to 1.9 (Allentown). Baum-Snow and Han measure single
neighbourhoods, which respond less because demand spills to neighbours. A statewide,
five-year response to a cost cut sits between the two, which is why the central value
is 1.0 and the range runs from the tract figure to above the metro figures. Neither
study measures permits responding to cost, so ε stays a draft until the cross-check
job (T6) is run.

## The pass-through share k

`fees.passthrough_k` (central 0.5, range 0.05–1.0). Evidence on who bears fees points
both ways: some studies find fees are passed back to landowners, others find home
prices rise by the full fee or more. The range is therefore the widest of the fee
inputs, and k leads the fee tornado. The parking reform uses the general
`supply.saving_passthrough_k` instead, because parking savings come with land freed
and are less likely to be bid away in full.

## Value of a new unit V

County medians are read from the ACS bracket counts summed over the county's tracts.
1-unit homes use the median home value; rented building types use the median gross
rent capitalised at the ledger's cap rate after running costs. Both get a
new-construction premium (`supply.new_construction_value_premium`, central 0.20).

## Pro-forma cross-check

The same saving, times the same k, is run through the development pro formas. For
each county and structure type the pro forma gives a break-even rent (or sale value
for 1-unit homes) covering construction, soft costs, fees, parking, the builder's
return and land (`supply.crosscheck_land_share` of development cost). The share of
the county's existing rents or values above the break-even, divided by
(1 + premium), is the share of sites where a project pencils. The open top bracket is
spread as a Pareto tail (`supply.crosscheck_pareto_alpha`), because new-building
break-evens often sit inside it. The saving lowers the break-even; the relative rise
in the feasible share, times baseline units, is the cross-check estimate.

The result reports the cross-check next to Channel B, with the percentage points of
sites that become feasible. The two are never added. When they disagree, the gap
itself is the finding: it shows how much rests on ε versus on the shape of the local
rent and value distribution.

## Second view: state fee levels

`view="state"` replaces $50,000 with `fees.pa_documented_fee_per_unit` (central
$15,000, range $5,000–$40,000). **This is still a draft judgement.** Per-unit impact,
tap and permit fees for typical new homes in Pennsylvania have not yet been sourced
(backlog P4-05). The result's notes say so. In this view the fee level itself, not k,
is the widest input, because its range is so wide.

## What this cannot tell you

- Whether municipalities would replace fee revenue with other charges.
- Whether infrastructure funded by fees is delayed, which could slow building.
- Effects after 2031, or which households live in the added homes.
- How the saving is split between landowners, builders and buyers in any one place.
