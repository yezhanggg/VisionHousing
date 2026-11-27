# Method: missing middle (Reform 2)

This note explains how the tool estimates added homes from a missing-middle reform, which numbers it uses, where they come from, and what the method cannot tell you. The code is `src/visionhousing/scenarios/missing_middle.py`; the module note is [`docs/modules/scenario_missing_middle.md`](../modules/scenario_missing_middle.md).

**Missing middle**: buildings with 2 to 6 homes (duplexes, triplexes, fourplexes, small apartment buildings), between a single house and a large apartment block in size.

## The question

If a state allows 2–6 homes, up to three storeys, on every lot now restricted to single-family use, with setbacks and lot coverage not binding, how many more homes would be permitted in 2027–2031 than under today's rules? The answer is a likely range by tract, rolled up to county and state.

## The funnel

The tool follows the same funnel as the ADU reform, with one change: a missing-middle project replaces a house. Instead of a shortcut ("`k − 1` homes per project"), each redeveloped lot carries four event fields, in both arms (steps 6 and 7).

```
lots redeveloped (tract) = E × f × Σ_k [ w_k × p_k ] × min(h, turnover) × Σ ramp(t)
added units authorized   = (policy units − counterfactual units) × (1 − x)
net added homes          = added units authorized − (policy demolitions − counterfactual demolitions)
```

| Term | Plain meaning | Where it comes from |
|---|---|---|
| `E` | Eligible lots: single-family lots in zones that allow only one home | Census units in structure (ACS B25024, "1, detached") × `adu.lot_proxy_adjustment` × `missing_middle.sf_only_zoned_share` |
| `f` | Share of eligible lots large enough for a 2–6 unit building | Measured share (Tier 2), else the block lot-size proxy, else `missing_middle.fit_share` |
| `k`, `w_k` | Building type (2 to 6 homes) and its weight in the mix | `missing_middle.typology_weight_*`, divided by their sum in each draw; limited to `min_units`–`max_units` |
| `p_k` | Share of lots large enough where a `k`-unit building pencils | Tract owner home values (ACS B25075 brackets) against the residual land value of a `k`-unit building |
| `h` | Share of feasible lots redeveloped each year | `missing_middle.redevelopment_hazard_annual` |
| `turnover` | Share of single-family homes sold each year; `h` can never exceed it | `missing_middle.sf_turnover_annual` |
| `ramp(t)` | Permits climb in the first years | `missing_middle.ramp_first_year_share`, `missing_middle.ramp_years_to_full` |
| `d` | Homes torn down per redeveloped lot (policy demolitions) | `missing_middle.demolished_per_project` (1) |
| `q`, `r` | Chance the baseline tears the same house down anyway, and the homes it rebuilds (counterfactual demolitions and units) | `missing_middle.baseline_replacement_probability`, `missing_middle.baseline_units_per_replacement` |
| `x` | Share that would have been built anyway | `missing_middle.displacement_share` |

The tool reports seven steps: **eligible lots → lots large enough → feasible lots → lots redeveloped → new units → net units after demolition → net added units**. Each is a P10 / P50 / P90 range from seeded draws of every ledger value (2,000 by default).

### Step 1 · Eligible lots `E`

Census single-family homes are turned into lots with the same correction the ADU funnel uses (counting lots from census building tables overstates real lots by about a tenth). Only lots in single-family-only zones gain anything from the reform. At Tier 0 that share is a ledger parameter (`missing_middle.sf_only_zoned_share`, 0.75, range 0.50–0.90): most single-family homes sit in zones that allow nothing else, but older boroughs and cities often allow two units already. At Tier 2 it will come from zoning data.

### A state floor by date (Vermont)

Where a state law already guarantees part of what the reform allows, the reform adds only the rest. Vermont's 24 V.S.A. § 4412(1)(D) already makes a duplex a permitted use on every lot in a district that allows year-round homes, and up to four units where municipal **water and sewer** serve the district. So in Vermont, `E` is the single-family lots whose own bylaw allows one home (the national ledger range above: no Vermont count exists), and then, in the mix method:

