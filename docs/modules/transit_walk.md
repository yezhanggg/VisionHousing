# Transit walksheds (`features/transit_walk.py`, `features/walkshed.py`, `data/sources/osm.py`, `data/sources/transit_peaks.py`)

Code: `src/visionhousing/features/transit_walk.py`, `src/visionhousing/features/walkshed.py`,
`src/visionhousing/data/sources/osm.py`, `src/visionhousing/data/sources/transit_peaks.py`,
`visionhousing transit-walk` (adds the tables to a built pack, `data/pack.py::add_transit_walk`).
Rules: `assumptions/transit_walk.yaml` (words) and the ledger ids `tod.frequent_bus_*` and
`tod.walk_*` in `assumptions/parameters.yaml` (numbers).
Tests: `tests/test_transit_walk.py` (offline: a tiny schedule feed and a tiny street grid with a river).

## What it does, in plain words

The transit-oriented density benchmark needs "land near a major transit stop". The first
version ([transit note](transit.md)) counts land in a **straight line** from
each stop. A straight line crosses rivers, rail yards and highways that a person on foot
cannot. This module adds two upgrades and puts them in two pack tables:

1. **A peak frequent-bus rule.** A bus stop is major when buses leave **the same platform
   side** on average **every 15 minutes or better in both weekday peaks** (7-9 am and
   4-6 pm), all routes added. Rail (fixed-guideway) stops stay major by mode.
2. **Network walksheds.** The land a person can reach by walking 1/4, 1/2 or 3/4 mile on
   streets, sidewalks and paths from a major stop, measured on the OpenStreetMap network,
   next to the straight-line circle of the same radius.

The engine reads the result per tract; nothing here changes a reform estimate on its own.

## Terms used

| Term | Meaning |
|---|---|
| Headway | Minutes between buses. "Average headway in a window" = window minutes / departures. |
| Peak window | 7:00-9:00 and 16:00-18:00 on the service day (ledger `tod.frequent_bus_am_*`, `tod.frequent_bus_pm_*`). |
| Platform-level stop | The `stop_id` trips call at: one pole or platform (not a whole station). |
| Side | The direction a pole serves. A pole is split into a main and an opposite side only when trips leave it both ways. |
| Heading | The compass bearing from a trip's previous stop to its next stop, at the pole. |
| Walkshed | The area within a walking distance of a stop. |
| Network walkshed | Reached streets and paths, each widened by the edge buffer, joined, and cut to the straight-line circle. |
| Straight-line buffer | A circle of the same radius around the stop. |
| Dissolve | Join overlapping walksheds so land near two stops counts once. |
| Fallback | A stop whose network is missing or fragmentary uses its straight-line circle; that area is reported separately. |
| OSM | OpenStreetMap, the open map; data © OpenStreetMap contributors, ODbL 1.0. The two `transit_walk` tables are a derived database of it: if published, they are offered under ODbL 1.0 with that attribution (`DATA_PROVENANCE.md`). |
| Overpass API | A public, keyless read-only query service for OSM data. |

## The frequent-bus rule, step by step

| Step | Rule | Where set |
|---|---|---|
| Day | The feed's pinned representative weekday (the date its daily major-stop counts used) if the feed fetched now runs trips that day; else its busiest Tuesday-Thursday | `tod.walk.day_rule` (`assumptions/transit_walk.yaml`) |
| Departures | Trips calling where riders may board; a trip's last stop and no-pickup calls are left out; blank times are interpolated along the trip; frequency-based trips are expanded into runs, timed from the trip's first stop even where riders may not board there; an unreadable `frequencies.txt` row is skipped and counted in the feed's note | `data/sources/transit_peaks.py` |
| Windows | 7:00-9:00 and 16:00-18:00, whole hours | `tod.frequent_bus_am_start_hour` ... `tod.frequent_bus_pm_end_hour` |
| Unit | Counted per platform-level stop, all routes and both GTFS `direction_id` values added: `direction_id` is defined route by route, so route A's direction 0 and route B's direction 0 need not point the same way | `tod.walk.direction_rule` |
| Two-way poles | Split only when one pole serves both directions: trips heading more than 120 degrees from the pole's main 30-degree sector are the opposite side. A trip with no heading joins its route and direction (the headsign with any "via ..." part cut, so "Downtown" and "Downtown via X" are one) | `tod.frequent_bus_opposite_heading_deg` |
| Stations | Each platform is counted on its own and never added to the others; the station is frequent when any one platform side is | `tod.walk.direction_rule` |
| Threshold | Average headway at or under 15 minutes in **both** windows **on the same platform side**: at least 8 departures in each 2-hour window. A commuter-only stop (8 one way in the morning, 8 the other way in the afternoon) does not qualify: the benchmark means all-day frequent service | `tod.frequent_bus_max_headway_min`, `tod.walk.headway_measure` |
| Rail | Every fixed-guideway stop is major | `tod.walk.rail_rule` |
| Matching | Pack stops take the counts of the same stop id within 200 m, else the nearest stop of the paired feed within 50 m (as for the daily counts) | `features/transit_walk.py` |
| Not covered | A bus stop with no current keyless schedule is not major and is counted as not covered in `transit_walk_county` | |

