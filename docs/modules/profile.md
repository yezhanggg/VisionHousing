# Module: Place profiles (`src/visionhousing/profile/`)

**What it does.** Describes one place, a county or a census tract, in short plain sections: who lives there, what the homes are like, how many homes are being permitted, how close homes are to frequent transit, and how good the data are. It is built for market research: easy to read, share, print and reuse.

**Why.** The reform estimates answer "what would a state law change?". People also ask "what is this place like?". A profile answers that from the same state data pack, with the same rule as the rest of the tool: every number carries its source, its edition and its year, and nothing is typed by hand.

## Terms

- **Fact**: one number (or one short word) about the place, with its label, unit, the text shown to people, its source id (an entry in `src/visionhousing/data/sources.yaml`), the source's edition ("vintage"), the year it describes, the geography it is measured at and, where the source gives one, its 90% margin of error and range.
- **Comparison**: the same measure for a bigger area, with plain words: "higher than the state", "lower than the county", "about the same as the state". A county is compared with its state; a tract with its county.
- **Section**: one topic. It opens with one plain sentence, then lists its facts, then says what it cannot tell you.
- **Margin of error**: the survey's 90% margin. The range shown is the value minus and plus the margin.

## Sections

| Key | Title | Built from | Status |
|---|---|---|---|
| `people` | People | ACS 2020-2024 (`acs5_summary_file`) | built |
| `housing` | Homes | ACS 2020-2024; FHFA price index (`fhfa_hpi_county`, `fhfa_hpi_tract`) | built |
| `jobs` | Jobs and employers | | reserved |
| `permits` | Permits | Building Permits Survey (`bps_county_annual`) | built |
| `pipeline_local` | Local permit pipeline | | reserved |
| `zoning` | Zoning and overlays | | reserved |
| `plans` | Comprehensive plans | | reserved |
| `incentives` | Tax credits and incentive areas | | reserved |
| `transit` | Transit | National Transit Map stops (`ntm_stops`); 2020 Census blocks (`tiger_2020_tabblock`) | built |
| `safety` | Safety | | reserved |
| `owners` | Institutional buyers | | reserved |
| `parcels` | Parcels and property | | reserved |
| `data_quality` | Data quality | the state data report card | built |

A reserved section shows "Coming soon: not available for this place yet" until its module exists.

### How each number is worked out

**People.** Residents and households (owner plus renter homes, B25003) are sums of the tracts. Their margin is the Census Bureau's approximation for a sum: the square root of the sum of squared margins. The share aged 65 or older is a tract's published share, or for a county the tracts' shares weighted by residents. Median household income is a tract's published median; the pack has no county income brackets, so a county's or state's figure is the average of its tracts' medians weighted by households, and is marked approximate. The share of households that rent is renters / households, with the Bureau's margin formula for a proportion. The rent-to-income gauge is median gross rent x 12 / median household income: a rough gauge only, not the share of rent-burdened renters (table B25070 is not in the pack yet).

**Homes.** Building-type shares are units in that type (B25024) / all housing units. Median home value and median gross rent are a tract's published medians; for a county or the state, the bracket counts (B25075 values, B25063 cash rents) are added over the tracts and the median is found by straight-line interpolation inside the bracket that holds it. Vacancy is vacant / all units (B25002). Age of homes (B25034): the share built before 1940 and in 2010 or later. Price changes (1, 5 and 10 years) come from the FHFA annual index: the county index for a county, the tract index for a tract where FHFA publishes one. The pack holds no state index, so county price changes are not compared with the state.

**Permits.** Units authorized by permit, from the Census Bureau's county files. Permits are counted only by county, so a tract shows its county's numbers and says so (`measured_at` is `county`). Facts: units in the last five years and the five before, the change between them, the yearly pace per 1,000 existing homes (five-year total / 5 / ACS housing units x 1,000), the single-family share, units in buildings of 5 or more, and each of the last ten years. The state figure is the sum of its counties. Years the Bureau partly estimated are named in the limits.

