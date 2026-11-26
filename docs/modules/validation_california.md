# California validation: the APR approval pipeline

Code: `src/visionhousing/validation/apr_data.py` (download and streaming reads), `src/visionhousing/validation/california.py` (groups, linking, Kaplan-Meier, rates, records, documents), `src/visionhousing/validation/commands.py` (the command). Settings: `assumptions/validation_california.yaml`. Command: `uv run visionhousing validate-california` (add `--refresh` to download the tables again).

Outputs (generated; do not edit by hand):

| File | What it holds |
|---|---|
| `assumptions/evidence/california_apr.yaml` | Comparison records, the jurisdiction choice, link quality, ADU permit rates by year, stage tables by place and project group, ADU application cohorts by year |
| `assumptions/calibration/apr_timing_donor_ca.yaml` | Transferred low-high ranges for timing and attrition, by project group |
| `assumptions/calibration/adu_rate_plausibility.yaml` | The model's ADU rate per 1,000 eligible lots per year next to California's observed rates |

## Why California

Pennsylvania has no statewide record that follows a housing application to approval, permit and completion. California does. Each city and county files a Housing Element Annual Progress Report (APR) with the Department of Housing and Community Development (HCD), and HCD publishes the tables for every jurisdiction and year from 2018 on the California Open Data Portal. This study uses that record to check how many applications reach each stage, how long each step takes, and how ADU permitting changed when rules changed. It is evidence about California. Nothing in it is a Pennsylvania input unless a scenario borrows a range and labels it transferred.

## Data

| Table | One row is | Fields read |
|---|---|---|
| APR Table A | an application received in the reporting year | jurisdiction, year, parcel number, unit category, application date, proposed homes, streamlining law, status, ministerial or discretionary |
| APR Table A2 | a project that reached a stage in the reporting year | jurisdiction, year, parcel number, unit category, entitlement date and homes, building permit date and homes, completion (certificate of occupancy) date and homes, streamlining law |
| ACS 2020–2024, table B25024 | a Census place or the state | one-unit detached homes (cell 002) |

Both APR files are large (about 90 MB and 300 MB). They are downloaded once into `data/raw/hcd_apr/` (never into the repository) and read in chunks of 100,000 rows, one file after the other. Only the listed fields are read: addresses, project names, notes and coordinates are never loaded. The parcel number is used only to join stages and is never written out. The ACS files are streamed and only California's rows are kept.

Terms: the portal lists the APR dataset with "No restrictions on public use" in its rights field; the licence field says "not specified", and the state's open data handbook releases portal data in the public domain. The reports are self-reported by each jurisdiction and HCD does not verify them. Only counts, day quantiles, shares and rates are stored here. ACS data are public domain.

## Terms

- **Stage**: application submitted, entitlement approved (a discretionary approval such as a use permit or map), building permit issued, completed (certificate of occupancy).
- **Project**: one jurisdiction, one parcel number (digits only) and one unit category. A2 repeats a project in each year it reaches a stage; those rows are joined on this key.
- **Project group**: ADU (accessory and junior accessory dwelling units), SB 9 (duplex or lot split under SB 9, as flagged on the report), ministerial 5+ (five or more homes approved under a streamlining law or a ministerial path), 2–4 homes, other 5+, single-family (detached, attached and mobile homes). Each project gets exactly one group, by rules tested in that order. An ADU stays an ADU even if an SB 9 flag is present, so ADU counts match the state's ADU series and the three by-right paths never mix.
- **Attrition**: the share of applications that has not reached a later stage by a given time: one minus the "reached" share.
- **Right-censoring**: a project still waiting when the data end (31 December of the last reporting year). Its wait is at least the time seen so far.
- **Kaplan-Meier estimate**: the share that has reached a stage by each day when some projects are still waiting. On each day with events, the share still waiting is multiplied by (1 − events that day ÷ still waiting that day). Censored projects count as waiting until the data end, then drop out. A quantile (P50 and so on) is the first day the reached share gets to that level; it is blank when the curve never gets there.
- **Identification quality**: `descriptive` (a before/after or a plain comparison), `matched` (compared with a peer chosen on its earlier trend), `causal` (a design that rules out other explanations). Nothing here is causal.

## How it works

