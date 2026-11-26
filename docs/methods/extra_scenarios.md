# Method: the extra scenarios

The five benchmark reforms stay exactly as the Challenge Handbook defines them. Four
**extra scenarios** answer questions the benchmarks leave open. Each is a separate reform
with its own entries in the Assumption Ledger (`assumptions/parameters.yaml`, ids
`infra.*`, `commercial.*`, `reuse.*`, `starter.*`, `fee_aff.*`), its own scenario entry
(`assumptions/scenarios.yaml`, kind `extra`), its own outcome-contract audit row
(`assumptions/outcome.yaml`) and its own named variants (`assumptions/variants.yaml`).

Like the benchmarks, each one reports, from seeded draws of every ledger range: the change
against the baseline (added units authorized by building permits, 2027-2031, as P10 / P50 /
P90), what drives it (the funnel records), how sensitive it is (the tornado and variants)
and what it cannot tell you (below). No number in this note is a result; every result
comes from the engine with its formula, inputs and ids.

| Scenario | Run it with | Builds on | May not share a package with |
|---|---|---|---|
| Transit zoning with funded infrastructure | `transit_zoning_funded_infrastructure` | TOD benchmark | `tod` |
| Housing by right on commercial land | `commercial_land_housing` | — | `tod`, the funded-TOD scenario |
| Adaptive reuse (conversions) | `adaptive_reuse` | — | — (adds no permits) |
| Starter-home lots | `starter_home_lots` | — | `adu`, `missing_middle` |
| Fee deferral or waiver with affordability terms | `fee_deferral_with_affordability` | fee benchmark | `fees` |

`GET /api/v1/scenarios` lists every scenario with its kind, description, sources, variants
and exclusions; `GET /api/v1/reforms` now gives each reform's `kind` so a screen can keep
its benchmark defaults. A package that holds two reforms reaching the same lots or projects
is refused (`ReformSpec.excludes`), so no home is counted twice.

The extra scenarios read three optional pack tables (`scenarios/extra_common.py`):
water and sewer readiness (`infra_tract`, [infrastructure.md](../modules/infrastructure.md)),
parcel land use (`parcels_area`) and HUD income limits (`afford_income_limits`). A pack
without one gets a stated fallback and a run note. Testland carries small synthetic
versions of all three, made by its seeded generator ([testland.md](../modules/testland.md)),
so the demo state shows each scenario's "table present" path. The sources a record cites for these
tables come from the pack, not from code: the readiness table's own `sewer_source` ids
(the official record behind each tract), else the ids the pack's data dictionary gives for
the table's columns (`extra_common.table_sources`). Testland's tables therefore cite
`synthetic`, and a state cites only its own records.

## 1. Transit zoning with funded infrastructure

**Question.** The TOD benchmark assumes every station area can take many more apartments.
If builders had to pay for water, sewer and power capacity, how many homes would the
upzoning alone add, and how many more if the public funds (or finances) that capacity?

**How.** The TOD funnel ([tod.md](tod.md)) runs twice on the same draws
(`scenarios/tod_infrastructure.py`):

| Run | What changes in the TOD pro forma |
|---|---|
| Zoning alone `A` | residual land value per apartment − `infra.capacity_cost_per_apartment` × need; land under a documented constraint removed |
| With funded capacity `B` | grant: no capacity cost, constraint lifted (this is the TOD benchmark run); financed: builders pay `infra.financed_builder_share` of the cost |

`need` per tract = verified share × `infra.need_share_verified` + outside share ×
`infra.need_share_outside` + unknown share × `infra.need_share_unknown`, from `infra_tract`
(`sewer_share` counts every home inside a sewer service area, including homes there under a
constraint such as a moratorium; so constraint = `constraint_share`, verified = `sewer_share` −
the constraint homes inside the area (the constraint is placed inside the area first),
outside = `sewer_known` − `sewer_share` − any constraint homes outside it, unknown = the rest;
a tract the table does not list is unknown).

```
infrastructure part (county) = max(B − A, 0) × share of B's permits on or after infra.in_service_year
total (county)               = A + infrastructure part
```

- **Never credits feasible permits.** Only `B − A`, never below zero, is credited to the
  money. With no capacity cost the money adds nothing (tested).
- **In-service date.** A year counts in full when service starts by January 1 and in part
  when it starts during the year; permits that need the capacity before that date are not
  issued in the window (record `waiting_for_service`).
- **No double gain.** With a grant serving from 2027 the total equals the TOD benchmark on
  the same draws (tested); a later date only lowers it.

