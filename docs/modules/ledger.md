# Assumption Ledger

Code: `src/visionhousing/ledger/`. Data: `assumptions/parameters.yaml`, `assumptions/scenarios.yaml`.

## What it is

Every number the model needs but cannot read from data lives in one file, `assumptions/parameters.yaml`. No such number is written in code.

Each entry has:

| Field | Meaning |
|---|---|
| `label`, `unit` | Plain name and unit |
| `central`, `low`, `high` | The value used by default, and the likely range |
| `shape` | How draws spread over the range: `pert` (smooth hump), `triangular`, or `fixed` |
| `kind` | `sourced` (published), `calibrated` (fitted to evidence), `benchmark` (set by the Challenge), `judgement` (the team's call) |
| `source` | Where it comes from: id, citation, web address, date read |
| `rationale` | Why this value, in a sentence or two |
| `used_by` | Which reforms read it |
| `revisit` | What evidence would change it |
| `status` | `sourced` once checked against a source with a web address; otherwise `draft` |

Draft entries are judgement with deliberately wide ranges. They are the list of things still to source.

## What the code does

- **Load and check.** `load_ledger()` reads the file and checks each entry. A typo in a field name, a low above the high, a benchmark that is not fixed, or a "sourced" entry with no web address stops the load. The message names the parameter, for example `parameter 'adu.loan_share': low <= central <= high`.
- **Overrides.** `apply_overrides(ledger, {...})` returns a new ledger with some values changed. A number pins a value. `"low"` or `"high"` pins one end of the range. A mapping changes only the fields it names. The original ledger stays as it was.
- **Sampling.** `sample(ledger, n, seed)` draws `n` values per parameter with numpy. The same seed always gives the same draws. Each parameter has its own random stream, so adding a parameter does not change the draws of the others. PERT draws are a beta distribution scaled to `[low, high]`; their mean is `(low + 4 × central + high) / 6`.
- **Hash.** `ledger.hash()` is a SHA-256 fingerprint of the whole set. It does not depend on the order of entries or on comments. Every run and export prints it.
- **Scenarios.** `load_scenarios(ledger)` reads `assumptions/scenarios.yaml`: the baseline and five benchmark reforms in the Handbook's own words, plus Pennsylvania HB 2186 and SB 1346. Every override in a scenario must name a real ledger entry.

## Limits

- The ledger holds single numbers with ranges. It does not hold correlations between parameters; draws are independent.
- Evidence figures (for example ADU permits per 100,000 residents) are stored as fixed, sourced entries for cross-checks. They are not funnel inputs.
