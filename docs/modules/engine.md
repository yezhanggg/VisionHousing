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
| `law_floor.py` | `middle_floor_for(state, ledger, method, local=...)`: the dated state floor handed to the missing-middle funnel (Vermont's § 4412(1)(D) schedule from `law/rights.py` plus the pack's mapped sewer share; with `local="town_bylaws"` also the verified town bylaws, `town_levels_for`); `local_parking_minimums(code)`: verified, in-force parking minimums from the Vermont and Wyoming readings for the parking option `minimums: local_readings`. Settings: `local_readings` in `assumptions/variants.yaml`. Scenario modules never read the law files themselves |
| `zone_scope.py` | The zoning-district reading for the named ADU variants `adu_zoning_districts` (verified code only) and `adu_zoning_districts_guidance` (also published guidance, a sensitivity): `district_rule(state, option=..., building_types=..., ledger=...)` turns the pack's `rule_area_weights` tract rows into each tract's already-legal share, `(1 - z) x benchmark share + sum of z_k x legal_k`, handed to the ADU funnel as `legal_rule` (ADU option `legal`); `jurisdiction_mix` gives the law-changes district view. Settings: `assumptions/law/zone_scope.yaml`. See [zone_rules.md](zone_rules.md#engine-use-named-adu-variants-and-the-district-view) |
| `packages.py` | Package rules: interactions and the lot-overlap hook |
| `capacity.py` | The run option `capacity_screen` and the `capacity_screen` reform option (variants `*_capacity_screen`): practical capacity multiplier per tract for sewer, flood zone and steep slope (`Result.capacity`; see [capacity.md](capacity.md)) |
| `market.py` | The package option `market` (`"choice"`, `"choice+capacity"`): project choice on shared lots and a shared market capacity limit, beside today's package (`Result.market`; see [market.md](market.md)) |
| `montecarlo.py` | The baseline's per-draw scale (and `baseline_scale_hook`, used only by named sensitivity runs) |
| `correlated.py` | Conservation checks (state = counties, five years = the years, every draw) and the named correlated-uncertainty sensitivities: macro shock + regional residuals, and high cost + tight credit + slow review (`visionhousing uncertainty-structure`; see [correlated.md](correlated.md)) |
| `hashes.py` | The code, data and assumption fingerprints |
| `records.py` | The record format (see [records.md](records.md)) |
| `readouts.py` | Readouts from the paired draws: shares of the modeled draws (with Monte Carlo standard errors), shares of the baseline, widths, and what each reform costs |
| `variants.py` | Named variants of the benchmark reforms (`assumptions/variants.yaml`): ledger swaps and engine options, checked on load |
| `extra_adapters.py` | Registers the four extra scenarios (five keys) beside the benchmarks; see [scenario_extra.md](scenario_extra.md) |
| `comparisons/` | Interactions, policy dials, the take-up × cost matrix, data ablation and ranking stability, on common draws (see [comparisons](comparisons.md)) |
| `outcome.py` | The outcome contract: years 2027-2031 per draw (state, county, tract), gross and net records, as-of date, data and policy vintages, the audit of each final stage (see [the method](../methods/outcome.md)) |

## The outcome contract (`outcome.py`)

Every headline is additional units authorized by building permits, 2027-2031,
in the baseline's unit. Each `ReformOutcome` carries:

- `gross_net`: `authorized` (the headline, the same record as `added`),
  `net_stock` (net added homes after demolition) and, for missing middle and
  TOD, `removed` (homes demolished) and `baseline_on_sites` (the baseline's own
  permits on the redeveloped sites). `net_stock_counties` holds the county
  records of net added homes for those two reforms.
- `annual`: the headline split into years by the reform's start delay and ramp
  (ledger ids in `assumptions/outcome.yaml`, `timing`). In every draw the years
  add up to the five-year total, for the state and each county;
  `tract_annual(county=None)` gives the same by tract. `annual.timing` says
  whether any mechanism moves permits in time (none does yet) with a zero
  "shifted into the window" record and a "newly feasible" record.
- `accounting` (`outcome.accounting`): the five record types every reform
  carries, (a) the baseline in the permit survey's definition, (b) additional
  comparable authorizations by work type, (c) locally authorized units outside
  that definition, (d) authorized net change if completed, (e) the timing
  ledger, with statuses and the headline's scope. Work types come from the
  module (`detail.work_types`, ADUs and missing middle) or from
  `family_work_type` in `assumptions/outcome.yaml`; they add up to the
  headline in every draw. `site_account` turns the four event fields of a
  redeveloped site (policy and counterfactual units and demolitions) into the
  gross and net accounts. Ids: `reform_results.adu.accounting.records.comparable`.

The `Result` adds `baseline_annual` (each county's own yearly point
projections, scaled to its draw), `package_annual` (each part by its own curve),
`total_annual`, `as_of` and `contract` (as-of date, measure, data and policy
vintages, effective-date rule, conditions held constant, the audit of each final
stage and the baseline's bias-correction decision). `to_dict()` has
`as_of`, `contract`, `annual` and, per reform, `gross_net`,
`net_stock_counties`, `annual` and `accounting`. County and tract years are compact tables
(`AnnualTable`: P10 / P50 / P90 per row and year, one formula and its ids);
state years are full records with their own ids (`reform_results.adu.annual.state.0`).

## The reform registry

Each reform registers one function:

```
adapter(context, draws, options) -> ReformResult
```

`ReformResult` holds net added units 2027–2031 by tract (ADU) or county (fees, parking) and draw, the reform's funnel or explanation steps, its other records, the ledger ids behind the headline, plain notes, and the ledger it actually used.

A state with no calibration of its own borrows the ranges measured elsewhere, widened: for every real state other than the one measured (Pennsylvania), `assumptions/transfer.yaml` stretches the ADU physical-fit position from -1..+1 to -1.5..+1.5 (shares kept between 0 and 1), and the state's Assumption Ledger shows the widened entry with its reason. Testland is never widened.

The ledger a reform sees: base ledger with the state's calibration (or the widened borrowed range) → the scenario's overrides in `assumptions/scenarios.yaml` → a named variant's ledger swaps (`assumptions/variants.yaml`) → the user's overrides (they win) → package changes.

| Key | Reform | Geography | Options |
|---|---|---|---|
| `adu` | Benchmark 1, ADUs | tract | `variant`; `categories` (default on: lot-weighted legal-status categories where read); `lot_reach` (policy dials: a share, or `owner_occupied`) |
| `pa_hb2186`, `pa_sb1346` | Pennsylvania ADU bills | tract | as `adu` (same family: only one per package) |
| `fees` | Benchmark 5, fee cut | county | `view`: `benchmark`, `state` or `deferral`; `variant` |
| `parking` | Benchmark 4, parking minimums | county | `response`: `share` (benchmark) or `provided`; `variant`; `minimum_share_kept` (policy dial: share of today's minimum left in force, 0 by default) |
| `missing_middle` | Benchmark 2, 2–6 units on single-family lots | tract | `method`: `mix` (benchmark) or `prototypes`; `adu_lots` (set by the package); `rivals` (set by the `market` option); `variant` |
| `tod` | Benchmark 3, transit-oriented density | tract | `walk`: `straight` (benchmark) or `network`; `distance`; `decompose` (variant and wider distance from `assumptions/variants.yaml`, `decomposition.tod`; low-rise ledger in the order base, scenario, variant, user); `variant` |

## Named variants (`variants.py`, `assumptions/variants.yaml`)

The five benchmark presets stay exactly as the Handbook defines them. A **variant** is a named alternative run of one reform that answers one question. It changes only two things: **ledger swaps** (a target entry takes the whole range of another ledger entry, so every number stays in `assumptions/parameters.yaml` with its id, range, kind, source and rationale) and **engine options**. The target keeps its own random stream, so a variant is paired with the benchmark draw by draw. Ask for one with the reform option `{"variant": "<name>"}`; the caller's own options win over the variant's, and the user's ledger overrides still win over its swaps. Every variant run carries a note naming it and what it changed.

| Variant | Reform | Changes |
|---|---|---|
| `adu_financing_tight`, `adu_financing_easy` | ADU | loan share and loan-rate spread |
| `adu_full_installed_cost` | ADU | site and utility cost, connection fees |
| `missing_middle_prototype_choice` | Missing middle | `method: prototypes` |
| `parking_provided_spaces` | Parking | `response: provided` |
| `parking_no_response` | Parking | shares still built = 1 (adds exactly zero) |
| `parking_all_surface`, `parking_all_structured` | Parking | parking mix shares |
| `fees_deferral` | Fees | `view: deferral` |
| `fees_documented_local` | Fees | `view: local` (documented city fee schedules where complete, the labelled state judgement elsewhere; `assumptions/fee_schedules.yaml`) |
| `tod_network_half_mile`, `tod_network_quarter_mile`, `tod_network_three_quarter_mile` | TOD | `walk: network`, distance |
| `tod_lowrise` | TOD | storeys, floor area ratio, cost per sq ft, parking cost |
| `tod_ma_acre_share` | TOD | `existing_capacity: acre_share` (the older rule for Section 3A land: a share of district acres taken out of the developable land) |

The methods are in [ADU](../methods/adu.md), [missing middle](../methods/missing_middle.md), [parking](../methods/parking.md), [fees](../methods/fees.md) and [TOD](../methods/tod.md). Four extra scenarios beyond the benchmarks (funded-infrastructure TOD, commercial land and adaptive reuse, starter-home lots, fee relief with affordability terms) are registered by `engine/extra_adapters.py`; see [scenario_extra.md](scenario_extra.md) and [the method](../methods/extra_scenarios.md). A reform's `excludes` (in `register`) names families it may not share a package with; `normalise_reforms` refuses such a package.

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
| `reforms[key]` | `added` (state record), `pct_change` (% of baseline, per draw), `counties`, `county_draws`, `state_draws`, `steps`, `records`, `assumption_ids`, `notes`, `readouts`, `readout_counties`, `readout_texts` (below) |
| `package` | `sum_of_parts`, `overlap`, `interaction`, `interactions` (by name), `combined`, `pct_change`, `counties`, `waterfall` (the four steps), the draws, `notes` |
| `total` | Baseline + package in each draw; exactly the baseline record when nothing is added |
| `records()`, `to_dict()` | Every record; plain JSON without draw arrays |

## Readouts from the paired draws (`readouts.py`)

In each draw the baseline and every reform are worked out from the same sampled inputs, so a reform's added homes in one draw are a **paired difference**: with the reform minus without it, in the same simulated future. Most of the baseline's uncertainty is shared by both sides and cancels. Each reform gets these records (`ReformOutcome.readouts`, ids `reform_results.<key>.readouts.<name>`):

| Name | What it is |
|---|---|
| `chance_at_least_1` … `_4` | Share of the modeled draws in which the state total is at least N homes; N from `explain.chance_threshold_<i>_units`. The key keeps its old name; the words say "in X% of the modeled draws" |
| `chance_at_least_<i>_se` | Monte Carlo standard error of that share, √(p (1 − p) / draws): draw-to-draw noise only (about 1.1 percentage points at p = 60%, 2,000 draws). Shares are printed no finer than it allows (`share_percent`) |
| `baseline_width`, `added_width`, `width_ratio` | P90 minus P10 of the baseline and of the added homes (same draws), and their ratio |

By county (`readout_counties`, ids `reform_results.<key>.readout_counties.<name>.<county>`): `share_of_baseline`, the county's added homes as a percent of its baseline in the same draw. The state figure is the reform's `pct_change`.

**What it costs.**

| Reform | Records | Formula, per draw |
|---|---|---|
| Fee cut | `revenue_given_up`, `cost_per_added_home`, `fee_cut_per_unit`, `share_to_added_homes`; by county `revenue_given_up`, `cost_per_added_home` | revenue = fee cut per unit × (baseline homes + added homes), county by county, summed to the state; cost per added home = revenue ÷ added homes |
| Fee cut, state fee level | `revenue_given_up_state_fees`, `cost_per_added_home_state_fees`, `fee_cut_per_unit_state_fees` | the same, with `fees.pa_assumed_fee_per_unit_judgement` (an assumed figure, a labelled team judgement, not a surveyed fee); the fee reform is rerun once in its `state` view for the added homes |
| Fee cut, fiscal identity | `fiscal_revenue_before`, `fiscal_revenue_after`, `fiscal_revenue_change`, `fiscal_net_loss_per_added_home`, `fiscal_zero_gain_share`; texts `fiscal_by_recipient`, `fiscal_by_year` | F × B, F′ × (B + A), (F′ − F) × B + F′ × A per paired draw; net loss per added home over draws that add homes, beside the share of draws that add none ([fees](../methods/fees.md)) |
| Fee cut, targeted | `revenue_given_up_targeted_<group>`, `cost_per_added_home_targeted_<group>` | the same, summed over the counties of one "places like this one" group (`StateContext.county_types`); no rerun, because the fee cut works county by county |
| Parking | `spaces_avoided_per_home`, `cost_per_space_avoided`, `private_saving_per_home`, `private_saving_total`; by county `private_saving_total` | saving per home = spaces avoided × cost per space, weighted by the baseline homes the reform reaches; total = saving per unit × (baseline + added) homes |

Cost per added home is summarised only over draws where the reform adds homes, and the record's name gives the share of such draws ("homes added in N% of the modeled draws", with the Monte Carlo standard error). When that share is below the ledger minimum `fees.cost_per_added_min_share`, or no draw adds homes, there is no record; `readout_texts["cost_per_added_home"]` says "not defined" instead, with the share and the minimum (never infinity). Fee deferral's cost per added home follows the same minimum. `readout_texts["cost"]` is the plain cost framing: ADUs and missing middle have no direct public cost (administrative cost not modelled); transit-oriented density likewise, and its infrastructure cost is not modelled either.

## Command line

`visionhousing run --state PA [--reforms adu,fees] [--seed 0] [--draws 2000] [--data-tier 0|1|2] [--format text|json]`
prints the baseline, each reform and the package as P10 / P50 / P90. `--data-tier`
hides every data input above the tier (`assumptions/data_tiers.yaml`) so the
fallbacks fire; tier 2 is the full-data headline.

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

Results are kept in memory, keyed by state, reforms with options, overrides, seed, draws, cycle and the three hashes. `run(..., use_cache=False)` forces a fresh run; `clear_cache()` empties it. The cache holds at most `CACHE_SIZE` (16) results and at most about 3 GB of them together (`CACHE_BYTES_DEFAULT`; the environment variable `VH_ENGINE_CACHE_BYTES` sets another cap in bytes). Sizes are estimated by `result_nbytes` (every array buffer counted once; the shared state data and ledger not counted). The oldest results go first; the newest is always kept. These are software settings, not model parameters: they change how often a run is repeated, never a number. `cache_info()` reports hits, misses, size, bytes and the cap. The loaded state context is also reused until a data or assumption file changes, and values worked out from the state's data alone (the transit-rich tract flags) are kept on it (`StateContext.derived`) for every later run. `tests/test_engine.py::test_single_reform_speed` times a Testland single-reform run with 2,000 draws.

**Keeping a finished result small.** A reform's result is tracts × draws, while the funnels work draws × tracts. The result holds a transposed *view* of the funnel's stage (the same memory), not a second copy. When the run is finished, `compute` drops the funnel detail that nothing reads afterwards (`_UNREAD_DETAIL`: feasible shares, break-even rents, TOD's per-tract homes and residual values, and the funnel's own duplicate of the demolition arrays; the reform result keeps the copy the records read). The API, grade cards, comparisons and district views read only the stages, the records and the reform result, so no reader changes. On Pennsylvania this roughly halves a five-reform result. Yearly tract tables are summarised 256 tracts at a time (`outcome.TRACT_BLOCK`), so the tracts × draws × years product never exists in full. None of this changes a number: `tests/test_perf_scenarios.py` checks the slim result, the cache cap, and that the fast bracket share gives the same records as the old per-draw copy.

## Limits

- The baseline position is shared by all counties. County-specific swings are not drawn; at state level they partly cancel, and the state range comes from the state's own backtest. Ledger inputs are drawn independently. A named sensitivity ([correlated.md](correlated.md)) adds regional residuals inside the state range and a joint high-cost, tight-credit, slow-review draw, beside the benchmark.
- Fee and parking savings on baseline production are added; their cross term is not modelled.
- A state without a built pack stops with a message; it never falls back to Testland.
- A state code from any caller is checked before a path is built (`visionhousing.paths.state_code`: two letters; `pack_dir`: a known state; `visionhousing.paths.safe_child`: the folder, as written and with links resolved, stays inside `data/packs/`), so `../x` or an absolute path is refused with an error (`tests/test_engine_paths.py`).

## How it is tested

`tests/test_readouts.py`: cost per added home is "not defined" (never infinite) when nothing is added; fee revenue by county sums to the state in every draw; chances and widths follow the draws and the ledger; the paired difference is narrower than the baseline, the total and an unpaired difference; county shares of the baseline; the targeted and state-fee variants; the parking saving; the "no direct public cost" texts.

`tests/test_engine.py` (registry, adapters equal direct calls of the reform modules, records, hashes, cache, context, speed) and `tests/test_engine_invariants.py` (plan §6.5): no reform = baseline exactly; P10 ≤ P50 ≤ P90; counties sum to the state in every draw; tracts roll up to counties; package ≤ sum of parts + stated interactions (with and without missing middle); the ADU / missing-middle overlap is never negative and never above missing middle's own units; interactions never negative; same seed, same result; widening an input range never narrows the output range; the housing cycle moves the baseline only.
