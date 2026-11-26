# Module: the extra scenarios (`scenarios/tod_infrastructure.py`, `commercial_land.py`, `starter_lots.py`, `fee_affordability.py`, `extra_common.py`; `engine/extra_adapters.py`)

**What it does.** Runs four named scenarios beyond the five benchmarks: transit zoning with
funded water and sewer capacity, housing by right on commercial land and adaptive reuse
(two separate routes), starter-home lots, and fee deferral or waiver tied to
income-restricted homes. The method, every formula and every limit are in
[methods/extra_scenarios.md](../methods/extra_scenarios.md).

**Why.** The benchmarks are fixed by the Challenge. Policymakers also ask about
infrastructure money, commercial land, small lots and affordability-linked fee relief.
These are kept as separate reforms so the benchmark presets never change.

## Files

| File | What it does |
|---|---|
| `scenarios/extra_common.py` | `pack_table` (optional pack tables, read once per state; tests place a table in `StateContext.derived`), the ramp helper, `in_service_weight`, `per_draw_ratio` |
| `scenarios/tod_infrastructure.py` | `readiness_shares` (verified / outside / constraint / unknown per tract), `infrastructure_funnel` (zoning alone vs funded capacity on the same draws, in-service date, public cost) |
| `scenarios/commercial_land.py` | `eligible_parcels` (NaN = not measured), `commercial_land_funnel` (route 1, BPS-comparable), `reuse_funnel` (route 2, conversions outside the BPS measure) |
| `scenarios/starter_lots.py` | `starter_lots_funnel` (newly splittable lots, keep vs rebuild, appraisal flag, homes replaced) |
| `scenarios/fee_affordability.py` | `fee_affordability` (deferral and waiver on the same draws, restricted homes, fiscal and cash-timing records) |
| `scenarios/tod.py` | `tod_funnel` takes two optional inputs used only here: `extra_cost_per_unit` (comes off the residual land value) and `land_factor` (share of developable land usable); both default to the benchmark |
| `engine/extra_adapters.py` | Registers the five keys; options `funding`, `appraisal`, `mode`, `variant` |
| `engine/registry.py` | `ReformSpec.excludes`: families a reform may not share a package with |

## Inputs and outputs

- Inputs: the state context (tracts, blocks, stops, baseline), the ledger, and when present
  the pack tables `infra_tract`, `parcels_area`, `afford_income_limits`.
- Output: a `ReformResult` like any reform (county level for the funded-TOD and fee
  scenarios, tract level for the others), with records named in the method note.
  `adaptive_reuse` returns zero added units and the record `conversion_homes`.

## API

`GET /api/v1/scenarios` lists every scenario (`id`, `title`, `kind`, `description`,
`reforms`, `runnable`, `run_with`, `excludes`, `variants`, `notes`, `sources`).
`GET /api/v1/reforms` adds `kind` to each reform. Reform ids may be up to 40 characters.

## Tests

`tests/test_extra_scenarios.py` (Testland; injected readiness, parcel and income-limit
tables): readiness shares add to 1; the funding is never credited with feasible permits;
a later in-service date never adds homes and a date past 2031 leaves zoning alone; a grant
from 2027 equals the TOD benchmark on the same draws; more capacity cost lowers zoning
alone; financed lies between; package exclusions; more parcels more homes and unmeasured
counties zero; costlier conversions fewer conversions; conversions stay outside the
package; a smaller minimum lot gives more splittable lots; a split legal today is not
credited; the binding appraisal case never adds; no sewer no lots; a bigger set-aside never
adds homes; no set-aside no restricted homes; HUD limits are used when present; deferred
cash is conserved; the `/scenarios` endpoint. The "no table" notes are checked on Testland
with its optional tables hidden.

`tests/test_testland_extras.py` runs each extra on Testland's own synthetic tables at
central costs: each reads its table and either shows a range above zero or is zero for a
stated economic reason that a market-sensitivity run confirms; the five benchmark reforms
give identical draws with the tables present or hidden.

```
.venv/bin/python -m pytest -q tests/test_extra_scenarios.py -p no:cacheprovider
```
