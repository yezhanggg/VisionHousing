# Method: development-fee reduction (benchmark 5)

## The question

How many more homes would be permitted in 2027–2031 if every development fee were
cut by half? The Handbook fixes the benchmark: fees total $50,000 per unit across all
major categories, and the reform cuts them by 50%, so the saving is $25,000 per unit.

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
study measures permits responding to cost, so ε stays a draft until the planned elasticity
cross-check (literature values against a long-difference estimate from
permits and prices) is run.

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

`view="state"` replaces $50,000 with `fees.pa_assumed_fee_per_unit_judgement` (central
$15,000, range $5,000–$40,000). **This is an assumed figure, a team judgement, not a
surveyed statewide fee.** No reproducible inventory of Pennsylvania impact, tap and
permit fee schedules stands behind it yet, and the id says so (it was called
`fees.pa_documented_fee_per_unit` before 2026-11-25; the old name overstated it).
The result's notes say so. In this view the fee level itself, not k,
is the widest input, because its range is so wide.

## Fiscal screen: who gives up what

A fee cut applies to every permitted home, not only the extra ones. Every
fee-cut run splits the money, in each draw (readouts `fiscal_*`):

| Line | Formula |
|---|---|
| Waived on homes that would be built anyway | cut per unit × baseline units |
| Waived on the added homes | cut per unit × added units |
| (these two add up to the revenue given up) | |
| Fees still paid by the added homes (new revenue) | (fees per unit − cut per unit) × added units |
| Replacement funding | `fees.replacement_funding_share` × revenue given up (none by default) |
| Net change in fee revenue | − waived on homes built anyway + paid by added homes + replacement |

The added homes would not exist without the reform, so the fees they still pay
are new revenue, and the part waived on them was never collected before. With
the benchmark's $50,000 cut by half, each added home brings $25,000 of new
revenue, while every baseline home costs $25,000; the net change is negative
unless the added homes are more than the baseline homes. The replacement share
is fixed at zero (the benchmark names no backfill); a user can set it.

### The fiscal identity, per paired draw

