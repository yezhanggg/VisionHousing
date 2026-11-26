# Permit timing: Philadelphia zoning-to-building stage

Code: `src/visionhousing/data/sources/phl_permit_timing.py` (queries and download), `src/visionhousing/features/permit_timing.py` (counts, lags, Kaplan-Meier). Settings: `assumptions/permit_timing.yaml`. Output: `assumptions/calibration/permit_lag_phl.yaml`. Command: `uv run visionhousing permit-timing` (add `--refresh` to read the City's table again).

## What it does

It measures how long one stage of the approval path takes in Philadelphia, for the "ADU plus predictable approval" case. It writes three small tables:

| Table | What it holds |
|---|---|
| `issue_counts` | New-construction building permits and their homes, by year and by month, for each project group |
| `stage_lag_issued` | Days from zoning permit issued to building permit issued, for building permits issued each year, by group (P10, P25, P50, P75, P90, count, share linked to a zoning permit) |
| `stage_lag_cohort` | Each year's new-structure zoning permits followed to the read date: Kaplan-Meier quantiles, share still waiting (censored), share reaching a building permit after 180, 365, 730 and 1,095 days |

Project groups: 1 home, 2–4 homes, 5 or more homes (from `numberofunits` on the building permit), ADU-like (scope text mentions an accessory dwelling unit, carriage house or in-law unit), and all new homes (the three unit groups together).

## What the City publishes, and what it does not

The City's open permit table (Carto table `permits`) has, for each permit: permit type, type of work, residential or commercial, approved scope (text), status, **issue date**, completed date, certificate-of-occupancy date, denial date, homes (`numberofunits`, building permits only), and the zoning job the building permit was built on (`zoningpermitjobid`). It has **no application or received date**. The older table `li_permits` (2007 to March 2020) has no application date either. Refused and denied applications appear only in small numbers and without a filing date.

The City's ArcGIS `PERMITS` map layer was checked as well (2026-11-24). Its description mentions a "date of application", but its published fields are the same as the table's: issue, completed, certificate-of-occupancy, denial and most-recent-inspection dates, and no application date. See [Cross-state comparison records](evidence_states.md#philadelphia-application-dates-permit-timing).

So the time from applying to being approved cannot be measured. The time from **zoning permit issued** to **new-construction building permit issued** can: it covers preparing, submitting and reviewing the building plans after zoning is settled. It is an observed stage, not the whole approval path. Any application-to-issue lag built on it is **illustrative**.

Pittsburgh's permit file (WPRDC, CC-BY 4.0) has an issue date only. The Allegheny County Conservation District file on WPRDC has an application date but no issue date, covers only earth-disturbance permits of an acre or more, and is shared under CC-BY-SA. Neither gives an application-to-issue lag.

## Terms

- **Right-censoring**: a zoning permit with no building permit yet. We know only that its wait is at least the days since zoning was issued.
- **Kaplan-Meier estimate**: the standard way to estimate the share that has reached an event by each day when some cases are still waiting. On each day with issued permits, the share still waiting is multiplied by (1 − issued that day ÷ still waiting that day). Waiting cases count until the read date, then drop out.
- **Quantile (P50 and so on)**: the first day on which the estimated share issued reaches that level. When the curve never reaches it, the table leaves it blank instead of guessing.

## How it works

1. Two queries run on the City's server and return only dates, homes and yes/no flags (no address, owner, contractor or scope text). The text patterns for "ADU-like" and "new structure, not an addition" are in the settings file and are applied on the server.
2. Building permits get their group. A permit flagged ADU-like goes to the ADU-like group only.
3. Stage lag for issued permits: issue date minus the zoning permit's first issue date. Negative gaps (a zoning amendment issued after the building permit) are dropped and counted.
4. Cohort: new-structure zoning permits from each year are followed. Those with a building permit end on its issue date; the rest are censored at the read date. The Kaplan-Meier curve gives the quantiles and the shares by 180–1,095 days.
5. The file records the read date, the source id, the SHA-256 hash of each query's text, and the limits below.

## How the engine uses it

The table is a calibration file, not part of the state data pack. A timing scenario can read `stage_lag_issued.all_new_homes.by_year` (or one unit group) as the likely range of an extra stage delay, and `stage_lag_cohort.by_zoning_year.*.reached_by_days` as the share that moves from zoning to building permit within one, two or three years. "Predictable approval" can be expressed as moving the P75–P90 days toward the P50 days; that changes when authorizations fall inside the five-year window, not how many projects exist. Label the result illustrative wherever it stands in for application-to-issue time.

## Limits

- No application date: application-to-issue time is not measured.
- Building permits before the late-2019 change of record system carry no zoning link, so 2019 and much of 2020 have few linked cases.
- Censored zoning permits mix projects still in progress, projects that will never be built, and building permits not linked to their zoning permit. The cohort curve flattens, so its upper quantiles are blank and its median is unstable for later years.
- The issued-permit lags describe permits that got through; they leave out projects that stalled.
- Zoning permits carry no home count, so the cohort table is not split by unit group.
- ADU-like permits are found by text and are very few; their lags are anecdotes, not a distribution.
- A 2021 rush ahead of a tax abatement change shapes that year's counts and lags.

## Tests

`tests/test_permit_timing.py` runs on recorded CSV extracts in `tests/fixtures/local/phl_timing_*.csv` with no network: query contents, a Kaplan-Meier example worked by hand, the no-censoring case, group labels, issue counts, stage lags, cohort censoring, the written file and the command.