**Records.** `zoning_alone`, `funded_no_date`, `infra_raw`, `infra_in`,
`waiting_for_service`, `total` (headline), `net`, `in_service_share`, `public_capital_cost`
(capacity cost × (1 − builder share) × need × funded-run permits after service),
`public_cost_windfall` (the part spent on apartments feasible anyway),
`public_cost_per_infra_home`, and `readiness_*` (station-area homes by sewer answer).
Variant `transit_infrastructure_financed` sets `funding: financed`.

**Drivers and sensitivity.** Everything that drives TOD (rents, costs, the absorption cap,
the net-new share) plus the capacity cost, the need shares (the unknown share is wide on
purpose) and the in-service date.

**Limits.** A service-area map shows service, not spare capacity. The capacity cost is a
per-apartment average; real upgrades are lumpy (a plant or trunk main serves many
projects at once). Most Pennsylvania tracts are "unknown". The in-service date is a
judgement range, not a funded project's schedule.

## 2. Housing on commercial land, and adaptive reuse (two separate routes)

**Question.** If apartments are allowed by right on land used for shops and offices (as
Texas SB 840 of 2025 does), how many homes would be permitted? Separately, how many homes
would conversions of existing offices and stores create?

**Eligible land.** Read from parcel land use (`parcels_area`, `lu_commercial`). In
Pennsylvania only Philadelphia (OPA) and Allegheny County have parcel tables; every other
county is marked **"eligible land not measured"** in its county record and counts no
homes. That is a gap in data, not a measured zero. No free statewide commercial-zoning
layer exists; the national land-cover map's developed classes cannot tell shops from
houses, so it is not used. The documented fallback for another state: add a parcel
land-use table (or a zoning layer read into the same columns) to its pack.

**Route 1, new construction** (`commercial_land_housing`, `scenarios/commercial_land.py`):

```
eligible acres = commercial parcels × commercial.parcel_acres
redevelopable  = eligible × commercial.redevelopable_share
feasible       = redevelopable × p
capacity       = feasible × commercial.units_per_acre
built          = capacity × min(1, commercial.redevelopment_hazard_annual × Σ ramp)
absorbed       = built, capped at commercial.absorption_cap_share × county baseline 5+ permits
authorized     = absorbed × commercial.net_new_share × (1 − commercial.baseline_rezoning_share)
```

