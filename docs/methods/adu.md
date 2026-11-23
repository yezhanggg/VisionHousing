# Method: accessory dwelling units (Reform 1)

This note explains how the tool estimates added homes from ADU reform, which numbers it uses, where they come from, and what the method cannot tell you. The code is `src/visionhousing/scenarios/adu.py`; the module note is [`docs/modules/scenario_adu.md`](../modules/scenario_adu.md).

**ADU** (accessory dwelling unit): a small second home on a lot that already has a house. It can be inside the house, attached to it, or a separate backyard cottage.

## The question

If a state lets every owner of a single-family lot add one ADU of at least 600 sq ft, with no added parking, how many more homes would be permitted in 2027–2031 than under today's rules? The answer is a likely range by tract, rolled up to county and state.

## The funnel

The tool follows one formula (plan §4.3, "Channel A"):

```
added units (tract) = E × f × p × h × Σ ramp(t) × n × (1 − x)
```

| Term | Plain meaning | Where it comes from |
|---|---|---|
| `E` | Eligible lots | Census units-in-structure counts (ACS B25024) turned into lots, minus lots where an ADU is already legal, minus places the scenario leaves out |
| `f` | Share of eligible lots with room for the ADU | Calibration table `assumptions/calibration/adu_fit.yaml`, by kind of home and area type |
| `p` | Share of lots with room where the ADU pays, plus family-use builders | Tract gross-rent brackets (ACS B25063) against the pro forma's break-even rent |
| `h` | Share of feasible lots that add an ADU each year | Ledger `adu.takeup_feasible_annual` |
| `ramp(t)` | Permits climb in the first years after a reform | Ledger `adu.ramp_first_year_share`, `adu.ramp_years_to_full` |
| `n` | New homes per ADU | 1 (ledger `adu.units_per_lot`, the Handbook benchmark) |
| `x` | Share that would have been built anyway | Ledger `adu.displacement_share` |

The tool reports the funnel step by step: **eligible lots → lots with room → feasible lots → ADUs built → net added units**. Each step is a P10 / P50 / P90 range from 2,000 seeded draws of every ledger value.

### Step 1 · Eligible lots `E`

Census tables count homes, not lots. They are turned into lots by kind of home:

| Kind of home | Lots |
|---|---|
| Single-family detached | homes in "1, detached" |
| Single-family attached (semi-detached and row) | homes in "1, attached" |
| 2–4 unit buildings (HB 2186 only) | homes in "2" ÷ 2 + homes in "3 or 4" ÷ 3.5 (`adu.homes_per_building_three_four`, 3–4) |

Then three corrections:

1. **Census-to-real-lot correction** (`adu.lot_proxy_adjustment`). Counting lots from census building tables overstates real lots by about a tenth where both were measured.
2. **Already legal** (`adu.already_legal_share`). Where ADUs are already allowed by right, the reform adds nothing. See "The already-legal share" below.
3. **Places left out** by a scenario (SB 1346: Philadelphia and Pittsburgh).

### Step 2 · Physical fit `f`

The share of lots where a 600 sq ft unit fits, after setbacks, comes from a calibration table of **aggregated shares only** (decision D-19). No lot-level data, imagery or image model is part of the tool.

Provenance: measured on samples of single-family lots in two Pennsylvania counties from 2022 USDA NAIP aerial imagery, Microsoft building footprints, and county parcel layers; method: open-space test for a 20 × 30 ft footprint after setbacks (4 ft from side and rear lot lines, 10 ft from the house, 5 ft from garages). The fit test itself uses lot lines and building outlines; the imagery was used only to review results. The two counties are Philadelphia and Allegheny (the cities of Philadelphia and Pittsburgh, every 1–4 unit lot). Smaller samples come from Allentown and nine boroughs and townships in Montgomery, Chester and Centre counties.

| Kind of home | Urban | Suburban | Rural |
|---|---|---|---|
| Single-family detached | **0.54** (0.45–0.62) measured | **0.75** (0.55–0.90) partly measured | **0.75** (0.50–0.95) extrapolated |
| Semi-detached and row | **0.08** (0.03–0.20) measured | **0.10** (0.02–0.30) extrapolated | **0.15** (0.03–0.35) extrapolated |
| 2–4 unit building | **0.15** (0.08–0.42) measured | **0.30** (0.10–0.55) extrapolated | **0.35** (0.10–0.60) extrapolated |