1. **Read.** Each APR table is streamed in chunks; dates before 2000 or after the end of the data are set blank; reporting years outside 2018 to the last complete year are dropped.
2. **Group.** Each Table A application and each Table A2 row gets its project group. A Table A2 project counts as streamlined if any of its rows says so.
3. **Join applications to later stages.** The same application reported twice (same project, same date) is kept once. For each stage, the first Table A2 date on or after the application date on the same project is taken. An earlier permit on the same parcel (for example an older ADU) is not used.
4. **Measure each stage.** For applications from 2018 on: application to entitlement, to building permit and to completion. For Table A2 projects whose first stage date is from 2018 on: entitlement to permit and permit to completion. Cases whose end date is before their start are dropped and counted. Each stage reports the number of projects, how many reached it, the censored share, Kaplan-Meier days (P10 to P90) and the share reached within one to five years. Cells with fewer than 30 projects are left blank.
5. **ADU rates.** ADU homes permitted in a year = homes on Table A2 ADU rows whose permit date falls in that reporting year (the same rule as `ev.adu.california_apr` in `assumptions/evidence.yaml`). The rate divides by one-unit detached homes in the ACS 2020–2024 estimates. The state rate counts every jurisdiction; place rates exist for incorporated cities whose Census place name matches.
6. **Choose places (rule written first).** Candidates are cities with at least 100,000 one-unit detached homes. Two focal cities are named in the settings for a documented pathway difference: San Diego (a local legal pathway: the ADU Bonus Program, adopted in 2020, allows extra ADUs in its transit-served area) and Los Angeles (a process pathway: ministerial ADU plan check at very large scale, with pre-approved standard plans). Each focal city is matched to the candidate whose 2018–2019 ADU permit rate is closest to its own, if within 25%; otherwise it has no peer and its record is a plain before-and-after. The selection code receives only the 2018–2019 rates and refuses any later year. The peer's own local programme is not reviewed.
7. **Comparison records.** Statewide before/after the 2020 state ADU laws; each focal city against its peer (difference in the change of rates), or before/after if it has no peer; SB 9 applications against other small projects (share permitted within one to three years); ministerial against other 5+ projects (median days and share permitted within two years). Each record has what changed, where, date, outcome, denominator, horizon, identification quality and caveats.
8. **Transferred ranges.** For each group and for application to permit, permit to completion and application to entitlement: the low and high, across the state and the chosen cities, of the median days, the 75th percentile days and the shares reached within one to five years.
9. **Plausibility.** The latest saved Pennsylvania run (`web/public/data/PA/runs/adu.json`; Testland if none exists) gives the ADU funnel. Rate = ADUs built over the five years (P10, P50, P90) ÷ P50 eligible lots ÷ 5 × 1,000. It is shown next to the California state rate for each year and the range across the chosen cities.

## How the engine uses it

It does not, by default. The evidence file is for the evidence and plausibility screens. A timing scenario (for example "ADU plus predictable approval") or a financing scenario may read `ranges.<group>.application_to_permit` from the donor file as the likely range of approval time or of the share of applications that reach a permit within one to five years. Wherever it does, the result must say "transferred from California".

## Limits

- Self-reported data. Missing or late reports lower counts; jurisdictions revise past years, so later downloads differ slightly.
- The join is by parcel number. Applications that never join a Table A2 row count as not reaching the stage, so reached shares are lower bounds. The share of applications that joins at all is reported next to every group (`ever_linked_share`).
- Several projects on one parcel with the same unit category (for example two ADUs years apart) merge into one project after the first application date.
- Entitlement is reported only where a discretionary approval exists. ADUs and most ministerial projects have none, so application-to-entitlement for those groups mostly reflects reporting habits.
- Completion reporting is uneven across jurisdictions, so permit-to-completion shares are lower bounds.
- SB 9 is known only from the report's streamlining field. Jurisdictions that leave it blank hide SB 9 projects among single-family and 2–4 projects.
- One ACS period serves as the denominator for every year; unincorporated county areas get no place rate.
- The jurisdiction comparisons rest on two pre-period years and do not test parallel trends. The 2020 state ADU law changed everywhere at once, along with rents, rates and the pandemic.
- The model's eligible lots include attached single-family homes; California's denominator counts detached homes only. California's rates are an upper reference for Pennsylvania, not a target.

## Tests

`tests/test_validation_california.py` runs on small synthetic extracts in `tests/fixtures/apr/` laid out like the published files, with no network: streaming reads keep only the listed fields and valid rows, group order, the first-later-date join (an older permit on the same lot is skipped; duplicates dropped), Kaplan-Meier worked by hand with censoring and blank quantiles, the ADU count rule and denominator, the selection rule (pre-period only, tolerance, minimum size), comparison record fields, donor ranges, the plausibility rate from a saved run, the written files (no parcel numbers or addresses), and the command.
