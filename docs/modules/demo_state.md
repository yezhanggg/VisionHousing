# The demo state: a reconciliation fixture

Code: `scripts/make_demo_fixture.py` (the generator), `tests/fixtures/demo_state/` (its
files), `tests/test_demo_state_reconcile.py` (the checks), `tests/e2e/test_demo_state_ui.py`
(the static site in a headless browser).

## What it is

The demo state (state code `DM`, made-up state number `98`) is a tiny, made-up state.
Nothing in it is real. It has 2 counties, 6 tracts, 54 blocks, 3 transit stops,
3 municipalities and permit counts for 1996 to 2025. Its tables use the same names and
columns as a real state pack, so the engine, the interface and the static exporter read it
unchanged. It is separate from [Testland](testland.md), which stays byte-identical.

## Why

A public decision demo can fail in ways a working map never shows: a total that does not
add up, an unknown turned into a zero, a law change that silently replaces the old rule.
This state holds those hard cases on purpose, and the tests check that every account adds
up in every Monte Carlo draw (one run of the model with one plausible value of every
uncertain input).

## The hard cases

| Case | How the fixture makes it | What must hold |
|---|---|---|
| Two overlapping transit districts | Two rail stations about 0.4 mile apart, so their half-mile station areas overlap | Land near either station is counted once: less than the two areas added, never more than the tract's land |
| Four existing legal units | One station-area tract already allows 4 homes by right (pack table `mbta_3a_capacity_tract`, the engine's table of land already zoned by right near transit) | The transit reform counts only additional legal capacity, max(0, proposed − existing); no tract or draw goes below zero |
| A service area with unknown connection capacity | One Valley County tract's sewer service has no stated capacity: its homes are `unknown` in `infra_tract` | The tract shows "no official sewer record" at 100% and status "unknown" in the API and the export, never a 0% "inside a service area" |
| One appeal | The dated law change has a timeline record with court status `challenged` and no stay | The revised rule is in force from its effective date while the appeal runs |
| A no-change parking minimum | Valley township (all of Valley County) already has no parking minimum | The parking reform adds exactly 0 there in every draw; "What this law changes" says "Already allowed" |
| One unobserved permit stage | `permit_stages.csv`: the application stage has no published date | It stays blank (not zero); no mechanism moves a permit in time, so the yearly split still adds up |

## The frozen law snapshot and the dated test law change

- `muni_zoning_rules.csv` and `municipal_planning.csv` are the law snapshot, read on
  2026-07-15. Ridge city requires 2 parking spaces per single-family home and 1.5 per
  apartment; Brook township 1 and 1; Valley township none.
- `law_timeline.yaml` holds the two dated versions of Ridge city's parking rule: the
  original (in force from 2015-03-01 to 2026-09-01) and the revised one (no minimum, from
  2026-09-01, under appeal).
- `law_change.yaml` describes the change. `apply_law_change(src, dst)` writes a copy of
  the state with only Ridge city's rule row changed (values, adoption and effective dates,
  the date it was read and a note on the appeal).

Both copies are run with the same seed and draws, so a reader sees the original rule and
the revised result side by side. The baseline does not move.

## What the tests check

`tests/test_demo_state_reconcile.py`, for the original and the revised state and two
fixed seeds, 2,000 draws each:

- **State, counties, tracts.** In every draw the counties add up to the state for the
  baseline, each reform and the package, and a tract-level reform's tracts add up to its
  counties.
- **Districts in each chamber.** The two upper-chamber and three lower-chamber districts
  (cut through tracts and across the county line) add up to the state in every draw, for
  the baseline, each reform and the package.
- **Five years and single years.** The five yearly totals add up to the five-year total,
  for the state and each county, for the baseline, each reform, the package and the total.
- **Package accounts.** The package equals the reforms run alone, less the lots counted
  twice, plus the interactions, in every draw; the overlap and interactions are never
  negative. Each named interaction (fees -> ADU, parking -> ADU, fees -> missing middle,
  parking -> missing middle) has its own draws (`PackageOutcome.interaction_draws_by_name`):
  its likely range is read from exactly those draws, and the four add up to the interaction
  total in every draw.
- **Read from its own folder.** The static export of a copy of the demo state in a
  temporary packs folder saves the comparisons (the sparse-data test included) and the
  switch ranges: every step reads the pack from the folder the state was loaded from, not
  from `data/packs/<code>`.
- **Its own name.** The state list says "Demo state (synthetic)" and every profile vintage
  "synthetic test data (Demo state)", never Testland.
- Each hard case above, the frozen snapshot and the same-seed rerun.
- The generator rebuilds every file byte for byte, the state is marked synthetic, and its
  homes and residents add up from blocks to tracts to counties and municipalities.

`tests/e2e/test_demo_state_ui.py` exports both copies with the static exporter, serves
them as plain files, and checks in a headless browser that the Place screen shows the
unknown service area and that "What this law changes" shows the original rule in one
export and the revised rule and county result in the other, exactly as exported.

## How the numbers are made

- One fixed seed (20261126, not Testland's) drives Testland's seeded generator, which
  uses only the standard library's `random()`, so the files do not drift between Python
  versions.
- Tract housing, prices and rents come from Testland's tract rules with higher rents,
  values and incomes, so the transit and missing-middle reforms add homes in some draws.
- Blocks split each tract's homes by the largest-remainder rule, so they add up exactly.
- Permits follow Testland's statewide cycle and rates.
- Water and sewer readiness goes through the real pack's table functions.
- District weights go through the real pack's weight function, from fixed cuts of
  latitude (upper chamber) and longitude (lower chamber).
- Municipalities hold whole tracts, so residents and homes add up exactly.

All settings are constants at the top of the generator.

## How to rebuild and check it

```
uv run python scripts/make_demo_fixture.py --out tests/fixtures/demo_state
uv run pytest tests/test_demo_state_reconcile.py
uv run pytest tests/e2e/test_demo_state_ui.py
```

## Limits

The demo state is for checking code, not for answers. Its numbers have plausible shapes
but no meaning. Do not quote any result from it as a finding. The engine reads no permit-stage
table, so the unobserved application stage is checked in the fixture only.
