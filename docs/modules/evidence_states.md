# Cross-state comparison records

Code: `src/visionhousing/evidence/` (schema, loader, matrix, Philadelphia field check, command). Records: `assumptions/evidence/states/*.yaml`. Endpoint: `GET /api/v1/evidence/states` (`src/visionhousing/api/evidence_states.py`). Command: `uv run visionhousing evidence-states [check|matrix|phl-fields]`.

## What it does

Pennsylvania is the tool's primary state. Most of its benchmark reforms have already been tried somewhere else. This module keeps a short, checked record of each of those real laws, court decisions, programs and studies, so the tool can say what happened elsewhere and how much weight that deserves.

It is **context**. No number in these records enters a model run. Numbers that do enter a run live in `assumptions/evidence.yaml` and the Assumption Ledger.

## One record

Each record answers seven questions.

| Question | Fields |
|---|---|
| What changed? | `title`, `policy_types`, `citation`, `bill`, `session` |
| Where? | `state`, `jurisdiction`, `geographic_scope`, `exclusions` |
| When? | `enacted_date`, `effective_dates` (each with what takes effect then) |
| Is it usable yet? | `implementation_status`, `litigation` |
| What was measured? | `outcome_measure`, `observed_result` (value, unit, the page it was read on) |
| Out of what, over how long? | `denominator`, `horizon` |
| How strong is the evidence? | `identification`, `caveats` |

Plus `url` and `sources` (where it was read), `retrieved` (the date it was read), `verified`, `verification_note` and `discovered_via`.

### Terms

- **Policy type**: one of twelve kinds of reform: `adu` (accessory dwelling units), `missing_middle` (two to six homes on lots zoned for one), `tod` (more homes near transit), `parking` (lower or no parking minimums), `fees` (impact and permit fees), `commercial_land_housing` (homes allowed on land zoned for shops or offices), `adaptive_reuse` (converting existing buildings to homes), `starter_lots` (smaller minimum lots), `permit_timing` (review deadlines and approval speed), `infrastructure_finance` (paying for water, sewer and roads), `water_compliance` (proof of water supply before building) and `court_decision`. A record can carry several; the first is its main one.
- **Implementation status**: where the rule is in its life. `pending` (a bill), `enacted` (signed, not yet in force), `legally_effective` (in force), `locally_implemented` (localities have adopted it), `practically_usable` (projects are using it). `stayed` means a court paused it, `struck_down` that a court voided it, `lapsed` that a bill died at the end of its session, `not_law` that the record is a plan, a data program or a study. The first six are the same steps as the law timeline kept with the law tables.
- **Litigation status**: `none_known`, `challenged`, `stayed`, `decided`, `upheld`, `struck`, `not_applicable` or `unknown`, with the court, the date and the citation in the note.
- **Identification**: how a study separates the reform's effect from everything else. `descriptive` counts before and after. `matched` compares with similar places that did not reform. `causal` uses a design built to isolate the effect, such as a difference-in-differences with checked pre-trends. A law with no measured outcome is `not_applicable`.
- **Denominator**: what the count is divided by (lots, cities, households). A count with no denominator cannot be compared across places.
- **Horizon**: how long after the change the outcome was measured. Five years of permits and nine years of housing stock are different things.

### When a record is "verified"

`verified: true` only when the citation and **every date** were read on an official page: a legislature, a court, a state agency or a city site, or, for a study, the publisher's own page. Anything else is `verified: false`, and `verification_note` says exactly what could not be confirmed. A verified record cannot rest on a commercial code publisher's page.

## How records are found

1. **Discovery.** Trackers (the NYU Furman Center Land Use Reform Tracker, the AEI Housing Center tracker, Open States) and searches point to candidate reforms. They are used to find things, never as the citation. Each tracker's terms were checked before any use, and what they allow is recorded in `discovery.yaml` under `trackers`. Where the terms are unclear or do not allow automated reuse, the tracker is only linked.
2. **Official text.** Each candidate is looked up on the official page: bill history, session law, statute section, court opinion, agency page.
3. **Record.** What was read becomes a record. Candidates nobody has yet read in official text stay in `discovery.yaml` as `entries`.

## The completeness matrix

One row per state (all 50 and DC), one column per policy type. Each cell is:

| Value | Meaning |
|---|---|
| `reviewed` | At least one verified policy record of that type exists for the state, and at least one of them is an enacted law (status `enacted`, `legally_effective`, `stayed`, `locally_implemented` or `practically_usable`) |
| `reviewed_no_enacted_law` | Verified records of that type exist, but none is an enacted law: each is a pending or lapsed bill, a struck-down law, or another record that is not law (`not_law`, `unknown`). The official text was read; the state has no enacted law of that type in this panel |
| `discovered_unreviewed` | Something of that type was found (an unverified record, or a tracker or search candidate) but its official text has not been read |
| `none_found` | Nothing was found in the sources searched so far |

`none_found` is **not** a statement that a state has no such law. It says only what this panel has looked at. City records (for example Minneapolis or New York City) count toward their state's row, and the cell names the city. A study never makes a cell `reviewed`, because a study is not a law; the cell's `studies` list shows where outcome evidence exists. A bill read on its official page is verified, but a bill that is still pending, or that died, is not a law either, so on its own it makes the cell `reviewed_no_enacted_law`, not `reviewed`. The cell's `enacted` list names the verified records that are enacted law.

`uv run visionhousing evidence-states matrix` writes `data/outputs/evidence_states_matrix.csv` (one row per state and type, with record ids; `enacted_records` names the enacted ones) and `data/outputs/evidence_states_matrix.json` (one letter per cell: `R` reviewed, `r` reviewed with no enacted law, `d` discovered but unreviewed, `.` none found). Both are generated, not published. The endpoint returns the same matrix.

## Philadelphia application dates (permit timing)

A lag from application to permit needs an application date. The layer description of the City's ArcGIS `PERMITS` FeatureServer (layer 0) says the underlying permit system holds a "date of application". `uv run visionhousing evidence-states phl-fields` reads the layer's published field list and counts the filled rows of each date field.

Checked on 2026-11-24: the layer has five date fields: `permitissuedate`, `mostrecentinsp`, `permitcompleteddate`, `certificateofoccupancydate` and `denialdate`. None is an application, received or filed date. The only field whose name mentions an application is `applicanttype`, a text field. The same columns are in the City's SQL table `permits`, which feeds the data.phila.gov L&I building-permits page; that page charts permits by issue date only. Rows are permits issued, refused or denied; applications still in review are not in either source. So Philadelphia's public data cannot give an application-to-issue lag. The observed zoning-to-building stage lag (see [Permit timing](permit_timing.md)) stays the local measure, and any application-to-issue lag stays illustrative or borrowed from other places for sensitivity.

## Tests

`tests/test_evidence_states.py` (offline): good and bad records (unknown policy type, wrong state prefix, unverified with no note, study with no identification, pending bill with an enacted date, code-publisher page), loading rules (misfiled and repeated ids), the matrix on a small panel (every state and type present, each status, studies and pending or lapsed bills never make a cell reviewed, CSV and JSON agree), every record in the repository loads, the Pennsylvania bills stay pending, the endpoint (all, one state, verified only, bad state) and the Philadelphia field check on a fake client.
