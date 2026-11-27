# Module: Comparisons (`src/visionhousing/engine/comparisons/`)

**What it does.** Answers the questions that follow the headline, always on the
same simulated futures (same seed, common random numbers): why five reforms
together differ from the sum of each alone, how much reform reaches a target,
how two key ADU inputs trade off, how much worse the answer gets with less
data, and whether the order of the reforms holds. The method, in plain words,
is in [the comparisons method](../methods/comparisons.md).

**Why.** "Reform or no reform" is the Handbook's question; "how much reform",
"what if we had less data" and "which reform is really ahead" are the questions
a policymaker asks next.

## Terms

- **Bench.** One prepared run (data, ledger, baseline, shared samples) kept for
  every comparison, with each reform's run-alone result cached.
- **Common random numbers.** Every rerun uses the same seed and draws, so two
  runs differ only by the one thing changed, and differences are taken draw by draw.
- **Last-in effect.** Package of all five minus the package of the other four.
- **Dial.** One reform setting turned through 3-4 levels (`assumptions/comparisons.yaml`).
- **Data tier.** 0 national, 1 plus state, 2 plus local sources (`assumptions/data_tiers.yaml`).

## Files

| File | What it holds |
|---|---|
| `settings.py` | Reads and checks `assumptions/comparisons.yaml` and `assumptions/data_tiers.yaml` |
| `bench.py` | `Bench`: the prepared run, `alone(key, options)`, `package(keys)` by the engine's own package rules (overlap first, then each interaction), with every rerun cached |
| `interactions.py` | Net interaction (state and county), last-in effects and interaction shares, the ordered allocation, the named mechanisms with rule-written sentences, the waterfall payload and its exactness check |
| `dials.py` | Each dial's levels (state record and county table), paired steps, "each step adds about X homes", the diminishing-return level, the target seeker (`invert_curve`) and the ADU adoption threshold |
| `matrix.py` | ADU take-up × construction cost: P10 / P50 / P90 per cell, one funnel run per cost column, take-up from the cached feasible stage |
| `ablation.py` | `masked_context` (hide data inputs so fallbacks fire; the masked context keeps the folder its state was read from), `context_at_tier`, the four measures (middle shift paired per draw), tiers 2/1/0, one-input masks and the "hurts most" ranking |
| `ranking.py` | Top inputs per reform by rank correlation; rankings with each at its low and high end; overlapping pairs and paired win shares |
| `common.py` | Records, compact county tables, plain number wording |
| `__init__.py` | `comparisons(state, part=...)`: runs the parts asked for on one bench |

Engine hooks used by the dials and the matrix (benchmark behaviour unchanged):

- ADU adapter option `lot_reach` (a share, or `"owner_occupied"`) and
  `adu_funnel(..., lot_reach=)`; `AduResult.late_stages(m)` recomputes built and
  net from the cached feasible stage with take-up × m.
- Parking adapter option `minimum_share_kept` (`parking_reform(..., min_share_kept=)`):
  the spaces still built are never below that share of today's minimum.
- The TOD decomposition (`decompose`) now builds its low-rise ledger in the
  documented order (base, scenario, variant, user), and reads its variant and
  wider distance from `assumptions/variants.yaml` (`decomposition.tod`).

## Interfaces

- Python: `comparisons("PA", ["interactions", "dials"], seed=0, draws=2000)`.
- Command line: `visionhousing comparisons --state PA [--part dials] [--out file.json]`;
  `visionhousing comparisons --warm --state PA` computes every part (with `market`)
  and keeps each in the disk cache, so the server answers at once;
  `visionhousing run --state PA --data-tier 0|1|2` prints the ranges with data above
  the tier hidden.
- API: `GET /api/v1/states/{st}/comparisons?part=...&seed=&draws=` (state and
  county results only; cached like a run; slow the first time on a large state).
  Each part is also kept on disk (`data/cache/comparisons/<ST>/<fingerprint>/<part>.json.gz`,
  `src/visionhousing/api/comparison_cache.py`) and read back after a restart while
  the pack, code, assumption files, seed and draws are unchanged; see
  [api.md](api.md), "Comparisons disk cache".
  `part=market` (asked for by name only; `all` leaves it out) returns the five
  benchmarks under the package option `market="choice+capacity"`: today's
  package, project choice and the capacity case side by side (see
  [market.md](market.md)).
  `part=tod_factorial` (also by name only; `engine/comparisons/tod_factorial.py`)
  runs the TOD funnel for every cell of service rule (daily count, peak average,
  peak longest gap) x access (straight line, network walk) x distance (the
  benchmark's rail 1/2 / bus 1/4 mile, 1/4, 1/2, 3/4 mile) on the benchmark's stop
  table, ledger, seed and draws, and reports each cell's P10 / P50 / P90, its paired
  difference from the benchmark (draw by draw), its outcome (estimate, small
  estimate, infeasible, no qualifying service, schedules missing, not measurable,
  not measured, not defined), the footprint label, the one-factor-at-a-time
  changes and which factor moves TOD most. Settings: `assumptions/comparisons.yaml`
  (`tod_factorial`); method: [methods/tod.md](../methods/tod.md), "Factorial
  comparison of the transit definition"; tests: `tests/test_tod_factorial.py`.
  The reply carries the text form of every number beside it
  (`src/visionhousing/api/comparison_text.py`): `shown` and `likely` on every
  P10 / P50 / P90, a rule-written `headline` per part, and the words of each
  target row, adoption row, matrix axis, ablation measure and ranking pair, so
  the browser writes no number.
- Browser: `api.comparisons(st, part)` and the selectors in `web/js/lib/api.js`
  (`interactionWaterfall`, `lastInEffects`, `dialCurve`, `dialTargets`,
  `adoptionThreshold`, `costMatrix`, `ablationTiers`, `missingDataRanking`,
  `rankingStability`). The static site reads `ST/comparisons/<part>.json`.

## How it is tested

`tests/test_comparisons.py` on Testland: the bench equals `engine.run` draw by
draw (each reform and the package); the waterfall and the ordered allocation add
up to the package in every draw; a reform without partners has last-in equal to
alone; parking with TOD counts nothing twice; every dial's benchmark level is
the headline exactly; more generous levels never give fewer homes (ADU, fees,
parking); the late-stage cache equals a full rerun; target inversion; adoption
threshold reaches its target and is "out of reach" above the ceiling; the matrix
benchmark cell is the headline and the matrix moves the right way; masks change
what they say; the middle shift is paired per draw, not a difference of medians;
the Testland fixture with all three tiers (read local law added) runs tiers
2/1/0 and a lower tier is never narrower at the default draws; ranking cases and
pairs; settings reject a benchmark level that changes something or a level that
changes another reform; the API endpoint. `tests/test_benchmark_variants.py`
checks the TOD decomposition keeps a user's override on a low-rise target.
`tests/test_cli.py` runs `visionhousing run` at each data tier.
`tests/test_comparisons_cache.py` checks the disk cache (keys, disk hits, a
missing drive, safe writes, the size budget, `--warm`).
