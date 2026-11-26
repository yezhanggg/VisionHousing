# Module: Practical capacity screen (`engine/capacity.py`)

**What it does.** Multiplies each reform's added homes by a practical capacity
multiplier per tract and draw, from the pack's water-and-sewer readiness table
(`infra_tract`) and hazards table (`hazard_tract`): public sewer service, FEMA
flood zone and steep slope. Wildfire is listed and reported as not measured.
The method, formulas and limits are in
[../methods/capacity.md](../methods/capacity.md).

**Why.** The benchmark reforms count homes that the rules allow and that pencil.
Some sit on lots that cannot be built on in practice. The screen shows how many,
and which screen binds most, beside today's numbers, never in place of them.

## How to run it

```
from visionhousing.engine import run

run("PA", ["adu", "missing_middle", "tod", "parking", "fees"], capacity_screen=True)
run("PA", {"adu": {"variant": "adu_capacity_screen"}})
```

API: `POST /api/v1/run` and `POST /api/v1/compare` (also per scenario) take
`"capacity_screen": true`; the reply gains a `capacity` block. Without it the
reply is unchanged. A reform can also be asked for with
`{"variant": "<reform>_capacity_screen"}` like any named variant.

## Pieces

| Function / type | What it does |
|---|---|
| `load_settings` / `parse_settings` | Reads and checks `assumptions/capacity.yaml` (screens, tables, septic rule, families with their own sewer screen, county-level weights, sentences) |
| `tract_inputs` | Each tract's sewer shares (`scenarios.tod_infrastructure.readiness_shares`; where the pack has `ma_sewer_estimated_tract`, the unknown homes split into `estimated_in`, `estimated_out` and, for placeholder circles, unknown), flood and steep shares, and the county (else state) homes-weighted fill for blanks; plus one "not listed" row. Cached on `StateContext.derived` |
| `multipliers` | Each screen's multiplier for some tract rows and the run's draws, all in [0, 1]; where a reform's own rule already removed lots (`legal_exclusions`: starter-home lots outside a mapped sewer area), the sewer screen works on the lots left |
| `screen_arrays` | One reform's screened homes, the combined multiplier and each screen's own loss; tract by tract, or by county with the exposure entry; `bounds` holds the screened homes at the two ends of the dependence range |
| `_frechet` | Bounds on the share of lots passing every screen from each screen's own share: `max(0, Σa − (K − 1))` to `min(a)`; the product (independence) is inside |
| `screen_result` | The variant path: a new `ReformResult` with the screened headline, a last funnel stage `capacity_screened`, the not-screened total and the losses as records, and a note |
| `screen_wanted` | Whether a reform's options (or its named variant) ask for the screen |
| `build_capacity` / `CapacityOutcome` | The run option: every reform and the package screened beside today's numbers; binding screen, county records, coverage, sentences; `records()` and `to_dict()` |

**Where it hooks in.** `engine.registry.register` wraps every adapter: when the
options ask for the screen, the adapter's result goes through `screen_result`.
So the run, the package reruns, the readouts, the market option and the
comparisons bench all see the same screened reform. `engine.core.compute` takes
`capacity_screen` and builds `Result.capacity` after the package; it refuses a
run that asks for both the run option and a screened reform.

**Same draws.** The `capacity.*` ledger entries are drawn with the run's seed
from each id's own stream, so screened and not-screened numbers pair draw by
draw.

## Files

| File | What it holds |
|---|---|
| `src/visionhousing/engine/capacity.py` | The screen |
| `assumptions/capacity.yaml` | The rules and sentences (no numbers) |
| `assumptions/parameters.yaml` (`capacity.*`) | The thirteen uncertain numbers, all team judgement (two of them, `capacity.estimated_sewer_served_share` and `capacity.estimated_outside_served_share`, read only where an estimated sewer table exists) |
| `assumptions/variants.yaml` | The five `*_capacity_screen` variants |
| `scenarios/extra_common.py` | `pack_table` (also reads `hazard_tract`, and the state-only tables `ma_sewer_estimated_tract` and `mbta_3a_capacity_tract`) |

## Tests

`tests/test_capacity.py` on Testland with small injected tables: "not screened"
equals today exactly, screening never adds a home (by county and draw, and for
the package), unknown sewer and unmapped flood are drawn as ranges (and a state
without tables is screened from ranges and says so), coverage counts, the
variants pair with the run option draw for draw, the same seed repeats, the
binding screen and its sentence, the settings check, and the API option.
`tests/test_capacity_bounds.py`: the bounds by hand (two and three screens),
the range brackets the independent case at tract and county level, one screen
alone collapses the range, starter-home lots are not excluded twice for sewer,
the settings name the new rules, and a run reports the range and its sentence.

`tests/test_tod_existing_capacity.py`: the estimated sewer class (inside and
outside an estimated area drawn as ranges, placeholder circles unknown,
official answers win, a tract not listed stays unknown), the coverage split
(verified / estimated / unknown add to 1; "measured" stays official only), and
a state without the table is unchanged.

```
.venv/bin/python -m pytest -q tests/test_capacity.py -p no:cacheprovider
```
