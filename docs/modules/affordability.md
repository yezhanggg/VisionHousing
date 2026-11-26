# Module: Cost and affordability (`features/affordability.py`, `data/sources/affordability.py`)

**What it does.** Adds four facts to a state pack and one section to the place profile:

1. **Who is cost-burdened**, by income and tenure (HUD CHAS).
2. **What incomes are**: HUD's area median income and its 30%, 50% and 80% limits for 1 to 8 people, and the highest rent each can afford.
3. **Which homes are income-restricted or assisted**, and how many of them have a contract or restriction that ends within 5 and 10 years.
4. **A county stock ledger**: existing homes, recent permits, assisted homes, homes at risk, lower-rent and older rental stock.

**Why.** A reform that adds homes is judged partly by who can afford them. The engine compares prototype rents with incomes at a stated share of area median income (benchmarks E2, financing E6), so it needs the income limits by county and household size. Users also ask how much affordable stock exists and how much could leave its program soon. These are three separate measures and are kept separate: modelled rent affordability, income-restricted units, and preservation risk. Nothing here predicts displacement or rent changes.

## Terms

- **AMI (area median income)**: HUD's median family income for an area (a metro area, or a county outside metro areas). CHAS calls it HAMFI.
- **Income limits**: shares of AMI set by HUD for each household size: 30% ("extremely low income"), 50% ("very low income"), 80% ("low income"). HUD applies caps and floors, so the 80% limit is not always 1.6 times the 50% limit.
- **Cost burden**: housing costs over household income. Renters: gross rent (rent plus utilities). Owners: mortgage, taxes, insurance and utilities. Over 30% is "cost-burdened"; over 50% is "severely cost-burdened". Households with no or negative income are "not computed".
- **Affordable rent**: a monthly housing cost of at most 30% of income (`rent_share_of_income`).
- **Assisted homes**: homes whose rent is held down by a program. Here: project-based rental assistance contracts (Section 8 and similar), public housing, and Low-Income Housing Tax Credit (LIHTC) restrictions. Vouchers (Housing Choice Vouchers) move with the tenant, so they are assisted *households* and are never added to homes.
- **Expiring**: a contract's end date, or a tax-credit restriction's end (30 years after the year placed in service), within 5 or 10 years of the day the HUD lists were read. Most contracts are renewed; an end date marks a renewal decision, not a certain loss.

## Sources and terms of use

| Source id | What | Terms | Used how |
|---|---|---|---|
| `hud_chas` | CHAS 2018-2022, Tables 8, 12, 15C, state, county, tract | Public domain (U.S. government work) | Fetched; state, county and tract aggregates kept |
| `hud_income_limits` | Section 8 income limits, FY2026 | Public domain | Fetched; one row per county kept |
| `hud_mf_assisted` | HUD Multifamily Properties - Assisted (map service) | Public domain | Fetched; only county and tract totals kept |
| `hud_public_housing` | HUD Public Housing Developments (map service) | Public domain | Fetched; only county and tract totals kept |
| `hud_hcv_tract` | Housing Choice Vouchers by tract (map service) | Public domain | Fetched; tract and county totals kept |
| `hud_lihtc` | HUD LIHTC database (already fetched for the incentive tables) | Public domain | Reused; county and tract totals kept |
| `nlihc_gap_2026` | NLIHC, *The Gap* 2026, state pages | NLIHC allows linking and citing with credit; copying and redistributing are not allowed | A few state figures quoted with credit in `assumptions/affordability.yaml` (`gap_context`); context only, never used in a calculation |
| `philly_fed_rental_explorer` | Federal Reserve Bank of Philadelphia, Rental Housing Affordability Data Explorer | No download and no reuse terms found ("All rights reserved") | Cited as the method reference for "affordable and available" rentals; no figures copied |

**Left out: the National Housing Preservation Database (NHPD).** Its terms of use (preservationdatabase.org, "Terms of Use", April 2020) require an account and forbid transferring, distributing or republishing the data or extracts without a separate licence. An open tool that publishes county aggregates would need that licence, so NHPD is not used. HUD's own lists above give contract end dates for assisted properties; what NHPD adds (state and local programs, USDA rural housing, a cross-program property match) is named in the limits instead.

