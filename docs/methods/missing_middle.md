# Method: missing middle (Reform 2)

This note explains how the tool estimates added homes from a missing-middle reform, which numbers it uses, where they come from, and what the method cannot tell you. The code is `src/visionhousing/scenarios/missing_middle.py`; the module note is [`docs/modules/scenario_missing_middle.md`](../modules/scenario_missing_middle.md).

**Missing middle**: buildings with 2 to 6 homes (duplexes, triplexes, fourplexes, small apartment buildings), between a single house and a large apartment block in size.

## The question

If a state allows 2–6 homes, up to three storeys, on every lot now restricted to single-family use, with setbacks and lot coverage not binding, how many more homes would be permitted in 2027–2031 than under today's rules? The answer is a likely range by tract, rolled up to county and state.

## The funnel

The tool follows the same formula as the ADU reform (plan §4.3, "Channel A"), with one change: a missing-middle project replaces a house, so each project adds `k − 1` homes, not `k`.

```
added units (tract) = E × f × Σ_k [ w_k × p_k × (k − 1) ] × min(h, turnover) × Σ ramp(t) × (1 − x)
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
| `− 1` | The house that is torn down | `missing_middle.demolished_per_project` (1) |
| `x` | Share that would have been built anyway | `missing_middle.displacement_share` |

The tool reports seven steps: **eligible lots → lots large enough → feasible lots → lots redeveloped → new units → net units after demolition → net added units**. Each is a P10 / P50 / P90 range from seeded draws of every ledger value (2,000 by default).

### Step 1 · Eligible lots `E`

Census single-family homes are turned into lots with the same correction the ADU funnel uses (counting lots from census building tables overstates real lots by about a tenth). Only lots in single-family-only zones gain anything from the reform. At Tier 0 that share is a ledger parameter (`missing_middle.sf_only_zoned_share`, 0.75, range 0.50–0.90): most single-family homes sit in zones that allow nothing else, but older boroughs and cities often allow two units already. At Tier 2 it will come from zoning data.

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

### Step 5 · Units and net units

New units = Σ_k lots redeveloped with `k`-unit buildings × `k`. Net units = new units − lots redeveloped × 1 (one house torn down per project). So a duplex adds one home, a sixplex five.

### Step 6 · Displacement

Some of these homes would have been built anyway, as single-family homes or apartments elsewhere. Net added units = net units × (1 − `missing_middle.displacement_share`) (0.15, range 0–0.40).

### Overlap with the ADU reform

ADUs and missing middle compete for the same lots (plan §4.5). In a package, the engine passes the lots ADUs used. A share `missing_middle.adu_lot_overlap_share` (0.75) of them leaves the eligible pool: a lot that just gained an ADU is rarely torn down soon after, and some ADU lots sit outside single-family-only zones anyway. The engine decides how lots are split between the two reforms; this module removes only what it is given.

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
- **Lending and building codes.** Five- and six-unit buildings often need commercial loans and stricter code rules. The mix weights stand in for this; they are not modelled from first principles.
- **Who lives in the new homes, what they rent for, effects after 2031, and whether towns comply or obstruct.**

## Parameters added for this funnel

`missing_middle.typology_weight_duplex`, `_triplex`, `_fourplex`, `_fiveplex`, `_sixplex`, `missing_middle.acquisition_premium`, `missing_middle.new_unit_rent_premium`, `missing_middle.rent_ratio_2br`, `missing_middle.sf_turnover_annual`, `missing_middle.ramp_first_year_share`, `missing_middle.min_lot_sqft`, `missing_middle.lot_proxy_net_share`, `missing_middle.adu_lot_overlap_share`. All are draft team judgements with wide ranges; each says what evidence would change it. The older `missing_middle.units_per_project` is not used by this funnel: the mix replaces it.