The daily major-stop rule (64 departures a day) is kept next to it in `transit_walk_county`
(`d07_bus_stops`), so the two can be compared county by county.

## The walksheds, step by step

1. **Network.** For each county, the major stops of the county and of its neighbours within
   3/4 mile are listed. The map is fetched in 0.1-degree tiles that touch a 3/4-mile circle
   around one of them (`data/raw/osm/tiles_0.1/`; the query and the excluded way types are
   in `assumptions/transit_walk.yaml`). Ways become straight segments between their nodes.
2. **Projection.** Local sinusoidal projection centred on the county, in metres (equal-area;
   distance error under about 0.5% within 40 km of the centre).
3. **Join the stops.** Each stop joins every network node within 80 m (`tod.walk_snap_m`)
   that lies on a connected network piece at least 1 mile long in total
   (`tod.walk_min_component_m`); each node starts at its straight-line distance.
4. **Walk.** One shortest-path search from all stops at once (Dijkstra) gives every node its
   walking distance to the nearest major stop; this is what dissolves overlaps.
5. **Draw.** For each distance, every segment or part of a segment within reach is widened by
   50 m on each side (`tod.walk_edge_buffer_m`, about one lot depth), joined, and cut to the
   straight-line circles. The network walkshed is therefore never larger than the circle.
   The shapes are drawn on a grid of 10 m square cells in the same projection
   (`network.cell_m` in `assumptions/transit_walk.yaml`): a cell is inside a shape when its
   centre is (`features/planar.py`, plain NumPy). Joining is a logical OR of grids, cutting
   is an AND, and an area is cells x 100 m². Counting by cell centre is an unbiased estimate
   of the true area; see "Grid accuracy" below.
6. **Fallback.** A stop with no node within 80 m, only a fragment of network, or a map tile
   that could not be fetched uses its straight-line circle. That land is added to
   `network_acres_*` and also shown in `fallback_acres_*`, so it can be taken out
   (`tod.walk.fallback_rule`).
7. **Tracts.** Every shape is cut by every tract outline (2020 cartographic boundary file):
   each cell goes to the tract holding its centre (ray casting, even-odd rule), and a tract's
   acres in a walkshed are its cells there. `tract_acres` is the outline's own area
   (shoelace formula), not a cell count. Areas are in acres.

### Grid accuracy

The 10 m cell was chosen so area errors stay under 1%:

| Check | Result |
|---|---|
| 1/4, 1/2 and 3/4-mile circles against pi r² (`tests/test_transit_walk.py`) | within 0.2% |
| One 400 m street widened by 50 m against 2wL + pi w² | within 1% (0.5% in the test) |
| A whole street grid's buffers at 10 m against 1 m cells | within 1% |
| Pennsylvania county totals against the earlier exact polygon method, same major stops (build of 2026-11-24, before the per-platform peak rule) | 1/2 mile: straight-line +0.02% to +0.24%, network -0.4% to +0.5% per county; state ratio 0.699 then, 0.700 now |
| Vermont county totals, same comparison | straight-line +0.10% to +0.18%, network +0.18% to +0.39%; state ratio 0.534 then and now |

The exact polygon method drew circles as 64-sided polygons, slightly smaller than true
circles (about 0.16%), which is most of the small straight-line difference. Per tract the
1/2-mile network acres moved by a median 0.1 acre (largest 2 acres).

## Outputs (state pack)

`transit_walk_tract`: one row per tract that the 3/4-mile straight-line circle touches.

| Column | Meaning |
|---|---|
| `geoid` | Tract code |
| `tract_acres` | Area of the tract outline (land and inland water in the cartographic file) |
| `major_rail`, `major_bus` | Major stops inside the tract (rail; bus meeting the peak rule) |
| `straight_acres_0_25mi` ... `_0_75mi` | Tract area within the straight-line circles |
| `network_acres_0_25mi` ... `_0_75mi` | Tract area within the network walksheds, fallback included |
| `fallback_acres_0_25mi` ... `_0_75mi` | Part of the network acres that is a straight-line fallback |

Shares and ratios are not stored, to keep the pack small: share of the tract =
`network_acres_* / tract_acres`; network-to-straight ratio =
`network_acres_* / straight_acres_*`. Tracts not listed have zero.

`transit_walk_county`: per county, bus stops, those counted from a current schedule
(peak coverage), frequent bus stops (peak rule) and daily-rule bus stops, rail stops, fallback
stops, map tiles read and failed, and the schedule date per feed with its day rule.

## How to run it

```
uv run visionhousing transit-walk --state PA
uv run visionhousing transit-walk --state PA --county 42101 --stop-report outputs/stops.csv
```

The first run downloads the schedule feeds again (peak counts) and the map tiles; later
runs reuse the cache. `--county` writes a partial table (for checks); `--stop-report` also
measures each rail stop's own 1/2-mile walkshed, for checks by hand.

## Pennsylvania, Vermont and Massachusetts (builds of 2026-11-24; Massachusetts 2026-11-25)

