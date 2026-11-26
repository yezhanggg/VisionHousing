# Evidence grades, number trace and one-assumption switches

Code: `src/visionhousing/grades/` (`rule.py`, `inputs.py`, `grade.py`, `trace.py`, `switch.py`), `src/visionhousing/api/grades.py`. Rule: `assumptions/grades.yaml`. Tests: `tests/test_grades.py`.

Three trust tools. Each reads a finished engine run and changes nothing in it.

## Evidence grades

**What it answers.** "How much evidence stands behind this number?" Every headline number gets a letter, A (most) to D (least): each reform's state figure, each county's figure, and the baseline.

**Terms.**

- *Component*: one kind of evidence, scored from 0 to 1.
- *Weight*: how much a component counts. The weights are in `assumptions/grades.yaml`.
- *Threshold*: the lowest weighted score that earns a letter.

**The five components.**

| Component | Input | Score |
|---|---|---|
| Source quality | The number's own source ids. Each is classed: a registry source takes its tier (local, many states, every state); a registry source the state's pack did not fetch is "not in the pack"; any other id is a published study, benchmark or evidence entry | Mean of the class scores |
| Back-test coverage | How often the baseline's past ranges (P10 to P90, built from other counties) caught the real five-year total. A county uses the counties of its own place type (the grouping the baseline uses); a state number uses all counties | `min(1, coverage / target) × min(1, cases / full_cases)` |
| Law coverage | Share of single-family lots (ADU, missing middle, the ADU bills) or of residents (other reforms) under verified local zoning reads. A state-law reading on file for the reform's legal-status parameter gives a floor | `min(1, share / full_share)`, at least the state-law credit |
| Calibration | For each of the reform's key fit and behaviour parameters: *local* (measured or read in this state), *transferred* (measured elsewhere and borrowed, `assumptions/transfer.yaml`), *sourced* (a published figure) or *judgement* | Mean of the status scores |
| Reform evidence | The best identification among verified outside records with an observed result for the reform's policy type (cross-state records and the retrospective panel). Causal beats matched beats descriptive; a verified law with no measured result counts as "law on record" | Score of the best identification |

**Which retrospective-panel file.** The reform-evidence component reads the panel's records from the treatment-date audit, `assumptions/evidence/retro_audit_results.yaml` (its `records`: the audited variant, which the audit's pre-registered reading rule makes the panel's reference record; it scores Saint Paul from 2024 instead of 2022). If that file is missing or has no records, the grades read the original run, `assumptions/evidence/retro_results.yaml`, and log a warning. Each grade's evidence detail says which file it used (`retro`: `file`, `variant` audited or original, `fallback`). The switch changed no letter or score in Pennsylvania, Vermont, Massachusetts or Wyoming: every case keeps the same design label (causal) in both files, and the grade reads only the label, not the size of the effect.

A component that does not apply is left out and the others are rescaled: the fee cut needs no local zoning read, and the baseline has no law, calibration or reform-evidence component. Which components apply is fixed per reform, never by the data.

**The letter.** Score = the weighted mean of the components that apply. The letter is the first one whose threshold the score reaches.

**The sentence.** Each component writes a few words from its inputs, for example "national data plus published studies; past ranges caught [share] of [cases] back-test cases in counties like this (small counties); local rules unread; lot fit borrowed from Pennsylvania, take-up set by judgement; reform tested elsewhere (causal design)". Every share and count in it comes from the run and the files.

**Why the rule is monotone.** Each component score never falls when its input gets better (a higher tier, more coverage, more cases, a local calibration, a stronger design). The weights are fixed and positive, and the letter is a fixed cut of the score. So a better input never lowers a grade. The tests check this for every component and for random scores.

**What a grade is not.** It is not a probability. It does not widen or narrow any range. It sits beside the two confidence signals (baseline performance and reform evidence) and does not replace them.

## Number trace

**What it answers.** "Where does this number come from?" For one record id (`reform_results.adu.added`, or `reform_results.adu.counties.<county id>`), a waterfall of steps. Each step has its middle value (P50), its likely range (P10 to P90), what was applied to the step before, its ledger ids (all, and the ones new at that step) and its sources.