HUD's file server (`www.huduser.gov`) answers clients that do not name themselves in the common `Mozilla/5.0 (compatible; ...)` form with an empty "202" page. The fetcher names itself `Mozilla/5.0 (compatible; visionhousing/1.0; open-source research tool)`: it says who it is and does not pretend to be a browser.

## Tables

| Table | One row per | Columns |
|---|---|---|
| `afford_chas` | state, county | `owner_<income>_<burden>` and `renter_<income>_<burden>` for 5 income bands (`le30`, `30_50`, `50_80`, `80_100`, `gt100` % of AMI) x 4 burdens (`lt30`, `30_50`, `gt50`, `nc`); `rent_le30`, `rent_30_50`, `rent_50_80`, `rent_gt80` (renter homes by the income their rent is affordable to), `rent_lacking`; `renter_built_<period>` and `renter_built_<period>_le50` for 1960-1979, 1940-1959, 1939 or earlier; each with `_moe` |
| `afford_chas_tract` | tract | renter cells collapsed to 3 income groups (`le30`, `30_80`, `gt80`) x 3 burdens; `renter_nc`; the 4 rent bands; `older_rental`, `older_rental_le50`. No margins |
| `afford_income_limits` | county | `hud_area_code`, `hud_area_name`, `areas_in_county`, `median` (AMI), `l30_1`..`l30_8`, `l50_1`..`l50_8`, `l80_1`..`l80_8` |
| `afford_assisted` | state, county, tract with any | `mf_properties`, `mf_units`, `mf_units_exp_5y`, `mf_units_exp_10y`, `mf_units_exp_past`, `ph_developments`, `ph_units`, `lihtc_projects`, `lihtc_li_units`, `lihtc_li_units_end_5y`, `lihtc_li_units_end_10y`, `lihtc_year_unknown`, `at_risk_overlap_5y`, `at_risk_overlap_10y`, `at_risk_unchecked_5y`, `at_risk_unchecked_10y`, `at_risk_5y`, `at_risk_10y`, `hcv_households`, `as_of` |
| `stock_ledger_county` | state, county | `housing_units`, `renter_households`, `owner_households`, `authorized_recent` (+ `authorized_years`), `assisted_units`, `at_risk_5y`, `at_risk_10y`, `at_risk_basis`, `lower_rent_units`, `older_rental`, `older_rental_le50`, `severe_le50_renters`, `as_of` |

Tract CHAS rows are collapsed to keep the Pennsylvania pack under its size budget (12 MB); a tract and its county are always collapsed the same way (`COLLAPSED`), so they compare like with like.

## How each number is worked out