| Measure | Pennsylvania | Vermont | Massachusetts |
|---|---|---|---|
| Major stops (peak rule) | 7,394: 964 rail, 6,430 frequent bus (daily rule: 6,175 bus) | 49 frequent bus (daily rule: 29); no stop is classed rail in the map's data | 2,729: 261 rail, 2,468 frequent bus (daily rule: 2,273 bus) |
| Bus stops with a current schedule (peak coverage) | 21,479 of 35,812 (60%); nearly all in Philadelphia, Allegheny, Montgomery, Delaware, Bucks, Chester, Lancaster and Berks; none in 42 counties whose agencies publish no keyless schedule | 1,540 of 1,649 (93%) | 12,155 of 14,855 (82%); Worcester County only 132 of 1,351, because its regional agency's only keyless schedule had expired when read (frequency unknown, so not major) |
| Map tiles read | 65 (none failed) | 12 (none failed) | 196 (none failed; in the first run one Worcester County tile timed out on both Overpass servers and was read on the second run) |
| Stops that fell back to straight lines | 1 (Wawa, Delaware County: not linked to the mapped paths) | 1 | 2 (one each in Essex and Norfolk counties) |
| 1/4 mile: network / straight-line acres | 103,009 / 131,652 = 0.78 | 1,686 / 2,598 = 0.65 | 61,505 / 80,509 = 0.76 |
| **1/2 mile: network / straight-line acres** | **166,735 / 241,516 = 0.69** (0.69 without the fallback) | **3,632 / 6,727 = 0.54** (0.49 without the fallback) | **115,513 / 176,615 = 0.65** (fallback land 860 acres) |
| 3/4 mile: network / straight-line acres | 223,216 / 338,537 = 0.66 | 6,018 / 12,069 = 0.50 | 170,652 / 287,499 = 0.59 |
| Tracts listed | 1,165 | 27 | 1,023 |
| Table size (gzipped) | 23 KB tract + 0.6 KB county | 0.8 KB + 0.3 KB | 20.6 KB + 0.5 KB |
| Build time with a warm cache | about 75 seconds (10 m grids, one county at a time, 1.7 GB peak) | 3 seconds | about 2 minutes |

The first download of the map tiles took about an hour for Pennsylvania (the public
Overpass server answers one tile in 30 to 90 seconds) and a few minutes for Vermont.

In a dense street grid with no barrier, a single station's 1/2-mile network walkshed covers
about 0.80 to 0.87 of its circle (block interiors beyond the 50 m edge buffer, parks, water).
Values well below that point to barriers. Checked by hand (1/2 mile, each station on its own):

| Station | County | Network / circle | Barrier |
|---|---|---|---|
| Matsonford (Norristown High Speed Line) | Montgomery | 0.12 | I-476 and the wooded Gulph Creek valley: few streets reach the platform |
| 30th Street Station | Philadelphia | 0.42 | Schuylkill River, I-76 and the rail yards to the north and west |
| Station Square (the T) | Allegheny | 0.43 | Monongahela River on one side, the Mount Washington bluff on the other |
| Spring Mill (Manayunk/Norristown Line) | Montgomery | 0.41 | Schuylkill River and the steep riverside slope |
| Cornwells Heights | Bucks | 0.35 | I-95 and the Delaware River |

For comparison, Kensington-Allegheny on the Market-Frankford elevated line, in an unbroken
rowhouse grid, scores 0.80.

## What it cannot tell you

- Frequency is **scheduled** service on one weekday. A pinned date can fall on a holiday that
  the agency's calendar does not mark; the date is shown per feed.
- Agencies without a keyless schedule have no counted bus frequency; their stops are not
  major (see `transit_walk_county`). Stops in a neighbouring state are not included, so
  walksheds across a state line are missing.
- OSM is volunteer-mapped. A street with no mapped sidewalk still counts as walkable; a
  fence or an unsafe crossing that is not on the map is not seen. Station entrances are not
  modelled: a stop joins the nearest network nodes.
- The edge buffer counts the lots that face a reached street. Very deep lots, parks and
  water inside the walkshed still count as area: this is eligible land, not practical
  building capacity (the TOD scenario's developable shares handle that).

## How to check it

- `tests/test_transit_walk.py`: a street grid split by a river: with no bridge the far bank
  is out of the walkshed; a bridge next to the stop brings it in; two stops at the same place
  give the same dissolved area; a stop off the network falls back to its full circle; a
  200 m fragment counts as incomplete; the grid areas stay within 1% of exact areas.
  `tests/test_planar.py` checks the point-in-polygon, area and grid-drawing helpers. A small schedule feed checks the peak counts by eye
  (every 10 minutes = 6 departures an hour; last stops and platforms handled), two routes
  with different `direction_id` codes on one street are added, routes turning opposite ways
  at a corner stay one side, a commuter-only stop and a station of weak platforms are not
  frequent, and bad `frequencies.txt` rows are skipped.
- `--stop-report`: the rail stations with the lowest network-to-straight ratio are the ones
  to look at on a map (rivers, highways, rail yards).