**How the steps are found.** Nothing is re-estimated; every step is read from the run.

- *Tract-level reforms* (ADU, missing middle, transit-oriented density, the ADU bills): the reform module's own funnel stages (eligible, fit, feasible, take-up, authorized), summed over the state's or the county's tracts in each draw, then P10 / P50 / P90 over the draws. "Applied" is the step divided by the step before **in each draw**, given as P10 / P50 / P90 of those per-draw ratios (draws where the step before is zero have no ratio and are counted out); it is never one percentile divided by another. The chain ends at the step named "added units authorized" (the headline). A later "net added homes after demolition" step is shown as a note.
- *Fee cut and parking, state number*: the module's explanation steps at central values. Each step's formula names the inputs it multiplied, divided or added, and the trace does that arithmetic again from the recorded inputs. The last step is the likely range over the draws. Its middle need not equal the central-value chain, because in the draws every input varies at once.
- *Fee cut and parking, county number*: from the module's per-draw arrays for the county: baseline units the saving reaches, the saving per unit, added units per baseline unit, and the added units (for parking, split into the cost channel and the site-yield gain).

**The check.** The last step must equal the number the run reports, within the rounding tolerance in `assumptions/grades.yaml`. For the funnels the first step times every "applied" ratio, multiplied draw by draw, rebuilds the reported middle value (`chain_p50`); the same holds for the county traces of the fee cut and parking (baseline units reached times added units per unit reached, per draw). For the central chains, every step that names its inputs must recompute to its recorded value. The tests run this for every reform and every county of the synthetic state.

**Reproduce this number.** When the state-intake module's lookup is installed (`visionhousing.intake.reproduce`), each trace also carries its receipt: the command, seed, draws and the run's code, data and assumption fingerprints.

## Switch off one assumption

**What it answers.** "What if this one assumption were at its low or high value?" For each reform, the top three drivers from the tornado (`sensitivity.tornado`), each at low, central and high: the input's value and the state's five-year total. A page flips a three-way switch and shows the stored total.

**Where the numbers come from.** The tornado pins every input at its central value and moves one at a time. When the static export already saved the reform's tornado with its run (and that run's code, data and assumption fingerprints match the live run), those stored rows are used. Otherwise the tornado is worked out now with the same function. The tornado does not depend on the seed.

**Likely ranges and counties.** When the engine's switch runs exist (`sensitivity.switch_runs`: the input fixed at low, central or high, every other input drawn as in the run, same seed and draws), each driver row also carries `ranges`: a P10 / P50 / P90 record per setting for the state, and for one county when `county=` is given. They are read, never computed, from the engine's caches (worked out by `visionhousing switch-ranges --state PA`) or from the static file `<ST>/switch-ranges/<reform>.json` when its fingerprints, seed and draws match the run. `ranges_from` says which (`engine`, `stored` or none).

**Limits.** Without switch runs, a switch total is a pinned-input total for the state, not a likely range, and has no county split. The central total can differ from the middle of the likely range.

## Endpoints

| Endpoint | Answer |
|---|---|
| `GET /api/v1/grades/{st}?r=&level=state` | Each reform's state headline and the baseline, graded: letter, score, components with their inputs, sentence, rule id |
| `GET /api/v1/grades/{st}?r=&level=county` | The same for every county (`county=` for one) |
| `GET /api/v1/trace/{st}?r=&id=` | The waterfall behind one number. `id` is a record id; with one reform in `r` it may also be `state`, a county id, or `counties` (every county's trace) |
| `GET /api/v1/switch/{st}?r=&county=` | Each reform's top three drivers at low / central / high, with likely ranges (state, and the county if given) when switch runs exist |

With no `r`, the five benchmark reforms are used. The usual scenario fields (`a`, `seed`, `draws`, `preset`) apply.

**Static site.** The final export can save `grades.json` (state level), `grades-county.json`, `trace/<reform>.json` and `trace/<reform>-counties.json`, and `switch.json` per state, by calling these endpoints.
