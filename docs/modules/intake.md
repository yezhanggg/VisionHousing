# State intake: manifest, value states, readiness and "reproduce this number"

Code: `src/visionhousing/intake/` (`value_state.py`, `manifest.py`,
`readiness.py`, `reproduce.py`, `commands.py`) and
`src/visionhousing/api/intake.py`. Settings: `assumptions/intake.yaml`.
Commands: `visionhousing state-readiness --state XX`,
`visionhousing state-intake --state XX [--out FILE]`,
`visionhousing reproduce ST:run:path`. Addresses: `GET /api/v1/intake/{st}`,
`GET /api/v1/reproduce?id=…`. Tests: `tests/test_intake.py`.

## Why

A new state is only as trustworthy as its inputs. Before its results are
published, a reader should be able to see, for every input, where it came
from, what it measures, when it was read, how it was changed and who checked
it. This module gathers that from records the repository already keeps. It
fetches nothing and types nothing twice.

## Value states

A blank is not one thing. Six words, used everywhere (report card, intake
manifest, plausibility data, command line, interface):

| State | Meaning |
|---|---|
| `true_zero` | Measured, and the count really is zero |
| `missing` | Not measured or not reported; no value stands in |
| `suppressed` | The publisher withheld the value (privacy, small counts) |
| `imputed` | A value is shown, but it was estimated, not measured |
| `not_applicable` | The question does not arise (a census table has no permit stage) |
| `feed_failure` | The source was expected but did not arrive, or arrived empty |

A measured, non-zero value is `observed`. `classify()` checks in a fixed
order: not applicable, then feed failure (a failed feed's zeros are not real),
then suppressed, blank, imputed, zero. `display()` shows a feed failure or a
gap as words, never as "0".

**In the report card.** Each input now has a `value_state`. A table that the
pack's data dictionary lists but the pack lacks, or a table with no rows, is a
`feed_failure`; its coverage cell reads "source failed to load (not zero)"
instead of "0%". `row_states` counts the states row by row: for permits, the
county-years the Census Bureau imputed; for construction pay, the counties
whose pay was estimated from other counties because the publisher withheld it.

## The intake manifest

`build_manifest(state)` returns four groups of rows. Every row has the same
fields; a blank field names its value state in `field_states`.

| Field | Data source rows | Ledger rows | Law rows | Comparison records |
|---|---|---|---|---|
| scenarios | reforms whose report-card inputs read the source's tables | the entry's `used_by` | the provision's reform | the record's policy types |
| source id, URL | registry id and address | the entry's source ids and addresses | the document's source id and page | the bill or record id, its page |
| clause or table | raw files read | citations | citation (section) | citation |
| published | registry vintage | not applicable | enacted or introduced date | enacted date |
| retrieved | download dates in the pack manifest | source access dates | date checked | date retrieved |
| valid period | registry vintage | not applicable | effective date or dated period | effective dates |
| geography | registry geography | statewide | who the law reaches | jurisdiction |
| quantity and unit | dictionary columns with units | label and unit | not applicable | outcome measure and denominator |
| permit stage | from `assumptions/intake.yaml` (permit sources only) | not applicable | not applicable | not applicable |
| coverage | tables fed and their filled share | the inputs it stands in for | scope | geographic scope |
| exclusions | registry limits | what would change it | binding conditions | exclusions |
| terms | licence and terms page | not applicable | document licence | official pages |
| transformation | registry cleaning steps | kind, range and, for this state, local or transferred | summary in our own words | context only |
| reviewer | not applicable (fetched by script) | "checked against a cited source" when sourced | verified reading and date | verified and date |
| version hash | SHA-256 of each raw file (else the pack table) | SHA-256 of the entry | SHA-256 of the fetched law text, or of the row | SHA-256 of the record |

Law rows come from the pack's `state_law_provisions` and `muni_zoning_rules`
tables, `assumptions/law/states/<st>.yaml`, the state's dated records in
`assumptions/law/timeline.yaml` and the state's already-legal calibration file.

## The two fractions

Both are shares of the eligible single-family lots, the ADU benchmark's
exposure.

- **Verified exposure** = lots in municipalities whose rule was read and
  verified / all lots. Lots come from `muni_zoning_rules.sf_lots` when the
  pack has the town-rule table, else from the tract counts of one-unit homes.
  A state with no town-rule table has a measured zero (`true_zero`): no town
  rule was read.
- **Transferred take-up**, per reform: of the take-up parameters listed in
  `assumptions/intake.yaml`, the share not measured in this state. A
  parameter is local when a calibration file names the state and the
  parameter, or when `assumptions/transfer.yaml` lists the state as measured;
  otherwise its value rests on evidence from other places. `lots_share` is
  the share of eligible lots whose take-up uses at least one transferred
  parameter; each applies statewide, so it is 1 whenever any is transferred.
  Testland is `not_applicable`.

## Readiness check

`visionhousing state-readiness --state XX` prints the five steps of the
intake and release standard, each with its checks:

1. National inputs: national tables present, the permit imputation flag
   kept, boundary files and data tiers recorded, no feed failure.
2. Current law: law rows exist, carry effective dates, and some were verified.
3. Local evidence: some lots under a verified local reading; the state held
   out in a validation set (the donor-pool test's held-out states, or the
   pre-registered state confidence check,
   `assumptions/calibration/state_holdouts.yaml`, written by
   `visionhousing validate-state-holdouts`; its coverage is shown in the
   detail but never gates); at least one verified comparison record.
4. Benchmark adapter: every engine input has data or a ledger stand-in;
   every borrowed range widened; every report-card input carries a value state
   and none failed.
5. Publication: the static site holds every benchmark run, each with its three
   hashes and a likely range; the pack has its hash.

A check is `pass`, `fail` (checked, does not hold) or `unknown` (cannot be
checked here, such as law for the synthetic state). A step fails if any check
fails, is unknown if none fails but one is unknown, and passes otherwise.

## Reproduce this number

A record id is `STATE:run:path`, where `path` is the record's id inside the
run (see `explain.md`), for example `PA:adu:reform_results.adu.added`. The
short form `PA:reform_results.adu.added` takes the run from the path.
`reproduce()` reads the saved run in `web/public/data/<ST>/runs/<run>.json`
and returns the record, the command line
(`uv run visionhousing report --state PA --reforms adu --seed 0 --draws 2000 --format json`),
the same run as a `POST /api/v1/run` body, the seed, draws, reforms and
overrides, the run's three hashes, the pack hash and the code version. A run
with ledger overrides or a non-default housing cycle has no command line (the
command line takes neither); the API body still reproduces it. For a state
with no saved run, the address runs the engine once at the default seed and
draws and says so in `run_source`.

## Limits

- The manifest reports what the records say. A field nobody recorded is
  `missing`; it is not filled in by guesswork.
- Publication dates of data files are rarely stated by publishers; the
  registry's edition (vintage) stands in.
- The transferred take-up share counts parameters, not their weight in the
  result; the sensitivity tornado shows which ones matter.
