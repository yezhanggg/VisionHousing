# Model and Assumptions Overview

> **How the numbers here are made.** Every result and assumption value in this overview sits inside a block marked `<!-- generated: ... -->`, written by `scripts/gen_docs_tables.py` from the Assumption Ledger, the source registry and the engine runs saved for the static site (`web/public/data/`). `make docs` (part of `make verify`) fails if a block is out of date. Figures outside those blocks are set by the Challenge, are fixed definitions, or cite their source.

This is the submitted overview (limit: 25 pages, single-spaced). The full ledger tables, the baseline method in full, the transit rules, the validation work and the notes for other states are in the **[Technical Appendix](TECHNICAL_APPENDIX.md)** (sections A to G). The plain-language comparison of the reforms is in [`POLICY_SCENARIO_COMPARISON.md`](POLICY_SCENARIO_COMPARISON.md); every source is documented in [`DATA_PROVENANCE.md`](../DATA_PROVENANCE.md).

---

## 1. Purpose and scope

**Purpose.** Show state policymakers how a housing-policy reform is likely to change housing production over five years, compared with what happens if nothing changes. Show the range, what drives it, how sensitive it is, and what the tool cannot say.

**Outcome measure.** This is the one definition every number in the tool uses:

> **Additional new housing units authorized by building permits in the five calendar years 2027–2031**, for one state, against a no-reform baseline.

The baseline is the same measure with no reform: all units permitted in 2027–2031. A reform's number is the difference from the baseline in the same simulated future. Each five-year total is also split into the five years (appendix E). For reforms that tear down homes (missing middle, transit-oriented density), the net change in the housing stock is shown beside the permits. Every run states its as-of date, its data and policy vintages, and the economic conditions it holds constant (method: [`docs/methods/outcome.md`](methods/outcome.md)).

Why permits:

- They are published every year for every county in the country, by one agency, with one definition (the Census Bureau's Building Permits Survey).
- They go back to 1990, so the baseline can be tested against history.
- They respond to policy faster than completions do, so a five-year window can see the effect.
- Limit: a permit is not a finished home. Some permitted units are never built, and some are finished after 2031.
- Limit for ADUs: many towns permit an accessory dwelling unit as an addition or conversion, not as a new home, and the Building Permits Survey counts only new housing units. So some ADUs the model adds may never appear in published permit counts. The tool labels them "homes added, of which some may not appear in permit statistics".

**Geography.** The model computes by census tract and reports by county and by state. It gives no result for a single parcel or address.

**States.** Pennsylvania in depth. Vermont runs on the same code as the thin-data test (section 3.4). Massachusetts and Wyoming are set up with the same commands (appendix G). The method is built to run for every state, with wider ranges where data is thinner (section 3.3).

**Scenarios in scope.**

| Scenario | Status |
|---|---|
| Baseline (current rules and economics) | Built |
| 1 · Accessory dwelling units (ADUs) | Built, with named variants |
| 2 · Missing middle (2 to 6 units) | Built, with named variants |
| 3 · Transit-oriented density | Built, with named variants |
| 4 · Parking reduction | Built, with named variants |
| 5 · Development fee reduction | Built, with named variants |
| Pennsylvania HB 2186 (ADU bill) | Built (Pennsylvania only) |
| Pennsylvania SB 1346 variant (ADU bill) | Built (Pennsylvania only) |

Scenario definitions live in `assumptions/scenarios.yaml`; named variants in `assumptions/variants.yaml`.

**Timing.** Reforms take effect on January 1, 2027. Take-up ramps up over the five years.

**What the tool is not.** It is not a forecast and not an official state document. It is decision support.

## 2. Core assumptions and their rationale

Every number the model needs but cannot read from data lives in one file, the **Assumption Ledger** (`assumptions/parameters.yaml`). No such number is written in code. Each entry has a central value, a low and a high value, a distribution shape, a kind, a source, a rationale, and a note on what evidence would change it ([`docs/modules/ledger.md`](modules/ledger.md)). The four kinds:

- **Benchmark**: set by the Challenge (for example, fees of $50,000 per unit, cut by half).
- **Sourced**: read from a cited publication, with its web address and access date.
- **Calibrated**: fitted by a validation job written down before its result was seen.
- **Judgement**: the team's documented choice, with a deliberately wide range and a note on what evidence would narrow it. Most entries are of this kind; the counts are in section 2.3.

### 2.1 Benchmark assumptions set by the Challenge

| Reform | Assumption |
|---|---|
| ADUs | One ADU on parcels with an existing single-family building. Detached and semi-detached ADUs of at least 600 sq ft allowed. No additional parking |
| Missing middle | 2 to 6 units on parcels now restricted to single-family use. Up to three storeys. Setbacks and lot coverage non-binding |
| Transit-oriented density | More height or floor area near major transit stops. Parking reduced |
| Parking reduction | Residential parking minimums reduced or removed |
| Fee reduction | Fees total $50,000 per unit and are cut by 50% |

### 2.2 Choices the team made

| Choice | What the model does | Why |
|---|---|---|
| Major transit stop (benchmark preset) | A fixed-guideway (rail) station, or a bus stop with a set number of weekday departures (`parking.transit_bus_min_departures`) | Separates frequent service from ordinary bus stops with a published, countable test |
| Distance to a major stop | Straight-line circles: a set radius for rail and a smaller one for frequent bus (`parking.transit_rail_radius_miles`, `parking.transit_bus_radius_miles`). The user can choose a quarter, a half or three quarters of a mile | Common walking distances. The selector shows how much the answer depends on it |
| TOD variant: frequent bus and walking distance | Rail, or a bus that comes at least as often as a set gap in both weekday peaks (`tod.frequent_bus_max_headway_min`); a half-mile walk on the OpenStreetMap street network (a quarter and three quarters as sensitivities) | Peak frequency is what riders feel; a network walk does not cross rivers or highways (appendix C) |
| Parking still built after reform | A share of the formerly required spaces, lower in transit-rich tracts than elsewhere (`parking.share_still_built_transit_rich`, `parking.share_still_built_elsewhere`) | Lenders and buyers still ask for parking, most of all where transit is weak |
| Fee view | The Challenge's $50,000 per unit, plus a second labelled view with an assumed Pennsylvania fee level, a team judgement not a fee survey (`fees.pa_assumed_fee_per_unit_judgement`) | Keeps the benchmark comparable and adds a local reality check |
| Fees on ADUs | The ADU pro forma prices its own permit and utility-connection fee lines (`adu.permit_fee`, `adu.utility_connection_fee`), not the $50,000 benchmark | The benchmark fee describes a new home on its own lot; an ADU pays a homeowner's permit and connection fees |
| Modelled ADU size | One ledger entry, `adu.unit_size_sqft`, used by every ADU run (the benchmark, HB 2186 and SB 1346). Its central value is the benchmark minimum of 600 sq ft, and every central-value calculation (for example the base of the sensitivity tornado) uses 600 sq ft. In the simulated futures that make the likely range, the size is drawn from the ledger range, which starts at 600 sq ft and lets some owners build larger (values in section 2.3) | The benchmark sets a floor, not a size. Using the same entry in every ADU run means differences between ADU scenarios come from which lots qualify, not from size |

### 2.3 Key assumptions (generated)

The entries below are the benchmark choices above and the inputs that move the results most (section 6). The other ledger entries, with their rationale and what would change them, are in appendix A.

<!-- generated: key_assumptions -->
| Id | Label | Central | Low | High | Unit | Kind | Rationale |
|---|---|---|---|---|---|---|---|
| `adu.unit_size_sqft` | Floor area of the modelled ADU | 600 | 600 | 900 | sq ft | judgement | The pro forma prices a unit at the benchmark minimum, with some owners building larger. The same size is used in every ADU scenario so that differences between scenarios come from eligibility, not size. |
| `adu.already_legal_share` | Share of eligible lots where an ADU is already effectively legal | 0.1 | 0.05 | 0.25 | share | judgement | The default for any state without its own value. Pennsylvania replaces it from its town-rule index (assumptions/calibration/adu_legal_share.yaml); Vermont, whose state law already requires towns to allow ADUs, replaces it from assumptions/calibration/already_legal_vt.yaml. Lots where ADUs are already allowed by right add nothing under the reform. In states without an ADU preemption law, 25 of 196 cities of 100,000 or more (13%) had permissive ADU rules in April 2026, and smaller places tend to be more restrictive, so 10% is the central value. The range runs from 5% to 25%, because a state with a strong preemption law has most lots already legal and needs its own value. |
| `adu.takeup_feasible_annual` | Share of feasible lots that add an ADU each year | 0.01 | 0.003 | 0.025 | share per year | judgement | The main behavioural parameter. About 1% a year of lots where an ADU fits and pays is consistent with the order of magnitude seen after reform: Seattle's permits rose to about 3.5 times the pre-reform rate (about 910 a year in 2022-2024), Massachusetts approved 1,224 ADUs in its first by-right year and 2,084 in eighteen months, and permissive cities outside California issued 26 permits per 100,000 residents in 2025. None of these is measured per feasible lot, so the range is not narrowed until the planned ADU take-up fit sets it. |
| `adu.nonfinancial_share` | Share of fitting lots where an owner builds for family use regardless of rent | 0.05 | 0.01 | 0.15 | share | judgement | Some ADUs house relatives or the owners themselves, so they are built even where rent would not cover cost. This is added to the financially feasible share. |
| `adu.hard_cost_per_sqft` | Construction cost of a new detached ADU per square foot | 250 | 175 | 375 | $/sq ft | judgement | No free public survey of ADU costs in Pennsylvania was found. Published owner surveys from California report medians near $250 per square foot; the range is wide and skewed up for small-unit cost premiums and 2026 prices. |
| `missing_middle.sf_only_zoned_share` | Share of single-family lots in single-family-only zones | 0.75 | 0.5 | 0.9 | share | judgement | Most single-family homes sit in zones that allow nothing else; older boroughs and cities often allow two units already. |
| `missing_middle.redevelopment_hazard_annual` | Share of feasible lots redeveloped each year | 0.003 | 0.0005 | 0.01 | share per year | judgement | Redevelopment needs a sale and a builder, so it is rare: well under 1% of feasible lots a year. Early results of gentle-density reforms were small. |
| `missing_middle.cap_rate` | Capitalisation rate used to value the finished building | 0.06 | 0.05 | 0.075 | share per year | judgement | Net operating income divided by value for small rental buildings; moves with interest rates. |
| `parking.transit_bus_min_departures` | Weekday departures that make a bus stop a major transit stop | 64 | 64 | 64 | departures per weekday | judgement | The benchmark rule: a major transit stop is a fixed-guideway station, or a bus stop with at least 64 weekday departures (about every 15 minutes over 16 hours). |
| `parking.transit_rail_radius_miles` | Distance around a rail station counted as near transit | 0.5 | 0.5 | 0.5 | miles | judgement | The benchmark rule: half a mile, straight line, for fixed-guideway stations. |
| `parking.transit_bus_radius_miles` | Distance around a frequent bus stop counted as near transit | 0.25 | 0.25 | 0.25 | miles | judgement | The benchmark rule: a quarter mile, straight line, for frequent bus stops. |
| `tod.upzoned_far` | Floor area ratio allowed near major stops after the reform | 2.5 | 2.5 | 2.5 | floor area / lot area | judgement | The reform's definition, not an uncertain number. FAR 2.5 with 850 sq ft apartments and 82% net floor area is about 105 homes per acre, inside California SB 79's 80-120 per acre floors. Change it to study a stronger or weaker upzoning; 0 (or anything at or below today's allowed FAR) adds nothing. |
| `multifamily.cap_rate` | Capitalisation rate used to value a finished mid-rise building | 0.055 | 0.045 | 0.07 | share per year | judgement | Larger, newer apartment buildings trade at somewhat lower cap rates than small ones. |
| `parking.share_still_built_transit_rich` | Share of formerly required spaces still built, transit-rich tracts | 0.6 | 0.45 | 0.75 | share | judgement | After minimums are removed, projects near frequent transit still build about 60% of the spaces once required, because lenders and buyers still want parking. |
| `parking.share_still_built_elsewhere` | Share of formerly required spaces still built, other tracts | 0.925 | 0.85 | 1 | share | judgement | Away from transit, most projects keep building close to the old minimum (85-100%), so the saving is small. |
| `supply.elasticity_medium_run` | Medium-run supply response to a change in profit per unit | 1 | 0.3 | 2 | ratio | judgement | Percent change in permitted units for a 1% change in the value-to-cost margin. Published metropolitan housing-supply elasticities for older northeastern metros mostly sit between about 0.5 and 2; 1.0 is a neutral central value until the cross-check job is run. |
| `supply.saving_passthrough_k` | Share of a cost saving that reaches the decision to build | 0.5 | 0.2 | 0.9 | share | judgement | Part of any cut in fees or parking cost is captured by landowners as higher land prices rather than turning into more homes. Half is a neutral start; the range is wide because the evidence on who bears fees is mixed. |
| `fees.passthrough_k` | Share of a fee cut that reaches the decision to build | 0.5 | 0.05 | 1 | share | judgement | Who gains from a fee cut is the most uncertain step of the fee reform. If landowners raise land prices by the full saving, almost nothing reaches the build decision (near 0); if land prices do not move, all of it does (1). Studies of fee incidence point both ways, so the range is kept wider than the general pass-through share and this parameter leads the fee tornado. |
<!-- /generated: key_assumptions -->

**The whole ledger, by kind and status:**

<!-- generated: ledger_summary -->
| Count by | Value | Entries |
|---|---|---|
| kind | benchmark | 9 |
| kind | judgement | 290 |
| kind | sourced | 14 |
| status | draft | 290 |
| status | sourced | 23 |
| all | entries | 313 |

*Ledger fingerprint `8f39317fed68`.*
<!-- /generated: ledger_summary -->

**How the judgement ranges were set.** Each judgement range is set wide on purpose: wide enough to include the values in the published evidence the team found (`assumptions/evidence.yaml`, [`docs/methods/evidence.md`](methods/evidence.md)), and wider still where there is little evidence. Take-up rates are checked against places that passed similar reforms and published permit counts, and against observed ADU rates in California (appendix F.2). The share of a cost saving that reaches the decision to build is set from the literature on who keeps a fee or cost saving. These ranges are then swung one at a time in the sensitivity analysis, so a reader can see which ones matter (section 6).

## 3. Data sources and data cleaning steps

All data is public and is downloaded by script from the publisher (`make data STATE=PA`). Nothing is stored in the repository. Each source is listed once in `src/visionhousing/data/sources.yaml`. Each download is recorded in `data/manifest.json` with a SHA-256 checksum, so a later run can show it used the same files.

### 3.1 Sources

The full table, one row per source with its publisher, version, access date, licence, cleaning steps and known limits, is generated in [`DATA_PROVENANCE.md`](../DATA_PROVENANCE.md) from the same registry the download script reads. The core sources are:

| What it gives the model | Source (publisher) |
|---|---|
| Permits by county, year and building size (the outcome and the baseline) | Building Permits Survey (U.S. Census Bureau) |
| Homes, home values, rents, incomes by tract | American Community Survey 5-year estimates (U.S. Census Bureau) |
| Tract and county outlines; housing density and a lot-size proxy | Cartographic boundary files and TIGER/Line blocks (U.S. Census Bureau) |
| Price trends | House Price Index, county and tract (Federal Housing Finance Agency) |
| Mortgage rate | Primary Mortgage Market Survey (Freddie Mac) |
| Rent benchmarks | Fair Market Rents (U.S. Department of Housing and Urban Development) |
| Construction pay | Quarterly Census of Employment and Wages, construction (U.S. Bureau of Labor Statistics) |
| Transit stops and schedules | National Transit Map (U.S. DOT, Bureau of Transportation Statistics); schedule feeds listed in the Mobility Database catalog (MobilityData) |
| Street network for walking distance | OpenStreetMap (ODbL; attribution and share-alike apply, see `DATA_PROVENANCE.md`) |
| State and local rules | Pennsylvania bills and the Municipalities Planning Code; municipal zoning ordinances, read and cited; Vermont and Massachusetts statutes |

### 3.2 Cleaning steps

| Step | What it does | Where it is explained |
|---|---|---|
| Safe download | Retries a busy server, stops on a bad request, never keeps a half-finished file, removes any key from logs | `docs/modules/data_fetch.md` |
| Permits | Parse county annual files by header name. Keep reported and imputed flags. Tidy to county by year by building size | `docs/modules/bps.md` |
| Survey tables | Check variable labels against the vintage. Keep margins of error. Handle missing-value codes | `docs/modules/acs.md` |
| Bracket shares | Turn published value and rent bracket counts into the share above a break-even, spreading units evenly inside a bracket | `docs/modules/brackets.md` |
| Blocks | Read only the attribute table of the block file. Compute housing density and a lot-size proxy per tract | `docs/modules/blocks.md` |
| Transit stops | Keep stops that meet the major-stop test. Measure distance from block centres; network walksheds for the TOD variants | `docs/modules/transit.md`, `docs/modules/transit_walk.md` |

### 3.3 Data tiers

Every number shows the tier of data behind it.

| Tier | What it means |
|---|---|
| 0 | National public data only (permits, the census survey, blocks, transit stops). Available for every state |
| 1 | Adds state-level sources: the state's own already-legal table (state law or a town-rule index), state construction pay |
| 2 | Adds local sources: verified ordinance readings, lot measurements, network walksheds, county construction pay |

The inputs and their tiers are listed in `assumptions/data_tiers.yaml`. A hide-the-data test runs Pennsylvania with the upper tiers hidden and reports how far the answer moves (appendix G.1).

### 3.4 A state with thin data: Vermont next to Pennsylvania

The method must also run where data are sparse. We ran it, unchanged, for **Vermont** (`make data STATE=VT`, then `make export-static STATE=VT`). Both states are on the static site.

Vermont is small and has much less of Pennsylvania's local data: town zoning rules read for a few towns only and not yet used in an estimate, no county parcel files, no pending bills read. It is thin even in some national files (the Bureau of Labor Statistics withholds its statewide construction pay). One state statute is read: Vermont law already makes one ADU with a single-family home, and duplexes wherever homes are allowed, a permitted use (24 V.S.A. § 4412(1)(D) and (1)(E)). Where an input was missing, the code falls back to a documented default instead of failing; each fallback, and the inputs available, used and missing in each state, are listed in appendix G.2.

<!-- generated: law_readings -->
*Generated from `assumptions/law/municipal_rules.yaml` and `assumptions/law/local_readings.yaml`.* Local zoning rules read and verified from each place's own published text (each reading cites a short quote and where it sits in the text): Pennsylvania: 174 municipalities (112 of them through county ordinances), plus 3 drafts that count for nothing; Vermont: 11 towns and cities (3 more could not be read: Brattleboro (town), St. Albans (city), Williston (town)); Wyoming: Laramie (city); Teton County (unincorporated) only in its former text (superseded). The Vermont and Wyoming readings are kept with their citations but do not enter an estimate yet.
<!-- /generated: law_readings -->

**How much wider the likely ranges are.** Width is the P10-to-P90 span divided by the middle value, so states of different size can be compared:

<!-- generated: sparse_state_comparison -->
*Generated from the default runs saved in `web/public/data/{PA,VT}/runs/` (seed 0, default draws). Width = (P90 - P10) / P50. Added units for a reform; permitted units for the baseline.*

| Run | PA likely range (units, 2027-2031) | PA width | VT likely range (units, 2027-2031) | VT width | VT width / PA width |
|---|---|---|---|---|---|
| Baseline (no change) | 71,363 to 192,239, middle 151,882 | 0.80 | 5,110 to 17,665, middle 12,243 | 1.03 | 1.29x |
| Accessory dwelling units | 3,481 to 15,671, middle 8,011 (5.3% of baseline) | 1.52 | 69 to 371, middle 173 (1.4% of baseline) | 1.74 | 1.15x |
| Missing middle | 119 to 1,886, middle 560 (0.4% of baseline) | 3.15 | 1 to 17, middle 4 (0.0% of baseline) | 3.60 | 1.14x |
| Transit-oriented density | 4 to 1,721, middle 367 (0.2% of baseline) | 4.68 | 0 to 0, middle 0 (0.0% of baseline) | - | - |
| Parking requirement reduction | 1,231 to 5,049, middle 2,633 (1.7% of baseline) | 1.45 | 34 to 166, middle 82 (0.7% of baseline) | 1.61 | 1.11x |
| Development fee reduction | 2,672 to 12,979, middle 6,433 (4.2% of baseline) | 1.60 | 183 to 1,026, middle 483 (3.9% of baseline) | 1.74 | 1.09x |
| All five together | 12,379 to 31,313, middle 20,468 (13.5% of baseline) | 0.93 | 425 to 1,450, middle 813 (6.6% of baseline) | 1.26 | 1.36x |
<!-- /generated: sparse_state_comparison -->

**What this means for a policymaker.**

- *The method travels.* Every reform ran on national files alone, and county results add up to the state total, as in Pennsylvania.
- *Most of the width comes from the assumptions* (take-up, costs, rents), which are equally uncertain in both states. A state's own *measurements* of lot fit and town rules narrow ranges more than more national data does.
- *A zero can be a finding.* Transit-oriented density adds no homes in Vermont. There are major stops (frequent buses in the Burlington area), but in the model a mid-rise building does not pay for itself at Vermont rents. The funnel on the Reform screen shows which step stops it.
- *The model sees a state's own law only once someone has read it.* With Vermont's statute read in, the Vermont ADU and missing-middle ranges count only what a new law would add where practice lags the statute, or where the reform allows more than the statute. How many towns comply has not been measured.
- *The baseline range in Vermont is widened for its small back-test*, which rests on far fewer county cases than Pennsylvania's.

**Other states.** Massachusetts (ADUs already allowed by state law) and Wyoming (no rail and no published bus schedules, so transit-oriented density cannot be measured there) are set up with the same commands and no state-only code. Their notes and saved headline results are in appendix G.3 and G.4.

## 4. Methodological approach

All model logic lives in the Python engine (`src/visionhousing/engine/`, [`docs/modules/engine.md`](modules/engine.md)). The interface only shows what the engine returns.

### 4.1 Logic flow

1. Fetch the sources and build the state data pack.
2. Project the baseline by county and building size.
3. For each reform, compute the added units by tract through channel A (new permission), channel B (lower cost), or both.
4. Remove overlap when several reforms are on.
5. Repeat steps 2 to 4 many times with inputs drawn from their ledger ranges (Monte Carlo draws, seeded so each run repeats exactly).
6. Report P10, P50 and P90 by county and state.

### 4.2 Baseline

The baseline carries each county's recent permits forward with a simple rule. Seven simple rules (averages over different windows, a median, a damped trend, two rules that borrow strength from similar counties or from the state, and their average) are back-tested on past five-year periods. **The rule for choosing the winner was written in `assumptions/backtest.yaml` before any score was computed**: among rules whose P10–P90 range caught the real total in a set share of past cases (`contest.coverage_band`), the one with the lowest typical miss wins, unless a simpler rule is within a set tolerance (`baseline.selection_tolerance`). The range around the projection comes from how far the winning rule missed in the past, using only years already observed at each past start date. Tested that way, the ranges catch the real total less often than the 80% a P10–P90 range aims for; a calibration fixed in advance (`oot_calibration`) widened them but did not reach the 75–85% band, so it was not adopted and the shortfall is stated as a limit (appendix B). The full method, including surge years, stress settings and error by kind of place, is in appendix B.

**The contest for Pennsylvania** (the chosen rule is marked):

<!-- generated: baseline_contest_pa -->
*Generated from `web/public/data/PA/validation.json`. Typical miss = median of |log(actual / projected)| over past county five-year totals; caught = share of past totals inside the P10-P90 range (built leaving the county out). The chosen rule here: `top_down_share`.*

| Rule | Past cases | Typical miss | Range caught the real total |
|---|---|---|---|
| `last_5_mean` | 1,395 | 0.333 | 50% |
| `last_3_mean` | 1,394 | 0.306 | 52% |
| `median_5` | 1,394 | 0.320 | 51% |
| `damped_trend` | 1,395 | 0.378 | 42% |
| `shrink_to_group` | 1,395 | 0.365 | 52% |
| `top_down_share` (chosen) | 1,395 | 0.342 | 54% |
| `average` | 1,395 | 0.321 | 51% |
<!-- /generated: baseline_contest_pa -->

**How often the baseline range caught the real total.** Out of time, the county ranges caught the real total well short of the 80% a P10–P90 range aims for (table below); the state range was caught more often, but at only a handful of start years per state. So the tool does not call the baseline range "likely". Every baseline range states its measured reliability: "In past years, ranges like this caught the real five-year total in N of M cases (out of time)", and its short label is "range (caught N% of past totals)". The counts come from a rule written down before they were made (`baseline_reliability` in `assumptions/backtest.yaml`): the state range is rebuilt at each past start year from the state's earlier totals only, with its widening, and county ranges from other counties' earlier errors in the same size class. They are shown for the state and for each county size group, in the baseline's formula and in the validation view. The reform ranges keep the name "likely range": they are simulation ranges, conditional on the baseline and the assumptions, with no past totals to check them against. A second calibration fixed in advance (`national_state_calibration`) built the state range from every state's earlier state-total errors, in two forms (one pool; a statewide shock plus each state's own part). One form fell below the 75–85% band and the other above it (above the band counts as wider than an 80% range should be, and the levels were not re-tuned afterwards), so neither was adopted and the state range is unchanged (appendix B).

<!-- generated: baseline_reliability -->
*Generated from `assumptions/calibration/baseline_reliability.yaml` (block `baseline_reliability`, fingerprint `3207b76a91e6`). Each cell: past cases whose real five-year total fell inside a range built the way the tool builds it, using only errors known at the time (out of time). State range: the state's own earlier totals, with the small-sample and growth-aware widening; county ranges: other counties' earlier errors in the same size class, by size class at the case's start year.*

| State | Rule | State range caught | County ranges caught | Small counties | Medium counties | Large counties |
|---|---|---|---|---|---|---|
| PA | `top_down_share` | 8 of 8 (100%) | 545 of 1,004 (54%) | 204 of 344 (59%) | 174 of 330 (53%) | 167 of 330 (51%) |
| VT | `shrink_to_group` | 8 of 8 (100%) | 103 of 210 (49%) | 37 of 75 (49%) | 33 of 75 (44%) | 33 of 60 (55%) |
| MA | `last_3_mean` | 7 of 8 (88%) | 97 of 210 (46%) | 24 of 75 (32%) | 47 of 75 (63%) | 26 of 60 (43%) |
| WY | `last_3_mean` | 8 of 8 (100%) | 205 of 340 (60%) | 66 of 115 (57%) | 73 of 120 (61%) | 66 of 105 (63%) |

*National state-total calibration (block `national_state_calibration`, fingerprint `9c74cf2d2053`), out of time over every state: `pooled` caught 283 of 408 state totals (69.4%, mean width 1.09; PA 7 of 8, VT 8 of 8, MA 6 of 8, WY 8 of 8; under_band); `shock` caught 358 of 408 state totals (87.8%, mean width 1.61; PA 8 of 8, VT 8 of 8, MA 7 of 8, WY 8 of 8; over_band). **Decision: keep_current**.*
<!-- /generated: baseline_reliability -->

**What the baseline is built from, and the 2026 gap.** The baseline reads the Building Permits Survey's estimated units in 1-, 2-, 3-4 and 5+ unit buildings from the final 2025 annual files. Only data released by the model's as-of date count. The last full year is 2025 and the projected years are 2027-2031, so 2026 has no full-year count: the rule's yearly level is carried flat through it and adds nothing to the total. The table checks each state's middle value against the state's own yearly totals. Pennsylvania's middle value leans on its high 2021 total, so a four-year window (2022-2025) is shown as a disclosed sensitivity. It was named after the spike was seen, so it is not a pre-registered test and not a replacement baseline, and it is never subtracted from any reform. A nowcast of 2026 from the monthly year-to-date files posted by the cutoff is shown beside the carried level as a check; no estimate uses it (`docs/modules/baseline.md`, "Baseline card").

<!-- generated: baseline_reconciliation -->
*Generated from `assumptions/calibration/baseline_reconciliation.yaml` (command `visionhousing baseline-card`, settings `assumptions/baseline_card.yaml`). Units: Estimated units in 1-, 2-, 3-4 and 5+ unit buildings (Building Permits Survey) (columns `est_1_units`, `est_2_units`, `est_3-4_units`, `est_5+_units`). Data release: 2025 annual files (final), posted 2026-05-14 (Last-Modified date of the state annual file). Information cutoff: 2026-11-24. Projected years: 2027-2031; forecast gap: 2026 (no full-year count; the rule's yearly level is carried through it).*

| State | 2021 | 2022 | 2023 | 2024 | 2025 | Shipped rule | Level years | State check |
|---|---|---|---|---|---|---|---|---|
| PA | 47,894 | 28,149 | 25,320 | 24,810 | 25,709 | `top_down_share` | 2021-2025 | **151,882** = state component = 5 x mean of 2021-2025 state units = 5 x (47,894 + 28,149 + 25,320 + 24,810 + 25,709) / 5 = 151,882 units (rule top_down_share, state series only; county shares do not change the sum) |
| MA | 19,853 | 16,830 | 13,214 | 14,338 | 12,477 | `last_3_mean` | 2023-2025 | **66,715** = state component = 5 x mean of 2023-2025 state units = 5 x (13,214 + 14,338 + 12,477) / 3 = 66,715 units (rule last_3_mean, state series only; county shares do not change the sum) |
| WY | 2,706 | 3,005 | 1,681 | 2,135 | 2,238 | `last_3_mean` | 2023-2025 | **10,090** = state component = 5 x mean of 2023-2025 state units = 5 x (1,681 + 2,135 + 2,238) / 3 = 10,090 units (rule last_3_mean, state series only; county shares do not change the sum) |
| VT | 2,319 | 2,403 | 2,456 | 2,654 | 2,294 | `shrink_to_group` | 2021-2025 | outside control only (county shrinkage; not reproducible from state totals) |

**Window sensitivity `baseline_pa_window_2022_2025`** (PA, record only): 5 x (28,149 + 25,320 + 24,810 + 25,709) / 4 = 5 x 25,997 = 129,985 units; difference from the shipped state component 151,882 = -21,897 units (-14.4%). Disclosed historical-window sensitivity. Named after the spike was seen, so not pre-registered. Not a replacement baseline; never subtracted from any reform or package result.

**2026 bridge, nowcast `baseline_bridge_nowcast_2026`** (diagnostic only; no estimate uses it). Latest monthly file released by the cutoff: `st2609y.txt` (posted 2026-10-27); the next file, `st2610y.txt`, was posted 2026-11-25 and is not used. Nowcast = year-to-date units / s, s = the same months' share of the full year in 2021-2025 (low, middle and high use the highest, median and lowest s). A year-to-date count is never a full year.

| State | Units Jan-Sep 2026 | Nowcast low-high | Nowcast middle | Carried yearly level |
|---|---|---|---|---|
| PA | 19,096 | 25,228-32,110 | 26,998 | 30,376 |
| MA | 11,156 | 13,255-16,690 | 15,097 | 13,343 |
| WY | 1,354 | 1,804-1,939 | 1,833 | 2,018 |
| VT | 1,210 | 1,380-1,811 | 1,651 | 2,425 (state-series mean) |
<!-- /generated: baseline_reconciliation -->

### 4.3 Channel A: new permission

For reforms that make a new housing form legal:

```
added units (tract) = E   eligible sites
                    x f   share that physically fit
                    x p   share that are financially feasible
                    x h   yearly take-up among feasible sites
                    x ramp over years 1 to 5
                    x n   net new units per project
                    x (1 - x)  share not taken from the baseline
```

- `p` is a share, not a yes or no. The tract's published value and rent brackets are compared with the pro forma's break-even (the price or rent at which a project covers its cost).
- `h` is the main behavioural input and the main source of uncertainty. Its ledger range is set from places that passed a similar reform and published permit counts, and checked against observed ADU rates in California (appendix F.2).
- Where a project replaces a home (missing middle, transit-oriented density), the headline counts every new unit as a permit, minus the permits the baseline would have issued on the same site anyway. Net added homes also subtract the homes torn down. Both are reported.
- The ramp splits the five years: year value = total × ramp(t) / Σ ramp, so the years add up to the total in every draw.

**Which law counts, and from when.** `E` starts from the lots where the reform changes the rule in force (docs/modules/law.md). Four rules keep that honest. (1) A law counts from its effective date, never its adoption or publication date; with no effective date stated, a reading counts only from the day a person saw it posted as the law. Pittsburgh's Ordinance 31 of 2026, for example, was passed on October 27 and took effect on October 29, 2026. (2) An ordinance that does not list ADUs is not read as prohibiting them unless its own clause says unlisted uses are barred; with no such clause read, the answer is unknown and those lots take the statewide range. (3) **Current law in both arms**: a change already enacted with a later effective date applies from that date in the baseline and the reform alike. Vermont's 24 V.S.A. § 4412(1)(D) is one: from January 1, 2028 the four-unit right on lots with municipal water and sewer does not apply in districts that require more than four units. A pending bill never enters the baseline. Holding today's rules fixed is shown only as a separately named comparison, "freeze today's rules". (4) A reform counts only the designs it newly allows: where a district already allows four units, a six-unit rule adds the five- and six-unit options, not the whole site.

### 4.4 Channel B: lower cost

For reforms that cut the cost of housing that is already legal:

```
added units (county) = baseline x elasticity x (cost saved / value per unit) x k
```

The elasticity is the supply response to a change in profit per unit. `k` is the share of the saving that reaches the building decision rather than raising land prices. A cross-check computes the same effect through the pro forma; both are reported side by side.

### 4.5 Scenario definitions

| Reform | Eligible sites | Fit | Feasible | Take-up and net units | Channel |
|---|---|---|---|---|---|
| ADUs | Single-family detached homes, plus attached homes at a lower fit, minus places where ADUs are already allowed | Lots with room for the modelled ADU | Homeowner pro forma, plus a non-financial share (family use) | Calibrated on places with published ADU permits. One unit each | A |
| Missing middle | Single-family lots in single-family-only zones | Lot large enough for the building | Value of 2 to 6 new units vs. the tract's home values | Yearly redevelopment rate among feasible lots. Net of the home removed | A |
| Transit-oriented density | Land and homes near a major stop | Developable share | Mid-rise pro forma at the higher floor area with less parking | Bounded by regional multifamily absorption | A and B |
| Parking reduction | Baseline multifamily and attached production | Not used | Cost saved per space not built | Supply response to the saving | B |
| Fee reduction | All baseline production | Not used | Half of the $50,000 fee saved per unit | Supply response. Who keeps the saving is the key input | B |

Where no zoning has been read, "restricted to single-family use" is a judgement share (`missing_middle.sf_only_zoned_share`, section 2.3); in counties whose municipal rules are read, lots count by their legal status.

Pennsylvania bills:

- **HB 2186.** One ADU by right on detached, attached and 2 to 4 unit lots, statewide.
- **SB 1346 variant.** Single-family detached lots only. Excludes Philadelphia and Pittsburgh (outside the Municipalities Planning Code). Utility connection fee set to zero.

The five benchmark presets stay exactly as the Handbook defines them. Beside them sit **refinements** (better measurement of the same reform, for example ADU lots counted by legal status where town rules are read) and **named variants** that each answer one question on the same draws (for example tight or easy homeowner financing for ADUs, the most profitable building size for missing middle, "no response" for parking, fee deferral instead of a cut, and network walking distances for TOD). Details: appendix C.2; methods: [ADU](methods/adu.md), [missing middle](methods/missing_middle.md), [parking](methods/parking.md), [fees](methods/fees.md), [TOD](methods/tod.md).

### 4.6 Packages and overlap

- A lot is used once. ADUs and missing middle compete for the same lots.
- Lots near transit are counted under the denser reform.
- Parking and fee savings enter the pro formas of the other reforms.
- The result is shown as: sum of parts, minus overlap, plus interaction, equals combined (appendix D.1).

### 4.7 Uncertainty

- Every input has a low, central and high value and a shape.
- Baseline and reform effect are drawn together. In each draw a reform's added homes are "with the reform" minus "without it" in the same simulated future, so the large shared uncertainty about the future level of building cancels in the difference.
- Results are shown as P10, P50 and P90, in words: "likely between ... and ...". The same range is also written as a frequency: "in eight of ten simulated futures, between X and Y homes".
- The fee cut is the only benchmark reform with a direct public cost, and it applies to every permitted home, not only the extra ones. The tool reports the revenue given up and the cost per added home (appendix D.2).

### 4.8 Headline results

**Pennsylvania, default run** (Vermont: section 3.4; year-by-year split: appendix E):

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

### 4.9 Calibration and validation, in brief

The engine never trains anything at run time. Each validation job writes a small reviewed file that the engine reads or shows beside the results; where a job's rule was written down before its result was seen, the file says so. Built: the baseline rule contest and its error by kind of place; small-state baseline ranges; the ADU physical-fit table (Pennsylvania lots, borrowed and widened elsewhere); approval timing and drop-out from California's Annual Progress Reports; Philadelphia permit timing; before-and-after permits in cities that legalised 2–4 unit homes; checked records of other states' laws and studies; and the hide-the-data test. None of these is a causal estimate of a Pennsylvania reform. They bound the assumptions and show where the model's ranges are plausible. Details: appendix F. Results can also be read by state House and Senate district (appendix F.3).

**Which exercise a count refers to.** Coverage counts quoted on different screens ("caught 8 of 8", "10 of 11", "6 of 11", "545 of 1,004") come from different studies, start years and geographies. Each has a validation card naming its study, start years, target (five-year total of units authorized), geography and data release, with an interval score that penalises both misses and width, the signed error, error by size, the count on start years five years apart (overlapping windows share four of five years, so 8 overlapping windows are about 2 independent checks) and a timeline of each fold (`docs/modules/validation_cards.md`; the validation screen's data carry the cards). Two things make these counts somewhat favourable and are disclosed on every card: each rule was chosen on the full record, which includes the years it is scored on, and the rule contest describes counties with today's place types.

**Past reforms: which law, which year.** The before-and-after panel was audited for its treatment dates (`docs/modules/evidence_retro.md`, "Treatment-date audit"). Saint Paul is now scored from the ordinance that allowed duplexes to fourplexes (effective 2023-11-26, scored from 2024), not its earlier small-homes phase; Portland stays scored from 2022 under the published convention (a rule starting after June 30 counts from the next year). Every case's interval still includes zero under every date convention tried: no change in 2-4 unit permits could be told apart from ordinary cities.

## 5. Limitations and known edge cases

- **Permits are not homes.** Some permitted units are never built. Completion lags permits.
- **Five years only.** Effects after 2031 are not modelled.
- **The 2026 gap.** The last full year of permit data is 2025. 2026 has no full-year count by the as-of date, so the baseline carries its 2025 rule level through 2026; a monthly nowcast is shown as a check only. A year-to-date count is never treated as a full year.
- **No parcel answers.** Results are for tracts, counties and the state.
- **Take-up is uncertain.** Few places publish clean before-and-after counts. The range is wide on purpose.
- **Compliance and approval time.** The model assumes local governments apply the state rule. It cannot model resistance. Approval delay is illustrative only: no Pennsylvania source publishes application dates, so the timing ranges come from Philadelphia's zoning-to-building stage and from California, and are labelled as borrowed.
- **ADUs and permit statistics.** Some ADUs are permitted as additions or conversions and never appear in the Building Permits Survey (section 1).
- **Borrowed calibrations.** Ranges measured in Pennsylvania (ADU lot fit) are borrowed, widened, by other states; timing ranges are borrowed from California. Each is labelled where it is used.
- **Philadelphia.** The baseline's own back-test is far off in Philadelphia, partly because of a surge of permits before a tax-abatement change, so its range is widened from its own errors. Treat Philadelphia's numbers as the least certain county result.
- **Districts.** District results are split from tracts and counties by 2020 home counts. Districts flagged as split rest on that split more than on their own data.
- **Water, sewer and hazards.** The tool screens where public sewer service and flood zones are recorded, as context. It does not limit any estimate by them.
- **Who benefits.** The model cannot say who lives in the new units or what they pay.
- **Labour and materials.** Construction capacity limits are not modelled.
- **Transit areas.** Some added units near transit may move from elsewhere in the region rather than add to the state total. In Massachusetts the TOD reform counts, on land that state-required multi-family (Section 3A) districts already zone, only the capacity above what the district allows (the parcels' modeled capacity); which part of that land lies inside the walk distance is worked out from areas, not outlines, and towns not yet in the compliance atlas are not resolved by tract.
- **Validation counts are few and overlapping.** Past coverage counts rest on start years one year apart whose five-year windows overlap, all in one housing cycle; the rule being scored was chosen on the full record, and the rule contest uses today's county place types rather than those known at each start year. Each count's validation card says so and shows the smaller non-overlapping count.
- **Past reforms are evidence, not effects for Pennsylvania.** Some reform dates could not be re-read on an official page by script (Raleigh, Arlington County; Spokane to the month only). The panel measures city-wide rates, not the lots a rule actually changed.
- **Survey error.** Survey estimates have margins of error. Small tracts are noisy.
- **Edge cases.** Tracts with no homes give "no data", not zero. Open top brackets give a lower bound. Counties with imputed permit counts are flagged. In a state with no published bus schedules, a zero for transit-oriented density means "not measurable", not a true zero. "No data" and "not modelled" are shown in grey and never count as a result.

## 6. Sensitivity notes

Sensitivity results come from the engine (`docs/modules/sensitivity.md`; methods in appendix F.4).

- **Tornado.** Each input is swung from its low to its high value with the others held central. Inputs are ranked by how much the state total moves.
- **Joint check.** Rank correlation between sampled inputs and the result confirms the order when inputs move together.
- **What would narrow the range.** For each input, the share of the spread in the result that would go away if that input were known exactly, computed from the run's own draws. The tool turns the top inputs into a short research agenda: "Knowing X would shrink the range by about Y%".
- **Policy dials.** Each reform at a few levels on the same seed; the benchmark level is the headline exactly. Readouts: "each step adds about X homes", the level where returns fall off, and "which level reaches N homes".
- **Ranking stability.** The top inputs per reform are pinned at their ends; the tool reports whether the order of the reforms changes and which reforms have overlapping likely ranges.
- **Choices the user can change.** Cycle strength, transit distance, fee view, and each assumption's low, central or high value.

**Top three inputs per reform, Pennsylvania:**

<!-- generated: top_drivers_pa -->
*Generated from the tornado saved with each reform's run in `web/public/data/PA/runs/`. Each input is moved alone from its low to its high value (Assumption Ledger) while the others stay central; the figures are the five-year homes the reform adds at each end.*

| Reform | Input | Added homes at its low value | At its high value |
|---|---|---|---|
| Accessory dwelling units | Share of feasible lots that add an ADU each year (`adu.takeup_feasible_annual`) | 2,595 at 0.003 | 21,629 at 0.025 |
| Accessory dwelling units | Construction cost of a new detached ADU per square foot (`adu.hard_cost_per_sqft`) | 16,807 at 175 | 4,522 at 375 |
| Accessory dwelling units | Share of fitting lots where an owner builds for family use regardless of rent (`adu.nonfinancial_share`) | 5,979 at 0.01 | 15,333 at 0.15 |
| Missing middle | Share of feasible lots redeveloped each year (`missing_middle.redevelopment_hazard_annual`) | 97 at 0.0005 | 1,946 at 0.01 |
| Missing middle | Capitalisation rate used to value the finished building (`missing_middle.cap_rate`) | 1,484 at 0.05 | 139 at 0.075 |
| Missing middle | Rent premium of a new 2-6 unit building's homes over the census two-bedroom median (`missing_middle.new_unit_rent_premium`) | 200 at 1.05 | 1,484 at 1.5 |
| Transit-oriented density | Capitalisation rate used to value a finished mid-rise building (`multifamily.cap_rate`) | 1,619 at 0.045 | 1 at 0.07 |
| Transit-oriented density | Construction cost per square foot, mid-rise apartment building (`multifamily.hard_cost_per_sqft`) | 1,428 at 230 | 4 at 400 |
| Transit-oriented density | Floor area per mid-rise apartment (`multifamily.unit_size_sqft`) | 1,463 at 700 | 192 at 1e+03 |
| Parking requirement reduction | Medium-run supply response to a change in profit per unit (`supply.elasticity_medium_run`) | 1,424 at 0.3 | 4,353 at 2 |
| Parking requirement reduction | Extra units a small site can hold when parking is not required (`parking.site_yield_gain`) | 1,723 at 0 | 4,445 at 0.15 |
| Parking requirement reduction | Share of a cost saving that reaches the decision to build (`supply.saving_passthrough_k`) | 1,597 at 0.2 | 4,009 at 0.9 |
| Development fee reduction | Share of a fee cut that reaches the decision to build (`fees.passthrough_k`) | 736 at 0.05 | 14,720 at 1 |
| Development fee reduction | Medium-run supply response to a change in profit per unit (`supply.elasticity_medium_run`) | 2,208 at 0.3 | 14,720 at 2 |
| Development fee reduction | Baseline housing cycle (permits at the low or high end of their likely range) (`engine.baseline_cycle_position`) | 3,458 at 0.47 | 9,315 at 1.27 |
<!-- /generated: top_drivers_pa -->

What changes when an input changes, in short: for ADUs, take-up and construction cost dominate; for missing middle and transit-oriented density, whether a new building is worth more than it costs (cost per square foot, and the capitalisation rate: the yearly yield investors ask of a finished building); for parking and fees, how strongly builders respond to a saving and how much of the saving reaches them rather than raising land prices. The table above gives the size of each swing.

## 7. Intended users and appropriate use cases

| User | Appropriate use | Not appropriate |
|---|---|---|
| State legislator or staffer | Compare reforms by likely range and read the caveats | Quoting the middle value as a promise |
| State housing agency analyst | Change assumptions, see which matter, download a full run | Budget or revenue forecasting |
| County or municipal planner | See the county range and the data tier behind it | Parcel or site decisions |
| Economist or reviewer | Check the evidence, back-tests and interval coverage | Treating the model as causal proof |
| Advocate or journalist | See which reform does the most, and why | Claims about rents, prices or who moves in |

**Good use:** comparing reforms against each other and against the baseline; finding which assumptions matter and what evidence would narrow them; checking how a proposed bill differs from the benchmark reform; seeing which counties carry most of a reform's effect, and how sure the tool is there.

**Poor use:** predicting an exact number of homes; any decision about a single property; reading a county with flagged data (imputed permits, borrowed calibrations) as precisely as a state total; applying the results after 2031 or under economic conditions very different from those the run holds constant.

**Using it in another state.** Run `make data STATE=XX` and `make export-static STATE=XX`. The tool shows, for every input, whether the state's own data was used or a fallback, and widens ranges where it falls back. Read the state's report card before quoting a number.