How the table is built, honestly:

- **Measured (urban column).** Detached homes: 0.536 in *both* cities, on about 27,000 and 65,000 lots. This is the most solid number in the table. Attached homes: 0.04 in Philadelphia (mostly rowhouses at 0.016) and 0.19 in Pittsburgh; 2–4 unit lots 0.09 and 0.41. The two cities differ a lot, so the attached and 2–4 ranges span both.
- **Partly measured (suburban detached).** Town samples, mostly detached homes, gave 0.59–0.87 by county group (0.41–0.90 by town). They pool all 1–4 unit lots, so they are not split by kind of home. They are stretched to every suburban tract.
- **Extrapolated (everything else).** No rural lot and no suburban attached or 2–4 unit lot was measured. These cells are reasoned from the measured ones (larger lots, but driveways, wells and septic fields take room). Their ranges are the widest.
- **How draws use the table.** Each draw picks one position in the range (`adu.fit_range_position`, −1 to +1, centred on 0) and applies it to every cell. The cells move together, so tract errors cannot cancel out in the state total. This makes the state range wider, not narrower.
- **Limits of the measurement.** No hand-checked accuracy yet (a 250-lot check was drawn and not labelled). About 1 lot in 10 has low-confidence lot lines. Footprints miss small sheds, which slightly overstates fit. Slope, flood zones and trees do not change the answer.

A tract whose area type is not urban, suburban or rural falls back to the ledger entries `adu.fit_share_sf_detached`, `adu.fit_share_sf_attached` and `adu.fit_share_two_to_four`.

The footprint and parcel data are only summarised. Microsoft building footprints are published under the Open Database License; the shares here are a produced work and are attributed.

### Step 3 · Financial feasibility `p`

The homeowner pro forma (`proforma/adu.py`, see [`docs/modules/proforma.md`](../modules/proforma.md)) gives the **break-even rent**: the monthly rent that covers the loan, the owner's own cash and running costs.

An ADU on a lot is assumed to rent like a new one-bedroom there:

```
ADU rent = gross rent × one-bedroom ratio × new-unit premium
```

So the ADU pays on a lot where the gross rent is at least `break-even ÷ (one-bedroom ratio × premium)`. The **share** of the tract's cash renters above that line is read from the published rent brackets with `share_above_many` (straight-line inside each bracket; the open top bracket counted at its lower edge, so the share is never overstated). That share is `p_rent`; a tract can be "12% feasible".

- The one-bedroom ratio is the tract's own `median_rent_b1 ÷ median_gross_rent` where both exist, else `adu.rent_ratio_1br` (0.77 across Pennsylvania counties).
- A tract with no cash renters in the brackets uses its one-bedroom median: `p_rent` = 1 if `median_rent_b1 × premium` covers the break-even, else 0.
- **Family use.** Some owners build for relatives whatever the rent. A share `s` of the remaining lots is added: `p = p_rent + s × (1 − p_rent)` (`adu.nonfinancial_share`, 0.05, range 0.01–0.15).

At central values the break-even rent is about $2,500 a month, far above most Pennsylvania rents. Feasibility is therefore low outside high-rent tracts. This is a finding, not a bug.

### Step 4 · Take-up `h` and ramp

`h` is the yearly share of feasible lots that add an ADU (`adu.takeup_feasible_annual`: 0.01, range 0.003–0.025). It is the main behavioural number and the largest source of uncertainty. It will be calibrated in job T2 (backlog P4-04). The current range is anchored on three published figures:

- **Seattle.** About 263 ADUs a year were permitted in 2016–2019; after the 2019 reform this rose to 987 in 2023 and the city's report says it may be levelling off near 1,000 a year. Seattle reached that plateau about three years after reform, and its first full year was about half of it (the ramp entries).
- **Massachusetts.** 1,224 ADUs were approved in 217 communities in the first year of statewide by-right ADUs. With roughly a million and a half single-family homes, about six in ten with room and perhaps a quarter where an ADU pays, that is near 0.5% of feasible lots in year one, and about 1% at a full rate after a first-year ramp of one half. Rough arithmetic, to be replaced by T2.
- **Pew, 2025 permits.** Cities with permissive rules outside California issued 26 ADU permits per 100,000 residents a year (81 including California). These are two different groups of cities, not a low and a high. The tool reports its own result per 100,000 residents per year next to the 26 as a cross-check.

`ramp(t) = min(1, r1 + (1 − r1)(t − 1)/(Y − 1))` for years t = 1…5, with `r1` the first-year share (0.5) and `Y` the years to the full rate (3). At central values the five-year sum is 0.5 + 0.75 + 1 + 1 + 1 = 4.25 years at the full rate. ADUs built never exceed the feasible lots.

### Step 5 · Net new `n` and displacement `x`

One ADU is one home (`n = 1`). A share `x` (0.10, range 0–0.30) would have been built anyway, for example as a legal conversion already in the baseline; it is taken off.

## The already-legal share

Pennsylvania's value comes from `assumptions/calibration/adu_legal_share.yaml`. ADU rules were read from the zoning codes of 221 towns and the two large cities, weighted by 2020 Census population. Together they hold about half of Pennsylvania's residents.

| Piece | Share of Pennsylvania residents |
|---|---|
| Known by right (read towns plus Pittsburgh) | 0.088 |
| Places with no zoning ordinance | 0.050 |
| Not read (unknown) | 0.445 |
| By-right rate among read towns' residents | 0.182 |

| End | Value | How |
|---|---|---|
| Low | 0.09 | Only places read as by right |
| Central | 0.15 | Known by right + half the no-zoning places + the unknown half at half the read-town rate |
| High | 0.22 | Known by right + every no-zoning place + the unknown half at the full read-town rate |

For a Pennsylvania run the engine overrides the ledger entry `adu.already_legal_share` with this range (`legal_share_override`), so the entry stays a knob users can change. Other states keep the ledger's national default.

One statewide share is applied to every tract. A place-by-place mask needs the tract-to-municipality split (task P4-08).

## Scenarios

| Scenario | Lots reached | Places left out | Other changes |
|---|---|---|---|
| Benchmark ADU (Handbook) | Single-family detached and attached | none | none |
| HB 2186 | Detached, attached and 2–4 unit lots | none | none |
| SB 1346 | Single-family detached only | Philadelphia (county 42101), Pittsburgh | Utility connection fee set to 0 |

**Pittsburgh is a city inside Allegheny County, not a county.** Until the tract-to-municipality split is built from the Census block assignment file (P4-08), SB 1346 removes a share of every Allegheny County tract's eligible lots (`adu.pittsburgh_share_of_allegheny_lots`, 0.20, range 0.15–0.25). Pittsburgh holds about 24% of the county's residents but fewer of its detached homes. **This is a flagged stub.**

SB 1346 reaches a subset of HB 2186's lots, so its likely range is lower. Its connection-fee waiver does raise feasibility per lot, so in a single draw with a high fee SB 1346 can come out slightly above HB 2186 where few attached lots fit. The tests check SB ≤ HB draw by draw with the fee treatment held equal, and check P10, P50 and P90 with each bill's own fees.

## Checks

- On Testland the five-year net added units are well under 1% of eligible lots.
- Testland scaled to Pennsylvania's population gives thousands to about ten thousand net units over five years, inside the plan's sanity band (thousands to a few tens of thousands).
- Method invariants (tested): more eligible lots never reduce units; a higher cost never raises units; a higher rent never lowers units; zero eligible lots give zero; the same seed gives the same result; P10 ≤ P50 ≤ P90; counties sum to the state.

## What this cannot tell you

- Who lives in the ADUs or what they rent for.
- Whether towns comply, or slow approvals down.
- Lot by lot where an ADU fits: the fit is a tract share, not a site plan.
- Septic capacity, slopes, flood zones, historic districts and private deed rules, none of which are in the fit test.
- Effects after 2031.
- Take-up is not yet calibrated to Pennsylvania-like places; it is the widest assumption.
