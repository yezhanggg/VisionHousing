# Transit stops and access (`data/sources/transit.py`, `features/transit.py`)

Code: `src/visionhousing/data/sources/transit.py`, `src/visionhousing/features/transit.py`.
Tests: `tests/test_transit.py`, `tests/test_report_card.py` (offline),
`tests/test_transit_realdata.py` (live files, `make test-realdata`).

## What it does, in plain words

The transit-oriented density and parking reforms need to know which homes are near good
transit. This module finds every **major transit stop** in the state and
measures, for every census tract, how many homes and how much land lie near one.

A major transit stop is:

- any **fixed-guideway station**: commuter or regional rail, subway, light rail or trolley,
  funicular (incline), monorail; or
- a **bus stop with at least 64 scheduled departures on a representative weekday**
  (about one every 15 minutes for 16 hours; ledger `parking.transit_bus_min_departures`).

A home is near transit when its census block's internal point is within **1/2 mile of a rail
stop or 1/4 mile of a frequent bus stop**, in a straight line (great-circle distance). The
interface also offers 1/4, 1/2 and 3/4 mile from any major stop.

## Terms used

| Term | Meaning |
|---|---|
| National Transit Map (NTM) | The federal list of transit stops, built from agencies' schedule feeds. |
| GTFS | General Transit Feed Specification: a zip of text tables that describes a schedule. |
| Schedule feed | One agency's GTFS zip. |
| Route type | GTFS number for the vehicle: 0 tram/trolley/light rail, 1 subway, 2 rail, 3 bus, 7 funicular, 11 trolleybus. |
| Representative weekday | The Tuesday, Wednesday or Thursday with the most trips in the feed, after holiday exceptions. |
| Departure | A trip calling at a stop where riders may board. A trip's last stop does not count. |
| Great-circle distance | Shortest distance over the Earth's surface (haversine formula, radius 3,958.76 miles). |

## Inputs

| Source id | What | Cached as (`data/raw/transit/`) |
|---|---|---|
| `ntm_stops` | Every NTM stop in the state's bounding box, 2,000 per page | `ntm_stops_42.csv.gz` (0.8 MB) |
| `ntm_agencies` | Agency names by National Transit Database id | `ntm_agencies_42.csv` |
| `mdb_catalog` | The Mobility Database catalog's schedule feeds for the state | `gtfs_catalog_42.csv` |
| `gtfs_agency_feeds` | Each keyless feed: stops, routes, trips, stop times, calendars, frequencies | `gtfs_<id>_stops.csv.gz` + `gtfs_<id>_note.json` |
| `transitland_atlas` | For agencies the Mobility Database does not list: the operator's current static schedule address, read from the Transitland Atlas catalogue (CC-BY 4.0) at the commit pinned in `assumptions/transit_feeds.yaml` | `gtfs_atlas_56.csv` (rows only for feeds marked `read: true` with legal status `usable`) |

Feeds are read table by table out of the zip with HTTP range requests where the server
allows it (the large `shapes.txt` is never downloaded). The agency's own address is tried
first, upgraded to https; the catalog's mirror is the fallback. A feed that needs a key is
skipped. No feed zip is kept: only per-stop departure counts and a note with the dates.
A note is written even when a feed fails, so rebuilds are identical; delete it to retry.

## Steps

1. Drop entrances and other non-boarding points; drop platforms whose station is listed.
2. Keep the stops inside one of the state's tracts (even-odd point-in-polygon test on the
   full-detail tract outlines). A stop within 0.1 mile of a state block but outside every
   outline (a shoreline) takes that block's tract.
3. Mode from the NTM route types: any fixed-guideway type makes the stop `rail`, else any
   bus type makes it `bus`. Where the NTM has no type, the schedule feed's routes decide.