`p` = share of the tract's owner home values `V` (a local price level) with
`V × commercial.site_value_home_equivalents_per_acre × (1 + acquisition premium)` ≤ the
residual land value of `units_per_acre` apartments in the four-storey walk-up (the TOD
low-rise prototype: `tod.lowrise_hard_cost_per_sqft`, surface parking, at most
`commercial.max_spaces_per_unit` = 1 space per home, SB 840's cap). No home stands on
commercial land, so no home is demolished.

**Route 2, conversions** (`adaptive_reuse`):

```
buildings   = commercial parcels × reuse.suitable_building_share
potential   = buildings × floor area × efficiency / unit size
feasible    = potential × share of renters paying ≥ break-even rent / new-unit rent ratio
              (tod.new_unit_rent_premium: a multiple of today's rent, not a premium added to 1)
conversions = feasible × min(1, reuse.conversion_hazard_annual × Σ ramp), capped at
              reuse.absorption_cap_share × county baseline 5+ permits
break-even rent = (acquisition + conversion cost per gross sq ft × gross sq ft per home
                   + fee × reuse.fee_share_of_benchmark) × (1 + margin) × cap rate
                  / (12 × (1 − operating share))
```

**Outside the permit measure.** A conversion is an alteration of an existing building,
which the Building Permits Survey leaves out ([outcome.md](outcome.md)). So
`adaptive_reuse` adds **0** to added units authorized and to any package; its homes are the
separate record `conversion_homes` (tested: a package of both routes equals route 1 alone).

**Sensitivity.** Route 1: parcel size and redevelopable share, the site price, density,
rents and the cap. Route 2: conversion cost (`reuse.conversion_cost_per_sqft` $150–400 per
sq ft, a representative range, team judgement), acquisition price, suitable share.

**Limits.** Parcel counts, not areas; no zoning (land use stands in for it); commercial land
prices are proxied by home values; the conversion screen does not look at floor plates,
windows or structure; Pennsylvania has eligible land in two counties only.

## 3. Starter-home lots

**Question.** If the minimum lot for a new subdivision on single-family land falls to
`starter.min_lot_sqft` (3,000–5,000 sq ft; Texas SB 15 caps 3,000 sq ft), how many homes?

**How** (`scenarios/starter_lots.py`): eligible lots as in the missing-middle funnel (1-unit
detached homes × `adu.lot_proxy_adjustment` × `missing_middle.sf_only_zoned_share`), less
homes outside a mapped public sewer area where the readiness table says so. Newly
splittable = share of lots at least 2 × the new minimum − share at least 2 × today's
minimum (`starter.current_min_lot_sqft`), from the block lot-size proxy; a split legal
today is never credited (tested). Two project types, `starter.keep_house_share` of them
"split and keep" (one new house beside the old one) and the rest "split and rebuild"
(`starter.rebuild_homes_per_lot` small homes replace the house). A starter home sells at
the tract median × `starter.small_home_value_ratio` × (1 + new-construction premium); its
residual land value pays for half the lot's land value (keep) or the whole house (rebuild).

```
added units authorized = (keep splits + rebuild splits × homes per rebuild)
                         × (1 − starter.baseline_split_share) × (1 − starter.displacement_share)
existing homes replaced = rebuild splits × (1 − starter.baseline_split_share)
net added homes         = added units authorized − existing homes replaced
```

**Appraisal and lending flag.** Where the share of the tract's homes valued within 20% of
the starter-home price is below `starter.min_comparable_share`, lenders may find no
comparables: record `appraisal_flag_share`. Variant `starter_home_lots_appraisal_binding`
counts no homes there. Records `homes_new_lot` and `homes_rebuild` give the mix of home types.

**Limits.** Lot sizes are block averages, not parcels; today's minimum lot is one range
for the whole state (the municipal zoning table could replace it where verified); deed
restrictions and HOA rules are not modelled; the $50,000 benchmark fee is kept for
comparability.

## 4. Fee deferral or waiver with affordability terms

**Question.** If rental projects that keep `fee_aff.restricted_share` of their homes
affordable at `fee_aff.ami_level` of area median income for `fee_aff.duration_years` get
their fees deferred to occupancy (or waived), how many homes are added, how many are
restricted, and what does the public give up?

**How** (`scenarios/fee_affordability.py`), per county and draw:

```
restricted rent    = 30% × income limit(AMI level, 3 people) / 12
                     (HUD limits: AMI level / 50% × the 50% limit; fallback without the table:
                      county median household income × fee_aff.ami_fallback_ratio × 0.9 × level)
affordability cost = restricted share × max(market rent − restricted rent, 0) × 12
                     × (1 − (1 + cap rate)^−duration) / cap rate
relief             = deferral: fee × construction-loan rate × months / 12; waiver: fee × fee_aff.waiver_share
net gain           = relief − affordability cost
induced homes      = baseline rental units × ε × (net gain / V) × k      (only where net gain > 0)
opted-in baseline  = baseline rental units × fee_aff.optin_share          (only where net gain > 0)
restricted homes   = restricted share × (opted-in baseline + induced homes)
```

Both modes run on the same draws (records `deferral_*`, `waiver_*`); option `mode` (variant
`fee_waiver_with_affordability`) chooses the headline. Fiscal records:

```
waiver:   relief per home  = waived fee = fee × fee_aff.waiver_share
          new revenue      = (fee − waived fee) × induced homes
deferral: relief per home  = fee × c,  c = d + loss − d × loss,
                             d = 1 − 1 / (1 + public rate)^(months / 12),
                             loss = fees.deferral_collection_loss_share
          new revenue      = fee × induced homes × (1 − loss) × (1 − d)   (present value)
revenue given up (gross)          = relief per home × (opted-in baseline + induced homes)
given up on homes built anyway    = relief per home × opted-in baseline homes
net public cost                   = given up on homes built anyway − new revenue
                                  (= gross − fee × induced homes, in both modes)
```

The induced homes would not exist without the deal, so the relief on them is not money the
public had before; only the fee they still pay counts, and it counts once. This is the same
rule as the fee benchmark's fiscal split. Net public cost per added home and per restricted
home are per-draw ratios, shown only when at least `fees.cost_per_added_min_share` of draws
add homes. The **cash-flow timing** gives the fees due each year at permit
(`cash_<year>_at_permit`), collected under the deferral after the share never collected
(`cash_<year>_deferred`) and collected after 2031 (`cash_after_window`).

**Drivers and sensitivity.** The rent gap (market rent against the HUD limit), the set-aside,
duration and AMI level, the elasticity and pass-through, the opt-in share.

**Limits.** All-or-nothing opt-in per county and draw (one representative project);
single-family homes are left out; the restricted rent uses a two-bedroom home; the
affordability cost is the present value of rent given up at the cap rate, with no tax credit
or other subsidy stacked on top.