**Transit.** A major transit stop follows decision D-07: any rail station, or a bus stop with at least 64 scheduled weekday departures. A home is near transit when its 2020 census block's centre is within 1/2 mile of a rail station or 1/4 mile of a frequent bus stop. Facts: major stops inside the place (stop points as listed; one station can have several), the share of homes near transit by D-07, and the share within 1/2 mile of any major stop. A real pack stores the per-tract counts; for Testland they are worked out from its blocks and stops with the same function the pack build uses.

**Data quality.** One fact per model input with a source: its tier (0 = national files for every state, 1 = published for many states, 2 = local only), status and coverage, from the state data report card. Inputs taken from the Assumption Ledger are named in the limits. The card describes the whole state, so these facts are measured at the state level.

### "About the same"

The comparison words call two values "about the same" when shares are within 2 percentage points, percent changes within 2 points, and other values within 5% of each other. These bands only choose the words; the exact values are always shown next to them. They are display wording, not model assumptions, so they live in `profile/format.py`, not in the ledger.

## Adding a section

Write one module, `src/visionhousing/profile/sections/<key>.py`, with:

```python
def build(ctx: ProfileContext, geoid: str) -> Section | None: ...
```

The key and title are already in `profile/registry.py`. Use `FactMaker` from `sections/_common.py` so the place and its comparison area use one formula and every fact gets its source id and vintage. Every source id must be in `sources.yaml`. When the data are missing, return `None` or raise `MissingDataError` (or `FileNotFoundError`): the profile then shows "No data for this place yet" for that section instead of failing. Any other error is a bug and is raised.

## Use

```
visionhousing profile --state PA --county 42003 [--tract 42003020100] [--format md|html|csv|json] [--out FILE]
```

Without `--out` the profile is printed. `--format html` is a self-contained page (no scripts, no outside requests) laid out for US Letter paper. `--format csv` has one row per fact with every field.

Interface: `GET /api/v1/places/{state}?q=alle` finds counties by name (`level=tract` for tracts); `GET /api/v1/profile/{state}/{geoid}` returns the profile as JSON (`format=md|html|csv` for a download).

Screen: **Place** (`#/place/42003?v=1&s=PA`). Search a county by name, then pick a tract if you like. Each section is a card with its one-sentence summary on top and its facts below. Hover or tap a fact's `[n]` to see its source, edition, year, geography and range. "Copy share link" copies the address, which holds the state and the place. "Print / save PDF" prints the profile on US Letter without the controls.

## Files

| File | What it does |
|---|---|
| `model.py` | `Fact`, `Comparison`, `Section`, `Profile`, each with `to_dict()` |
| `context.py` | `ProfileContext`: the pack's small tables, the source registry and vintages; `Area`: one state, county or tract and its measures (sums with margins, proportions, published or bracket medians, weighted means) |
| `format.py` | Number formatting and the comparison words |
| `registry.py` | Section order, titles, the reserved keys, and the build that never fails for missing data |
| `builder.py` | `build_profile(state, geoid)` and `list_places(ctx, query)` |
| `render.py` | Markdown, HTML, CSV and JSON |
| `sections/*.py` | `people`, `housing`, `permits`, `transit`, `data_quality` |

## Limits

- ACS numbers are five-year survey averages with margins of error; small tracts often have wide margins.
- County and state income are approximate; county and state medians of value and rent are estimated from brackets and can differ a little from published medians.
- Permits are counted by county only; a tract shows its county.
- Transit access is understated where agencies publish no readable schedule feed; distances are straight lines.
- Testland is synthetic: every vintage then reads "synthetic test data (Testland)" and every page says the numbers are not real.

## Tests

`tests/test_profile.py` and `tests/test_profile_api.py`, on Testland:

```
.venv/bin/python -m pytest -q tests/test_profile*.py -p no:cacheprovider
```

They check the model, each section's numbers against the Testland tables, that every fact cites a source in `sources.yaml` with a vintage, that missing tables give "no data" sections, that reserved and newly added sections are picked up, each renderer, the command line, and that the interface returns exactly the builder's numbers.
