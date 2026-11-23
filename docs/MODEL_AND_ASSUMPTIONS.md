# Model and Assumptions Overview

> **Status.** This is the outline of the required overview. It describes the method as designed. Parts of the engine are not built yet and are marked **Planned**. Every number in this document will be generated from an engine run or from the assumption ledger. Places for those numbers are marked `<!-- generated: ... -->`. No result is typed by hand.

Length limit: 25 pages, single-spaced.

---

## 1. Purpose and scope

**Purpose.** Show state policymakers how a housing-policy reform is likely to change housing production over five years, compared with what happens if nothing changes. Show the range, what drives it, how sensitive it is, and what the tool cannot say.

**Outcome measure.** Housing units authorized by building permits, summed over the calendar years 2027 to 2031, for one state.

Why permits:

- They are published every year for every county in the country, by one agency, with one definition (the Census Bureau's Building Permits Survey).
- They go back to 1990, so the baseline can be tested against history.
- They respond to policy faster than completions do, so a five-year window can see the effect.
- Limit: a permit is not a finished home. Some permitted units are never built, and some are finished after 2031.

**Geography.** The model computes by census tract and reports by county and by state. It gives no result for a single parcel or address.

**States.** Pennsylvania in depth. The method is built to run for every state, with wider ranges where data is thinner (see data tiers in section 3).

**Scenarios in scope.**

| Scenario | Status |
|---|---|
| Baseline (current rules and economics) | **Planned** |
| 1 · Accessory dwelling units (ADUs) | **Planned** |
| 2 · Missing middle (2 to 6 units) | **Planned** |
| 3 · Transit-oriented density | **Planned** |
| 4 · Parking reduction | **Planned** |
| 5 · Development fee reduction | **Planned** |
| Pennsylvania HB 2186 | **Planned** |
| Pennsylvania SB 1346 variant | **Planned** |

Scenario definitions live in `assumptions/scenarios.yaml`.

**Timing.** Reforms take effect on January 1, 2027. Take-up ramps up over the five years.

**What the tool is not.** It is not a forecast and not an official state document. It is decision support.

## 2. Core assumptions and their rationale

Every number the model needs but cannot read from data lives in one file, `assumptions/parameters.yaml`. No such number is written in code. Each entry has a central value, a low and a high value, a distribution shape, a kind (sourced, calibrated, benchmark or judgement), a source, a rationale, and a note on what evidence would change it. See `docs/modules/ledger.md`.

### 2.1 Benchmark assumptions set by the Challenge

| Reform | Assumption |
|---|---|
| ADUs | One ADU on parcels with an existing single-family building. Detached and semi-detached ADUs of at least 600 sq ft allowed. No additional parking |
| Missing middle | 2 to 6 units on parcels now restricted to single-family use. Up to three storeys. Setbacks and lot coverage non-binding |
| Transit-oriented density | More height or floor area near major transit stops. Parking reduced |
| Parking reduction | Residential parking minimums reduced or removed |
| Fee reduction | Fees total $50,000 per unit and are cut by 50% |

### 2.2 Choices the team made

| Choice | Value | Why |
|---|---|---|
| Major transit stop | A fixed-guideway station, or a bus stop with at least 64 weekday departures | Separates frequent service from ordinary bus stops with a published, countable test |
| Distance to a major stop | Half a mile for rail, a quarter mile for frequent bus, straight-line. Selectable: a quarter, a half, three quarters of a mile | Common walking distances. The selector shows how much the answer depends on it |
| Parking still built after reform | About 60% of formerly required spaces in transit-rich tracts, 85% to 100% elsewhere, shown as a range | Lenders and buyers still ask for parking, most of all where transit is weak |
| Fee view | The Challenge's $50,000 per unit, plus a second labelled view with documented Pennsylvania fee levels | Keeps the benchmark comparable and adds a local reality check |
| Modelled ADU size | 600 sq ft in every scenario | The benchmark minimum. Keeps scenarios comparable |

### 2.3 Assumption tables (generated)

<!-- generated: assumption_table_sourced -->
| Id | Label | Central | Low | High | Unit | Source | Status |
|---|---|---|---|---|---|---|---|
| `adu.added_parking_spaces` | Added parking spaces required for an ADU | 0 | 0 | 0 | spaces/unit | [handbook_benchmarks](https://housingchallenge.turnout.rocks/handbook.html) | sourced |
| `adu.evidence_massachusetts_first_year` | ADUs approved in Massachusetts in the first year of statewide by-right ADUs | 1,224 | 1,224 | 1,224 | units | [massachusetts_adu_first_year](https://mass.gov/news/governor-healey-announces-more-than-1200-adus-approved-in-first-year-with-more-to-come) | sourced |
| `adu.evidence_permits_per_100k_permissive_all` | ADU permits per 100,000 residents in 2025, permissive U.S. cities | 81 | 81 | 81 | permits per 100k people per year | [pew_adu_2026](https://www.pew.org/research-and-analysis/reports/2026/10/accessory-dwelling-units-can-meaningfully-boost-housing-supply) | sourced | <!-- secret-scan: allow (public web address) -->
| `adu.evidence_permits_per_100k_permissive_outside_ca` | ADU permits per 100,000 residents in 2025, permissive cities outside California | 26 | 26 | 26 | permits per 100k people per year | [pew_adu_2026](https://www.pew.org/research-and-analysis/reports/2026/10/accessory-dwelling-units-can-meaningfully-boost-housing-supply) | sourced | <!-- secret-scan: allow (public web address) -->
| `adu.evidence_seattle_permits_2023` | ADUs permitted in Seattle in 2023 | 987 | 987 | 987 | permits per year | [seattle_adu_report_2023](https://www.seattle.gov/documents/Departments/OPCD/OngoingInitiatives/EncouragingBackyardCottages/OPCD-ADU-Report-2023.pdf) | sourced | <!-- secret-scan: allow (public web address) -->
| `adu.evidence_seattle_permits_prereform_avg` | Average yearly ADUs permitted in Seattle, 2016-2019 (before reform) | 263 | 263 | 263 | permits per year | [seattle_adu_report_2023](https://www.seattle.gov/documents/Departments/OPCD/OngoingInitiatives/EncouragingBackyardCottages/OPCD-ADU-Report-2023.pdf) | sourced | <!-- secret-scan: allow (public web address) -->
| `adu.min_size_sqft` | Smallest ADU the benchmark allows | 600 | 600 | 600 | sq ft | [handbook_benchmarks](https://housingchallenge.turnout.rocks/handbook.html) | sourced |
| `adu.rent_ratio_1br` | One-bedroom rent as a share of the all-unit median rent | 0.77 | 0.7 | 0.86 | ratio | [acs_2024_5yr_b25031](https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/acsdt5y2024-b25031.dat) | sourced | <!-- secret-scan: allow (public web address) -->
| `adu.units_per_lot` | ADUs allowed per lot | 1 | 1 | 1 | units/lot | [handbook_benchmarks](https://housingchallenge.turnout.rocks/handbook.html) | sourced |
| `baseline.horizon_years` | Years in the projection horizon | 5 | 5 | 5 | years | [handbook_benchmarks](https://housingchallenge.turnout.rocks/handbook.html) | sourced |
| `econ.mortgage_rate_30y` | 30-year fixed mortgage rate | 0.066 | 0.055 | 0.0775 | share per year | [freddie_mac_pmms](https://www.freddiemac.com/pmms/docs/PMMS_history.csv) | sourced |
| `fees.fee_cut_share` | Cut applied to every fee category | 0.5 | 0.5 | 0.5 | share | [handbook_benchmarks](https://housingchallenge.turnout.rocks/handbook.html) | sourced |
| `fees.fee_total_per_unit` | Development fees per unit, all major categories | 50,000 | 50,000 | 50,000 | $/unit | [handbook_benchmarks](https://housingchallenge.turnout.rocks/handbook.html) | sourced |
| `missing_middle.max_storeys` | Tallest building the reform allows | 3 | 3 | 3 | storeys | [handbook_benchmarks](https://housingchallenge.turnout.rocks/handbook.html) | sourced |
| `missing_middle.max_units` | Most units the reform allows on a lot | 6 | 6 | 6 | units/lot | [handbook_benchmarks](https://housingchallenge.turnout.rocks/handbook.html) | sourced |
| `missing_middle.min_units` | Fewest units the reform allows on a lot | 2 | 2 | 2 | units/lot | [handbook_benchmarks](https://housingchallenge.turnout.rocks/handbook.html) | sourced |
| `parking.cost_per_space_structured` | Cost of one space in an above-ground parking structure | 52,000 | 29,000 | 99,000 | $/space | [ucla_its_parking_2026](https://www.its.ucla.edu/2026/02/19/no-such-thing-as-free-parking) | sourced | <!-- secret-scan: allow (public web address) -->
| `parking.cost_per_space_surface` | Cost of one surface parking space, land and construction | 8,000 | 4,000 | 15,000 | $/space | [vtpi_parking_costs_2022](https://www.vtpi.org/tca/tca0504.pdf) | sourced |
| `parking.cost_per_space_underground` | Cost of one underground parking space | 73,000 | 40,000 | 111,000 | $/space | [ucla_its_parking_2026](https://www.its.ucla.edu/2026/02/19/no-such-thing-as-free-parking) | sourced | <!-- secret-scan: allow (public web address) -->
| `supply.evidence_saiz_philadelphia` | Metro housing supply elasticity, Philadelphia PA-NJ (Saiz 2010) | 1.65 | 1.65 | 1.65 | ratio | [saiz_2010_qje](https://doi.org/10.1162/qjec.2010.125.3.1253) | sourced |
| `supply.evidence_saiz_pittsburgh` | Metro housing supply elasticity, Pittsburgh PA (Saiz 2010) | 1.2 | 1.2 | 1.2 | ratio | [saiz_2010_qje](https://doi.org/10.1162/qjec.2010.125.3.1253) | sourced |
| `supply.evidence_tract_unit_elasticity` | Average neighbourhood housing-unit supply elasticity (Baum-Snow and Han 2024) | 0.3 | 0.3 | 0.3 | ratio | [baum_snow_han_2024_jpe](https://ideas.repec.org/a/ucp/jpolec/doi10.1086-728110.html) | sourced |
<!-- /generated: assumption_table_sourced -->

<!-- generated: assumption_table_judgement -->
| Id | Label | Central | Low | High | Unit | Rationale | Revisit |
|---|---|---|---|---|---|---|---|
| `adu.already_legal_share` | Share of eligible lots where an ADU is already effectively legal | 0.1 | 0.05 | 0.25 | share | The default for any state without its own town-rule index; Pennsylvania replaces it from assumptions/calibration/adu_legal_share.yaml. Lots where ADUs are already allowed by right add nothing under the reform. In states without an ADU preemption law, 25 of 196 cities of 100,000 or more (13%) had permissive ADU rules in April 2026, and smaller places tend to be more restrictive, so 10% is the central value. The range runs from 5% to 25%, because a state with a strong preemption law has most lots already legal and needs its own value. | For each new state, check whether it has a state ADU law (22 states by August 2026) and set a state value; replace with a town-rule index where one is built. |
| `adu.cash_return` | Yearly return the owner's own cash could earn elsewhere | 0.05 | 0.03 | 0.08 | share per year | The part of an ADU not paid by a loan is the owner's cash, which could have stayed in savings or investments. Its cost is spread over the loan term like a loan at this rate. Between a savings rate and a stock return. | Compare with published savings and Treasury yields when the data pack is rebuilt. |
| `adu.displacement_share` | Share of ADUs that replace a home that would have been built anyway | 0.1 | 0 | 0.3 | share | Some ADUs are built by builders who would otherwise have built something else on the lot, or replace legal conversions already counted in the baseline; they are not net new. | Estimate from permit data before and after reform in hindcast places. |
| `adu.fit_range_position` | Position inside the calibrated ADU fit range | 0 | -1 | 1 | factor | The physical-fit share comes from the calibration table assumptions/calibration/adu_fit.yaml, which gives a central, low and high share for each building type and area type. Each draw picks one position: -1 puts every cell at its low, 0 at its central value, +1 at its high. All cells move together, so errors do not cancel across tracts. | Replace by the fit-transfer error band once job T3 is run (P5-04). |
| `adu.fit_share_sf_attached` | Share of semi-detached and row lots with room for the ADU | 0.15 | 0.02 | 0.35 | share | Measured between about 0.02 (rows) and 0.29 (semi-detached) in two cities; attached lots rarely have the yard space. | Becomes calibrated from the physical-fit table (D-19, job T3). |
| `adu.fit_share_sf_detached` | Share of single-family detached lots with room for the ADU | 0.55 | 0.35 | 0.9 | share | Measured near 0.54 for detached homes in two Pennsylvania cities with 4-foot setbacks. Suburban and rural lots are larger, so the high end is well above the city value. | Becomes calibrated from the physical-fit table (D-19, job T3). |
| `adu.fit_share_two_to_four` | Share of two-to-four-unit lots with room for an ADU | 0.1 | 0.02 | 0.4 | share | Measured between about 0.09 and 0.40 in two cities; only HB 2186 reaches these lots. | Becomes calibrated from the physical-fit table (D-19, job T3). |
| `adu.hard_cost_per_sqft` | Construction cost of a new detached ADU per square foot | 250 | 175 | 375 | $/sq ft | No free public survey of ADU costs in Pennsylvania was found. Published owner surveys from California report medians near $250 per square foot; the range is wide and skewed up for small-unit cost premiums and 2026 prices. | Look for a free public cost source (state housing agency, census construction cost data, or a Pennsylvania builder survey). |
| `adu.homes_per_building_three_four` | Average homes per building in the census three-or-four-unit class | 3.5 | 3 | 4 | units/lot | The census class "3 or 4" counts homes, not buildings. Dividing by the average size turns homes into lots; 3.5 is the midpoint of the class. | Check the three-to-four split in Tier 2 parcel counties (P5-03). |
| `adu.homes_per_building_two_family` | Homes per building in the census two-unit class | 2 | 2 | 2 | units/lot | The census units-in-structure class "2" counts homes in two-unit buildings, so dividing by 2 gives the number of such lots. | None expected; this follows the class definition. |
| `adu.loan_rate_spread` | ADU loan rate above the 30-year mortgage rate | 0.0075 | 0 | 0.02 | share per year | Home-equity and construction loans usually cost somewhat more than a first mortgage. | Source a published home-equity rate series. |
| `adu.loan_share` | Share of ADU cost financed with a loan | 0.8 | 0.5 | 1 | share | Most owners borrow part of the cost (in Seattle about 62% used a loan for some of it); some pay cash. | Cite owner survey results once read from the primary reports. |
| `adu.loan_term_years` | Loan term for ADU financing | 20 | 15 | 30 | years | Home-equity lines run about 20 years; construction-to-permanent loans up to 30. | Check typical home-equity and renovation loan terms. |
| `adu.lot_proxy_adjustment` | Correction from the census building count to real lots | 0.9 | 0.85 | 0.95 | ratio | Counting lots from census units-in-structure tables overstates real lots by about 9-13% in two cities where both were measured; this scales it down. | Recompute from Tier 2 parcel counties (P5-03). |
| `adu.new_unit_rent_premium` | Rent premium of a new unit over the census median | 1.1 | 1 | 1.3 | ratio | Census medians include long-time tenants with older, cheaper leases, so a newly built unit usually rents above them. | Compare with HUD Fair Market Rents for recent movers (P1-08). |
| `adu.nonfinancial_share` | Share of fitting lots where an owner builds for family use regardless of rent | 0.05 | 0.01 | 0.15 | share | Some ADUs house relatives or the owners themselves, so they are built even where rent would not cover cost. This is added to the financially feasible share. | Cite owner survey shares of family use (Oregon, California). |
| `adu.operating_cost_share` | Vacancy, upkeep, insurance and property tax as a share of rent | 0.3 | 0.2 | 0.4 | share | A common rule of thumb for small rentals is that 25-35% of gross rent goes to running costs. | Build from parts: rental vacancy, property tax rate, insurance, upkeep. |
| `adu.permit_fee` | Building and zoning permit fees for one ADU | 2,500 | 500 | 8,000 | $/unit | Permit fees for a small unit are usually a small fixed fee plus a share of construction value. | Sample published fee schedules of Pennsylvania municipalities. |
| `adu.pittsburgh_share_of_allegheny_lots` | Share of Allegheny County's eligible lots inside the City of Pittsburgh | 0.2 | 0.15 | 0.25 | share | FLAGGED STUB. SB 1346 leaves Pittsburgh out, but Pittsburgh is a city inside Allegheny County. Until the tract-to-municipality split is built, this share of every Allegheny tract's eligible lots is removed. Pittsburgh holds about 24% of the county's 2020 residents (302,971 of 1,250,578) but fewer of its detached homes, so the central value is lower. | Replace with the exact tract-by-municipality split from the Census block assignment file (P4-08). |
| `adu.ramp_first_year_share` | First-year ADU permits as a share of the steady yearly rate | 0.5 | 0.3 | 0.8 | share | Permits climb for a few years after a reform; Seattle's first full year was about half its later plateau. | Fit to published post-reform permit paths (job T4). |
| `adu.ramp_years_to_full` | Years for ADU permits to reach the steady yearly rate | 3 | 2 | 5 | years | Seattle reached its plateau about three years after its 2019 reform. | Fit to published post-reform permit paths (job T4). |
| `adu.required_coverage` | Net rent needed per dollar of annual cost to build | 1 | 0.9 | 1.25 | ratio | An owner builds for rent when net rent covers the yearly cost of the loan (and the return the cash could earn elsewhere). 1.0 is break-even. | Calibrate jointly with take-up (job T2). |
| `adu.site_utility_cost` | Site work and utility runs for one ADU | 15,000 | 5,000 | 40,000 | $/unit | Trenching, sewer and water lines and electrical service; the high end covers septic upgrades where there is no public sewer. | Source from public ADU cost guides; split sewered and septic lots. |
| `adu.soft_cost_share` | Design, permits and contingency as a share of construction | 0.2 | 0.12 | 0.3 | share | Design, permit work and contingency typically add a fifth to construction cost on small projects. | Source from public ADU cost guides or owner surveys. |
| `adu.takeup_feasible_annual` | Share of feasible lots that add an ADU each year | 0.01 | 0.003 | 0.025 | share per year | The main behavioural parameter. About 1% a year of lots where an ADU fits and pays is consistent with the order of magnitude seen after reform: Seattle's permits rose to about 3.5 times the pre-reform rate (about 910 a year in 2022-2024), Massachusetts approved 1,224 ADUs in its first by-right year and 2,084 in eighteen months, and permissive cities outside California issued 26 permits per 100,000 residents in 2025. None of these is measured per feasible lot, so the range is not narrowed until job T2 fits it. | Becomes calibrated by job T2 on jurisdictions with published ADU permits (the California Table A2 panel, Seattle, Massachusetts); check against the per-capita rates below. |
| `adu.unit_size_sqft` | Floor area of the modelled ADU | 600 | 600 | 900 | sq ft | The pro forma prices a unit at the benchmark minimum, with some owners building larger. The same size is used in every ADU scenario so that differences between scenarios come from eligibility, not size. | Check against size data in published ADU permit reports. |
| `adu.utility_connection_fee` | Water and sewer connection fees for one ADU | 3,000 | 0 | 6,000 | $/unit | Tap fees vary widely by authority; large cities charge a few hundred dollars, many suburban authorities several thousand. SB 1346 sets this to 0. | Sample published tap-fee schedules of Pennsylvania authorities. |
| `adu.value_uplift_share` | Value an ADU adds, as a share of the host home's value | 0.2 | 0.1 | 0.35 | share | Used only for a value-based feasibility test: an ADU pays where the value it adds (this share of the home's value) covers its total cost. Appraisal practice credits a second unit with a fifth to a third of the main home's value; the range is wide. | Source from appraisal studies or sales of homes with and without ADUs. |
| `baseline.cycle_quantile_strong` | Error quantile used for a strong housing cycle | 0.8 | 0.8 | 0.8 | share | The "strong cycle" choice reads the 80th percentile of the historical error distribution. | Review once the backtest error distribution is published. |
| `baseline.cycle_quantile_typical` | Error quantile used for a typical housing cycle | 0.5 | 0.5 | 0.5 | share | The "typical cycle" choice reads the median of the historical error distribution. | Review once the backtest error distribution is published. |
| `baseline.cycle_quantile_weak` | Error quantile used for a weak housing cycle | 0.2 | 0.2 | 0.2 | share | The "weak cycle" choice reads the 20th percentile of the historical actual-to-projected error distribution. | Review once the backtest error distribution is published. |
| `baseline.ewm_alpha` | Weight on the latest year in the exponentially weighted mean | 0.3 | 0.1 | 0.6 | share | Each year back gets (1 - alpha) times the weight of the year after it. At 0.3 the latest year carries about 30% of the weight and the last five years about 83%; the range runs from nearly a long average to nearly the last two years. | Set by the rolling-origin backtest (job T1). |
| `baseline.first_year` | First calendar year counted | 2,027 | 2,027 | 2,027 | year | Reforms are assumed to take effect on January 1, 2027, so the five years counted are 2027-2031 (project decisions D-02 and D-14). | Change only if the assumed effective date changes. |
| `baseline.long_window_years` | Years in the long-average baseline rule and the trend fit | 10 | 10 | 10 | years | The second candidate rule averages the last ten years, which spans most of a housing cycle. The damped-trend rule fits its straight line over the same ten years, and every backtest origin needs this much history so all rules are scored on the same cases. | Review after the rolling-origin backtest on all U.S. counties (job T1). |
| `baseline.recent_window_years` | Years in the recent-average baseline rule | 5 | 3 | 10 | years | The baseline projects each county's permits from a weighted recent average. Five years is a common window; the range covers the short and long rules the backtest compares. | Set by the rolling-origin backtest (job T1); then kind becomes calibrated. |
| `baseline.selection_tolerance` | Tolerance for choosing a simpler baseline rule | 0.05 | 0 | 0.15 | share | The simplest candidate rule is chosen if its median absolute log error is within this share of the best rule's (5% means at most 1.05 times the best). Simple rules are easier to explain and rarely lose much skill. | Review once the skill table for all U.S. counties is published. |
| `baseline.size_classes` | Number of county size classes for the baseline interval | 3 | 3 | 3 | classes | Counties are split into terciles (small, medium, large) by their average yearly permits over the recent window. Small counties have larger proportional errors, so each class gets its own error distribution; three classes keep enough backtest cases in each. | Revisit if a class has too few backtest cases in a state with few counties. |
| `baseline.trend_damping` | Damping applied to the recent permit trend | 0.5 | 0 | 1 | share | 0 ignores the recent trend (flat average); 1 carries it forward in full. Half is a neutral starting point. | Set by the rolling-origin backtest (job T1). |
| `explain.verdict_a_lot_pct` | Smallest middle-estimate gain that earns the verdict "Adds a lot" | 5 | 5 | 5 | % of baseline | A reform whose middle estimate (P50) adds at least this share of the baseline's five-year permits is called "Adds a lot". One home in twenty is large next to the year-to-year swing in statewide permits. | Revisit once the benchmark reforms are compared with observed reforms (P2-17). |
| `explain.verdict_overlap_share` | Share of a reform's own gain lost to overlap that earns the verdict "Overlaps" | 0.5 | 0.5 | 0.5 | share | In a package, when at least this share of a reform's middle estimate run alone is removed because another reform in the package uses the same lots, the reform is called "Overlaps": most of what it would add is already counted elsewhere. | Revisit with the package allocation rule (P2-11). |
| `explain.verdict_some_pct` | Smallest middle-estimate gain that earns the verdict "Adds some" | 1 | 1 | 1 | % of baseline | Below this share of the baseline's five-year permits (middle estimate, P50) a reform is called "Little effect": the gain is smaller than the rounding in most published permit totals. | Revisit once the benchmark reforms are compared with observed reforms (P2-17). |
| `fees.pa_documented_fee_per_unit` | Typical Pennsylvania development fees per new home (second view) | 15,000 | 5,000 | 40,000 | $/unit | For the labelled second view (decision D-09) that uses state fee levels instead of the benchmark. Permit, tap and impact fees for a new home in Pennsylvania are likely well under $50,000; the range is wide until sourced. | Source per-unit impact, tap and permit fees for typical new homes (P4-05). |
| `fees.passthrough_k` | Share of a fee cut that reaches the decision to build | 0.5 | 0.05 | 1 | share | Who gains from a fee cut is the most uncertain step of the fee reform. If landowners raise land prices by the full saving, almost nothing reaches the build decision (near 0); if land prices do not move, all of it does (1). Studies of fee incidence point both ways, so the range is kept wider than the general pass-through share and this parameter leads the fee tornado. | Read the evidence on development-fee incidence (backlog P4-05) and narrow the range; until then it stays the widest input of the fee reform. |
| `missing_middle.acquisition_premium` | Price a builder pays above a home's value to buy it | 0.1 | 0 | 0.25 | share | An owner who is not selling must be paid more than the home is worth, and census home values are owners' own estimates. A lot pencils when its home value x (1 + this premium) is at or below the residual land value. | Compare teardown sale prices with assessed or census values in Tier 2 counties. |
| `missing_middle.adu_lot_overlap_share` | Share of lots used by ADUs that leave the missing-middle pool | 0.75 | 0.5 | 1 | share | In a package with the ADU reform, a lot that gets an ADU is rarely torn down soon after. Lots the engine passes as used by ADUs are removed from eligible lots at this share; the rest sit outside single-family-only zones or would be redeveloped anyway. | Revisit with the package allocation rule (P2-11). |
| `missing_middle.cap_rate` | Capitalisation rate used to value the finished building | 0.06 | 0.05 | 0.075 | share per year | Net operating income divided by value for small rental buildings; moves with interest rates. | Source from a published cap-rate survey for small multifamily. |
| `missing_middle.demolished_per_project` | Existing homes removed per project | 1 | 1 | 1 | units/lot | Each project is assumed to replace one existing single-family home, so net units = new units - 1. | Allow for vacant-lot projects (0 removed) once lot data allow it. |
| `missing_middle.developer_margin` | Builder profit required, as a share of total cost | 0.15 | 0.1 | 0.2 | share | Small builders typically need 10-20% on cost to take on a project. | Source from published feasibility studies of small multi-unit buildings. |
| `missing_middle.displacement_share` | Share of missing-middle units that replace baseline construction | 0.15 | 0 | 0.4 | share | Some builders would have built single-family homes or apartments elsewhere instead. | Estimate from hindcast places. |
| `missing_middle.fit_share` | Share of those lots large enough for a 2-6 unit building | 0.45 | 0.2 | 0.75 | share | Setbacks and coverage are assumed not to bind (Handbook), so lot area and shape decide. | Estimate from the block lot-size proxy (P1-05) and parcel counties. |
| `missing_middle.hard_cost_per_sqft` | Construction cost per square foot, 2-6 unit building | 200 | 150 | 275 | $/sq ft | Wood-frame small multifamily building; wide range for region and finish. | Look for a free public construction cost source. |
| `missing_middle.lot_proxy_net_share` | Share of block land per home that is the home's own lot | 0.6 | 0.45 | 0.75 | share | The block lot-size proxy divides a block's land by its homes, so it also counts half the streets, parks and shops. Lot area = proxy x this share. | Compare the proxy with parcel areas in Tier 2 counties (P5-03). |
| `missing_middle.min_lot_sqft` | Smallest lot that holds a 2-6 unit building | 5,000 | 3,500 | 7,000 | sq ft | With setbacks and coverage not binding (Handbook), a three-storey building of 2-6 units of about 1,000 sq ft plus surface parking needs roughly 3,500-7,000 sq ft of lot. Used only when the tract has the block lot-size proxy. | Measure built missing-middle lot sizes in Tier 2 parcel counties. |
| `missing_middle.new_unit_rent_premium` | Rent premium of a new 2-6 unit building's homes over the census two-bedroom median | 1.25 | 1.05 | 1.5 | ratio | Census medians mix long-time tenants in older buildings with new leases. A newly built apartment of about 1,000 sq ft usually asks well above the median two-bedroom rent of its tract, more so than a small backyard ADU (adu.new_unit_rent_premium), so this entry is its own. | Compare Census SOMA asking rents for new apartments with ACS median gross rent. |
| `missing_middle.operating_cost_share` | Operating costs as a share of rent, 2-6 unit building | 0.35 | 0.25 | 0.45 | share | Vacancy, management, upkeep, insurance and property tax for a small rental building. | Build from parts as for ADUs. |
| `missing_middle.ramp_first_year_share` | Missing-middle permits in the first year, as a share of the steady rate | 0.2 | 0.05 | 0.4 | share | Builders must learn the new rules, find sites and arrange money, so the first year is slow; the rate climbs to full over missing_middle.ramp_years_to_full. | Fit to published post-reform permit paths. |
| `missing_middle.ramp_years_to_full` | Years for missing-middle permits to reach the steady yearly rate | 4 | 2 | 5 | years | Builders need time to learn new rules and assemble sites; slower than ADUs. | Fit to published post-reform permit paths. |
| `missing_middle.redevelopment_hazard_annual` | Share of feasible lots redeveloped each year | 0.003 | 0.0005 | 0.01 | share per year | Redevelopment needs a sale and a builder, so it is rare: well under 1% of feasible lots a year. Early results of gentle-density reforms were small. | Calibrate on places with gentle-density reforms and published permits. |
| `missing_middle.rent_ratio_2br` | Two-bedroom rent as a share of the all-unit median rent (fallback) | 1.08 | 0.98 | 1.2 | ratio | A missing-middle unit of about 1,000 sq ft rents like a two-bedroom. The tract's own two-bedroom median is used where it exists; this ratio times the tract's median gross rent is the fallback where it is blank. | Compute county ratios from ACS B25031 as for the one-bedroom ratio. |
| `missing_middle.sf_only_zoned_share` | Share of single-family lots in single-family-only zones | 0.75 | 0.5 | 0.9 | share | Most single-family homes sit in zones that allow nothing else; older boroughs and cities often allow two units already. | Replace with zoning data in Tier 2 counties and the town-rule index. |
| `missing_middle.sf_turnover_annual` | Share of single-family homes sold each year | 0.045 | 0.03 | 0.06 | share per year | A lot is redeveloped only after it is sold, so the yearly redevelopment rate can never exceed the yearly sale rate of single-family homes. The funnel uses the smaller of the two in every draw. | Source from county deed-transfer counts or a published home-sales series. |
| `missing_middle.soft_cost_share` | Soft costs as a share of construction, 2-6 unit building | 0.25 | 0.18 | 0.32 | share | Design, permits, financing and fees (other than the benchmark fee) for a small project. | Source from published development pro formas. |
| `missing_middle.typology_weight_duplex` | Relative weight of duplexes (2 units) in the missing-middle mix | 0.35 | 0.2 | 0.5 | ratio | The funnel prices five building types, from a duplex to a sixplex, and weights them. The five weights are drawn and then divided by their sum, so only their relative size matters. Duplexes are the most common gentle-density project in places that allow them, because they need the least land and money. | Set from permits by structure type in places after gentle-density reforms. |
| `missing_middle.typology_weight_fiveplex` | Relative weight of five-unit buildings in the missing-middle mix | 0.05 | 0 | 0.1 | ratio | Five or more units usually moves a building to commercial financing and stricter building-code rules, so five-unit buildings are rare. Weights are divided by their sum. | Set from permits by structure type in places after gentle-density reforms. |
| `missing_middle.typology_weight_fourplex` | Relative weight of fourplexes (4 units) in the missing-middle mix | 0.25 | 0.1 | 0.4 | ratio | Fourplexes are the largest building that many lenders still finance as residential; a common middle case. Weights are divided by their sum. | Set from permits by structure type in places after gentle-density reforms. |
| `missing_middle.typology_weight_sixplex` | Relative weight of sixplexes (6 units) in the missing-middle mix | 0.15 | 0.05 | 0.3 | ratio | If a builder moves to commercial rules anyway, the most units allowed pay best, so sixplexes are more common than five-unit buildings. Weights are divided by their sum. | Set from permits by structure type in places after gentle-density reforms. |
| `missing_middle.typology_weight_triplex` | Relative weight of triplexes (3 units) in the missing-middle mix | 0.2 | 0.1 | 0.3 | ratio | Second most common small form; weights are divided by their sum in every draw. | Set from permits by structure type in places after gentle-density reforms. |
| `missing_middle.unit_size_sqft` | Floor area per missing-middle unit | 1,000 | 750 | 1,300 | sq ft | Units in small multi-unit buildings are typically two-bedroom homes of about 1,000 sq ft. | Check against new small multifamily construction data. |
| `missing_middle.units_per_project` | Units in a typical missing-middle project | 4 | 2 | 6 | units/lot | Projects range from duplexes to sixplexes; fourplexes are a common middle case. | Check against permits by structure type in places after gentle-density reforms. |
| `multifamily.cap_rate` | Capitalisation rate used to value a finished mid-rise building | 0.055 | 0.045 | 0.07 | share per year | Larger, newer apartment buildings trade at somewhat lower cap rates than small ones. | Source from a published cap-rate survey for Pennsylvania metros. |
| `multifamily.developer_margin` | Developer profit required on a mid-rise project, as a share of total cost | 0.15 | 0.1 | 0.2 | share | Developers and their investors typically need 10-20% on cost for larger projects. | Source from published feasibility studies. |
| `multifamily.hard_cost_per_sqft` | Construction cost per square foot, mid-rise apartment building | 300 | 230 | 400 | $/sq ft | Wood or light-steel frame over a concrete podium costs more per square foot than small buildings; the range covers region and finish. | Look for a free public construction cost source. |
| `multifamily.operating_cost_share` | Operating costs as a share of rent, mid-rise apartment building | 0.38 | 0.3 | 0.45 | share | Vacancy, management, upkeep, insurance and property tax for a professionally run building. | Build from parts as for ADUs. |
| `multifamily.soft_cost_share` | Soft costs as a share of construction, mid-rise apartment building | 0.25 | 0.18 | 0.32 | share | Design, permits, financing and other soft costs (other than the benchmark fee). | Source from published development pro formas. |
| `multifamily.unit_size_sqft` | Floor area per mid-rise apartment | 850 | 700 | 1,000 | sq ft | New mid-rise apartments are mostly studios to two-bedrooms averaging about 850 sq ft. | Check against new multifamily construction characteristics (Census SOC). |
| `multifamily.units_per_project` | Units in a typical mid-rise apartment project | 60 | 24 | 150 | units/lot | Four- to six-storey buildings near transit stops typically hold a few dozen to about 150 apartments. | Check against permits for 5+ unit buildings in Pennsylvania metros. |
| `parking.mix_structured_elsewhere` | Share of spaces in structures, other tracts | 0.05 | 0 | 0.2 | share | Outside dense areas almost all residential parking is surface or garage parking. | Estimate from local project data or land prices. |
| `parking.mix_structured_transit_rich` | Share of spaces in above-ground structures, transit-rich tracts | 0.4 | 0.15 | 0.7 | share | Where land is dear, more parking is stacked in structures; the rest is surface or underground. | Estimate from local project data or land prices. |
| `parking.mix_surface_attached` | Share of spaces that are surface spaces, 2-4-unit buildings | 0.95 | 0.8 | 1 | share | Small buildings park on driveways and small lots; the rest are tuck-under garages, costed as structured spaces. | Check against a sample of new 2-4-unit buildings in Pennsylvania. |
| `parking.mix_underground_elsewhere` | Share of spaces underground, other tracts | 0 | 0 | 0.05 | share | Away from transit, underground residential parking is very rare. | Estimate from local project data or land prices. |
| `parking.mix_underground_transit_rich` | Share of spaces underground, transit-rich tracts | 0.1 | 0 | 0.3 | share | Underground parking is mostly limited to large projects in the densest places. | Estimate from local project data or land prices. |
| `parking.required_spaces_per_unit_attached` | Parking spaces required per townhouse or small multi-unit home | 2 | 1 | 2 | spaces/unit | Townhouse and two-to-four-unit minimums are usually one to two spaces per unit. | Replace with a sample of Pennsylvania zoning codes (P4-08). |
| `parking.required_spaces_per_unit_multifamily` | Parking spaces required per apartment under current rules | 1.5 | 1 | 2 | spaces/unit | Common local minimums for apartments are one to two spaces per unit. | Replace with a sample of Pennsylvania zoning codes (P4-08). |
| `parking.share_still_built_elsewhere` | Share of formerly required spaces still built, other tracts | 0.925 | 0.85 | 1 | share | Decision D-08: away from transit, most projects keep building close to the old minimum (85-100%), so the saving is small. | Cite the post-reform studies of what developers built (backlog P4-01). |
| `parking.share_still_built_transit_rich` | Share of formerly required spaces still built, transit-rich tracts | 0.6 | 0.45 | 0.75 | share | Decision D-08: after minimums are removed, projects near frequent transit still build about 60% of the spaces once required, because lenders and buyers still want parking. | Cite the post-reform studies of what developers built (backlog P4-01). |
| `parking.site_yield_gain` | Extra units a small site can hold when parking is not required | 0.05 | 0 | 0.15 | share | Land no longer needed for parking can hold more homes on tight sites. The gain is small on average and zero where land is plentiful. | Test with the pro forma on typical lot sizes; cite site-yield studies. |
| `parking.transit_bus_min_departures` | Weekday departures that make a bus stop a major transit stop | 64 | 64 | 64 | departures per weekday | Decision D-07: a major transit stop is a fixed-guideway station, or a bus stop with at least 64 weekday departures (about every 15 minutes over 16 hours). | Only if decision D-07 changes. |
| `parking.transit_bus_radius_miles` | Distance around a frequent bus stop counted as near transit | 0.25 | 0.25 | 0.25 | miles | Decision D-07: a quarter mile, straight line, for frequent bus stops. | Only if decision D-07 changes. |
| `parking.transit_rail_radius_miles` | Distance around a rail station counted as near transit | 0.5 | 0.5 | 0.5 | miles | Decision D-07: half a mile, straight line, for fixed-guideway stations. | Only if decision D-07 changes (the interface offers 1/4, 1/2 and 3/4 mile). |
| `parking.transit_rich_min_share` | Share of a tract's homes near a major stop that makes it transit-rich | 0.5 | 0.5 | 0.5 | share | A tract is flagged transit-rich when at least half of its housing units sit in census blocks within the D-07 distance of a major transit stop. | Test 1/4 and 3/4 once real stop data are in the pack. |
| `supply.crosscheck_land_share` | Land cost as a share of development cost, pro-forma cross-check | 0.15 | 0.05 | 0.3 | share | Used only by the pro-forma cross-check of the fee and parking reforms: a project pencils where its value covers development cost, the builder's return and land. Land is often a tenth to a quarter of total cost. | Source land shares from published development pro formas or land sales. |
| `supply.crosscheck_pareto_alpha` | Pareto shape of the open top rent and value bracket, pro-forma cross-check | 2.5 | 1.5 | 4 | ratio | Break-evens for new buildings often fall inside the open top ACS bracket ($3,500+ rent, $2,000,000+ value). The cross-check spreads that bracket as a Pareto tail with this shape instead of treating it as zero. Larger values mean a thinner tail. Rents are less spread out than incomes, so the central value sits above typical income-tail shapes. | Fit from tract rent microdata (ACS PUMS) for Pennsylvania metros. |
| `supply.elasticity_medium_run` | Medium-run supply response to a change in profit per unit | 1 | 0.3 | 2 | ratio | Percent change in permitted units for a 1% change in the value-to-cost margin. Published metropolitan housing-supply elasticities for older northeastern metros mostly sit between about 0.5 and 2; 1.0 is a neutral central value until the cross-check job is run. | Cite the metro elasticity study and the 2024 tract-level study after reading them (backlog P4-05); cross-check with job T6. |
| `supply.new_construction_value_premium` | Value of a newly built unit above the area's median, as a share | 0.2 | 0.05 | 0.4 | share | Value per new unit (V) starts from the county median home value (or median gross rent, for rented building types) read from ACS brackets. New homes sell and rent above the median of the existing stock, so V adds this premium. | Compare new-construction sale prices (Census SOC) with ACS county medians. |
| `supply.saving_passthrough_k` | Share of a cost saving that reaches the decision to build | 0.5 | 0.2 | 0.9 | share | Part of any cut in fees or parking cost is captured by landowners as higher land prices rather than turning into more homes. Half is a neutral start; the range is wide because the evidence on who bears fees is mixed. | Read the evidence on fee incidence (backlog P4-05) and narrow the range. |
<!-- /generated: assumption_table_judgement -->

<!-- generated: ledger_summary -->
| Count by | Value | Entries |
|---|---|---|
| kind | benchmark | 9 |
| kind | judgement | 95 |
| kind | sourced | 13 |
| status | draft | 95 |
| status | sourced | 22 |
| all | entries | 117 |

*Ledger fingerprint `75c91c9c2452`.*
<!-- /generated: ledger_summary -->

## 3. Data sources and data cleaning steps

All data is public and is downloaded by script from the publisher. Nothing is stored in the repository. Each source is listed once in `src/visionhousing/data/sources.yaml`. Each download is recorded in `data/manifest.json` with a SHA-256 checksum. The full provenance table (source, version, access date, licence, cleaning, limits) is in `DATA_PROVENANCE.md` and is generated from the same records.

### 3.1 Sources

<!-- generated: source_summary_table -->
*Generated from the source registry. Columns: source, publisher, version, tier, used for.*
<!-- /generated: source_summary_table -->

### 3.2 Cleaning steps

| Step | What it does | Status |
|---|---|---|
| Safe download | Retries a busy server, stops on a bad request, never keeps a half-finished file, removes any key from logs | Built (`docs/modules/data_fetch.md`) |
| Permits | Parse county annual files by header name. Keep reported and imputed flags. Tidy to county by year by structure type | **Planned** |
| Survey tables | Check variable labels against the vintage. Keep margins of error. Handle missing-value codes | **Planned** |
| Bracket shares | Turn published value and rent bracket counts into the share above a break-even, spreading units evenly inside a bracket | Built (`docs/modules/brackets.md`) |
| Blocks | Read only the attribute table of the block file. Compute housing density and a lot-size proxy per tract | **Planned** |
| Transit stops | Keep stops that meet the major-stop test. Measure distance from block centres | **Planned** |

### 3.3 Data tiers

Every number shows the tier of data behind it.

| Tier | What it means |
|---|---|
| 0 | National public data only. Available for every state |
| 1 | Adds statewide building footprints or similar national layers |
| 2 | Adds local parcel or zoning data |

## 4. Methodological approach

All model logic lives in the Python engine. The interface only shows what the engine returns. **Planned:** the engine described here is not built yet. The parts built so far are the assumption ledger, the result record format, the bracket-share method, the data layer and the synthetic test state.

### 4.1 Logic flow

1. Fetch the sources and build the state data pack.
2. Project the baseline by county and structure type.
3. For each reform, compute the added units by tract through channel A, channel B, or both.
4. Remove overlap when several reforms are on.
5. Repeat steps 2 to 4 many times with inputs drawn from their ranges.
6. Report P10, P50 and P90 by county and state.

### 4.2 Baseline

- Data: Building Permits Survey, annual, by county and structure type (1 unit, 2 units, 3 to 4 units, 5 or more units), 1990 onward.
- Rule: the simplest rule that backtests acceptably. The candidates are a 5-year average, a 10-year average, a damped trend and a pooled regression on prices and rates.
- Selection: rolling-origin backtest across all U.S. counties. Choose the simplest rule within tolerance of the best.
- Interval: empirical. Apply the rule at every past origin. Use the spread of actual over projected five-year totals, by county size class, as the interval.
- Cycle choice: weak, typical or strong housing cycle, mapped to quantiles of that error spread.

<!-- generated: baseline_skill_table -->
<!-- generated: baseline_interval_coverage -->

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

- `p` is a share, not a yes or no. The tract's published value and rent brackets are compared with the pro forma's break-even.
- `h` is the main behavioural input and the main source of uncertainty. It is calibrated on places that passed the reform and published results (**Planned**).

### 4.4 Channel B: lower cost

For reforms that cut the cost of housing that is already legal:

```
added units (county) = baseline x elasticity x (cost saved / value per unit) x k
```

`k` is the share of the saving that reaches the building decision rather than raising land prices. A cross-check computes the same effect through the pro forma. Both are reported side by side.

### 4.5 Scenario definitions

| Reform | Eligible sites | Fit | Feasible | Take-up and net units | Channel |
|---|---|---|---|---|---|
| ADUs | Single-family detached homes, plus attached homes at a lower fit, minus places where ADUs are already allowed | Lots with room for a 600 sq ft unit | Homeowner pro forma, plus a non-financial share (family use) | Calibrated on places with published ADU permits. One unit each | A |
| Missing middle | Single-family lots in single-family-only zones | Lot large enough for the building | Redevelopment value of 2 to 6 units vs. the tract's home values | Yearly redevelopment rate among feasible lots. Net of the home removed | A |
| Transit-oriented density | Land and homes near a major stop | Developable share | Multifamily pro forma at the higher intensity with less parking | Bounded by regional multifamily absorption | A and B |
| Parking reduction | Baseline multifamily and attached production | Not used | Cost saved per space not built | Supply response to the saving | B |
| Fee reduction | All baseline production | Not used | $25,000 saved per unit | Supply response. Who keeps the saving is the key input | B |

Pennsylvania scenarios:

- **HB 2186.** ADUs on detached, attached and 2 to 4 unit lots.
- **SB 1346 variant.** Single-family detached lots only. Excludes Philadelphia and Pittsburgh. Utility connection fee set to zero.

### 4.6 Packages and overlap

- A lot is used once. ADUs and missing middle compete for the same lots.
- Lots near transit are counted under the denser reform.
- Parking and fee savings enter the pro formas of the other reforms.
- The result is shown as: sum of parts, minus overlap, plus interaction, equals combined.

### 4.7 Uncertainty

- Every input has a low, central and high value and a shape.
- Monte Carlo draws, seeded so each run repeats exactly.
- Baseline and reform effect are drawn together.
- Results are shown as P10, P50 and P90, in words: "likely between ... and ...".

### 4.8 Calibration and validation

**Planned.** Each job produces a small reviewed table that the engine reads. The engine never trains anything at run time.

| Job | What it checks |
|---|---|
| Baseline rule selection | Which projection rule backtests best |
| ADU take-up | Take-up rate from places with published ADU permits, held out by county |
| Physical fit | How well cheap data predicts the share of lots with room |
| Hindcasts | The finished model on reforms already passed, predicted vs. observed |
| Hide-the-data test | How much the answer moves when local data is withheld |
| Elasticity cross-check | Literature elasticities vs. a simple estimate from permits and prices |

<!-- generated: validation_summary -->

## 5. Limitations and known edge cases

- **Permits are not homes.** Some permitted units are never built. Completion lags permits.
- **Five years only.** Effects after 2031 are not modelled.
- **No parcel answers.** Results are for tracts, counties and the state.
- **Take-up is uncertain.** Few places publish clean before-and-after counts. The range is wide on purpose.
- **Compliance.** The model assumes local governments apply the state rule. It cannot model resistance or slow permitting.
- **Who benefits.** The model cannot say who lives in the new units or what they pay.
- **Labour and materials.** Construction capacity limits are not modelled.
- **Transit areas.** Some added units near transit may move from elsewhere in the region rather than add to the state total.
- **Survey error.** Survey estimates have margins of error. Small tracts are noisy.
- **Edge cases.** Tracts with no homes give "no data", not zero. Open top brackets give a lower bound. Counties with imputed permit counts are flagged. "No data" and "not modelled" are shown in grey and never count as a result.

<!-- generated: edge_case_counts -->

## 6. Sensitivity notes

**Planned.** Sensitivity results come from the engine.

- **Tornado.** Each input is swung from low to high with the others held central. Inputs are ranked by how much the state total moves.
- **Joint check.** Rank correlation between sampled inputs and the result confirms the order when inputs move together.
- **Choices the user can change.** Cycle strength, transit distance, fee view, and each assumption's low, central or high value.

<!-- generated: tornado_by_reform -->
<!-- generated: top_drivers_table -->

Expected main drivers, to be confirmed by the runs: take-up rate, physical fit share, construction cost, and the share of cost savings that reaches builders.

## 7. Intended users and appropriate use cases

| User | Appropriate use | Not appropriate |
|---|---|---|
| State legislator or staffer | Compare reforms by likely range and read the caveats | Quoting the middle value as a promise |
| State housing agency analyst | Change assumptions, see which matter, download a full run | Budget or revenue forecasting |
| County or municipal planner | See the county range and the data tier behind it | Parcel or site decisions |
| Economist or reviewer | Check the evidence, backtests and interval coverage | Treating the model as causal proof |
| Advocate or journalist | See which reform does the most, and why | Claims about rents, prices or who moves in |

Good use: comparing reforms against each other and against the baseline, and finding which assumptions matter.

Poor use: predicting an exact number of homes, or any decision about a single property.
