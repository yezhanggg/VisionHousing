# State data report card (`data/report_card.py`)

Code: `src/visionhousing/data/report_card.py`. Tests: `tests/test_report_card.py`,
`tests/test_transit_realdata.py::test_report_card_renders_for_the_built_pack`.

## What it does, in plain words

For one state it lists every input the model needs and says how good that state's data are.
It reads only the state pack (tables, `data_dictionary.json`, and `pack_manifest.json` when
there is one). It fetches nothing and does not read the ledger, so it runs for any built
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
| Coverage | Share of the table's rows where the input's columns are all filled; for transit frequency, the share of bus stops whose departures were counted. |
| Limits | The coverage sentence, the module's own note, and each source's limits from the manifest. Ledger ids that stand in are named. |

An input whose pack columns are absent but which the engine computes from other tables
(transit access, from blocks and stops, as in Testland) is `used`, with a note saying so.

## Inputs checked

Permits; housing stock by structure type; rents; home values; lot sizes and area type;
blocks; transit stops; transit frequency; transit access; house price trend; mortgage rates;
construction wage index; and the always-assumed inputs: single-family-only zoning share,
ADU already-legal share, ADU physical fit, development fees, parking minimums. The list is
`INPUTS` in the module; a test checks every ledger id it names exists.

## Pennsylvania (pack of 2026-11-22)

9 inputs used, 3 available as context, 5 assumed, 0 missing. Rents are filled for 95% of
tracts and home values for 97%; bus departures are counted for 60% of bus stops.