- **Only bigger buildings count.** A `k`-unit building counts only where `k` is above what is already allowed: 5 or 6 units on lots with water and sewer, 3 to 6 on lots without. The share with both services is `s × a + (1 − s) × b`: `s` is the tract's share of homes inside a mapped municipal sewer service area (pack table `infra_tract`), `a` the share of those also on municipal water (`missing_middle.floor_water_share_in_sewer_area`, 0.85, range 0.60–0.97) and `b` the share of homes with no sewer record that have both (`missing_middle.floor_served_share_no_sewer_record`, 0.03, range 0–0.10). The pack has no statewide municipal water map, so the four-unit right is unknown on every lot; the two draft judgement entries turn "unknown" into a likely range, never into "no right".
- **Count the change, not the level.** On a lot where the already-allowed design (a fourplex with services, a duplex without) also pencils, a bigger building adds only its extra homes: `k − m` where `m` is what is already allowed (a six-unit building instead of a fourplex adds two). The share of such lots is `min(p_m, p_k) / p_k`.
- **No double count.** "Already allowed" is the larger of the rights in force that year and the rights the baseline's permit history already reflects. Act 47 of 2023 put the duplex and four-unit rights in force on July 1, 2023, so permits since then include them, and the baseline already carries that production.
- **Current law in both arms.** Each window year reads the version of § 4412(1)(D) in force on July 1 of that year: the Act 179 text in 2027, and from January 1, 2028 the text that adds an exception for districts that require multiunit buildings of more than four homes. Both arms use the same schedule. The exception does not reach the reform's lots (a district whose bylaw allows one home requires no multiunit buildings), so it changes nothing here.
- **The same lot in both arms.** On a lot where the already-allowed `m`-unit design also pencils, the counterfactual is that building: `m` new units and the same demolition, with no `q × r` rebuild on top. The other redeveloped lots keep the usual counterfactual (`q × r` new units, `q ×` the demolitions). So a fourplex (4 units, 1 demolition) against an already-allowed duplex (2 and 1) gives 2 added authorizations and +2 homes. Records `baseline_on_sites` and `counterfactual_removed` include those duplexes and their demolitions.
- **"Freeze today's rules"** keeps the version in force on November 25, 2026 in every year. It is a separate comparison (record `floor_freeze_difference`), not a forecast arm; in Vermont it is zero.

Records: `floor_served_share`, `floor_newly_legal_share`, `floor_existing_alternative` (homes not counted because the already-allowed design also pencils) and `floor_freeze_difference`. The parameters of each version are in `assumptions/law/statutory_rights.yaml` (block `missing_middle_application`); the code is `law/rights.py` (`middle_floor_schedule`), `engine/law_floor.py` and the funnel's `floor` argument. The prototype method is not changed by the floor. This replaces the earlier judged Vermont range for `missing_middle.sf_only_zoned_share` (15–50% of lots "not yet legal"), which folded the statute into one share.

### Town bylaws (named variant `vt_missing_middle_town_bylaws`)

The benchmark uses the statewide floor everywhere. The named variant (option `local_rights: town_bylaws`) also reads the Vermont town readings in `assumptions/law/local_readings.yaml`:

