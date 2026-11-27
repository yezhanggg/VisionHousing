# State data report card (`data/report_card.py`)

Code: `src/visionhousing/data/report_card.py`. Tests: `tests/test_report_card.py`,
`tests/test_transit_realdata.py::test_report_card_renders_for_the_built_pack`.

## What it does, in plain words

For one state it lists every input the model needs and says how good that state's data are.
It reads only the state pack (tables, `data_dictionary.json`, and `pack_manifest.json` when
there is one). It fetches nothing and does not read the ledger's values, so it runs for any built
state and for Testland.

```
uv run python -m visionhousing.data.report_card PA              # Markdown to the screen
uv run python -m visionhousing.data.report_card TL --out tmp/   # TL_report_card.json and .md
```

## Columns

| Column | Meaning |
|---|---|
| Status | `used`: in the pack and read by the engine. `available`: in the pack as context or evidence; the engine uses a ledger value. `assumed`: no state data; ledger parameters stand in. `missing`: no data and no stand-in. |
| Tier | 0 national files for every state; 1 published for many states (transit schedules); 2 local data only (zoning, parcels, fees). |
| Vintage | The source edition from the manifest's provenance rows; for transit frequency also the range of schedule dates; `synthetic` for Testland; `ledger` for assumed inputs. |
| Coverage | Share of the table's rows where the input's columns are all filled; for transit frequency, the share of bus stops whose departures were counted. When there is nothing to measure, the cell shows the value state in words (for example "source failed to load (not zero)"), never "0%". |
| Value state (JSON `value_state`, `row_states`) | Why a value is or is not there, in the six shared words of [intake.md](intake.md): a table the data dictionary lists but the pack lacks, or one with no rows, is a `feed_failure`; no state data is `missing`; filled rows are `observed`. `row_states` counts the states row by row (imputed permit offices, withheld county pay estimated from other counties). |
| Limits | The coverage sentence, the module's own note, and each source's limits from the manifest. Ledger ids that stand in are named. |

## Inputs that exist only for some states

Some inputs are a state's own tables: Pennsylvania's town-by-town zoning rules, county parcel
records and state bills, and Massachusetts's Section 3A districts, 3A compliance status and
ADU survey. Each carries a `states` list in `INPUTS`. For any other state the card does not
list the input as missing: it moves to a short "does not apply" line (JSON
`not_applicable`, value state `not_applicable`) and is left out of the counts. If such a
table is in a pack anyway, it is shown. For the states it belongs to, the usual value states
hold: `missing` when no copy was read (for example the Massachusetts ADU survey, which
www.mass.gov refuses to scripts), `feed_failure` when the pack promised it but it is absent
or empty.

The Massachusetts files a person saves by hand (the 3A status sheet, categories file and
development tracker, and the ADU survey) are optional. When one was not supplied, the input
says so: a limits line starting "not supplied" names the file under `data/raw/local/ma/`,
the JSON row has `not_supplied: true`, and that file's source is left out of the row's
sources and vintage, so a table built without it never claims its edition. The 3A status
list built from the atlas alone stays `available`; the ADU survey stays `missing`. When the survey copy is supplied (`data/raw/local/ma/ma_adu_survey.csv` with its
`.edition.yaml` note), the row is `available`, marked hand-saved with its edition, and one
limits line gives the observed counts per survey period and the towns that answered
(`features.ma_intake.survey_context`): context, never a rate.

## Add-on tables that failed to load

A full build (`make data`) also runs the add-on steps (districts, affordability,
infrastructure, hazards, transit walk). A step whose source fails is skipped and its tables
are left out. In a real state's pack an absent add-on table is therefore a `feed_failure`,
not missing data: for example the flood-zone, land-cover and slope rows when the land-cover
service times out. Testland has no add-ons and never claims them.

An input whose pack columns are absent but which the engine computes from other tables
(transit access, from blocks and stops, as in Testland) is `used`, with a note saying so.

## Inputs checked

Permits; housing stock by structure type; rents; home values; lot sizes and area type;
blocks; transit stops; transit frequency; transit access; house price trend; mortgage rates;
construction wage index; flood zones, land cover and slope (the hazards add-on); the always-assumed inputs: single-family-only zoning share,
ADU already-legal share, ADU physical fit, development fees, parking minimums; and the local
(Tier 2) tables a state may or may not have: town-by-town zoning rules, county parcel records,
and state bills (Pennsylvania only); and Massachusetts's own Section 3A and ADU survey tables.
The list is `INPUTS` in the module; a test checks every ledger id it names exists.

For a real state the card also says what is thinner than in the calibration state: whether
the ADU fit table is borrowed and widened (`assumptions/transfer.yaml`) and whether the
already-legal share is the national default; for these it reads only the calibration files'
state codes. It also says what share of recent county-years carry Census imputation, and how
many counties have published construction pay (and whether the statewide figure was withheld).

## Pennsylvania (pack of 2026-11-22)

9 inputs used, 9 available as context, 5 assumed, 0 missing (Vermont: 9, 6, 5 and 0
missing; Pennsylvania's town rules, parcels and state bills are listed as not applying there). Rents are filled for 95% of
tracts and home values for 97%; bus departures are counted for 60% of bus stops.