4. Pair each NTM feed with the schedule feed that has the most of its stop ids at the same
   place (200 m); at least half must match (`paired_by` = `id` in `transit_feeds`). Where an
   agency renumbered its stops, pair **by place** (`paired_by` = `place`) only when all three
   hold: the agency names share a distinctive word (state names and words such as "transit",
   "authority", "regional", "valley", "cape", "greater", "port", "county" do not count);
   half the NTM feed's stops have a schedule stop within 50 m; and the reverse check: half
   the schedule feed's in-state stops lie within 50 m of the NTM feed's stops
   (`parking.transit_feed_pair_reverse_min_share`), or the stop names agree
   (`parking.transit_feed_pair_name_similarity`). So an airport operator whose few stops sit
   at a big agency's stops is not read from the big agency's timetable. A stop takes the
   departures of the same id, or of the nearest stop of that feed within 50 m. A
   `frequencies.txt` row with a blank or unreadable time or headway is skipped and counted
   in the feed's note (`frequencies_skipped`); headways may be written with decimals.
5. Frequency status: `mode_only` (rail: major by mode), `counted`, `stale` (the only keyless
   schedule had expired when fetched; not major), `unknown` (no keyless feed; not major).
6. Per block: the distance to the nearest rail stop and to the nearest frequent bus stop.
   Each point becomes a unit vector; the nearest stop has the largest dot product, found by
   one matrix product per chunk; the distance to it is then computed by haversine. Exact,
   pure numpy, about 4 seconds for Pennsylvania's 330,000 blocks.
7. Per tract (2020 block counts): units and land within 1/4, 1/2, 3/4 mile of any major stop
   and within the major-stop distance, as counts and shares; major stops inside the tract; and
   `transit_rich` = at least half the units within the major-stop distance
   (`parking.transit_rich_min_share`) or a major stop inside the tract. This is the same rule
   as `scenarios/parking.py`; a test checks they agree on Testland.

## Feed discovery and the status ladder

`assumptions/transit_feeds.yaml` lists schedule feeds found outside the Mobility Database
catalog (today: Wyoming), each with its agency's National Transit Database id, its Transitland
Atlas entry, whether the build reads it (`read`) and a legal record (`usable`, `forbidden` or
`not_reviewed`, with the date checked and the basis). Only `read: true` feeds whose status is
`usable` are fetched, by the same reader as every other feed; a feed whose terms forbid reuse is
never fetched. The feed itself is never stored: only per-stop departure counts and a note with
the service dates (`valid_from`, `valid_to`, `representative_date`, and the publisher's own
`feed_start_date`/`feed_end_date` from `feed_info.txt` when given).

`features/transit_status.py` then answers five questions for every county and for the state,
so that "not measured" is never shown as "none":

| Stage | Question | Answers |
|---|---|---|
| `discovered` | Is a schedule feed listed for the agencies whose bus stops are here? | yes / partial / no / not_reached (no bus stop) |
| `legal` | May the discovered feeds be read and their counts used? | yes / partial / no / unknown (terms not reviewed) / not_reached |
| `coverage` | Bus stops whose weekday departures were counted from a current feed | yes / partial / no; rail counts as measured (major by mode) |
| `headway` | Any stop meeting the benchmark rule (rail, or a bus stop with ≥ 64 weekday departures)? | yes / measured_zero (every stop measured, none qualifies) / unknown (some stops not measured) |
| `headway_peak` | The same with the peak rule (every 15 minutes or better in both weekday peaks; `transit_walk_county`) | yes / measured_zero / unknown |
| `feasible` | Does the mid-rise prototype pencil on any eligible land (TOD funnel, P50)? | yes / no / not_reached |

