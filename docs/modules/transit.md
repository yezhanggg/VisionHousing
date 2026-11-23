# Transit stops and access (`data/sources/transit.py`, `features/transit.py`)

Code: `src/visionhousing/data/sources/transit.py`, `src/visionhousing/features/transit.py`.
Tests: `tests/test_transit.py`, `tests/test_report_card.py` (offline),
`tests/test_transit_realdata.py` (live files, `make test-realdata`).

## What it does, in plain words

The transit-oriented density and parking reforms need to know which homes are near good
transit. This module finds every **major transit stop** in the state (decision D-07) and
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
   place (200 m); at least half must match. A stop takes the departures of the same id, or
   of the nearest stop of that feed within 50 m.
5. Frequency status: `mode_only` (rail: major by mode), `counted`, `stale` (the only keyless
   schedule had expired when fetched; not major), `unknown` (no keyless feed; not major).
6. Per block: the distance to the nearest rail stop and to the nearest frequent bus stop.
   Each point becomes a unit vector; the nearest stop has the largest dot product, found by
   one matrix product per chunk; the distance to it is then computed by haversine. Exact,
   pure numpy, about 4 seconds for Pennsylvania's 330,000 blocks.
7. Per tract (2020 block counts): units and land within 1/4, 1/2, 3/4 mile of any major stop
   and within the D-07 distance, as counts and shares; major stops inside the tract; and
   `transit_rich` = at least half the units within the D-07 distance
   (`parking.transit_rich_min_share`) or a major stop inside the tract. This is the same rule
   as `scenarios/parking.py`; a test checks they agree on Testland.

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
| 2020 homes within the D-07 distance | 19.1% |
| Transit-rich tracts | 809 of 3,445 |

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
  counts like a subway station), as D-07 says.

## How to check it

- `tests/test_transit.py::test_hand_checkable_tract_features`: one station, four blocks at
  0.2, 0.4, 0.6 and 1.0 mile, a frequent bus stop; every count is in the docstring.
- `tests/test_transit.py::test_departures_skip_last_stops_and_no_pickup_and_count_frequency_runs`:
  a six-stop feed whose departures can be counted by eye.
- `make test-realdata`: Pennsylvania's station count is between 250 and 700; SEPTA and
  Pittsburgh bus stops are over 95% counted; Philadelphia has over 80% of its homes within the
  D-07 distance.
