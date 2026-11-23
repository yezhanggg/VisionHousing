# Method: the evidence library

Code: `src/visionhousing/ledger/evidence.py`. Data: `assumptions/evidence.yaml`. Tests: `tests/test_evidence.py`.

## What it is

The evidence library is a list of real reforms and published studies that the model leans on. Each entry records:

| Field | Meaning |
|---|---|
| `place`, `reform`, `date_of_change` | Where, which of the five benchmark reforms, and when the rule changed |
| `entry_type` | `outcome` (before and after one reform), `cross_section` (many places or projects at one time) or `rule` (a law or definition, no outcome) |
| `what_changed` | The rule change, in plain words |
| `before`, `after`, `findings` | The figures: measure, value, unit, period, and the source it was read from |
| `before_note` | Why there is no "before" figure, when no earlier count exists |
| `rule_summary` | The rule itself, for `rule` entries |
| `sources` | Citation, web address, date read, where in the document, and how it was checked |
| `verification` | The weakest check among the entry's sources (see below) |
| `comparability` | How far the place and its figures carry over to Pennsylvania, and what to watch for |
| `used_for` | `calibration` (sets a range in the Assumption Ledger), `hindcast` (job T4 runs the model on the place and compares), `evidence_card` (shown on a reform page) |

The numbers themselves live only in `assumptions/evidence.yaml`. This note does not repeat them, so the two cannot disagree.

## How each figure was checked

Every figure was read again in its original source before it entered the file. Each source carries one of five levels, strongest first:

| Level | Meaning |
|---|---|
| `primary_document` | Read in the publisher's own report, web page or law text |
| `primary_data` | Computed here from the publisher's own data file (the file is streamed, not stored) |
| `abstract_only` | Read in the published abstract; the full paper was not read |
| `secondary_summary` | Read in another author's account of the original study |
| `excerpt_only` | Seen only as a short excerpt; treat as unconfirmed |

Some rules followed in this pass:

- **One table per series.** Where a city's reports disagree (Seattle's 2022 and 2023 reports print higher counts than the revised series in its 2024 report), the entry uses one table, names it, and notes the difference.
- **State the counting rule.** The California figures are summed from the state's Table A2 data. Two reasonable rules give different totals, so the entry states the rule it used and shows the other total once.
- **Leave out what could not be found.** A five-year Massachusetts projection, a regional Auckland figure and a 2018 California bill could not be confirmed in an original source in this pass. They are named in the comparability notes and left out of the figures.
- **Pages that refuse scripts.** A few publishers refuse scripted downloads. Those pages were read in a browser and the entry says so. Nothing was downloaded into the repository.

## How the ledger points at evidence

A ledger parameter cites an evidence entry by giving a source whose `id` is the entry id, for example `ev.adu.seattle_2019`. Every id that starts with `ev.` must exist in the library. `unresolved_links(ledger, library)` lists any that do not, and a test fails if the list is not empty.

## What the code does

- `load_evidence()` reads `assumptions/evidence.yaml` and checks every entry. It refuses unknown fields, a source without a web address or date read, a figure that cites a source the entry does not list, an outcome without "after" figures, an outcome without "before" figures or a note saying why, a rule without a summary, and an entry that claims a stronger check than its weakest source. The error names the entry.
- `EvidenceLibrary.by_reform()` and `.used_for()` list entry ids, for the evidence cards and the hindcast job.
- `evidence_links()` and `unresolved_links()` connect the library to the Assumption Ledger.

## Limits

- Most entries are single places with their own markets. They inform ranges; they are not forecasts for Pennsylvania.
- Cross-section studies compare places that differ in more than their rules.
- Published counts are revised. The California data in particular change as jurisdictions amend past reports, so a later download will give slightly different totals.
- Three entries rest on abstracts and one on a secondary summary; their `verification` field says so.

## How to add an entry

1. Find the original document or data file. Read the figure there.
2. Add the entry under an id `ev.<reform>.<short_name>`, with a source for every figure, a locator and an honest verification level.
3. Run `uv run pytest tests/test_evidence.py`.