A feed that is only discovered adds no homes: homes come only from the TOD funnel, on land near
stops that were measured and qualify. Each row carries a **completeness label**
("complete", or "partial footprint: M of N bus stops (x%) from a of b agencies have measured
service"). When the state's measured share of bus stops is below
`tod.status_min_measured_bus_share` (0.5), the unmeasured part has no defensible bound: the TOD
run marks its statewide range and percent change as not shown (`status.statewide.shown` =
false, and the record names say "measured contribution only"), and reports the measured
contribution with its label. The TOD run's JSON result carries the whole ladder under
`status` (state, counties, and the display decision).

`visionhousing transit-stops --state WY` (`data/pack.py::add_transit_stops`) rebuilds a built
pack's `transit_stops`, `transit_feeds` and the tracts' `transit_*` columns from the cache, as a
full build makes them; run `visionhousing transit-walk --state WY` after it.

## Outputs (state pack)

- `transit_stops`: one row per stop in the state. The first five columns are Testland's
  (`stop_id, lat, lon, mode, weekday_departures`); then `major, frequency, route_types,
  county, tract, ntd_id, feed_id, stop_name` (names only for major stops, to save space).
- `transit_feeds`: one row per NTM feed with stops in the state: bus stops, how many were
  counted, major bus stops, which schedule feed and date. Schedule feeds that matched no NTM
  stops are listed too.
- `tracts`: the `transit_*` columns listed in the data dictionary.

## Pennsylvania (build of 2026-11-22)

| Measure | Value |
|---|---|
| Stops in the state | 36,790 (964 rail, 35,812 bus, 14 unknown) |
| Rail stations, platforms within 0.1 mile joined | 413 |
| Major stops | 7,139 (964 rail, 6,175 frequent bus) |
| Bus stops with counted departures | 21,521 of 35,812 (60%) |
| Schedule feeds read | 12 (SEPTA bus and rail, Pittsburgh Regional Transit, Red Rose, BARTA, Beaver, Monroe, PHLASH; Erie, LANTA, CATA and Rabbit Transit only as expired schedules) |
| 2020 homes within 1/4 / 1/2 / 3/4 mile of a major stop | 16.6% / 22.1% / 25.6% |
| 2020 homes within the major-stop distance | 19.1% |
| Transit-rich tracts | 809 of 3,445 |

## Wyoming (transit rebuilt 2026-11-25)

The Mobility Database lists no Wyoming feed. The Transitland Atlas list in
`assumptions/transit_feeds.yaml` gives two operator feeds that are read and three that are only
recorded as discovered.

| Measure | Value |
|---|---|
| Stops in the state (National Transit Map) | 367 (349 bus, 18 unknown mode), 7 agencies |
| Feeds read | START Bus, Jackson (valid 2026-11-25 to 2026-12-25; representative weekday 2026-11-25) and Cheyenne Transit Program (2026-01-01 to 2030-12-31; peak counts on 2026-01-06) |
| Discovered, not read | Casper Area Transit, University of Wyoming, Goose Creek Transit (Sheridan); terms not reviewed |
| Bus stops with counted departures | 135 of 349 (38.6%), from 2 of 7 agencies |
| Major stops | 8 (START, Teton County); peak rule: 6. Cheyenne's busiest stop has 52 weekday departures |
| Status (state) | discovered partial; legal partial; coverage partial; headway yes; feasible no; statewide range not shown |

START's feed was read in late November, before its winter season, so its counts are the fall
schedule. Both Teton County walking-network tiles timed out on the keyless Overpass server, so
the network walksheds there are straight-line fallback.

## What it cannot tell you

- Frequency is **scheduled**, not delivered, service, on one weekday.
- Agencies without a keyless feed (for example Lehigh and Northampton, Erie, Centre Area,
  Lackawanna, Luzerne, Altoona, Cambria, Westmoreland, Susquehanna/rabbittransit, River
  Valley, NJ Transit buses in Philadelphia) have no counted bus frequency. Their bus stops
  never count as major, so transit access outside Philadelphia, Pittsburgh, Lancaster and
  Reading is understated. The `transit_feeds` table lists them.
- Bus rapid transit is not told apart from other buses; Pittsburgh's busway stations count
  because their departures reach 64.
- Straight-line distance overstates walking access where rivers, rail lines or highways cut
  the path.
- Every rail stop counts, whatever its frequency (a regional rail stop with a few trains a day
  counts like a subway station), as the major-stop rule says.

## How to check it

- `tests/test_transit.py::test_hand_checkable_tract_features`: one station, four blocks at
  0.2, 0.4, 0.6 and 1.0 mile, a frequent bus stop; every count is in the docstring.
- `tests/test_transit.py::test_departures_skip_last_stops_and_no_pickup_and_count_frequency_runs`:
  a six-stop feed whose departures can be counted by eye.
- `make test-realdata`: Pennsylvania's station count is between 250 and 700; SEPTA and
  Pittsburgh bus stops are over 95% counted; Philadelphia has over 80% of its homes within the
  major-stop distance.
