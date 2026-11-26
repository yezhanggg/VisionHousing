# Prices, rates and construction costs (`data/sources/prices.py`, `costs.py`)

Code: `src/visionhousing/data/sources/prices.py`, `src/visionhousing/data/sources/costs.py`.
Tests: `tests/test_prices_costs.py` (offline, hand-made files).

## What it does, in plain words

Three kinds of market context, by county and tract:

- how fast house prices have risen (FHFA house price index),
- what mortgages cost (Freddie Mac weekly rate survey, averaged by year),
- how construction pay differs by county (BLS wages), as a proxy for building costs.

None of these sets a model number by itself. Rates and costs are ledger parameters with ranges;
these tables are the evidence for those ranges and the regional adjustment.

## Terms used

| Term | Meaning |
|---|---|
| House price index (HPI) | A repeat-sales index: price changes of homes that sold more than once. Levels compare only within one area over time. |
| Percent change | `(index in the end year / index in the start year − 1) × 100` |
| PMMS | Primary Mortgage Market Survey: weekly national average offered rates |
| QCEW | Quarterly Census of Employment and Wages: jobs and pay from unemployment-insurance records |
| NAICS 23 | The construction sector |
| Wage index | County average pay ÷ state average pay |

## Inputs and what is kept

| Source id | Published file | Size | Kept in `data/raw/` |
|---|---|---|---|
| `fhfa_hpi_tract` | `hpi_at_tract.csv` (national) | 89.9 MB, streamed | `prices/hpi_at_tract_42.csv` (3.6 MB, state rows only) |
| `fhfa_hpi_county` | `hpi_at_county.xlsx` (national) | 5.3 MB | `prices/hpi_at_county_42.csv` (90 KB); the `.xlsx` is deleted |
| `freddie_pmms` | `PMMS_history.csv` | 98 KB | as published |
| `bls_qcew_construction` | `2025/a/industry/23.csv` (national) | 1.0 MB, streamed | `costs/qcew_2025_naics23_42.csv` (26 KB, state and U.S. rows) |

**Reading the `.xlsx` with the standard library.** An `.xlsx` is a zip of XML files. Text cells
point into `xl/sharedStrings.xml`; the sheet holds numbers and those pointers. The reader uses
`zipfile` and `xml.etree.ElementTree`, finds the header row by its "FIPS code" cell, and reads
columns by name. Members over 400 MB are refused before parsing.

**Tract vintage.** Every Pennsylvania tract code in the FHFA tract file is a 2020 tract code,
so it joins to the ACS tracts directly.

## Outputs (pack tables)

| Table | Columns |
|---|---|
| `prices_county` | `county, hpi_year, hpi_annual_change_pct, hpi_change_5yr_pct, hpi_change_10yr_pct` |
| `prices_tract` | the same with `tract` |
| `hpi_county_year` | `county, year, hpi, annual_change_pct, hpi_1990_base, hpi_2000_base` |
| `mortgage_rates_year` | `year, rate_30yr_pct, rate_15yr_pct, weeks_30yr, weeks_15yr` |
| `costs_county` | `county, year, construction_employment, construction_avg_annual_pay, wage_index_county_vs_state, wage_index_state_vs_us, wage_index_method` |

Pennsylvania, latest year 2025: 65 of 67 counties and 2,497 of 3,445 tracts have an index; 2,060
tracts have a five-year change. Construction pay is published for 63 counties; the other 4 are
withheld by BLS and take the state value (index 1.0, method `state_value`). State construction
pay is 1.017 times the national figure.

Small states: BLS can withhold the **statewide** figure too (Vermont, 2025: private construction
pay is flagged "N" for the state and for 8 of its 14 counties). The build then does not fail. The
state pay becomes the job-weighted mean pay of the counties BLS does publish, and withheld counties
are marked `state_from_counties` (or `national_value` if no county is published either). The state
report card says how many counties were published.

## Which cost source, and why

Both key-free options answered on 2026-11-22. **BLS QCEW construction wages** was chosen over
BEA regional price parities: QCEW is by county and is about construction; the price parities
exist only for states and metro areas and measure consumer prices. Recorded in the registry
entry `bls_qcew_construction`.

## Limits

- The HPI is built from Fannie Mae and Freddie Mac mortgages; it is thin in rural areas, where
  values are blank. Blanks are kept, never filled.
- The current year's rate average is partial (`weeks_30yr` shows how many weeks).
- Pay is counted where the employer reports, not where it builds. Labour is only part of
  construction cost; the labour share that turns the wage index into a cost index is a ledger
  parameter, not applied here. Small counties have noisy averages.
