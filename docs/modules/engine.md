# Module: Engine (`src/visionhousing/engine/`)

**What it does.** Runs the model. One call, `run(state, reforms, overrides, seed)`, projects the baseline, runs each reform alone, builds the package of all the reforms asked for, adds tracts up to counties and counties up to the state in every Monte Carlo draw, and returns a `Result`: likely ranges (P10 / P50 / P90) with their formulas, assumption ids, sources and data tier, stamped with three hashes.

**Why.** Every screen, export, document and test calls this one function, so they always show the same numbers.

## Terms

- **Draw.** One run of the model with one plausible value of every uncertain input. The engine does 2,000 by default.
- **P10 / P50 / P90.** The 10th, 50th and 90th percentiles of the draws: "likely between P10 and P90, most likely near P50".
- **Seed.** The number that fixes the random draws. Same seed, same result, bit for bit.
- **Baseline position.** In each draw the baseline sits at one place in its likely range, the same for every county (a statewide housing cycle). Reforms that act on baseline production (fees, parking) use the same position in the same draw.
- **Package.** Several reforms on at once: sum of parts, minus lots counted twice, plus interactions.

## Files

| File | What it holds |
|---|---|
| `core.py` | `run` (entry point, with the result cache), `compute` (the pure core), `Result`, `ReformOutcome`, `PackageOutcome`, `reform_total` (one pinned draw, used by the tornado) |
| `context.py` | `load_context(state)`: reads Testland (`state="TL"`) or `data/packs/<STATE>/`; the only file reading in the engine |
| `registry.py` | The common reform signature, `ReformResult`, `RunContext`, `Draws`, `register` |
| `adapters.py` | Thin adapters for `scenarios/adu.py` (also the two Pennsylvania ADU bills), `scenarios/fees.py`, `scenarios/parking.py` |
| `packages.py` | Package rules: interactions and the lot-overlap hook |
| `montecarlo.py` | The baseline's per-draw scale |
| `hashes.py` | The code, data and assumption fingerprints |
| `records.py` | The record format (see [records.md](records.md)) |

## The reform registry

Each reform registers one function:

```
adapter(context, draws, options) -> ReformResult
```

`ReformResult` holds net added units 2027–2031 by tract (ADU) or county (fees, parking) and draw, the reform's funnel or explanation steps, its other records, the ledger ids behind the headline, plain notes, and the ledger it actually used.

The ledger a reform sees: base ledger with the state's calibration → the scenario's overrides in `assumptions/scenarios.yaml` → the user's overrides (they win) → package changes.

| Key | Reform | Geography | Options |
|---|---|---|---|
| `adu` | Benchmark 1, ADUs | tract | — |
| `pa_hb2186`, `pa_sb1346` | Pennsylvania ADU bills | tract | — (same family as `adu`: only one per package) |
| `fees` | Benchmark 5, fee cut | county | `view`: `benchmark` or `state` |
| `parking` | Benchmark 4, parking minimums | county | — |
| `missing_middle` | Benchmark 2, 2–6 units on single-family lots | tract | `adu_lots` (set by the package) |

Transit-oriented density registers the same way when it is built.

## Shared draws

Every ledger parameter has its own random stream, fixed by the seed and the parameter id. So all reforms in one run see the same values of any parameter they share, draw by draw (for example the cost of a surface parking space in the ADU pro forma and in the parking reform). `Draws.values` holds the run's samples once; the reform modules draw from the same streams.

## Packages (plan §4.5, simple version)

1. Sum of the reforms run alone, in each draw.
2. Minus lots counted twice. ADU and missing middle compete for single-family lots. When both are on, the lots the ADU reform (run alone) builds on are passed to the missing-middle funnel, which takes a share of them out of its pool (`missing_middle.adu_lot_overlap_share`). Missing middle is rerun with the same seed; the overlap is the units it loses, draw by draw. ADUs keep their lots (cheaper and faster than a rebuild).
3. Plus interactions. With the fee cut on, the ADU pro forma's permit and connection fees and the 2–6 unit pro forma's fee line (`fees.fee_total_per_unit`) are cut by `fees.fee_cut_share`. With parking reform on, the ADU's required spaces and the 2–6 unit building's spaces per unit are scaled by `parking.share_still_built_elsewhere`. Each reform is rerun with the same seed (missing middle with the ADU lots already out); the interaction is the difference, draw by draw. The benchmark ADU needs no added parking, so that interaction is zero under the benchmark (the rerun is skipped when it would change no input).
4. Combined = 1 − 2 + 3.

## The Result

| Field | Content |
|---|---|
| `state`, `reform_ids`, `options`, `overrides`, `seed`, `draws`, `cycle` | What was asked |
| `hashes` | `code`, `data`, `assumptions` (64-character SHA-256) |
| `county_ids` | County order of every county array |
| `baseline`, `baseline_counties` | The baseline module's records, unchanged |
| `baseline_draws`, `baseline_county_draws` | The baseline in each draw |
| `reforms[key]` | `added` (state record), `pct_change` (% of baseline, per draw), `counties`, `county_draws`, `state_draws`, `steps`, `records`, `assumption_ids`, `notes` |
| `package` | `sum_of_parts`, `overlap`, `interaction`, `interactions` (by name), `combined`, `pct_change`, `counties`, `waterfall` (the four steps), the draws, `notes` |
| `total` | Baseline + package in each draw; exactly the baseline record when nothing is added |
| `records()`, `to_dict()` | Every record; plain JSON without draw arrays |

## Example

```python
from visionhousing.engine import run

result = run("TL", ["adu", "fees"], seed=0)
for key, out in result.reforms.items():
    print(key, out.added.p10, out.added.p50, out.added.p90, out.pct_change.p50)
print(result.package.combined.formula)
print(result.hashes.short())
```

## Cache and speed

Results are kept in memory, keyed by state, reforms with options, overrides, seed, draws, cycle and the three hashes. `run(..., use_cache=False)` forces a fresh run; `clear_cache()` empties it. The loaded state context is also reused until a data or assumption file changes. `tests/test_engine.py::test_single_reform_speed` times a Testland single-reform run with 2,000 draws.

## Limits

- The baseline position is shared by all counties. County-specific swings are not drawn; at state level they partly cancel, and the state range comes from the state's own backtest.
- Fee and parking savings on baseline production are added; their cross term is not modelled.
- A state without a built pack stops with a message; it never falls back to Testland.

## How it is tested

`tests/test_engine.py` (registry, adapters equal direct calls of the reform modules, records, hashes, cache, context, speed) and `tests/test_engine_invariants.py` (plan §6.5): no reform = baseline exactly; P10 ≤ P50 ≤ P90; counties sum to the state in every draw; tracts roll up to counties; package ≤ sum of parts + stated interactions (with and without missing middle); the ADU / missing-middle overlap is never negative and never above missing middle's own units; interactions never negative; same seed, same result; widening an input range never narrows the output range; the housing cycle moves the baseline only.