- **Which towns.** A town counts when its reading is verified, its `small_multiunit` fact is verified (`require_verified` in `local_readings` of `assumptions/variants.yaml`) and the reading counts from a date on or before the run's as-of date: its effective date, else its `in_force_by` date, else its review date. Drafts, guidance, superseded texts and towns not read never count.
- **Its level.** For each window year, each listed district is read with the state floor of that year (`rights.state_floor`: the district's own by-right maximum, its served-district statements, the 2028 exception), and the town's level is the smallest over its districts. One district with an unknown answer leaves the whole town on the statewide floor.
- **Where it applies.** On the part of a tract's lots inside the town (2020 housing units of the tract piece, pack table `tract_municipalities`), "already allowed" becomes `max(state floor comparator, town level)`; the rest of the tract keeps the statewide floor. A town that allows no more than the floor changes nothing. Record `floor_town_bylaw_share` gives the share of eligible lots reached; the run's notes name the towns used and why each other one was not.
- **Checks (tested).** With no town placed the variant equals the benchmark draw for draw; a town allowing four homes takes the fourplex out on its unserved lots, in proportion to its share.

Since D6 (2026-11-26) the Vermont pack has `tract_municipalities` (2020 block assignment, county subdivisions). Seed 0, 2,000 draws, as-of 2026-11-24: benchmark 4.36 (1.17-16.86), variant 4.36 (1.17-16.86), paired difference 0 (0 to 0; below the benchmark in 4.4% of draws, at most 2.3 homes). Only Barre and Montpelier count on the as-of date; both allow 4 homes by right, so the fourplex there is no longer counted as added. With the readings read on 2026-11-26 (a check, not shipped: Barre, Burlington, Montpelier, Rutland, South Burlington and Winooski, all 4 by right) the variant is 4.32 (1.17-16.44), paired -0.02 (-0.51 to 0).

Limits: how far each town's bylaw has caught up with the statute is not measured.

### Step 2 · Lot large enough `f`

The tool uses the best data the state pack has, in this order:

1. **Measured** (Tier 2): a share per tract passed in by the engine.
2. **Block lot-size proxy** (Tier 0, when the pack has it). For each 2020 census block, land per home = block land ÷ homes. Per tract the pack keeps the 25th, 50th and 75th percentiles over homes. Block land also holds streets and parks, so the lot is taken as `missing_middle.lot_proxy_net_share` (0.60) of it. A lot is large enough when it is at least `missing_middle.min_lot_sqft` (5,000 sq ft, range 3,500–7,000). The share of homes above that line is read from the three percentiles, with straight lines on a log scale between them and the nearest line extended beyond them.
3. **Ledger** `missing_middle.fit_share` (0.45, range 0.20–0.75), where neither exists.

The proxy percentiles are over all homes in the tract, including apartments, so they understate single-family lot sizes. That pushes `f` down, not up.

### Step 3 · Feasible share `p`

A builder has to buy the house first. The **residual land value** (RLV) is what a builder could pay for the lot and still make the required profit (see `docs/modules/proforma.md`):

```
RLV_k = value of a finished k-unit building − (construction + soft costs + fees + parking) × (1 + developer margin)
value = k × rent × 12 × (1 − operating-cost share) ÷ cap rate
```

- **Rent** for a new unit = the tract's two-bedroom median rent (ACS B25031) × `missing_middle.new_unit_rent_premium` (1.25). A newly built apartment asks more than the census median, which includes long-time tenants in older buildings. Where the two-bedroom median is blank: median gross rent × `missing_middle.rent_ratio_2br`.
- **Fees** are the Handbook's $50,000 per unit (`fees.fee_total_per_unit`). **Parking** is surface parking at `parking.required_spaces_per_unit_attached` spaces per unit. The fee and parking reforms change these through ledger overrides, so in a package they raise feasibility here.
- **The lot test.** A lot pencils when its home value × (1 + `missing_middle.acquisition_premium`) ≤ RLV. The premium (10%, range 0–25%) is what it takes to get an owner who is not selling to sell. So `p_k` = share of the tract's owner home values at or below `RLV_k ÷ (1 + premium)`, read from the ACS value brackets with straight-line interpolation inside each bracket (`features.brackets.share_below_many`).
- **The open top bracket** ($2,000,000 or more) has no upper edge. The test never counts those homes as cheap enough: the threshold is capped at $2,000,000. This can only lower feasibility.
- **No owner homes in the brackets.** The tract's median home value decides: `p_k` is 1 if it passes the test, else 0.
- **The mix.** Every line of the pro forma grows in step with the number of units, so if RLV is positive, a sixplex always beats a duplex on paper. In practice lot shape, lending rules (four units is the usual limit for residential mortgages) and building codes keep most projects small. The mix weights (duplex 0.35, triplex 0.20, fourplex 0.25, five-unit 0.05, sixplex 0.15, each with a range) stand in for that. `p = Σ w_k p_k`.

### Step 4 · Lots redeveloped

```
lots redeveloped = feasible lots × min(h, turnover) × Σ_{t=2027..2031} ramp(t)
```

`h` (0.3% of feasible lots a year, range 0.05–1%) is the main behavioural parameter. It is still a draft judgement; it is to be calibrated on places with gentle-density reforms that published permits. Redevelopment needs a sale, so `h` is capped at the yearly sale rate of single-family homes (`missing_middle.sf_turnover_annual`, 4.5%). The ramp has the same form as the ADU ramp: `ramp(t) = min(1, r1 + (1 − r1)(t − 1)/(Y − 1))`.

### Step 5 · New units authorized

New units authorized = Σ_k lots redeveloped with `k`-unit buildings × `k`. Every unit in the new building needs a permit.

### Steps 6 and 7 · Events on the same lots, then gross and net

Each redeveloped lot has four event fields:

| Field | Policy | Counterfactual (the baseline) |
|---|---|---|
| New units authorized | `k` (the new building) | `q × r` (with chance `q` the lot is rebuilt with `r` homes anyway) |
| Homes demolished | `d` | `q × d` (the same house, torn down anyway) |

One function (`engine.outcome.site_account`) gives both accounts:

```
added units authorized (the headline) = (policy units − counterfactual units) × (1 − x)
net added homes if completed          = added units authorized − (policy demolitions − counterfactual demolitions)
```

`x` = `missing_middle.displacement_share`: homes that would have been permitted
elsewhere anyway. It moves no demolition. Records `units`, `removed`,
`baseline_on_sites`, `counterfactual_removed` and `demolitions_caused` keep
every field, so a reader can redo the subtraction.

**Worked cases** (tested, `tests/test_outcome_accounting.py`):

- A fourplex replacing a house that the baseline would have rebuilt as one
  house (`q = 1`): 4 − 1 = 3 added authorizations; demolitions 1 − 1 = 0, so +3
  homes if completed.
- The same fourplex where the baseline leaves the house alone (`q = 0`): 4 added
  authorizations, 1 demolition caused, +3 homes.
- An already-permitted duplex under a two-unit ceiling: both arms build the
  same duplex, so 0 and 0. Such a lot is not in a single-family-only zone, so
  it is not eligible in the first place.

**Work types.** Every new building here is new construction, which the
Building Permits Survey counts, so the headline equals the comparable record
(`accounting.records.comparable`). On a detached lot it is a stand-alone
building; on a semi-detached or row lot (only in scenarios that reach them) a
new attached structure. See [`outcome.md`](outcome.md) for the five record
types.

### Overlap with the ADU reform

ADUs and missing middle compete for the same lots (plan §4.5). In a package, the engine passes the lots ADUs used. A share `missing_middle.adu_lot_overlap_share` (0.75) of them leaves the eligible pool: a lot that just gained an ADU is rarely torn down soon after, and some ADU lots sit outside single-family-only zones anyway. The engine decides how lots are split between the two reforms; this module removes only what it is given.

Under the package option `market` (project choice, [market.md](market.md)) this rule is replaced: each lot both rules reach goes to its best use (an ADU, the best 2-6 unit prototype, a conventional new house, or nothing), compared on residual value lot by lot, and the funnel is rerun with those rivals (option `rivals`, prototype method). The benchmark headline keeps the rule above.

## Prototypes: duplex, fourplex, six-unit building

The benchmark preset keeps its ledger mix of building types (above). Beside it,
every run compares three **prototypes** on the same lots and draws
(`scenarios/prototypes.py`; records `proto_*`), to show why one pencils where
another does not:

| Prototype | Homes | Smallest lot | Floor area per home | Rent | Code and financing |
|---|---|---|---|---|---|
| Duplex | 2 | `missing_middle.prototype_duplex_min_lot_sqft` (4,000 sq ft) | 1,200 sq ft | three-bedroom median | residential code; residential mortgage |
| Fourplex | 4 | `..._fourplex_min_lot_sqft` (6,000) | 950 | two-bedroom median | building code: + `missing_middle.code_cost_ibc_per_sqft`; accessible ground-floor homes: + `missing_middle.accessibility_cost_per_ground_unit` |
| Six-unit | 6 | `..._sixplex_min_lot_sqft` (8,000) | 850 | two-bedroom median | as the fourplex, plus commercial financing: + `missing_middle.commercial_finance_margin_add` on the required return |

**Code and financing thresholds.** The thresholds themselves are cited: the
residential code covers one- and two-family homes (IRC R101.2), so three or more
homes are built under the building code, which requires sprinklers in
residential buildings (IBC 903.2.8); federal accessible design applies to
ground-floor homes in buildings of four or more without an elevator (24 CFR
100.205); residential mortgages stop at four units, so five or more need a
multifamily (commercial) loan. The dollar and percentage amounts attached to
each threshold are judgement, flagged `draft` in the ledger.

For each prototype, in each tract and draw:

1. **Lots big enough** = the block lot-size proxy against its smallest lot, as
   in step 2 (bigger buildings never fit on more lots).
2. **Land bid** = its residual land value (rent capitalised, minus hard cost
   with any code add-on, soft cost, the $50,000 fees, parking and the required
   return with any finance add-on) − demolition
   (`missing_middle.demolition_cost_per_lot`) − accessible design on a third of
   its homes (three storeys). This is the most a builder of that prototype
   could pay for the lot.
3. **Pencils alone** = lots big enough × share of owner home values with value ×
   (1 + acquisition premium) at or below the land bid. Records
   `proto_<k>_fit`, `proto_<k>_alone` and `proto_<k>_bid` compare the three, next
   to `proto_lot_price` (the documented lot price: ACS home value × (1 +
   premium), eligible-lot-weighted).
4. **Best choice.** Lots fall into size bands (fits six; fits four but not six;
   fits a duplex only). In each band the builder takes the prototype with the
   highest land bid among those that fit: the lot price is the same whatever is
   built, so the highest bid is the most profitable. The lot is feasible when
   its price is at or below that bid (`proto_<k>_chosen`).

**Why a duplex pencils where a six-unit building does not.** On small lots only
the duplex fits. Where both fit, the six-unit building spreads land over more
homes but pays the building-code and accessibility costs and a higher required
return, and its homes are smaller; a duplex with three-bedroom rents and no code
step can bid more per lot in mid-rent tracts. The records show which effect
wins where.

**The named variant `missing_middle_prototype_choice`** (`method: prototypes`)
uses the best choice as the funnel's feasible step: feasible lots = the
banded feasible share × eligible lots, and new units = lots redeveloped × the
homes of the chosen prototype. Take-up, the baseline's own permits on the
sites, displacement and demolition then follow steps 4–7 unchanged, on the same
draws as the benchmark.

## What the numbers say on Testland

Testland, the synthetic test state (168,399 residents, 31,018 detached homes, two-bedroom median rents of $777–$1,650), 2,000 draws, seed 0:

| Step | P10 | P50 | P90 |
|---|---|---|---|
| Eligible lots | 17,681 | 20,575 | 23,166 |
| Lots large enough | 6,395 | 9,347 | 12,675 |
| Feasible lots | 0 | 0 | 0 |
| Net added units, 2027–2031 | 0 | 0 | 0 |

At Testland's rents, no building type pencils, even with favourable draws: at the central values a duplex's residual land value is about −$320,000 (median tract). A new unit costs about $360,000 to build with the $50,000 benchmark fee, two surface parking spaces and the developer's margin. At Testland rents it is worth about $210,000 as a rental. **This is a finding, not a bug.** It is in line with the very small early results reported after citywide triplex and fourplex legalization.

With rents doubled, as in a high-cost metro, and Testland scaled to Pennsylvania's size (13 million residents), the same funnel gives 646 / 4,882 / 14,338 net added units over five years (about 7.5 per 100,000 residents a year at P50). That is thousands, not hundreds of thousands, which is the right order of magnitude next to the plan's anchors (baseline in the low hundreds of thousands over five years; a statewide ADU reform thousands to a few tens of thousands). The tests check both bands.

## What this cannot tell you

- **For-sale projects.** The value test treats every building as a rental. Townhouses and condominiums sold one by one may pencil where rentals do not. The pro forma already supports a sale basis; using it needs a sale value per new unit by tract.
- **Which lots.** Fit and feasibility are tract shares. The model does not know which lots sit next to each other, or which owners would sell.
- **Teardown prices.** Census home values are owners' own estimates. The real price of a house bought to tear down may differ; the acquisition premium carries this uncertainty.
- **Zoning detail.** At Tier 0 the single-family-only share is one statewide range. Places that already allow two units are not mapped.
- **Lending and building codes.** In the benchmark the mix weights stand in for these. The prototype comparison prices the code and financing thresholds explicitly, but the amounts are judgement; local amendments to the codes are not read.
- **Who lives in the new homes, what they rent for, effects after 2031, and whether towns comply or obstruct.**

## Parameters added for this funnel

`missing_middle.typology_weight_duplex`, `_triplex`, `_fourplex`, `_fiveplex`, `_sixplex`, `missing_middle.acquisition_premium`, `missing_middle.new_unit_rent_premium`, `missing_middle.rent_ratio_2br`, `missing_middle.sf_turnover_annual`, `missing_middle.ramp_first_year_share`, `missing_middle.min_lot_sqft`, `missing_middle.lot_proxy_net_share`, `missing_middle.adu_lot_overlap_share`. All are draft team judgements with wide ranges; each says what evidence would change it. The older `missing_middle.units_per_project` is not used by this funnel: the mix replaces it.