Let `B` be the baseline units that pay the fee (the homes built anyway), `A`
the added units, `F` the fee before the cut and `F'` the fee after. With the
same collection timing and no backfill:

    revenue before = F × B
    revenue after  = F' × (B + A)
    revenue change = (F' − F) × B + F' × A

Records `fiscal_revenue_before`, `fiscal_revenue_after` and
`fiscal_revenue_change` hold these, in each draw. A worked check (tested):
B = 100, A = 5, F = $50,000, F' = $25,000 gives $5.0 million before, $2.625
million after, a $2.375 million decline, or **$475,000 per added home**
(`fiscal_net_loss_per_added_home` = − revenue change / A).

That per-home figure exists only in draws where the cut adds homes. So it is
always read beside **`fiscal_zero_gain_share`**: the share of the modeled draws
in which the cut adds no homes, with its Monte Carlo standard error. In those
draws the revenue is still lost on the baseline homes. When builders get none
of the saving (incidence zero, `fees.passthrough_k` = 0), the cut adds no
permits, the zero-gain share is 100%, no cost per added home is printed, and
the revenue lost on baseline homes is still shown (tested). The gross figure,
revenue given up / added homes (`cost_per_added_home`), is kept beside it; it
counts the fees the added homes still pay as lost, so it is higher.

The fees are not split by recipient (municipality, school district, water or
sewer authority) or by year: the benchmark's $50,000 is one total and no pack
holds who collects each part. The readout texts `fiscal_by_recipient` and
`fiscal_by_year` say so.

## Named fiscal cases

| Variant | What changes | What to read |
|---|---|---|
| `fees_funded_backfill` | `fees.replacement_funding_share` takes the range of `fees.funded_backfill_share` (1): a state appropriation replaces every dollar given up | `fiscal_replacement` is the state outlay; `fiscal_net_change` is what the places that charge fees keep (F × A); added homes are unchanged |
| `fees_delayed_capacity` | The water and sewer part of the fees (`fees.utility_earmark_share`, 30%, 10–50%) is cut and not replaced, so capacity comes later; of the added homes that need it, `fees.delayed_capacity_share` (50%, 20–80%) are not permitted inside 2027–2031 | added homes × (1 − earmarked share × delayed share); record `delayed_by_capacity` |
| `fees_baseline_window_pa_2022_2025` | Pennsylvania only: each county's baseline is scaled to the 2022–2025 window diagnostic of the baseline card (record `baseline_window_factor`); the fiscal B is scaled the same way | A sensitivity shown beside the headline; never subtracted |

All three are named options. The benchmark preset is unchanged.

## Documented local fee schedules (variant `fees_documented_local`)

The benchmark's $50,000 is a fixed competition input. It is never overwritten.
To show what fees actually are, a separate named variant starts from each
city's own published schedules.

**What was read.** Every amount is copied, with a short quote and the date
read, into `assumptions/fee_schedules.yaml`. The documents were fetched by
script after checking each site's robots.txt and terms in a separate request:

| City | Documents read | What was refused or not reviewed |
|---|---|---|
| Philadelphia | L&I Construction Permit Fees (PG_012_INF, Rev 7.2026, effective October 1, 2026); L&I Zoning & Use Permit Fees (PZ_008_INF, Rev 7.2026); the City's building permit page (filing fee, surcharges, record fee); Department of Revenue Development Impact Tax page; Water Department Rates and Charges (effective September 1, 2026); Water Department Regulations Chapter 5 (§504.14 sewer connection fees) | Streets Department street-opening permits (not reviewed; the total is a floor for them) |
| Pittsburgh | The City's 2026 fee schedule resolution (Legistar file 2025-2463, effective January 1, 2026: its text only); the water and sewer authority's Permits page (residential permit fee $40; typical single-family tap and meter $570) | The fee book itself (building, trade and zoning fees) is published only as an attachment on a server whose robots.txt disallows all robots, and the City's own site answers scripted requests with HTTP 403; the authority's fee schedule PDF is disallowed by its robots.txt; the regional sewage tap-in fee is outside the allowed sources |

**How a prototype is priced.** Each fee item records its category, its basis
(flat, per sq ft step, per fixture, per register or sprinkler head, per $1,000
of work, or a percent of the City's table value), its exemptions, when it is
paid, its dollars year, who collects it and the service it pays for. Five
standard prototypes supply the quantities: a 600 sq ft ADU (the benchmark
minimum), a 1,600 sq ft single-family home, a duplex, a fourplex and a 20-unit
apartment building. Where a reading could go two ways, both are kept: an ADU
filed as a new accessory structure (and sharing the house's water service) or
as a new dwelling (with its own tap, meter and sewer connection); a 20-unit
building with a combined 2-inch service or a separate 4-inch fire service.
In each draw the fee is the low total plus `fees.local_schedule_position`
(0–1) times the gap to the high total. The electrical permit ($25 per $1,000
of electrical work) uses the ledger's construction cost per square foot times
`fees.local_electrical_cost_share`, so the range also carries cost
uncertainty.

Three counting rules:

- **An application fee credited to the final permit is counted once.**
  Philadelphia's filing fee ($25 for one- or two-family, $100 otherwise) "is
  applied towards the final permit fee"; it adds only what exceeds that
  permit fee, which is nothing for every prototype (tested).
- **"Not listed" is unknown, never zero.** An item stays unknown until a
  person has reviewed the document that would list it. A city is used in the
  variant only when every core category (zoning, building, trade permits,
  impact charges, water connection, sewer connection) is documented or
  reviewed and inapplicable, and only when the city is a whole county (the fee
  reform works county by county). Philadelphia meets both. Pittsburgh meets
  neither: its building, trade and zoning fees are unknown, and it is only
  part of Allegheny County. Allegheny and every other county stay on the
  labelled judgement `fees.pa_assumed_fee_per_unit_judgement`.
- **Tap and connection charges are explicit and counted once.** Water taps,
  meters and sewer connection permits are their own categories. In the ADU
  pro forma they correspond to `adu.utility_connection_fee`; digging and
  laying the laterals is site work, `adu.site_utility_cost`, and is never a
  fee. A package fee cut scales `adu.permit_fee` and
  `adu.utility_connection_fee` once and never the site work; this variant
  changes only the fee reform's saving, not the ADU pro forma.

Philadelphia's Development Impact Tax (1% of the City's computed
construction cost, half at permit and half at final inspection) is a tax
charged with the building permit, so it is counted, with its exemptions
(certified affordable housing, Keystone Opportunity Zones, H.O.M.E. plan).
Electric and gas company service charges are not government fees and are not
counted. Amounts are in the dollars of the schedule in force when read.

**Documented fees per home, by prototype** (written by
`scripts/gen_docs_tables.py` from the schedule and the ledger; low = every item
at its low reading with low ledger costs, high = high readings and high costs):

<!-- generated: local_fee_prototypes -->
| City | Prototype | Documented fees per home | Central / $50,000 | In variant |
|---|---|---|---|---|
| City of Philadelphia | Detached ADU on a single-family lot, 600 sq ft (the benchmark minimum) | $1,301 - $5,207 (central $3,129) | 6% | yes |
| City of Philadelphia | New single-family home, 1,600 sq ft | $5,319 - $6,892 (central $5,918) | 12% | yes |
| City of Philadelphia | New two-family building, 2 units x 1,000 sq ft | $3,160 - $4,306 (central $3,621) | 7% | yes |
| City of Philadelphia | New four-unit building, 4 units x 1,000 sq ft, sprinklered (13R) | $3,507 - $5,201 (central $4,241) | 8% | yes |
| City of Philadelphia | New 20-unit apartment building, 20 units x 850 sq ft + 15% common area, sprinklered | $3,190 - $5,268 (central $4,303) | 9% | yes |
| City of Pittsburgh | Detached ADU on a single-family lot, 600 sq ft (the benchmark minimum) | unknown; documented part only: $0 - $610 | n/a | no |
| City of Pittsburgh | New single-family home, 1,600 sq ft | unknown; documented part only: $610 - $610 | n/a | no |
| City of Pittsburgh | New two-family building, 2 units x 1,000 sq ft | unknown (no documented item) | n/a | no |
| City of Pittsburgh | New four-unit building, 4 units x 1,000 sq ft, sprinklered (13R) | unknown (no documented item) | n/a | no |
| City of Pittsburgh | New 20-unit apartment building, 20 units x 850 sq ft + 15% common area, sprinklered | unknown (no documented item) | n/a | no |

| Prototype (Philadelphia, central) | building | zoning | trade | impact | water_connection | sewer_connection | stormwater | administrative |
|---|---|---|---|---|---|---|---|---|
| Detached ADU on a single-family lot, 600 sq ft (the benchmark minimum) | $730 | $284 | $858 | $552 | $485 | $188 | $0 | $34 |
| New single-family home, 1,600 sq ft | $1,328 | $189 | $1,524 | $1,471 | $988 | $375 | $0 | $42 |
| New two-family building, 2 units x 1,000 sq ft | $664 | $142 | $937 | $920 | $735 | $200 | $0 | $23 |
| New four-unit building, 4 units x 1,000 sq ft, sprinklered (13R) | $702 | $205 | $1,110 | $1,162 | $544 | $94 | $407 | $17 |
| New 20-unit apartment building, 20 units x 850 sq ft + 15% common area, sprinklered | $724 | $41 | $1,354 | $1,245 | $639 | $26 | $267 | $7 |
<!-- /generated: local_fee_prototypes -->

Documented Philadelphia fees are a small fraction of the benchmark's $50,000.
The benchmark is a competition input and stays as it is; the variant is the
"current documented fees" counterpart.

**The variant.** `fees_documented_local` (`view: local`) runs the same
Channel B formula and the same 50% cut from these fees: in Philadelphia the
fee per unit is the documented prototype fee of each structure type
(1-unit: single-family home; 2-unit: duplex; 3–4 units: fourplex; 5+ units:
20-unit building); everywhere else the labelled judgement. It writes the same
fiscal identity records as the benchmark (`fiscal_revenue_before/after/change`,
`fiscal_net_loss_per_added_home`, `fiscal_zero_gain_share`), with each county's
fee per baseline home weighted by its baseline mix of building types and per
added home by the added mix. Records `local_fee_philadelphia_units_*` and
`local_fee_state_average` hold the fees used. The pro-forma cross-check keeps
the state judgement for its fee line (noted in the result).

## Variant: fee deferral instead of a cut

**Variant `fees_deferral`** (`view: deferral`) keeps the $50,000 but collects
it at occupancy instead of at permit. The builder no longer carries the fees
through construction:

```
saving per unit = fees per unit × construction-loan rate × deferral months / 12
```

(`fees.construction_loan_rate` 8.5%, 7–10%; `fees.deferral_months` 12, 6–24),
about $4,000 per home at central values. It enters the same cost channel and
cross-check as the cut. The public does not give up the fee, only its time
value and a small collection loss (readouts `deferral_*`):

```
public cost = fees deferred × (1 − 1 / (1 + public discount rate)^(months / 12))
              + fees deferred × share never collected
net change  = fees paid by the added homes − public cost
```

(`fees.public_discount_rate` 4%, `fees.deferral_collection_loss_share` 1%). The
deferral adds fewer homes than the cut, at a far smaller public cost.

## What this cannot tell you

- Whether municipalities would replace fee revenue with other charges.
- Whether infrastructure funded by fees is delayed, which could slow building,
  except as the labelled `fees_delayed_capacity` case with judgement ranges.
- A literature price elasticity is not an elasticity of permits with respect to
  fees; the cost channel uses it as an approximation and says so.
- Effects after 2031, or which households live in the added homes.
- How the saving is split between landowners, builders and buyers in any one place.
- What fees are outside Philadelphia. Pittsburgh's fee book could not be read
  by script, and no reproducible inventory of other Pennsylvania schedules
  exists yet, so the documented local variant keeps the labelled judgement
  everywhere else.