- **CHAS cells** are published counts, rounded by the Census Bureau. A band is a sum of published cells; its margin is the square root of the sum of squared cell margins. Which cell is which is checked against HUD's data dictionary in the tests.
- **Shares** (profile): households in the numerator bands / households in the denominator bands. The margin uses the Bureau's formula for a proportion. "Not computed" households are left out of cost-burden shares.
- **Income limits**: published 30%, 50% and 80% limits. Other levels come from the 50% limit: 60% = 1.2x, 100% = 2x, 120% = 2.4x (`derived_limit_multiples`, the convention HUD uses for tax-credit limits). In New England the file lists towns; a county takes the HUD area that covers most of its towns, and `areas_in_county` says how many areas it spans.
- **Highest affordable rent** for a home with `b` bedrooms at `p`% of AMI: `0.30 x limit(p, size) / 12`, with size = 1 person for a studio and 1.5 people per bedroom (`persons_per_bedroom`, the tax-credit rule); a half size is the mean of the two whole sizes. The result is gross rent (contract rent plus tenant-paid utilities).
- **Contract units**: a property's assisted units, capped at its total units. Each of its first two contracts carries units and an end date; units ending after the read date and within N years count as "expiring within N years". Dates already passed are counted apart (`mf_units_exp_past`).
- **Tax-credit units**: low-income units (all units when the low-income count is blank). The restriction ends `lihtc_extended_use_years` (30) after the year placed in service. A restriction ending in the year the lists were read still counts in both windows (only the year is known, so it has not certainly ended). Unknown years (8888, 9999) are left out and counted.
- **Assisted homes** (`assisted_units`) = contract units + public housing units + tax-credit low-income units. Programs overlap, so this is an upper bound. **At risk** (`at_risk_5y`, `at_risk_10y`) = contract units ending + tax-credit units ending in the window − units counted on both lists. HUD's two lists share no property id, so a tax-credit project and a contract property count as one property when their points are within 50 m (`at_risk_dedup.match_distance_m`) and their total unit counts differ by at most 10% of the larger (`at_risk_dedup.unit_count_max_diff_share`). Each contract property matches at most one project, the nearest. For a matched pair, the smaller of the two ending counts is subtracted once (`at_risk_overlap_*`). A row ending in the window that has no map point or no unit count cannot be tested (`at_risk_unchecked_*`); the ledger then labels the county's count `at_risk_basis = "upper bound"` instead of `"matched"`. Pairs geocoded further apart than 50 m are missed, so even a "matched" count can be a little high.
- **Places**: points are placed in 2020 tracts with the point-in-polygon test the incentive tables use. The county comes from HUD's own county code.
- **Ledger**: `authorized_recent` = units authorized in the last 5 years of the pack's permit table; `lower_rent_units` = renter homes with rents affordable at 50% of AMI or less; `older_rental` = renter households in buildings built before 1980; `severe_le50_renters` = renters at or below 50% of AMI paying over half their income (context only). Homes removed (demolitions, conversions) are not observed in any public file used here, so the ledger has no removal column.

## Engine inputs

`income_limit(limits, pct, size, county=...)`, `max_rent(limits, pct, bedrooms, county=...)` and `ami_long(limits)` (county x household size x AMI level -> yearly income and the 30% monthly cost, with `published` true for HUD's own limits) read the `afford_income_limits` pack table. Benchmarks can compare a prototype's rent with the rent affordable at a stated AMI and size. All settings are in `assumptions/affordability.yaml`.

## Place profile section

`profile/sections/affordability.py`, key `affordability`, title "Cost and affordability". Headline: renters at or below 80% of AMI paying over 30% of income. Summary points: AMI and the 2-bedroom rent affordable at 50% of AMI; assisted homes ending within 5 years; NLIHC's statewide count of affordable and available homes per 100 extremely low-income renters. Charts: renter cost burden in three income groups (stacks), renter homes by rent affordability band (bar), assisted homes by program (bar), contract units by end date (stack); each with the bigger area's shares. See [profile.md](profile.md).

## Use

```
visionhousing affordability --state PA     # fetch (or reuse) the extracts and add the tables to data/packs/PA
```

It prints each table's rows and bytes and the pack total against its budget, and exits 1 when the pack is over budget.

## Limits

- CHAS describes 2018-2022; it is older than the ACS 2020-2024 figures elsewhere in the profile and is not comparable with them cell by cell.
- Tract CHAS figures are small, rounded and very uncertain; tract rows carry no margins.
- Income limits and affordable rents are for the county's HUD area, not the tract.
- Only a property's first two contracts are dated. Tax-credit records in HUD's map service stop at projects placed in service in 2020, and some states require restrictions longer than 30 years, so tax-credit expirations are an early bound.
- State and local programs, USDA rural housing and naturally occurring affordable housing without a program are not in the assisted counts.
- The NLIHC figures are statewide, from NLIHC's own method and data year, and are context only.

## Tests

`tests/test_affordability.py`, offline, on recorded slices of the published files in `tests/fixtures/affordability/`:

```
.venv/bin/python -m pytest -q tests/test_affordability.py -p no:cacheprovider
```

They check every CHAS cell against HUD's data dictionary, that bands add up to the published totals, the tract collapse, the income-limit reader and the New England county rule, the published and derived limits and rents, the engine table, contract and tax-credit expiry windows on a synthetic case with known answers, county totals on recorded Vermont rows, the stock ledger, that every pack column is described, the range-read fetch through a fake server, and the profile section for a county and a tract (facts, sources, headline, chart shares).
