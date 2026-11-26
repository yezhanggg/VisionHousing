# Policy Scenario Comparison

Written for readers who are not modellers (limit: 4 pages). Every number below is copied by a script from the tool's own saved runs; none is typed by hand. The method is in the [Model and Assumptions Overview](MODEL_AND_ASSUMPTIONS.md).

## What the tool does

VisionHousing estimates how many **more homes a state would likely permit over five years (2027 to 2031)** if it passed a reform, compared with passing nothing. It counts housing units authorized by building permits. It gives a likely range, not a single number: in eight of ten simulated futures the answer falls inside it. It is decision support, not a forecast.

**The baseline** is what happens under today's rules and economics. Each county's recent permits are carried forward by a simple rule that won a contest against other simple rules on past years, judged by a test written down before any score was seen. The range around it comes from how far such projections missed in the past ([method](MODEL_AND_ASSUMPTIONS.md#42-baseline)).

## The reforms compared (Pennsylvania)

<!-- generated: headline_table_pa -->
*Generated from the default runs saved in `web/public/data/PA/runs/` (seed 0, 2,000 draws). Likely range = P10 to P90; eight in ten simulated futures fall inside it. Frozen as of 2026-11-24.*

| Run | Added homes permitted, 2027-2031: likely range | Change against the baseline | Net added homes (after homes torn down) |
|---|---|---|---|
| Baseline (no reform): homes permitted | 71,363 to 192,239 (middle 151,882) | - | - |
| Accessory dwelling units | 3,481 to 15,671 (middle 8,011) | 2.4% to 14.4% (middle 5.8%) | 3,481 to 15,671 (middle 8,011) |
| Missing middle | 119 to 1,886 (middle 560) | 0.1% to 1.6% (middle 0.4%) | 88 to 1,407 (middle 415) |
| Transit-oriented density | 4 to 1,721 (middle 367) | 0.0% to 1.4% (middle 0.3%) | 4 to 1,561 (middle 348) |
| Parking requirement reduction | 1,231 to 5,049 (middle 2,633) | 1.2% to 3.2% (middle 2.0%) | 1,231 to 5,049 (middle 2,633) |
| Development fee reduction | 2,672 to 12,979 (middle 6,433) | 2.3% to 8.6% (middle 5.0%) | 2,672 to 12,979 (middle 6,433) |
| Pennsylvania HB 2186: one ADU by right on any residential lot | 3,529 to 15,934 (middle 8,124) | 2.4% to 14.7% (middle 5.9%) | 3,529 to 15,934 (middle 8,124) |
| Pennsylvania SB 1346: ADUs on single-family detached lots outside Philadelphia and Pittsburgh | 3,311 to 14,758 (middle 7,604) | 2.3% to 13.5% (middle 5.5%) | 3,311 to 14,758 (middle 7,604) |
| All five together | 12,379 to 31,313 (middle 20,468) | 9.6% to 24.9% (middle 15.2%) | - |

*The baseline row counts all homes permitted with no reform. Every other row counts the homes a reform adds to that, in the same simulated futures. Net added homes are shown where the saved run carries them (missing middle and transit-oriented density replace some existing homes); `-` means not in the saved run.*
<!-- /generated: headline_table_pa -->

**Added homes** are the extra homes permitted because of the reform, in the same simulated future as the baseline. The future level of building is uncertain; the reform's effect on it is less so. **Change against the baseline** is the same thing as a share of all homes the state would permit anyway. **Net added homes** subtract homes torn down (only missing middle and transit-oriented density replace homes).

**1. Accessory dwelling units (ADUs).** *Drives it:* how many single-family lots have room, whether a small rental pays back its cost, and how many owners choose to build. *Use it for:* comparing statewide ADU bills and seeing which counties have the most eligible lots. *Cannot tell you:* who lives in the units or what they rent for. Some ADUs are permitted as additions, so they may never show up in published permit counts.

**2. Missing middle (2 to 6 homes on a lot).** *Drives it:* whether a small apartment building is worth more than the house on the lot, and how often lots are redeveloped. Outside the counties whose zoning has been read, the share of lots zoned only for single-family homes is a judgement range, and it is in the sensitivity list. *Use it for:* the order of magnitude, statewide. *Cannot tell you:* change street by street, or effects on prices.

**3. Transit-oriented density.** *Drives it:* how much land is near frequent transit, and whether a mid-rise building pays for itself at local rents. The result is small because, at today's construction costs and the yields investors ask, a new mid-rise building pays for itself on few sites, even with more floor area and less parking; the cost and yield inputs in the table below show how fast it grows if that changes. *Use it for:* seeing whether a density bonus alone is enough. *Cannot tell you:* whether the new homes are new to the state or moved from elsewhere in the region.

**4. Parking reduction.** *Drives it:* how many spaces builders still choose to build, and what each space costs. *Use it for:* the size of the cost saving and where it lands (transit-rich tracts save most). *Cannot tell you:* effects on street parking or traffic.

**5. Development fee reduction.** *Drives it:* how much of the Challenge's set fee cut (half of $50,000 per home) reaches the decision to build, rather than raising land prices. *Use it for:* weighing homes added against revenue given up; the tool shows the public cost per added home, which is high because most of the cut goes to homes that would have been built anyway. *Cannot tell you:* how local governments would replace the lost revenue.

**Pennsylvania bills.** HB 2186 and the SB 1346 variant are ADU bills. They reach the same lots as the ADU reform, with different rules on which lots qualify, so they are compared with the ADU reform, not added to it.

**Reforms together.** Reforms do not simply add up. A lot can be used only once, and savings from one reform help the others.

<!-- generated: package_waterfall_pa -->
*Generated from `web/public/data/PA/runs/package.json`.*

| Step | Homes, 2027-2031: likely range |
|---|---|
| Each reform alone, added up | 11,993 to 30,629 (middle 19,894) |
| Minus lots counted twice (overlap) | 2 to 30 (middle 9) |
| Plus savings that help another reform (interaction) | 208 to 1,172 (middle 491) |
| Equals all five together | 12,379 to 31,313 (middle 20,468) |
<!-- /generated: package_waterfall_pa -->

## The assumptions that matter most

<!-- generated: top_drivers_pa -->
*Generated from the tornado saved with each reform's run in `web/public/data/PA/runs/`. Each input is moved alone from its low to its high value (Assumption Ledger) while the others stay central; the figures are the five-year homes the reform adds at each end.*

| Reform | Input | Added homes at its low value | At its high value |
|---|---|---|---|
| Accessory dwelling units | Share of feasible lots that add an ADU each year (`adu.takeup_feasible_annual`) | 2,595 at 0.003 | 21,629 at 0.025 |
| Accessory dwelling units | Construction cost of a new detached ADU per square foot (`adu.hard_cost_per_sqft`) | 16,807 at 175 | 4,522 at 375 |
| Missing middle | Share of feasible lots redeveloped each year (`missing_middle.redevelopment_hazard_annual`) | 97 at 0.0005 | 1,946 at 0.01 |
| Missing middle | Capitalisation rate used to value the finished building (`missing_middle.cap_rate`) | 1,484 at 0.05 | 139 at 0.075 |
| Transit-oriented density | Capitalisation rate used to value a finished mid-rise building (`multifamily.cap_rate`) | 1,619 at 0.045 | 1 at 0.07 |
| Transit-oriented density | Construction cost per square foot, mid-rise apartment building (`multifamily.hard_cost_per_sqft`) | 1,428 at 230 | 4 at 400 |
| Parking requirement reduction | Medium-run supply response to a change in profit per unit (`supply.elasticity_medium_run`) | 1,424 at 0.3 | 4,353 at 2 |
| Parking requirement reduction | Extra units a small site can hold when parking is not required (`parking.site_yield_gain`) | 1,723 at 0 | 4,445 at 0.15 |
| Development fee reduction | Share of a fee cut that reaches the decision to build (`fees.passthrough_k`) | 736 at 0.05 | 14,720 at 1 |
| Development fee reduction | Medium-run supply response to a change in profit per unit (`supply.elasticity_medium_run`) | 2,208 at 0.3 | 14,720 at 2 |
<!-- /generated: top_drivers_pa -->

For each reform, the share of new permissions that owners or builders actually use each year, and the share of a saving that reaches builders, are the largest uncertainties. **Why these ranges are reasonable:** each range is wide enough to hold the published evidence the team found, such as ADU permit counts in cities and states that legalised ADUs, studies of who keeps a cut in development fees, and published construction and parking costs. Every value, its source and what would change it are in the Assumption Ledger ([Overview, section 2](MODEL_AND_ASSUMPTIONS.md#2-core-assumptions-and-their-rationale)).

## A state with thin data (Vermont)

The same method runs, unchanged, for Vermont, which has none of Pennsylvania's local data. Ranges are wider where inputs are missing, and transit-oriented density adds nothing: Vermont has frequent bus stops, but a mid-rise building does not pay for itself at Vermont rents ([comparison](MODEL_AND_ASSUMPTIONS.md#34-a-state-with-thin-data-vermont-next-to-pennsylvania)).

<!-- generated: headline_table_vt -->
*Generated from the default runs saved in `web/public/data/VT/runs/` (seed 0, 2,000 draws). Likely range = P10 to P90; eight in ten simulated futures fall inside it. Frozen as of 2026-11-24.*

| Run | Added homes permitted, 2027-2031: likely range | Change against the baseline | Net added homes (after homes torn down) |
|---|---|---|---|
| Baseline (no reform): homes permitted | 5,110 to 17,665 (middle 12,243) | - | - |
| Accessory dwelling units | 69 to 371 (middle 173) | 0.6% to 4.3% (middle 1.5%) | 69 to 371 (middle 173) |
| Missing middle | 1 to 17 (middle 4) | 0.0% to 0.2% (middle 0.0%) | 1 to 15 (middle 4) |
| Transit-oriented density | 0 to 0 (middle 0) | 0.0% to 0.0% (middle 0.0%) | 0 to 0 (middle 0) |
| Parking requirement reduction | 34 to 166 (middle 82) | 0.4% to 1.2% (middle 0.8%) | 34 to 166 (middle 82) |
| Development fee reduction | 183 to 1,026 (middle 483) | 2.2% to 7.9% (middle 4.6%) | 183 to 1,026 (middle 483) |
| All five together | 425 to 1,450 (middle 813) | 4.5% to 12.1% (middle 7.5%) | - |

*The baseline row counts all homes permitted with no reform. Every other row counts the homes a reform adds to that, in the same simulated futures. Net added homes are shown where the saved run carries them (missing middle and transit-oriented density replace some existing homes); `-` means not in the saved run.*
<!-- /generated: headline_table_vt -->

## What the tool cannot tell you

- It counts permits, not finished or occupied homes, and it stops at five years.
- It gives no answer for a single parcel or address, and cannot say who will live in new homes or what they will pay.
- It assumes local governments apply the state rule as written, and treats approval delays as illustrative only.
- It does not model shortages of builders, materials or water and sewer capacity.
- Where data is thin, the range is wider. Every number shows its data tier.
