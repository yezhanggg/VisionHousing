# Legislative districts (`districts/`, `data/sources/sld.py`, `api/districts.py`)

Code: `src/visionhousing/districts/` (`weights.py`, `aggregate.py`, `results.py`, `build.py`,
`commands.py`), `src/visionhousing/data/sources/sld.py`, `src/visionhousing/api/districts.py`,
`visionhousing districts --state PA` (adds the tables to a built pack).
Rule: ledger id `districts.split_caveat_share` in `assumptions/parameters.yaml`; the plain
limits in `assumptions/limits.yaml` under `districts`.
Tests: `tests/test_districts.py` (offline, on made-up Testland districts).

## What it does, in plain words

A legislator asks what a reform means where their voters live. The engine works out added
homes per census tract (ADUs, missing middle, transit-oriented density) or per county (the fee
cut, parking, and the baseline). Districts do not follow tract or county lines. This module
shares the engine's results out to the districts of each chamber (state senate = upper
chamber, state house = lower chamber) by **2020 housing units**, draw by draw, and only then
takes the likely range.

Pennsylvania has 50 Senate and 203 House districts; Vermont has 16 Senate and 109 House
districts (several elect more than one member).

## Terms used

| Term | Meaning |
|---|---|
| Census block | The smallest Census area. Districts are drawn from whole blocks, so each block is in exactly one district. |
| Block equivalency file | A Census list of every 2020 block and the district it is in (Redistricting Data Program). |
| SLDU / SLDL | Census codes for upper and lower chamber state legislative districts. |
| Weight | Share of a tract's (or county's) 2020 homes that lie in a district. |
| Split tract | A tract with homes in two or more districts of the same chamber. |
| Split share | Share of a district's 2020 homes that lie in split tracts. |
| Draw | One possible future out of the engine's Monte Carlo runs. |
| P10 / P50 / P90 | The 10th, 50th and 90th percentiles over draws: the likely range and its middle. |

## Steps

1. **Fetch** (`data/sources/sld.py`). For each chamber, read the state's member of the Census
   2022 block equivalency zip by HTTP range requests (about 0.5 MB compressed for
   Pennsylvania) and keep `geoid, district` in `data/raw/districts/`. Download the 2024
   cartographic district outlines (`cb_2024_{state}_{sldu|sldl}_500k.zip`) for names and map
   shapes. Both are keyless. The 2020 block housing units come from the block table the pack
   already uses (`HOUSING20`, the same count as the Census redistricting table H1).
2. **Weights** (`districts/weights.py`). Join blocks to districts. For every tract:
   `weight(tract, district) = 2020 units in the tract's blocks in the district / 2020 units in
   the tract`. A tract with no homes is shared by land area. The same is done for counties.
   Weights are rounded to four decimals by the largest-remainder method, so each tract's
   weights add up to exactly 1.0000; rows with weight zero are not stored.
3. **District list.** Name, 2020 units, number of tracts and counties touched, split share and
   county split share.
4. **Outlines.** Simplified (Douglas-Peucker; 0.003 degrees for the upper chamber, 0.002 for
   the lower, about 300 m and 200 m) and written with four decimals to
   `boundaries/districts_upper.geojson` and `boundaries/districts_lower.geojson`.
5. **Aggregate per draw** (`districts/aggregate.py`, a pure function). For every draw `k`:
   `district[d, k] = sum over tracts t of weight(t, d) x tract[t, k]`. Then P10 / P50 / P90 per
   district. Percentiles are never added or weighted: the median of a sum is not the sum of
   medians. Each tract's stored weights are divided by their sum, so the districts add up to
   the state in every draw.
6. **Which draws.** The baseline uses the county draws and county weights. A tract-level reform
   uses its per-tract draws (`ReformResult.added`) and tract weights. A county-level reform
   (fee cut, parking) uses its county draws and county weights. The package adds its tract
   part (tract weights) and its county part (county weights) in every draw.

## Pack tables

| Table | One row per | Columns |
|---|---|---|
| `districts` | district | `chamber, district, name, units, tracts, split_share, counties, county_split_share` |
| `district_tract_weights` | district and tract with weight above zero | `chamber, district, tract, units, weight` |
| `district_county_weights` | district and county with weight above zero | `chamber, district, county, units, weight` |

Plus `boundaries/districts_{upper,lower}.geojson` and `district_vintage.json` (which plans,
outlines and housing counts were used). A full build (`make data`) adds them as its first
add-on step; `visionhousing districts --state PA` refreshes them alone.

## The interface

`GET /api/v1/states/{st}/districts?chamber=lower&r=adu,fees` returns, for every district of the
chamber, the baseline and each reform's added homes (and the package with two or more
reforms) as P10 / P50 / P90, the split shares, a `caveat` flag, the plans' vintage, a
`state_check` (districts summed per draw, equal to the state ranges), and a `brief` block. The
brief names what a one-page district brief shows: the baseline and each reform's range, a
pointer to each reform's top driver sentence (`explanation.drivers.<key>` of
`POST /api/v1/run` with `drivers=true`; the drivers are statewide), and pointers to the
reform's limits and the district limits. `district=<code>` returns one district only.
Testland answers with made-up districts (`synthetic: true`).

The reply is dressed for the screens by `api/districts.py` (`dress`), which changes no number:
every range carries `shown` (the engine's own number writer); each district adds `per_1000`
(the same ranges per 1,000 of its 2020 homes: the 2020 count is one fixed number, so this is the
range scaled, worked out from the unrounded percentiles), a `headline` (the package with two or
more reforms, else the one reform, else `null`), a `class` for the map (`q1` to `q4` by the
fixed per-1,000 breaks of `assumptions/decision.yaml`, `none` with no reform on), the split
share and 2020 homes as text, a `caveat_text` when flagged and `counties_touched` (each county
with some of the district's homes: name and the share of the county's 2020 homes inside the
district, from the county weights; the "find my district" search uses it). The top adds
`classes`, `threshold_shown` and `limits` (districts, general, baseline and each reform, from
`assumptions/limits.yaml`).

`GET /api/v1/states/{st}/district-outlines?chamber=` sends the pack's outlines. For Testland it
draws the made-up districts as rectangles around their bands of block points
(`weights.synthetic_outlines`), inside the box of Testland's county outlines, so the map layer
can be tested; no number comes from them.

## The split-share caveat

Inside a split tract, the tract's added homes are shared by where homes stood in 2020. That
assumes new homes follow existing homes inside the tract. A district whose split share is above
the ledger value `districts.split_caveat_share` gets `caveat: true`, and the interface shows a
note. The rule changes no number. Vermont House districts are small, so many are flagged.

## What it cannot tell you

- District numbers are shares of tract and county results, not separate estimates.
- County-level results (fee cut, parking, baseline) reach a district only in proportion to its
  homes in each county; they say nothing about where in the county the effect falls.
- The plans are those first used in 2022. A later redraw needs the newer Census file.
- Housing units are 2020 counts; places that grew since then are under-weighted.

## How it is tested

`tests/test_districts.py` builds made-up districts for Testland (bands of longitude and
latitude that cut through tracts), then checks that weights lie in [0, 1] and add up to 1 per
tract and per county (exactly 10,000 ten-thousandths), that a homes-in-no-district block stops
the build, that districts add up to the state in every draw for the baseline and every reform,
that the `state_check` equals the state percentiles, the endpoint's shape and errors, the block
equivalency reader, and the pack retrofit (tables, outlines, manifest).
