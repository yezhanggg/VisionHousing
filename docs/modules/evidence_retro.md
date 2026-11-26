# Retrospective reform panel

Code: `src/visionhousing/evidence/retro.py`. Design (written before any estimate): `assumptions/evidence/retro_design.yaml`. Results (generated): `assumptions/evidence/retro_results.yaml`. Command: `uv run visionhousing evidence-retro run` (fetch, estimate, write) or `uv run visionhousing evidence-retro check` (check the results file's records against the comparison-record format, its `design_sha256` against the design file now, and the design against its recorded fingerprint). Tests: `tests/test_evidence_retro.py`.

Treatment-date audit (2026-11-25): rules in `assumptions/evidence/retro_audit.yaml` (block `retro_treatment_audit`, fingerprinted before any re-score), results in `assumptions/evidence/retro_audit_results.yaml`, command `uv run visionhousing evidence-retro audit` (re-score) or `evidence-retro audit-check`, code `src/visionhousing/evidence/retro_audit.py`, tests `tests/test_retro_audit.py`. See "Treatment-date audit" below.

## What it asks

Several U.S. cities have already allowed 2 to 4 homes on lots once zoned for one: Minneapolis (2020), Saint Paul (first phase 2022, duplex-to-fourplex phase late 2023), Portland (2021, second phase 2022), Raleigh (2021), Spokane (2022) and Arlington County, Virginia (2023). The years in brackets are when each rule took effect; the year a case is *scored from* follows the convention in "The design" and is audited below. Did permits for those building types rise afterwards, compared with ordinary cities that did not change their rules?

The answer is **evidence**. It can help set or check the range of a stage parameter, for example how many lots take up a 2–4 unit option. It is never a multiplier applied to a model run.

## Data

| Input | Source | Kept |
|---|---|---|
| Units authorized by permit, by building size, per permit office and year, 2012–2025 | Census Building Permits Survey place files (`bps_place_annual`), one file per region and year | Raw files in `data/raw/bps_place/` (not committed); only yearly rates for the case offices, the comparison mean and the estimates are written |
| Housing units per place and county | ACS 2020–2024 5-year table B25001 (`acs5_summary_file`), streamed; only place and county rows kept | `data/raw/acs/` (not committed) |

**Outcome.** Units authorized by building permits in one year, per 1,000 existing housing units. Five outcomes: units in 1-unit, 2-unit, 3–4 unit and 5+ unit buildings, and the 2–4 sum (the declared primary outcome).

**Permit office.** BPS counts permits by the office that issues them: a city, town, township or a county's unincorporated area. Offices are matched to census places by state and place code, or to a county when the office covers the whole county (Arlington County).

## The design, declared first

`retro_design.yaml` was written before the first estimate. Every later change is listed in its `changes:` with the date and reason (on 2026-11-24 the cut-off date for statewide laws moved from the code into the file; same date, no estimate changed). Its fingerprint is recorded in `assumptions/preregistration.yaml` (first recorded on 2026-11-24, after the first run, so it guards later edits, not the first run): `evidence-retro run` refuses to estimate if the design's values differ from it, and `evidence-retro check` also fails when the results' `design_sha256` (the design file's bytes when the results were made) no longer matches the file. Method of the check: `validation_donor_pool.md`, "Pre-registration check". It fixes:

- **Cases.** Only reforms with an effective date read on an official page and kept in a verified comparison record in `assumptions/evidence/states/`. Durham is left out because its city site refused automated reading, so its date is unverified. California's statewide ADU laws, Auckland (outside the U.S.) and Walla Walla (no official date read) are also left out, with reasons.
- **Treatment year g.** The first calendar year mostly under the new rule: the effective date's year if on or before June 30, else the next year.
- **Horizon.** At most five years (event times 0 to 4), fewer where the data end in 2025. Fewer than two years means descriptive only.
- **Base.** The mean of the three years before g. Using three years lowers the noise from one large project landing in a single year.
- **Comparison pool.** Permit offices with a BPS population of at least 100,000 in 2019, present every year, reporting all 12 months in at least 12 of 14 years, with housing units, outside states with a statewide ADU or middle-housing law in force by `comparison_pool.law_in_force_by` (2025-12-31; computed from the verified records, plus California, Oregon and Maine by name), and not on a short list of cities with known but unverified local reforms. 81 offices pass. A later case also serves as a comparison for an earlier one, but only in years before its own g ("not yet treated").
- **Pass marks.** Pre-trend p at least 0.10, placebo-date p at least 0.10, at least 30 comparison offices and, for the synthetic control, a pre-period fit ratio of at most 0.25.

Any later change is logged in the file's `changes:` list with its date and reason.

## The estimators

All in numpy.

**Group-time effect (difference-in-differences).** For a city reforming in year g and a year t:

ATT(g, t) = (city's rate in t − city's base) − mean over comparison cities of (their rate in t − their base)

This is the Callaway–Sant'Anna idea: each reform year is compared only with cities not treated by then, so cities that reformed earlier never act as controls. Event time e = t − g. A case's headline is the mean of ATT over its post years.

**Event study.** The same quantity for every event time, before and after the reform, averaged across cases for the pooled view.

**Pre-trend check.** The same quantity for years 4 to 6 before the reform (outside the base). If the city was already drifting away from the comparison, these are far from zero. The statistic is the sum of squared coefficients, each divided by how much ordinary cities vary at that event time.

**Placebo date.** Pretend the reform came three years earlier, use only years before the real one, and estimate again. A clear "effect" at the fake date is a warning sign.

**Uncertainty for one city (placebo in space).** With one reformed city per case, the usual formulas do not apply. Instead the same estimator is run on every comparison city as if it had reformed in year g. The spread of those fake effects shows how much an ordinary city moves by chance. The 90% interval is the estimate minus the 95th and 5th percentiles of the fake effects. The p-values of the effect, the pre-trend and the placebo date are the share of fake values at least as large. A narrower interval that only resamples the comparison cities is also stored (`ci_comparison_sampling`); it ignores the city's own noise and is for reference only.

**Uncertainty for the pooled estimate (cluster bootstrap).** Whole cities are resampled with replacement, reformed and comparison cities separately, 999 times. The pooled pre-trend test is a Wald statistic using the bootstrap covariance.

**Synthetic control (Minneapolis).** Non-negative donor weights summing to one that best reproduce Minneapolis's 2012–2019 path (projected gradient on the simplex). Fit ratio = pre-period root mean squared gap ÷ Minneapolis's pre-period mean. Each donor is then treated as if it were Minneapolis to rank the post/pre gap ratio and to give a placebo interval for the mean post gap.

## Identification label

| Label | When |
|---|---|
| `causal` | Pre-trend and placebo-date p at least 0.10, at least 30 comparisons, at least 2 post years (and donor fit at most 0.25 for the synthetic control) |
| `matched` | A comparison was used but at least one check failed |
| `descriptive` | Fewer than 2 post years or fewer than 30 comparisons |

The label grades the design, not the size of the effect. A `causal` record whose interval includes zero says: the checks passed, and no change could be told apart from ordinary cities.

## What the first run found (2026-11-24)

The primary outcome is units in 2–4 unit buildings per 1,000 homes per year. All figures are generated in `retro_results.yaml`; the table below summarises them.

- Every case's 90% interval includes zero. The changes are small next to how much ordinary cities move from year to year (roughly ±0.7 units per 1,000 homes).
- Pre-2020 rates of 2–4 unit permits were already low in every case city (about 0 to 0.4 per 1,000 homes a year), so even a large relative change is a small absolute one.
- The 5+ outcome fails the pre-trend or placebo checks in four cases (labelled `matched`): large apartment permits come in lumps, and a boom just before a reform makes the following years look like a fall.
- The Minneapolis synthetic control reproduces the 2–4 unit path almost exactly before 2020. That is easy with 81 donors and 8 years, so the placebo rank (p about 0.13) is the more useful number; the post gap is close to zero.

## Treatment-date audit (2026-11-25)

**Why.** The first run listed Portland and Saint Paul with a treatment year of 2022. Saint Paul was scored from its first phase (Ordinance 22-1, effective 2022-03-05: small homes, ADUs, small lots). The rule that allows duplexes to fourplexes, and so matches the 2-4 unit outcome, is the second phase (Ordinance 23-43, effective 2023-11-26). Portland's first Residential Infill phase (Ordinance 190093) took effect 2021-08-01 and its second (RIP2, Ordinance 190851) 2022-06-30; the convention that turns these dates into 2022 was not shown next to the result.

**Year convention (published).** Permits are counted per calendar year, so a year is either treated or not; nothing is pro-rated. The treatment year g is the effective date's year if the date is on or before June 30, else the next year. When a rule starts after June 30, its first months fall in the last base year (a partly treated base year, named per case); when it starts on or before June 30, the first treated year includes up to six months before the rule.

| Case | Law and phase scored | Effective | g before | g audited | Partial year | Date read by script on an official page (2026-11-25) |
|---|---|---|---|---|---|---|
| Minneapolis | Minneapolis 2040 policies | 2020-01-01 | 2020 | 2020 | none | Yes (plan date); the 1-3 unit zoning ordinance's own date not confirmed |
| Saint Paul | Ord. 23-43 (Phase 2) | 2023-11-26 | 2022 (Phase 1) | 2024 | 2023 in base | Yes (both phases) |
| Portland | Ord. 190093 (RIP1); RIP2 2022-06-30 | 2021-08-01 | 2022 | 2022 | 2021 in base | Yes (ordinance text; RIP2 page) |
| Raleigh | TC-5-20 | 2021-08-05 | 2022 | 2022 | 2021 in base | No: the city site refused the request (HTTP 403); earlier verified record kept |
| Spokane | Ord. C36232 (interim) | 2022-08-27 | 2023 | 2023 | 2022 in base | Month only ("August 2022"); the city's document server refused robots.txt |
| Arlington County | EHO amendments | 2023-07-01 | 2024 | 2024 | 2023 in base | No: the county site refused robots.txt (HTTP 403); earlier verified record kept |

**Variants (fixed before scoring).** `audited` (the reference): the years above, comparing only with cities that never reformed, so no city with an earlier phase in force sits in the comparison group. `dates_only`: the same years with the original comparison rule (later cases count as comparisons before their own g), to separate the two changes. `any_exposure`: g = the effective date's own year. `first_full_year_all_phases`: the first year every phase was in force all year (Portland 2023). Everything else (pool, outcomes, base, horizon, tests, pass marks) is the original design's. The pool is kept at the original run's 81 offices (see the note below). A reproduction check re-runs the original design on that pool and files and matches the published results exactly.

**Reading rule (fixed before scoring).** The audited variant is the reference record whatever it shows; the original results file stays unchanged as the "before" record. A case is "robust to the date convention" only if its intervals under `audited`, `any_exposure` and `first_full_year_all_phases` all have the same relation to zero. Intervals that include zero are reported as "no change distinguishable from ordinary cities".

**What changed.** All figures are in `retro_audit_results.yaml`; units in 2-4 unit buildings per 1,000 homes per year, 90% placebo interval.

- Saint Paul: before (Phase 1, g 2022, 4 post years) +0.05 (−0.69 to +0.73); after (Phase 2, g 2024, 2 post years) +0.16 (−1.06 to +0.81). Still no change distinguishable from ordinary cities; the interval is wider because only 2024-2025 are observed. Its base years 2021-2023 include Phase 1.
- Portland: unchanged at g 2022, −0.39 (−1.12 to +0.29). With both phases in force all year (g 2023): −0.16 (−1.04 to +0.76).
- Every case's interval includes zero under every variant, so every case is robust to the date convention. All design checks still pass (`causal` label: the checks passed, not that an effect was found).
- Pooled: before −0.01 (−0.26 to +0.25); audited +0.00 (−0.25 to +0.27). Under `any_exposure` the pooled pre-trend p is 0.06, below the 0.10 mark.

**Pool note.** Re-running the unchanged original design today gives a 68-office pool, not 81: more states now have verified statewide ADU or middle-housing records in force by 2025-12-31 and are left out. The audit keeps the original 81 so that before and after differ only in the treatment definition. The original results file has not been re-run on today's records.

## How to use these records

- They are not inputs. Nothing here enters a run.
- Use them to check or bound a stage parameter. Example: if a Pennsylvania 2–4 unit take-up range implies several extra 2–4 unit permits per 1,000 homes a year in the first five years, that is well above what any of these cities saw.
- Read the interval, not the point.
- Never apply a city's change as a multiplier. The reforms, markets and timing differ, and several cases overlap with other changes (parking reforms in Minneapolis and Saint Paul, Oregon's and Washington's statewide laws for Portland and Spokane, the court halt in Arlington).

## Limits

- Permits are units authorized, not built. A reform can move permits between years or to nearby suburbs; that diversion is not measured.
- BPS counts townhouses as 1-unit buildings, so middle housing built as townhouses shows up in the 1-unit outcome, not the 2–4 outcome. ADUs are often not counted as new units at all.
- The 2020–2021 pandemic and the 2022–2023 rate rises hit every city; the comparison removes only the shared part.
- The housing-unit denominator is 2020–2024, partly after some reforms. The stock moves a few percent at most, so the bias is small and toward zero.
- Comparison cities may have changed their own rules without a record here; that would pull estimates toward zero. One listed reformer (Charlotte) has no city permit office of its own in the BPS file, so its exclusion has no effect.
- One reformed city per case: the interval shows chance variation among cities, not uncertainty across many reforms.
